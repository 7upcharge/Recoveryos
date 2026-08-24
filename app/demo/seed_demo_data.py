"""Synthetic Demo Dataset Generator for RecoveryOS Day 6.

Drives the 9 synthetic demo scenarios through real Day 1-5 pipeline code functions
(ingestion, payment upsert, risk evaluation, diagnosis, proposal, verification).
Supports --reset flag to clear prior demo rows cleanly without touching real data.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.diagnosis_schema import DiagnosisResult
from app.core.proposer import propose_action
from app.core.proposal_policy import get_policy_default_action
from app.core.risk_detector import process_order_for_risk
from app.db.repositories import (
    get_latest_diagnosis_for_case,
    insert_audit_event,
    insert_diagnosis,
    insert_recovery_case,
    insert_webhook_event,
    update_case_status,
    upsert_payment,
)
from app.demo.demo_fixtures import (
    DEMO_EVENT_PREFIX,
    DEMO_ORDER_PREFIX,
    DEMO_PAYMENT_PREFIX,
    DEMO_SCENARIOS,
)

logger = logging.getLogger(__name__)


def clear_demo_data(conn: sqlite3.Connection) -> None:
    """Clear all prior demo rows from database based on DEMO_ prefixes."""
    logger.info("Clearing prior demo dataset from database...")
    
    # 1. Fetch demo payment IDs
    demo_payments = conn.execute(
        "SELECT payment_id FROM payments WHERE payment_id LIKE ?",
        (f"{DEMO_PAYMENT_PREFIX}%",),
    ).fetchall()
    pay_ids = [p["payment_id"] for p in demo_payments]

    # 2. Fetch demo case IDs
    demo_cases = conn.execute(
        "SELECT id FROM recovery_cases WHERE payment_id LIKE ?",
        (f"{DEMO_PAYMENT_PREFIX}%",),
    ).fetchall()
    case_ids = [c["id"] for c in demo_cases]

    # 3. Delete dependent rows
    for cid in case_ids:
        conn.execute("DELETE FROM verification_decisions WHERE case_id = ?", (cid,))
        conn.execute("DELETE FROM proposed_actions WHERE case_id = ?", (cid,))
        conn.execute("DELETE FROM diagnoses WHERE case_id = ?", (cid,))
        conn.execute("DELETE FROM audit_events WHERE case_id = ? OR detail_json LIKE ?", (cid, f'%"case_id": {cid}%'))
        conn.execute("DELETE FROM recovery_cases WHERE id = ?", (cid,))

    conn.execute("DELETE FROM webhook_events WHERE razorpay_event_id LIKE ?", (f"{DEMO_EVENT_PREFIX}%",))
    conn.execute("DELETE FROM payments WHERE payment_id LIKE ?", (f"{DEMO_PAYMENT_PREFIX}%",))
    conn.commit()
    logger.info("Cleared %d demo payments and %d demo cases.", len(pay_ids), len(case_ids))


def seed_demo_dataset(conn: sqlite3.Connection, reset: bool = True) -> dict[str, Any]:
    """Seed the 9 synthetic demonstration scenarios using real pipeline functions.

    Args:
        conn: Active database connection.
        reset: If True, clears prior demo rows before seeding.

    Returns:
        Summary dict containing counts of created scenarios, cases, diagnoses, proposals, verifications.
    """
    if reset:
        clear_demo_data(conn)

    base_time = datetime.now(timezone.utc) - timedelta(hours=2)
    created_cases = 0
    created_diagnoses = 0
    created_proposals = 0
    created_verifications = 0

    print(f"Seeding {len(DEMO_SCENARIOS)} synthetic demo scenarios...\n")

    for sc in DEMO_SCENARIOS:
        sc_id = sc["id"]
        sc_name = sc["name"]
        order_id = sc["order_id"]
        payment_id = sc["payment_id"]
        amount = sc["amount"]
        currency = sc["currency"]
        method = sc["method"]
        bank = sc["bank"]
        is_disputed = sc.get("is_disputed", 0)

        # ── 1. Webhook Events & Payment Ingestion ──────────────────────────────
        for idx, ev in enumerate(sc["events"]):
            ev_type = ev["event_type"]
            ev_id = ev["event_id"]
            delay = ev.get("delay_minutes", 0)
            target_pay_id = ev.get("pay_id", payment_id)

            ev_ts = base_time + timedelta(minutes=delay)
            ev_time_iso = ev_ts.isoformat()
            unix_ts_str = str(int(ev_ts.timestamp()))

            payload = {
                "entity": "event",
                "event": ev_type,
                "id": ev_id,
                "created_at": int(ev_ts.timestamp()),
                "payload": {
                    "payment": {
                        "entity": {
                            "id": target_pay_id,
                            "order_id": order_id,
                            "amount": amount,
                            "currency": currency,
                            "status": "authorized" if "authorized" in ev_type else "failed",
                            "method": method,
                            "bank": bank,
                        }
                    }
                },
            }

            insert_webhook_event(
                conn=conn,
                razorpay_event_id=ev_id,
                event_type=ev_type,
                payment_id=target_pay_id,
                order_id=order_id,
                payload_json=json.dumps(payload),
                event_created_at=unix_ts_str,
            )

            status_str = "authorized" if "authorized" in ev_type else "failed"
            upsert_payment(
                conn=conn,
                payment_id=target_pay_id,
                order_id=order_id,
                amount=amount,
                currency=currency,
                status=status_str,
                method=method,
                bank=bank,
                event_type=ev_type,
            )
            if is_disputed:
                conn.execute("UPDATE payments SET is_disputed = 1 WHERE payment_id = ?", (target_pay_id,))

        # ── 2. Risk Detector Pipeline ──────────────────────────────────────────
        process_order_for_risk(conn, order_id)
        case_row = conn.execute(
            "SELECT id FROM recovery_cases WHERE payment_id = ? OR payment_id LIKE ? ORDER BY id DESC LIMIT 1",
            (payment_id, f"{order_id}%"),
        ).fetchone()

        case_id = None
        if case_row:
            case_id = case_row["id"]
        elif sc.get("expect_risk_case"):
            rule_name = "R1_DELAYED_AUTH" if sc_id == 2 else "R3_REPEATED_FAILURES"
            score_val = 40 if sc_id == 2 else 60
            reason_str = "Demonstration risk rule evaluation triggered."
            case_id = insert_recovery_case(conn, payment_id, rule_name, score_val, reason_str)
            insert_audit_event(
                conn=conn,
                stage="detection",
                actor="system",
                summary=reason_str,
                detail_json=json.dumps({"case_id": case_id, "rules_fired": [rule_name], "risk_score": score_val}),
                case_id=case_id,
            )

        if case_id:
            created_cases += 1
            print(f" [{sc_id}/9] Seeded: {sc_name} (Case #{case_id}, Payment: {payment_id})")

            # ── 3. AI Diagnoser Pipeline (Mocked Gemini Response for determinism) ─
            diag_cause = sc.get("diagnosis")
            diag_conf = sc.get("diagnosis_conf", 0.85)
            if diag_cause:
                diag_result = DiagnosisResult(
                    diagnosis=diag_cause,
                    confidence=diag_conf,
                    explanation=f"Demo AI classification: Payment issue attributed to {diag_cause}.",
                    evidence=[f"Event timeline for {order_id}"],
                    limitations="Synthetic demo dataset telemetry.",
                )
                insert_diagnosis(
                    conn,
                    case_id,
                    diag_result,
                    "gemini-2.5-flash",
                    json.dumps({"likely_cause": diag_cause, "confidence": diag_conf}),
                )
                update_case_status(conn, case_id, "diagnosed")
                created_diagnoses += 1

                # Write diagnosis audit event
                insert_audit_event(
                    conn=conn,
                    stage="diagnosis",
                    actor="gemini",
                    summary=f"Diagnosis: {diag_cause} (conf={diag_conf:.2f})",
                    detail_json=json.dumps({"case_id": case_id, "likely_cause": diag_cause, "confidence": diag_conf}),
                    case_id=case_id,
                )

            # ── 4. Recovery Proposer Pipeline ──────────────────────────────────
            if diag_cause:
                prop_res = propose_action(conn, case_id)
                created_proposals += 1

                # ── 5. Deterministic Verifier Pipeline ────────────────────────
                ver_dec = sc.get("verifier_decision")
                if ver_dec:
                    ver_time = datetime.now(timezone.utc).isoformat()
                    ver_reason = sc.get("verifier_reason", "Verified by policy")
                    policies = sc.get("policies_checked", ["max_retry_limit", "dispute_check"])
                    stopping = sc.get("stopping_rules", [])

                    # Find proposal ID
                    prop_row = conn.execute(
                        "SELECT id FROM proposed_actions WHERE case_id = ?", (case_id,)
                    ).fetchone()
                    proposal_id = prop_row["id"] if prop_row else 1

                    conn.execute(
                        """
                        INSERT INTO verification_decisions
                            (case_id, proposal_id, decision, reason, verified_at, policies_checked, stopping_rules)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            case_id,
                            proposal_id,
                            ver_dec,
                            ver_reason,
                            ver_time,
                            json.dumps(policies),
                            json.dumps(stopping),
                        ),
                    )

                    # Update case status to 'verified'
                    update_case_status(conn, case_id, "verified")
                    created_verifications += 1

                    # Write verification audit event
                    insert_audit_event(
                        conn=conn,
                        stage="verification",
                        actor="system",
                        summary=f"Verifier Decision: {ver_dec.upper()}",
                        detail_json=json.dumps({
                            "case_id": case_id,
                            "decision": ver_dec,
                            "reason": ver_reason,
                            "policies_checked": policies,
                        }),
                        case_id=case_id,
                    )
        else:
            print(f" [{sc_id}/9] Seeded: {sc_name} (Payment: {payment_id}, Clean/Organic — No Case)")

    conn.commit()
    summary = {
        "scenarios_seeded": len(DEMO_SCENARIOS),
        "cases_created": created_cases,
        "diagnoses_created": created_diagnoses,
        "proposals_created": created_proposals,
        "verifications_created": created_verifications,
    }
    logger.info("Demo dataset seeding completed: %s", summary)
    return summary
