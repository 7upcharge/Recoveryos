"""Repository functions for webhook_events, payments, and audit_events.

All functions receive an explicit sqlite3.Connection — no hidden global
database state. This makes tests trivially isolatable with temp databases.

Uses parameterized queries exclusively to prevent SQL injection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any



def is_duplicate_event(conn: sqlite3.Connection, razorpay_event_id: str) -> bool:
    """Check whether an event with this Razorpay ID has already been stored.

    Args:
        conn: Active database connection.
        razorpay_event_id: The Razorpay-assigned event identifier.

    Returns:
        True if the event already exists in webhook_events.
    """
    row = conn.execute(
        "SELECT 1 FROM webhook_events WHERE razorpay_event_id = ?",
        (razorpay_event_id,),
    ).fetchone()
    return row is not None


def insert_webhook_event(
    conn: sqlite3.Connection,
    razorpay_event_id: str,
    event_type: str,
    payment_id: str | None,
    order_id: str | None,
    payload_json: str,
    event_created_at: str | None,
) -> int:
    """Insert a raw webhook event into webhook_events.

    The raw payload is stored immutably for future replay/audit.

    Args:
        conn: Active database connection.
        razorpay_event_id: Unique event identifier from Razorpay.
        event_type: The event type string (e.g. "payment.failed").
        payment_id: Razorpay payment ID, if applicable.
        order_id: Razorpay order ID, if applicable.
        payload_json: The complete raw JSON payload as a string.
        event_created_at: Timestamp from the event payload, if present.

    Returns:
        The auto-incremented row ID of the inserted event.
    """
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO webhook_events
            (razorpay_event_id, event_type, payment_id, order_id,
             payload_json, received_at, event_created_at, signature_verified)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            razorpay_event_id,
            event_type,
            payment_id,
            order_id,
            payload_json,
            now,
            event_created_at,
        ),
    )
    return cursor.lastrowid


def upsert_payment(
    conn: sqlite3.Connection,
    payment_id: str,
    order_id: str | None,
    amount: int,
    currency: str,
    status: str,
    method: str | None,
    bank: str | None,
    event_type: str,
) -> None:
    """Insert or update a payment record.

    If the payment does not exist, inserts it with attempt_count=1.
    If it exists, updates status, last_status_at, and relevant fields.
    Increments attempt_count only for genuine new payment attempts
    (payment.created events), not for status transitions on the same attempt.

    Args:
        conn: Active database connection.
        payment_id: Razorpay payment ID (primary key).
        order_id: Associated order ID.
        amount: Amount in integer paise (never floating-point).
        currency: Currency code (default INR).
        status: Current payment status string.
        method: Payment method (e.g. "card", "upi", "netbanking").
        bank: Bank identifier, if applicable.
        event_type: The webhook event type, used to decide whether to
            increment attempt_count.
    """
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT payment_id, attempt_count FROM payments WHERE payment_id = ?",
        (payment_id,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO payments
                (payment_id, order_id, amount, currency, status, method, bank,
                 first_attempt_at, last_status_at, attempt_count, is_disputed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
            """,
            (payment_id, order_id, amount, currency, status, method, bank, now, now),
        )
    else:
        # Increment attempt_count only when the event represents a genuinely
        # new payment attempt. "payment.created" signals a new attempt;
        # other events (authorized, captured, failed) are status transitions
        # on an existing attempt and must not inflate the counter.
        new_attempt_count = existing["attempt_count"]
        if event_type == "payment.created":
            new_attempt_count += 1

        conn.execute(
            """
            UPDATE payments
            SET status = ?,
                order_id = COALESCE(?, order_id),
                amount = ?,
                currency = ?,
                method = COALESCE(?, method),
                bank = COALESCE(?, bank),
                last_status_at = ?,
                attempt_count = ?
            WHERE payment_id = ?
            """,
            (
                status,
                order_id,
                amount,
                currency,
                method,
                bank,
                now,
                new_attempt_count,
                payment_id,
            ),
        )


def insert_audit_event(
    conn: sqlite3.Connection,
    stage: str,
    actor: str,
    summary: str,
    detail_json: str | None = None,
    case_id: int | None = None,
) -> int:
    """Insert an audit log entry.

    On Day 1, case_id is always NULL because recovery_cases does not exist yet.

    Args:
        conn: Active database connection.
        stage: Pipeline stage (e.g. "ingestion").
        actor: Who/what triggered this event (e.g. "system").
        summary: Human-readable description of what happened.
        detail_json: Optional JSON string with structured detail.
        case_id: Foreign key to recovery_cases (NULL on Day 1).

    Returns:
        The auto-incremented row ID of the inserted audit event.
    """
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO audit_events
            (case_id, timestamp, stage, actor, summary, detail_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (case_id, now, stage, actor, summary, detail_json),
    )
    return cursor.lastrowid


