"""Tests for RecoveryOS Day 3: Gemini Diagnoser.

Covers 13 test scenarios using mocked Gemini API calls:
1. Valid Gemini response -> fallback_used=False, stored values match
2. Structured output parsing -> evidence list, limitations string correctly parsed
3. Unsupported diagnosis category -> fallback to UNKNOWN (unsupported_diagnosis)
4. Invalid confidence -> fallback to UNKNOWN (invalid_confidence)
5. Malformed model output -> fallback to UNKNOWN (malformed_output)
6. Gemini timeout / API error -> fallback to UNKNOWN (timeout / api_unavailable)
7. UNKNOWN fallback stored correctly in database (likely_cause='UNKNOWN', confidence=0.0, fallback_used=1)
8. Diagnosis stored correctly in database for valid response
9. Diagnosis audit event created with stage='diagnosis' and case_id for both success and fallback
10. Existing diagnosis / idempotency -> second call returns stored result, mock called once
11. Evidence passed to model contains risk_rule, risk_score, and event_history
12. Webhook path is unaffected by diagnosis -> webhook returns 200, opens case with status='open', zero diagnoses rows
13. Full suite regression check
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.diagnosis_schema import ALLOWED_DIAGNOSES, validate_diagnosis_output
from app.core.diagnoser import diagnose_case
from app.core.gemini_client import GeminiUnavailableError
from app.core.risk_detector import process_order_for_risk
from app.db.repositories import (
    insert_audit_event,
    insert_recovery_case,
    insert_webhook_event,
    upsert_payment,
)
from tests.conftest import TEST_WEBHOOK_SECRET, compute_signature


# ── Fixture Helpers ──────────────────────────────────────────────────────────

def setup_test_case(db_conn, order_id="order_diag_1", payment_id="pay_diag_1",
                    risk_rule="R3_REPEATED_FAILURES", risk_score=60):
    """Helper to insert webhook event, payment, and recovery_case into test DB."""
    payload = {
        "entity": "event",
        "event": "payment.failed",
        "id": "evt_diag_01",
        "created_at": 1724300000,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 50000,
                    "currency": "INR",
                    "status": "failed",
                    "error_code": "BAD_REQUEST_ERROR",
                }
            }
        },
    }

    insert_webhook_event(
        conn=db_conn,
        razorpay_event_id="evt_diag_01",
        event_type="payment.failed",
        payment_id=payment_id,
        order_id=order_id,
        payload_json=json.dumps(payload),
        event_created_at="1724300000",
    )
    upsert_payment(
        conn=db_conn,
        payment_id=payment_id,
        order_id=order_id,
        amount=50000,
        currency="INR",
        status="failed",
        method="upi",
        bank=None,
        event_type="payment.failed",
    )
    case_id = insert_recovery_case(
        conn=db_conn,
        payment_id=payment_id,
        risk_rule=risk_rule,
        risk_score=risk_score,
        risk_reason="Repeated failures detected.",
    )
    db_conn.commit()
    return case_id


# ── Test 1: Valid Gemini response ────────────────────────────────────────────

class TestValidGeminiResponse:
    """Mock Gemini returning valid output -> fallback_used=False, values match."""

    def test_valid_response(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "BANK_DELAY",
            "confidence": 0.85,
            "explanation": "Bank response delayed during authorization window.",
            "evidence": ["payment.failed error BAD_REQUEST_ERROR"],
            "limitations": "No direct bank logs available.",
        }

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "BANK_DELAY"
        assert result.confidence == 0.85
        assert result.explanation == "Bank response delayed during authorization window."
        assert result.fallback_used is False
        assert result.fallback_reason is None


# ── Test 2: Structured output parsing ───────────────────────────────────────

class TestStructuredOutputParsing:
    """Mock Gemini returning full JSON -> parsed into DiagnosisResult fields."""

    def test_structured_fields_parsing(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "AUTHENTICATION_FAILURE",
            "confidence": 0.92,
            "explanation": "Customer failed 3DS verification multiple times.",
            "evidence": ["evt_1", "evt_2"],
            "limitations": "OTP SMS gateway latency unknown.",
        }

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "AUTHENTICATION_FAILURE"
        assert result.evidence == ["evt_1", "evt_2"]
        assert result.limitations == "OTP SMS gateway latency unknown."


# ── Test 3: Unsupported diagnosis category ─────────────────────────────────

class TestUnsupportedDiagnosisCategory:
    """Mock returning diagnosis not in ALLOWED_DIAGNOSES -> UNKNOWN fallback."""

    def test_unsupported_diagnosis_fallback(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "CUSTOMER_IS_LYING",
            "confidence": 0.99,
            "explanation": "Custom category not in allowed schema.",
            "evidence": [],
            "limitations": "None.",
        }

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "UNKNOWN"
        assert result.confidence == 0.0
        assert result.fallback_used is True
        assert result.fallback_reason == "unsupported_diagnosis"


# ── Test 4: Invalid confidence ──────────────────────────────────────────────

class TestInvalidConfidence:
    """Mock returning confidence out of range or non-numeric -> UNKNOWN fallback."""

    def test_confidence_out_of_bounds(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "LOW_BALANCE",
            "confidence": 1.5,  # Invalid: > 1.0
            "explanation": "Balance is low.",
            "evidence": [],
            "limitations": "None.",
        }

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "UNKNOWN"
        assert result.confidence == 0.0
        assert result.fallback_used is True
        assert result.fallback_reason == "invalid_confidence"

    def test_confidence_non_numeric(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "LOW_BALANCE",
            "confidence": "HIGH",  # Invalid: non-numeric
            "explanation": "Balance is low.",
            "evidence": [],
            "limitations": "None.",
        }

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "UNKNOWN"
        assert result.fallback_used is True
        assert result.fallback_reason == "invalid_confidence"


# ── Test 5: Malformed model output ──────────────────────────────────────────

class TestMalformedModelOutput:
    """Mock returning missing keys or non-dict -> UNKNOWN fallback."""

    def test_missing_required_keys(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {"diagnosis": "LOW_BALANCE"}  # Missing confidence, explanation, etc.

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "UNKNOWN"
        assert result.fallback_used is True
        assert result.fallback_reason == "schema_validation_failed"


# ── Test 6: Gemini timeout / API error ──────────────────────────────────────

class TestGeminiTimeoutAndAPIError:
    """Mock throwing GeminiUnavailableError -> UNKNOWN fallback."""

    def test_timeout_error_fallback(self, db_conn):
        case_id = setup_test_case(db_conn)

        with patch(
            "app.core.diagnoser.call_gemini_for_diagnosis",
            side_effect=GeminiUnavailableError("API timed out", reason="timeout"),
        ):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "UNKNOWN"
        assert result.confidence == 0.0
        assert result.fallback_used is True
        assert result.fallback_reason == "timeout"

    def test_api_unavailable_error_fallback(self, db_conn):
        case_id = setup_test_case(db_conn)

        with patch(
            "app.core.diagnoser.call_gemini_for_diagnosis",
            side_effect=GeminiUnavailableError("API key invalid", reason="api_unavailable"),
        ):
            result = diagnose_case(db_conn, case_id)

        assert result.diagnosis == "UNKNOWN"
        assert result.fallback_used is True
        assert result.fallback_reason == "api_unavailable"


# ── Test 7: UNKNOWN fallback stored correctly ───────────────────────────────

class TestUnknownFallbackStoredCorrectly:
    """Verify fallback row persisted in diagnoses table has fallback_used=1."""

    def test_fallback_row_in_database(self, db_conn):
        case_id = setup_test_case(db_conn)

        with patch(
            "app.core.diagnoser.call_gemini_for_diagnosis",
            side_effect=GeminiUnavailableError("Network error", reason="api_unavailable"),
        ):
            diagnose_case(db_conn, case_id)

        row = db_conn.execute("SELECT * FROM diagnoses WHERE case_id = ?", (case_id,)).fetchone()
        assert row is not None
        assert row["likely_cause"] == "UNKNOWN"
        assert row["confidence"] == 0.0
        assert row["fallback_used"] == 1
        assert row["fallback_reason"] == "api_unavailable"


# ── Test 8: Diagnosis stored correctly ──────────────────────────────────────

class TestDiagnosisStoredCorrectly:
    """After successful diagnosis, query diagnoses table directly and assert match."""

    def test_successful_diagnosis_stored_in_database(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "NETWORK_OR_GATEWAY_ERROR",
            "confidence": 0.78,
            "explanation": "Timeout between gateway and issuing bank.",
            "evidence": ["pay_diag_1"],
            "limitations": "No direct gateway logs.",
        }

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            diagnose_case(db_conn, case_id)

        row = db_conn.execute("SELECT * FROM diagnoses WHERE case_id = ?", (case_id,)).fetchone()
        assert row is not None
        assert row["case_id"] == case_id
        assert row["likely_cause"] == "NETWORK_OR_GATEWAY_ERROR"
        assert row["confidence"] == 0.78
        assert row["explanation"] == "Timeout between gateway and issuing bank."
        assert row["fallback_used"] == 0
        assert row["fallback_reason"] is None


# ── Test 9: Diagnosis audit event created ───────────────────────────────────

class TestDiagnosisAuditEventCreated:
    """Verify audit_events row created with stage='diagnosis' and correct case_id."""

    def test_audit_event_on_success(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "BANK_DELAY",
            "confidence": 0.80,
            "explanation": "Delayed bank confirmation.",
            "evidence": [],
            "limitations": "None.",
        }

        with patch("app.core.diagnoser.call_gemini_for_diagnosis", return_value=mock_output):
            diagnose_case(db_conn, case_id)

        audit_row = db_conn.execute(
            "SELECT * FROM audit_events WHERE stage = 'diagnosis' AND case_id = ?", (case_id,)
        ).fetchone()

        assert audit_row is not None
        assert audit_row["actor"] == "gemini"
        assert audit_row["case_id"] == case_id
        assert "BANK_DELAY" in audit_row["summary"]

    def test_audit_event_on_fallback(self, db_conn):
        case_id = setup_test_case(db_conn)

        with patch(
            "app.core.diagnoser.call_gemini_for_diagnosis",
            side_effect=GeminiUnavailableError("API Error", reason="api_unavailable"),
        ):
            diagnose_case(db_conn, case_id)

        audit_row = db_conn.execute(
            "SELECT * FROM audit_events WHERE stage = 'diagnosis' AND case_id = ?", (case_id,)
        ).fetchone()

        assert audit_row is not None
        assert audit_row["actor"] == "system"
        assert audit_row["case_id"] == case_id
        assert "fallback" in audit_row["summary"].lower()


# ── Test 10: Existing diagnosis / idempotency ────────────────────────────────

class TestIdempotency:
    """Call diagnose_case twice for same case -> mock called once, 1 row in diagnoses."""

    def test_idempotent_calls_do_not_duplicate(self, db_conn):
        case_id = setup_test_case(db_conn)
        mock_output = {
            "diagnosis": "MANDATE_OR_AUTHORIZATION_ISSUE",
            "confidence": 0.88,
            "explanation": "Mandate limit exceeded.",
            "evidence": [],
            "limitations": "None.",
        }

        mock_client = MagicMock(return_value=mock_output)
        with patch("app.core.diagnoser.call_gemini_for_diagnosis", mock_client):
            res1 = diagnose_case(db_conn, case_id)
            res2 = diagnose_case(db_conn, case_id)

        assert mock_client.call_count == 1
        assert res1.diagnosis == res2.diagnosis == "MANDATE_OR_AUTHORIZATION_ISSUE"

        count = db_conn.execute(
            "SELECT COUNT(*) FROM diagnoses WHERE case_id = ?", (case_id,)
        ).fetchone()[0]
        assert count == 1


# ── Test 11: Evidence passed to model contains event history ───────────────

class TestModelPromptPayload:
    """Inspect mock call arguments to ensure risk rules, risk score, and event history included."""

    def test_prompt_payload_contents(self, db_conn):
        case_id = setup_test_case(db_conn, risk_rule="R1_DELAYED_AUTH,R3_REPEATED_FAILURES", risk_score=100)
        mock_output = {
            "diagnosis": "BANK_DELAY",
            "confidence": 0.80,
            "explanation": "Reasonable",
            "evidence": [],
            "limitations": "None",
        }

        mock_client = MagicMock(return_value=mock_output)
        with patch("app.core.diagnoser.call_gemini_for_diagnosis", mock_client):
            diagnose_case(db_conn, case_id)

        assert mock_client.call_count == 1
        payload = mock_client.call_args[0][0]
        assert payload["case_id"] == case_id
        assert payload["risk_score"] == 100
        assert "R1_DELAYED_AUTH" in payload["rules_fired"]
        assert "R3_REPEATED_FAILURES" in payload["rules_fired"]
        assert isinstance(payload["event_history"], list)
        assert len(payload["event_history"]) >= 1


# ── Test 12: Webhook path is unaffected by diagnosis ─────────────────────────

class TestWebhookPathDecoupled:
    """Sending a signed webhook creates case with status='open', 0 diagnoses rows, 200 OK.

    Gemini API client must NOT be invoked at all during webhook ingestion.
    """

    def test_webhook_does_not_trigger_diagnosis(self, client, db_conn):
        # Build 3 failures to trigger R3 and open a case via webhook
        mock_client = MagicMock()
        with patch("app.core.diagnoser.call_gemini_for_diagnosis", mock_client):
            ts = 1724300000
            for i in range(3):
                payload = {
                    "entity": "event",
                    "event": "payment.failed",
                    "id": f"evt_dec_{i}",
                    "created_at": ts + (i * 180),
                    "payload": {
                        "payment": {
                            "entity": {
                                "id": "pay_dec_same",
                                "order_id": "order_dec_1",
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

        # Assert case was opened with status='open'
        cases = db_conn.execute("SELECT * FROM recovery_cases WHERE payment_id = 'pay_dec_same'").fetchall()
        assert len(cases) == 1
        assert cases[0]["status"] == "open"

        # Assert Gemini mock was NEVER called
        assert mock_client.call_count == 0

        # Assert zero rows in diagnoses table
        diag_count = db_conn.execute("SELECT COUNT(*) FROM diagnoses").fetchone()[0]
        assert diag_count == 0

        # Assert zero stage='diagnosis' audit events exist
        diag_audit_count = db_conn.execute("SELECT COUNT(*) FROM audit_events WHERE stage = 'diagnosis'").fetchone()[0]
        assert diag_audit_count == 0
