"""Repository functions for webhook_events, payments, and audit_events.

All functions receive an explicit sqlite3.Connection — no hidden global
database state. This makes tests trivially isolatable with temp databases.

Uses parameterized queries exclusively to prevent SQL injection.
"""

import json
import sqlite3
from datetime import datetime, timezone


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
