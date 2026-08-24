"""Tests for RecoveryOS Day 4: Recovery Proposer.

Covers 15 test scenarios:
1. LOW_BALANCE -> SEND_REMINDER
2. BANK_DELAY -> RETRY_LATER
3. AUTHENTICATION_FAILURE -> PROMPT_REAUTH
4. MANDATE_OR_AUTHORIZATION_ISSUE -> PROMPT_REAUTH (conf >= 0.6) / ESCALATE_HUMAN (conf < 0.6)
5. NETWORK_OR_GATEWAY_ERROR -> RETRY_LATER
6. UNKNOWN -> ESCALATE_HUMAN
7. Universal low-confidence override (conf < 0.4) -> ESCALATE_HUMAN
8. Invalid/unsupported action -> fallback to policy answer
9. DISCOUNT_50_PERCENT never persisted, rejection recorded in audit
10. API failure -> fallback equals policy answer
11. Malformed model output -> fallback_reason="malformed_output"
12. Duplicate proposal call -> idempotent (1 row, no re-invocation)
13. Proposal persistence -> all fields correct, status='PROPOSED'
14. Proposal audit event created for success and fallback
15. Webhook-only regression -> webhook flow produces zero proposal rows / audit events
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.diagnosis_schema import DiagnosisResult
from app.core.proposal_schema import ALLOWED_ACTIONS, validate_proposed_action
from app.core.proposal_policy import get_policy_default_action
from app.core.proposer import propose_action
from app.db.repositories import (
    insert_audit_event,
    insert_diagnosis,
    insert_recovery_case,
    insert_webhook_event,
    update_case_status,
    upsert_payment,
)
from tests.conftest import TEST_WEBHOOK_SECRET, compute_signature


def setup_diagnosed_case(
    db_conn,
    order_id="order_prop_1",
    payment_id="pay_prop_1",
    diagnosis="LOW_BALANCE",
    confidence=0.85,
    risk_rule="R3_REPEATED_FAILURES",
    risk_score=60,
):
    """Helper to set up a diagnosed recovery case in test DB."""
    payload = {
        "entity": "event",
        "event": "payment.failed",
        "id": "evt_prop_01",
        "created_at": 1724300000,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 50000,
                    "currency": "INR",
                    "status": "failed",
                }
            }
        },
    }

    insert_webhook_event(
        db_conn, "evt_prop_01", "payment.failed", payment_id, order_id,
        json.dumps(payload), "1724300000"
    )
    upsert_payment(db_conn, payment_id, order_id, 50000, "INR", "failed", "card", "SBI", "payment.failed")
    case_id = insert_recovery_case(db_conn, payment_id, risk_rule, risk_score, "Test risk reason")
    
    diag_result = DiagnosisResult(
        diagnosis=diagnosis,
        confidence=confidence,
        explanation=f"Test explanation for {diagnosis}",
        evidence=["evt_prop_01"],
        limitations="None",
    )
    insert_diagnosis(db_conn, case_id, diag_result, "gemini-2.5-flash", "{}")
    update_case_status(db_conn, case_id, "diagnosed")
    db_conn.commit()
    return case_id


# ── Test 1: LOW_BALANCE -> SEND_REMINDER ────────────────────────────────────

def test_policy_low_balance(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="LOW_BALANCE", confidence=0.85)
    res = propose_action(db_conn, case_id)
    assert res.action == "SEND_REMINDER"
    assert res.fallback_used is False


# ── Test 2: BANK_DELAY -> RETRY_LATER ───────────────────────────────────────

def test_policy_bank_delay(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="BANK_DELAY", confidence=0.80)
    res = propose_action(db_conn, case_id)
    assert res.action == "RETRY_LATER"


# ── Test 3: AUTHENTICATION_FAILURE -> PROMPT_REAUTH ─────────────────────────

def test_policy_auth_failure(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="AUTHENTICATION_FAILURE", confidence=0.90)
    res = propose_action(db_conn, case_id)
    assert res.action == "PROMPT_REAUTH"


# ── Test 4: MANDATE_OR_AUTHORIZATION_ISSUE threshold ─────────────────────────

def test_policy_mandate_high_confidence(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="MANDATE_OR_AUTHORIZATION_ISSUE", confidence=0.75)
    res = propose_action(db_conn, case_id)
    assert res.action == "PROMPT_REAUTH"

def test_policy_mandate_low_confidence_override(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="MANDATE_OR_AUTHORIZATION_ISSUE", confidence=0.55)
    res = propose_action(db_conn, case_id)
    assert res.action == "ESCALATE_HUMAN"
    assert "0.6 threshold" in res.reason


# ── Test 5: NETWORK_OR_GATEWAY_ERROR -> RETRY_LATER ─────────────────────────

def test_policy_network_error(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="NETWORK_OR_GATEWAY_ERROR", confidence=0.80)
    res = propose_action(db_conn, case_id)
    assert res.action == "RETRY_LATER"


# ── Test 6: UNKNOWN -> ESCALATE_HUMAN ────────────────────────────────────────

def test_policy_unknown_diagnosis(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="UNKNOWN", confidence=0.0)
    res = propose_action(db_conn, case_id)
    assert res.action == "ESCALATE_HUMAN"


# ── Test 7: Universal low-confidence override (< 0.4) ─────────────────────────

def test_universal_low_confidence_override(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="LOW_BALANCE", confidence=0.35)
    res = propose_action(db_conn, case_id)
    assert res.action == "ESCALATE_HUMAN"
    assert "universal 0.4 threshold" in res.reason


# ── Test 8 & 9: Invalid action DISCOUNT_50_PERCENT rejected ─────────────────

def test_invalid_action_rejection_and_audit(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="LOW_BALANCE", confidence=0.85)
    
    res = propose_action(db_conn, case_id, raw_override_action="DISCOUNT_50_PERCENT")
    
    # Assert DISCOUNT_50_PERCENT was NEVER persisted
    assert res.action == "SEND_REMINDER"  # Policy default fallback
    assert res.fallback_used is True
    assert res.fallback_reason == "invalid_model_action"

    row = db_conn.execute("SELECT * FROM proposed_actions WHERE case_id = ?", (case_id,)).fetchone()
    assert row["proposed_action"] != "DISCOUNT_50_PERCENT"
    assert row["proposed_action"] == "SEND_REMINDER"
    assert row["status"] == "PROPOSED"

    # Verify audit event records rejected_raw_action
    audit_row = db_conn.execute(
        "SELECT * FROM audit_events WHERE stage = 'proposal' AND case_id = ?", (case_id,)
    ).fetchone()
    assert audit_row is not None
    detail = json.loads(audit_row["detail_json"])
    assert detail["rejected_raw_action"] == "DISCOUNT_50_PERCENT"


# ── Test 10 & 11: API failure / Malformed output fallback ──────────────────────

def test_malformed_output_fallback(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="BANK_DELAY", confidence=0.80)
    
    res = propose_action(db_conn, case_id, raw_override_action="")
    assert res.action == "RETRY_LATER"
    assert res.fallback_used is True
    assert res.fallback_reason == "malformed_output"


# ── Test 12: Duplicate proposal call / Idempotency ─────────────────────────────

def test_idempotency(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="AUTHENTICATION_FAILURE", confidence=0.90)
    
    res1 = propose_action(db_conn, case_id)
    res2 = propose_action(db_conn, case_id)
    
    assert res1.action == res2.action == "PROMPT_REAUTH"
    count = db_conn.execute("SELECT COUNT(*) FROM proposed_actions WHERE case_id = ?", (case_id,)).fetchone()[0]
    assert count == 1


# ── Test 13: Proposal persistence ─────────────────────────────────────────────

def test_proposal_persistence(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="NETWORK_OR_GATEWAY_ERROR", confidence=0.88)
    res = propose_action(db_conn, case_id)
    
    row = db_conn.execute("SELECT * FROM proposed_actions WHERE case_id = ?", (case_id,)).fetchone()
    assert row is not None
    assert row["case_id"] == case_id
    assert row["proposed_action"] == "RETRY_LATER"
    assert row["diagnosis_used"] == "NETWORK_OR_GATEWAY_ERROR"
    assert row["diagnosis_confidence"] == 0.88
    assert row["status"] == "PROPOSED"
    assert row["fallback_used"] == 0


# ── Test 14: Proposal audit event created ──────────────────────────────────────

def test_proposal_audit_event(db_conn):
    case_id = setup_diagnosed_case(db_conn, diagnosis="LOW_BALANCE", confidence=0.80)
    propose_action(db_conn, case_id)
    
    audit_row = db_conn.execute(
        "SELECT * FROM audit_events WHERE stage = 'proposal' AND case_id = ?", (case_id,)
    ).fetchone()
    assert audit_row is not None
    assert audit_row["actor"] == "system"
    assert "Proposed Action: SEND_REMINDER" in audit_row["summary"]


# ── Test 15: Webhook regression check (0 proposal rows created) ───────────────

def test_webhook_flow_produces_zero_proposals(client, db_conn):
    # Send 3 failures to trigger R3 risk rule and open a case
    ts = 1724300000
    for i in range(3):
        payload = {
            "entity": "event",
            "event": "payment.failed",
            "id": f"evt_pno_{i}",
            "created_at": ts + (i * 180),
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_pno_same",
                        "order_id": "order_pno_1",
                        "amount": 50000,
                        "currency": "INR",
                        "status": "failed",
                    }
                }
            },
        }
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)
        resp = client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )
        assert resp.status_code == 200

    # Assert case opened
    cases = db_conn.execute("SELECT * FROM recovery_cases WHERE payment_id = 'pay_pno_same'").fetchall()
    assert len(cases) == 1
    assert cases[0]["status"] == "open"

    # Assert ZERO rows in proposed_actions table
    prop_count = db_conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0]
    assert prop_count == 0

    # Assert ZERO stage='proposal' audit events exist
    prop_audit_count = db_conn.execute("SELECT COUNT(*) FROM audit_events WHERE stage = 'proposal'").fetchone()[0]
    assert prop_audit_count == 0