# ── Day 2: Recovery Cases & Event History ────────────────────────────────────

def get_event_history_for_order(
    conn: sqlite3.Connection,
    order_id: str,
) -> list:
    """Query webhook_events for all rows matching the order.

    Parses payload_json, maps into NormalizedEvent objects, and returns
    them sorted by timestamp ascending. This is the bridge between stored
    raw JSON and the pure-logic detector's input type.

    Args:
        conn: Active database connection.
        order_id: Razorpay order ID to fetch events for.

    Returns:
        List of NormalizedEvent objects sorted by timestamp ascending.
    """
    from app.core.risk_detector import NormalizedEvent

    rows = conn.execute(
        """
        SELECT event_type, payment_id, order_id, payload_json, event_created_at, received_at
        FROM webhook_events
        WHERE order_id = ?
        ORDER BY COALESCE(event_created_at, received_at) ASC
        """,
        (order_id,),
    ).fetchall()

    events = []
    for row in rows:
        # Parse timestamp: prefer event_created_at (Razorpay's timestamp),
        # fall back to received_at (our ingestion timestamp).
        raw_ts = row["event_created_at"] or row["received_at"]
        try:
            # Razorpay sends created_at as Unix timestamp (stored as string).
            ts = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc)
        except (ValueError, TypeError):
            # If it's already an ISO string (e.g. from received_at).
            try:
                ts = datetime.fromisoformat(raw_ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ts = datetime.now(timezone.utc)

        # Extract error_code from payload if present.
        error_code = None
        try:
            payload = json.loads(row["payload_json"])
            payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
            error_code = payment_entity.get("error_code")
        except (json.JSONDecodeError, AttributeError):
            pass

        events.append(NormalizedEvent(
            event_type=row["event_type"],
            payment_id=row["payment_id"] or "",
            order_id=row["order_id"] or order_id,
            timestamp=ts,
            error_code=error_code,
        ))

    return events


def get_open_case_for_payment(
    conn: sqlite3.Connection,
    payment_id: str,
) -> dict | None:
    """Return an existing recovery_cases row with status='open' for this payment.

    Used for idempotency — prevents opening duplicate recovery cases for
    the same active condition.

    Args:
        conn: Active database connection.
        payment_id: Razorpay payment ID to check.

    Returns:
        A dict-like Row if an open case exists, None otherwise.
    """
    row = conn.execute(
        "SELECT * FROM recovery_cases WHERE payment_id = ? AND status = 'open'",
        (payment_id,),
    ).fetchone()
    return dict(row) if row else None


def insert_recovery_case(
    conn: sqlite3.Connection,
    payment_id: str,
    risk_rule: str,
    risk_score: int,
    risk_reason: str,
) -> int:
    """Insert a new recovery case and return its ID.

    Args:
        conn: Active database connection.
        payment_id: Razorpay payment ID that triggered the case.
        risk_rule: Comma-joined list of fired rules.
        risk_score: Combined risk score (0-100).
        risk_reason: Human-readable explanation of why the case was opened.

    Returns:
        The auto-incremented row ID of the new case.
    """
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO recovery_cases
            (payment_id, opened_at, risk_rule, risk_score, risk_reason, status)
        VALUES (?, ?, ?, ?, ?, 'open')
        """,
        (payment_id, now, risk_rule, risk_score, risk_reason),
    )
    return cursor.lastrowid


# ── Day 3: Diagnoses & Case Status Updates ───────────────────────────────────

def get_recovery_case_by_id(
    conn: sqlite3.Connection,
    case_id: int,
) -> dict | None:
    """Fetch a recovery case by its primary key ID.

    Args:
        conn: Active database connection.
        case_id: Recovery case primary key ID.

    Returns:
        Dict representing recovery_case row if found, or None.
    """
    row = conn.execute(
        "SELECT * FROM recovery_cases WHERE id = ?",
        (case_id,),
    ).fetchone()
    return dict(row) if row else None


def get_latest_diagnosis_for_case(
    conn: sqlite3.Connection,
    case_id: int,
) -> dict | None:
    """Fetch the latest diagnosis entry for a recovery case.

    Args:
        conn: Active database connection.
        case_id: Case ID to query.

    Returns:
        Dict representing diagnosis row if existing, or None.
    """
    row = conn.execute(
        "SELECT * FROM diagnoses WHERE case_id = ? ORDER BY id DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    return dict(row) if row else None


def insert_diagnosis(
    conn: sqlite3.Connection,
    case_id: int,
    result: Any,  # DiagnosisResult
    model_name: str,
    raw_model_output: str,
) -> int:
    """Insert a diagnosis entry into the diagnoses table.

    Args:
        conn: Active database connection.
        case_id: Associated recovery_case ID.
        result: DiagnosisResult dataclass object.
        model_name: Gemini model name used.
        raw_model_output: Raw JSON string from model response.

    Returns:
        The auto-incremented row ID of the inserted diagnosis.
    """
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO diagnoses
            (case_id, diagnosed_at, likely_cause, confidence, explanation,
             evidence_json, limitations, raw_model_output, model_name,
             fallback_used, fallback_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id,
            now,
            result.diagnosis,
            result.confidence,
            result.explanation,
            json.dumps(result.evidence),
            result.limitations,
            raw_model_output,
            model_name,
            1 if result.fallback_used else 0,
            result.fallback_reason,
        ),
    )
    return cursor.lastrowid


def update_case_status(
    conn: sqlite3.Connection,
    case_id: int,
    new_status: str,
) -> None:
    """Update the status column of a recovery_cases row.

    Args:
        conn: Active database connection.
        case_id: Recovery case ID.
        new_status: Target status string (e.g. 'diagnosed').
    """
    conn.execute(
        "UPDATE recovery_cases SET status = ? WHERE id = ?",
        (new_status, case_id),
    )


# ── Dashboard Query Helpers ──────────────────────────────────────────────────

def get_dashboard_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Calculate summary metrics for the developer dashboard from live SQLite data.

    Returns:
        Dict with total_payments, payments_at_risk, open_recovery_cases,
        diagnosed_cases, and total_value_at_risk_paise.
    """
    total_payments = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    payments_at_risk = conn.execute("SELECT COUNT(DISTINCT payment_id) FROM recovery_cases").fetchone()[0]
    open_cases = conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE status = 'open'").fetchone()[0]
    diagnosed_cases = conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE status = 'diagnosed'").fetchone()[0]
    
    val_row = conn.execute(
        """
        SELECT COALESCE(SUM(p.amount), 0)
        FROM payments p
        JOIN recovery_cases rc ON p.payment_id = rc.payment_id
        WHERE rc.status IN ('open', 'diagnosed')
        """
    ).fetchone()
    total_val_paise = val_row[0] if val_row else 0

    return {
        "total_payments": total_payments,
        "payments_at_risk": payments_at_risk,
        "open_recovery_cases": open_cases,
        "diagnosed_cases": diagnosed_cases,
        "total_value_at_risk_paise": total_val_paise,
    }


def get_all_recovery_cases_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Fetch all recovery cases with associated payment and latest diagnosis info.

    Sorted newest cases first (id DESC).

    Returns:
        List of dicts containing case, payment, and diagnosis summary fields.
    """
    sql = """
    SELECT 
        rc.id AS case_id,
        rc.payment_id,
        COALESCE(p.order_id, '') AS order_id,
        rc.risk_rule,
        rc.risk_score,
        rc.risk_reason,
        rc.status AS case_status,
        rc.opened_at,
        COALESCE(p.amount, 0) AS amount,
        COALESCE(p.currency, 'INR') AS currency,
        d.likely_cause,
        d.confidence
    FROM recovery_cases rc
    LEFT JOIN payments p ON rc.payment_id = p.payment_id
    LEFT JOIN (
        SELECT d1.* FROM diagnoses d1
        INNER JOIN (
            SELECT case_id, MAX(id) AS max_id FROM diagnoses GROUP BY case_id
        ) d2 ON d1.id = d2.max_id
    ) d ON rc.id = d.case_id
    ORDER BY rc.id DESC
    """
    rows = conn.execute(sql).fetchall()
    return [dict(row) for row in rows]


def get_case_detail_full(conn: sqlite3.Connection, case_id: int) -> dict[str, Any] | None:
    """Fetch complete detail payload for a single case including timeline and audit.

    Returns:
        Dict containing case, payment, diagnosis, timeline events, and audit trail, or None.
    """
    case_row = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (case_id,)).fetchone()
    if not case_row:
        return None

    case_dict = dict(case_row)
    payment_id = case_dict["payment_id"]

    payment_row = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    payment_dict = dict(payment_row) if payment_row else {}

    diagnosis_row = conn.execute(
        "SELECT * FROM diagnoses WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
    ).fetchone()
    diagnosis_dict = dict(diagnosis_row) if diagnosis_row else None
    if diagnosis_dict and diagnosis_dict.get("evidence_json"):
        try:
            diagnosis_dict["evidence"] = json.loads(diagnosis_dict["evidence_json"])
        except Exception:
            diagnosis_dict["evidence"] = []
    elif diagnosis_dict:
        diagnosis_dict["evidence"] = []

    order_id = payment_dict.get("order_id") or ""
    
    # Timeline events from webhook_events
    if order_id:
        timeline_rows = conn.execute(
            """
            SELECT razorpay_event_id, event_type, payment_id, order_id, event_created_at, received_at, payload_json
            FROM webhook_events
            WHERE order_id = ? OR payment_id = ?
            ORDER BY COALESCE(event_created_at, received_at) ASC
            """,
            (order_id, payment_id),
        ).fetchall()
    else:
        timeline_rows = conn.execute(
            """
            SELECT razorpay_event_id, event_type, payment_id, order_id, event_created_at, received_at, payload_json
            FROM webhook_events
            WHERE payment_id = ?
            ORDER BY COALESCE(event_created_at, received_at) ASC
            """,
            (payment_id,),
        ).fetchall()

    timeline_events = []
    for tr in timeline_rows:
        ts = tr["event_created_at"] or tr["received_at"]
        timeline_events.append({
            "event_id": tr["razorpay_event_id"],
            "event_type": tr["event_type"],
            "payment_id": tr["payment_id"],
            "order_id": tr["order_id"],
            "timestamp": ts,
        })

    # Audit events
    case_pattern = f'%"case_id": {case_id}%'
    audit_rows = conn.execute(
        """
        SELECT * FROM audit_events
        WHERE case_id = ? OR detail_json LIKE ?
        ORDER BY id ASC
        """,
        (case_id, case_pattern),
    ).fetchall()

    audit_events = [dict(ar) for ar in audit_rows]

    # Proposal
    proposal_row = conn.execute(
        "SELECT * FROM proposed_actions WHERE case_id = ? ORDER BY id DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    proposal_dict = dict(proposal_row) if proposal_row else None

    return {
        "case": case_dict,
        "payment": payment_dict,
        "diagnosis": diagnosis_dict,
        "proposal": proposal_dict,
        "timeline": timeline_events,
        "audit_events": audit_events,
    }


# ── Day 4: Proposed Actions Repository Functions ─────────────────────────────

def get_active_proposal_for_case(
    conn: sqlite3.Connection,
    case_id: int,
) -> dict[str, Any] | None:
    """Fetch the latest proposal entry for a recovery case.

    Args:
        conn: Active database connection.
        case_id: Case ID to query.

    Returns:
        Dict representing proposed_actions row if existing, or None.
    """
    row = conn.execute(
        "SELECT * FROM proposed_actions WHERE case_id = ? ORDER BY id DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    return dict(row) if row else None


def insert_proposed_action(
    conn: sqlite3.Connection,
    case_id: int,
    diagnosis_id: int,
    result: Any,  # ProposalResult
    model_name: str | None = None,
) -> int:
    """Insert a proposed action entry into proposed_actions table with status='PROPOSED'.

    Args:
        conn: Active database connection.
        case_id: Associated recovery_case ID.
        diagnosis_id: Associated diagnosis ID.
        result: ProposalResult dataclass object.
        model_name: Optional model identifier string.

    Returns:
        The auto-incremented row ID of the inserted proposal.
    """
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO proposed_actions
            (case_id, diagnosis_id, proposed_action, reason, diagnosis_used,
             diagnosis_confidence, created_at, model_name, fallback_used,
             fallback_reason, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROPOSED')
        """,
        (
            case_id,
            diagnosis_id,
            result.action,
            result.reason,
            result.diagnosis_used,
            result.diagnosis_confidence,
            now,
            model_name,
            1 if result.fallback_used else 0,
            result.fallback_reason,
        ),
    )
    return cursor.lastrowid




