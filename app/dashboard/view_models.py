"""View Model Aggregation & Data Shaping Layer for RecoveryOS Day 6 Dashboard.

All database queries, metrics calculations, status logic, and template payloads live here.
Flask templates MUST NEVER execute raw SQL or ad-hoc query logic directly.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any


def get_dev_mode_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Fetch test mode and developer tunnel telemetry for the dashboard.

    Returns:
        Dict containing public/local webhook endpoints, event telemetry, and verification status.
    """
    base_url = os.environ.get("PUBLIC_WEBHOOK_BASE_URL", "").strip()
    if base_url:
        public_url = f"{base_url.rstrip('/')}/webhooks/razorpay"
    else:
        public_url = "Not Configured (Set PUBLIC_WEBHOOK_BASE_URL in .env)"

    total_webhooks = conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] or 0

    latest_row = conn.execute(
        "SELECT event_type, received_at FROM webhook_events ORDER BY id DESC LIMIT 1"
    ).fetchone()

    latest_event_type = latest_row["event_type"] if latest_row else "None"
    latest_event_at = latest_row["received_at"] if latest_row else "None"

    return {
        "mode": "TEST MODE (Development)",
        "local_webhook_url": "http://localhost:5000/webhooks/razorpay",
        "public_webhook_url": public_url,
        "total_webhooks": total_webhooks,
        "latest_event_type": latest_event_type,
        "latest_event_at": latest_event_at,
        "signature_verification_active": True,
    }



def compute_display_status(case_row: dict[str, Any], verification_row: dict[str, Any] | None = None) -> str:
    """Compute the single source of truth display status for a recovery case.

    Status hierarchy:
        APPROVED   -> verification_decisions decision == 'approved'
        BLOCKED    -> verification_decisions decision == 'blocked'
        PROPOSED   -> recovery_cases.status == 'proposed' (or proposed_actions row exists)
        DIAGNOSED  -> recovery_cases.status == 'diagnosed' (or diagnoses row exists)
        RISK       -> recovery_cases.status == 'open'
        RECOVERED  -> Reserved for future execution stage (not reachable today)

    Args:
        case_row: Dict representation of recovery_cases record.
        verification_row: Optional dict representation of verification_decisions record.

    Returns:
        Exact status string in uppercase.
    """
    if verification_row:
        dec = str(verification_row.get("decision", "")).lower()
        if dec == "approved":
            return "APPROVED"
        elif dec == "blocked":
            return "BLOCKED"

    status = str(case_row.get("status", "")).lower()
    if status == "proposed":
        return "PROPOSED"
    elif status == "diagnosed":
        return "DIAGNOSED"
    elif status == "open":
        return "RISK"

    return status.upper() if status else "RISK"


def get_hero_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Calculate hero summary metrics for the Control Center homepage.

    Args:
        conn: Active database connection.

    Returns:
        Dict with total payments, revenue at risk, cases counts, and honest ₹0 recovered.
    """
    # 1. Total Payments Processed
    pay_count = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] or 0

    # 2. Revenue at Risk (sum of payment amounts in open/diagnosed/proposed cases)
    risk_sum_row = conn.execute(
        """
        SELECT SUM(p.amount) FROM payments p
        JOIN recovery_cases c ON p.payment_id = c.payment_id
        WHERE c.status IN ('open', 'diagnosed', 'proposed')
        """
    ).fetchone()[0]
    revenue_at_risk_paisa = risk_sum_row or 0
    revenue_at_risk_inr = revenue_at_risk_paisa / 100.0

    # 3. Case Counts by Stage
    open_cases = conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE status = 'open'").fetchone()[0] or 0
    diagnosed_cases = conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE status = 'diagnosed'").fetchone()[0] or 0
    proposed_cases = conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0] or 0

    # 4. Verification Decisions
    ver_approved = 0
    ver_blocked = 0
    ver_total = 0
    ver_table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='verification_decisions'"
    ).fetchone()
    if ver_table_check:
        ver_total = conn.execute("SELECT COUNT(*) FROM verification_decisions").fetchone()[0] or 0
        ver_approved = conn.execute("SELECT COUNT(*) FROM verification_decisions WHERE decision = 'approved'").fetchone()[0] or 0
        ver_blocked = conn.execute("SELECT COUNT(*) FROM verification_decisions WHERE decision = 'blocked'").fetchone()[0] or 0

    return {
        "payments_processed": pay_count,
        "revenue_at_risk": revenue_at_risk_inr,
        "open_cases": open_cases,
        "diagnosed_cases": diagnosed_cases,
        "proposed_actions_count": proposed_cases,
        "verified_actions_count": ver_total,
        "approved_actions_count": ver_approved,
        "blocked_actions_count": ver_blocked,
        "recovered_amount": 0.0,  # Honesty rule: execution stage not implemented yet
    }


def get_pipeline_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Calculate exact pipeline counts for the 6 horizontal stage flow visualization.

    Stage Pipeline:
        Payments -> At Risk -> Diagnosed -> Proposed -> Verified -> Recovered

    Args:
        conn: Active database connection.

    Returns:
        Dict mapping stage names to exact database row counts.
    """
    payments = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] or 0
    at_risk = conn.execute("SELECT COUNT(*) FROM recovery_cases").fetchone()[0] or 0
    diagnosed = conn.execute("SELECT COUNT(*) FROM diagnoses").fetchone()[0] or 0
    proposed = conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0] or 0

    verified = 0
    ver_table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='verification_decisions'"
    ).fetchone()
    if ver_table_check:
        verified = conn.execute("SELECT COUNT(*) FROM verification_decisions").fetchone()[0] or 0

    return {
        "payments": payments,
        "at_risk": at_risk,
        "diagnosed": diagnosed,
        "proposed": proposed,
        "verified": verified,
        "recovered": 0,  # Honesty rule: execution stage not implemented yet
    }


def get_cases_table_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Fetch and shape all recovery cases for the 11-column Cases Table view.

    Columns: Case ID, Amount, Order ID, Risk Rule, Risk Score, Diagnosis, Confidence,
             Proposed Action, Verification Decision, Case Status, Created Time.

    Args:
        conn: Active database connection.

    Returns:
        List of formatted dicts ordered by newest case first.
    """
    rows = conn.execute(
        """
        SELECT 
            c.id AS case_id,
            c.opened_at,
            c.risk_rule,
            c.risk_score,
            c.risk_reason,
            c.status AS case_status,
            p.payment_id,
            p.order_id,
            p.amount,
            p.currency,
            d.likely_cause AS diagnosis,
            d.confidence AS diagnosis_confidence,
            pa.proposed_action,
            pa.status AS proposal_status
        FROM recovery_cases c
        JOIN payments p ON c.payment_id = p.payment_id
        LEFT JOIN diagnoses d ON c.id = d.case_id
        LEFT JOIN proposed_actions pa ON c.id = pa.case_id
        ORDER BY c.id DESC
        """
    ).fetchall()

    ver_table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='verification_decisions'"
    ).fetchone()

    result = []
    for r in rows:
        row_dict = dict(r)
        cid = row_dict["case_id"]

        ver_row = None
        if ver_table_check:
            v_res = conn.execute(
                "SELECT * FROM verification_decisions WHERE case_id = ? ORDER BY id DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if v_res:
                ver_row = dict(v_res)

        display_status = compute_display_status(row_dict, ver_row)

        ver_decision = ver_row.get("decision", "").upper() if ver_row else "-"

        amount_inr = (row_dict["amount"] or 0) / 100.0

        result.append({
            "case_id": cid,
            "opened_at": row_dict["opened_at"],
            "order_id": row_dict["order_id"] or "-",
            "payment_id": row_dict["payment_id"],
            "amount_inr": amount_inr,
            "currency": row_dict["currency"] or "INR",
            "risk_rule": row_dict["risk_rule"],
            "risk_score": row_dict["risk_score"],
            "risk_reason": row_dict["risk_reason"],
            "diagnosis": row_dict["diagnosis"] or "-",
            "confidence": float(row_dict["diagnosis_confidence"]) if row_dict["diagnosis_confidence"] is not None else None,
            "proposed_action": row_dict["proposed_action"] or "-",
            "verification_decision": ver_decision,
            "display_status": display_status,
        })

    return result


def get_case_detail(conn: sqlite3.Connection, case_id: int) -> dict[str, Any] | None:
    """Fetch all 7 ordered sections of data for a single recovery case detail view.

    Sections: Payment, Timeline, Risk, AI Diagnosis, Proposed Action, Verification, Recovery Result + Audit.

    Args:
        conn: Active database connection.
        case_id: Primary key case ID.

    Returns:
        Structured dict for case_detail.html template, or None if case not found.
    """
    case_row = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (case_id,)).fetchone()
    if not case_row:
        return None

    case_dict = dict(case_row)
    payment_id = case_dict["payment_id"]

    # 1. Payment Info
    payment_row = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    payment_dict = dict(payment_row) if payment_row else {}

    # 2. Timeline (webhook events)
    timeline_events = []
    order_id = payment_dict.get("order_id")
    if order_id:
        we_rows = conn.execute(
            """
            SELECT razorpay_event_id, event_type, payment_id, order_id, received_at
            FROM webhook_events
            WHERE order_id = ? OR payment_id = ?
            ORDER BY id ASC
            """,
            (order_id, payment_id),
        ).fetchall()
        for we in we_rows:
            timeline_events.append({
                "event_id": we["razorpay_event_id"],
                "event_type": we["event_type"],
                "payment_id": we["payment_id"],
                "order_id": we["order_id"],
                "timestamp": we["received_at"],
            })

    # 3. Diagnosis Info
    diag_row = conn.execute(
        "SELECT * FROM diagnoses WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
    ).fetchone()
    diagnosis_dict = None
    if diag_row:
        diagnosis_dict = dict(diag_row)
        evidence = diagnosis_dict.get("evidence_json")
        if evidence:
            try:
                diagnosis_dict["evidence"] = json.loads(evidence)
            except (json.JSONDecodeError, TypeError):
                diagnosis_dict["evidence"] = [evidence]
        else:
            diagnosis_dict["evidence"] = []

    # 4. Proposed Action Info
    prop_row = conn.execute(
        "SELECT * FROM proposed_actions WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
    ).fetchone()
    proposal_dict = dict(prop_row) if prop_row else None

    # 5. Verification Decision Info
    ver_dict = None
    ver_table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='verification_decisions'"
    ).fetchone()
    if ver_table_check:
        ver_row = conn.execute(
            "SELECT * FROM verification_decisions WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
        ).fetchone()
        if ver_row:
            ver_dict = dict(ver_row)
            if ver_dict.get("policies_checked"):
                try:
                    ver_dict["policies_checked_list"] = json.loads(ver_dict["policies_checked"])
                except (json.JSONDecodeError, TypeError):
                    ver_dict["policies_checked_list"] = [ver_dict["policies_checked"]]
            if ver_dict.get("stopping_rules"):
                try:
                    ver_dict["stopping_rules_list"] = json.loads(ver_dict["stopping_rules"])
                except (json.JSONDecodeError, TypeError):
                    ver_dict["stopping_rules_list"] = [ver_dict["stopping_rules"]]

    # 6. Audit Trail
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

    # Pre-compute display status
    display_status = compute_display_status(case_dict, ver_dict)

    return {
        "case": case_dict,
        "payment": payment_dict,
        "timeline": timeline_events,
        "diagnosis": diagnosis_dict,
        "proposal": proposal_dict,
        "verification": ver_dict,
        "execution": None,  # Honesty rule: "Execution pending"
        "audit_events": audit_events,
        "display_status": display_status,
    }


def get_audit_trail(conn: sqlite3.Connection, case_id: int | None = None) -> list[dict[str, Any]]:
    """Fetch chronological audit trail entries for dashboard audit page.

    Args:
        conn: Active database connection.
        case_id: Optional case ID filter.

    Returns:
        List of audit event dicts.
    """
    if case_id is not None:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY id DESC", (case_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT 100").fetchall()

    result = []
    for r in rows:
        d = dict(r)
        detail = d.get("detail_json")
        if detail:
            try:
                d["detail_obj"] = json.loads(detail)
            except (json.JSONDecodeError, TypeError):
                d["detail_obj"] = None
        else:
            d["detail_obj"] = None
        result.append(d)

    return result


def get_organic_vs_assisted_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Calculate organic recovery (R2 signal) vs assisted recovery metrics.

    Args:
        conn: Active database connection.

    Returns:
        Dict with organic and assisted counts and amounts.
    """
    organic_count = conn.execute(
        "SELECT COUNT(*) FROM recovery_cases WHERE risk_rule LIKE '%R2_FAILED_THEN_AUTH%'"
    ).fetchone()[0] or 0

    organic_sum_row = conn.execute(
        """
        SELECT SUM(p.amount) FROM payments p
        JOIN recovery_cases c ON p.payment_id = c.payment_id
        WHERE c.risk_rule LIKE '%R2_FAILED_THEN_AUTH%'
        """
    ).fetchone()[0]
    organic_amount_inr = (organic_sum_row or 0) / 100.0

    return {
        "organic_count": organic_count,
        "organic_amount": organic_amount_inr,
        "assisted_count": 0,  # Honesty rule: execution stage not implemented yet
        "assisted_amount": 0.0,
    }


def get_diagnosis_insights_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Fetch breakdown of diagnosis categories for the Failure Insights panel.

    Returns:
        List of dicts containing category name, count, and percentage ratio.
    """
    total_diag = conn.execute("SELECT COUNT(*) FROM diagnoses").fetchone()[0] or 0
    if total_diag == 0:
        return []

    rows = conn.execute(
        """
        SELECT likely_cause, COUNT(*) as count
        FROM diagnoses
        GROUP BY likely_cause
        ORDER BY count DESC
        """
    ).fetchall()

    result = []
    for r in rows:
        c_name = r["likely_cause"]
        cnt = r["count"]
        pct = round((cnt / total_diag) * 100, 1)
        result.append({
            "category": c_name,
            "count": cnt,
            "pct": pct,
        })
    return result

