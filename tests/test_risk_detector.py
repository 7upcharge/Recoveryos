"""Tests for the Day 2 Risk Detector.

Covers 11 test scenarios matching the Day 2 spec:

1.  Normal successful payment → flagged=False, no case
2.  Single isolated failure (R5) → flagged=False, no case
3.  Delayed authorization (R1) → score=40, case with R1_DELAYED_AUTH
4a. R2 alone → flagged=False, no case
4b. R2 + R3 together → flagged=True, score=60 (R2 contributes 0)
5.  Three failures in window (R3) → score=60, case created
6.  Two failed attempts, no success (R4) → score=50, case created
7.  Failures outside window (adversarial sliding window) → R3 does NOT fire
8.  Duplicate processing → exactly 1 case row after 2 runs
9.  Multiple rules firing → R1+R3 score=100; R3+R4 capped at 100
10. Audit event on case open → stage='detection' row exists
11. Existing open case reused → no second row created

Most tests construct NormalizedEvent lists directly (fast, no DB needed).
Integration tests use the DB-backed conftest fixtures.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.risk_detector import (
    NormalizedEvent,
    RiskEvaluation,
    check_r1_delayed_authorization,
    check_r2_failed_then_authorized,
    check_r3_repeated_failures,
    check_r4_multiple_attempts_intent,
    check_r5_single_isolated_failure,
    evaluate_risk,
    process_order_for_risk,
)
from tests.conftest import TEST_WEBHOOK_SECRET, compute_signature


# ── Helpers ──────────────────────────────────────────────────────────────────

BASE_TIME = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)


def make_event(event_type, payment_id="pay_001", order_id="order_001",
               offset_seconds=0, error_code=None):
    """Create a NormalizedEvent with a timestamp offset from BASE_TIME."""
    return NormalizedEvent(
        event_type=event_type,
        payment_id=payment_id,
        order_id=order_id,
        timestamp=BASE_TIME + timedelta(seconds=offset_seconds),
        error_code=error_code,
    )


def send_signed_webhook(client, payload):
    """Send a correctly signed webhook request via the test client."""
    body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(body, TEST_WEBHOOK_SECRET)
    return client.post(
        "/webhooks/razorpay",
        data=body,
        content_type="application/json",
        headers={"X-Razorpay-Signature": sig},
    )


def make_razorpay_payload(event_id, event_type, payment_id, order_id,
                          created_at, amount=50000, status=None,
                          error_code=None):
    """Build a Razorpay-shaped webhook payload dict."""
    if status is None:
        status = event_type.split(".")[-1]  # e.g. "payment.failed" -> "failed"
    payload = {
        "entity": "event",
        "account_id": "acc_Test",
        "event": event_type,
        "contains": ["payment"],
        "id": event_id,
        "created_at": created_at,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount,
                    "currency": "INR",
                    "status": status,
                    "order_id": order_id,
                    "method": "upi",
                    "bank": None,
                }
            }
        },
    }
    if error_code:
        payload["payload"]["payment"]["entity"]["error_code"] = error_code
    return payload


# ── Test 1: Normal successful payment ────────────────────────────────────────

class TestNormalSuccessfulPayment:
    """Single payment.authorized/captured with no failures → not flagged."""

    def test_evaluate_risk_returns_not_flagged(self):
        events = [
            make_event("payment.authorized", offset_seconds=0),
            make_event("payment.captured", offset_seconds=60),
        ]
        result = evaluate_risk(events)
        assert result.flagged is False
        assert result.risk_score == 0
        assert result.rules_fired == []

    def test_no_case_created_via_route(self, client, db_conn):
        """Full route test: authorized payment → no recovery case."""
        p1 = make_razorpay_payload(
            "evt_ok_1", "payment.authorized", "pay_ok_1", "order_ok_1",
            created_at=int(BASE_TIME.timestamp()),
        )
        send_signed_webhook(client, p1)

        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 0


# ── Test 2: Single isolated failure (R5) ─────────────────────────────────────

class TestSingleIsolatedFailure:
    """Exactly one payment.failed, no other events → R5 path, not flagged."""

    def test_evaluate_risk_returns_not_flagged(self):
        events = [make_event("payment.failed")]
        result = evaluate_risk(events)
        assert result.flagged is False
        assert result.risk_score == 0

    def test_r5_function_returns_none(self):
        events = [make_event("payment.failed")]
        assert check_r5_single_isolated_failure(events) is None

    def test_no_case_created_via_route(self, client, db_conn):
        p = make_razorpay_payload(
            "evt_iso_1", "payment.failed", "pay_iso_1", "order_iso_1",
            created_at=int(BASE_TIME.timestamp()), error_code="BAD_REQUEST_ERROR",
        )
        send_signed_webhook(client, p)
        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 0


# ── Test 3: Delayed authorization (R1) ───────────────────────────────────────

class TestDelayedAuthorization:
    """Gap of 130s with 90s threshold → R1 fires, score=40, case created."""

    def test_r1_fires_with_correct_score(self):
        events = [
            make_event("payment.failed", offset_seconds=0),
            make_event("payment.authorized", offset_seconds=130),
        ]
        result = evaluate_risk(events)
        assert result.flagged is True
        assert result.risk_score == 40
        assert "R1_DELAYED_AUTH" in result.rules_fired
        assert "130 seconds" in result.risk_reason
        assert "90-second threshold" in result.risk_reason

    def test_r1_does_not_fire_within_threshold(self):
        events = [
            make_event("payment.failed", offset_seconds=0),
            make_event("payment.authorized", offset_seconds=60),
        ]
        result = check_r1_delayed_authorization(events)
        assert result is None

    def test_r1_does_not_fire_without_authorization(self):
        events = [make_event("payment.failed", offset_seconds=0)]
        result = check_r1_delayed_authorization(events)
        assert result is None

    def test_case_created_via_route(self, client, db_conn):
        # Send payment.failed first
        p1 = make_razorpay_payload(
            "evt_r1_1", "payment.failed", "pay_r1_1", "order_r1_1",
            created_at=int(BASE_TIME.timestamp()),
        )
        send_signed_webhook(client, p1)

        # Send payment.authorized 130s later
        p2 = make_razorpay_payload(
            "evt_r1_2", "payment.authorized", "pay_r1_1", "order_r1_1",
            created_at=int(BASE_TIME.timestamp()) + 130,
        )
        send_signed_webhook(client, p2)

        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 1
        assert "R1_DELAYED_AUTH" in cases[0]["risk_rule"]
        assert cases[0]["risk_score"] == 40


# ── Test 4: Failed then authorized (R2) ─────────────────────────────────────

class TestFailedThenAuthorized:
    """R2 fires informationally but never independently flags."""

    def test_r2_alone_not_flagged(self):
        """4a: R2 alone → flagged=False, no case."""
        events = [
            make_event("payment.failed", offset_seconds=0),
            # Authorized within threshold so R1 doesn't fire.
            make_event("payment.authorized", offset_seconds=30),
        ]
        result = evaluate_risk(events)
        assert result.flagged is False
        assert result.risk_score == 0
        # R2 should still be in rules_fired for transparency.
        assert "R2_FAILED_THEN_AUTH" in result.rules_fired

    def test_r2_with_r3_flagged_score_is_r3_only(self):
        """4b: R2 + R3 together → flagged=True, score=60 (R2 adds 0)."""
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=60),
            make_event("payment.failed", payment_id="pay_c", offset_seconds=120),
            # Authorized within threshold (30s after first failure) so R1
            # doesn't fire, but R2 sees the failed→authorized sequence.
            make_event("payment.authorized", payment_id="pay_d", offset_seconds=30),
        ]
        result = evaluate_risk(events)
        assert result.flagged is True
        assert "R3_REPEATED_FAILURES" in result.rules_fired
        assert "R2_FAILED_THEN_AUTH" in result.rules_fired
        # Score is R3's 60, not 60 + anything from R2.
        assert result.risk_score == 60

    def test_no_case_when_r2_alone_via_route(self, client, db_conn):
        p1 = make_razorpay_payload(
            "evt_r2_1", "payment.failed", "pay_r2_1", "order_r2_1",
            created_at=int(BASE_TIME.timestamp()),
        )
        send_signed_webhook(client, p1)

        p2 = make_razorpay_payload(
            "evt_r2_2", "payment.authorized", "pay_r2_1", "order_r2_1",
            created_at=int(BASE_TIME.timestamp()) + 30,
        )
        send_signed_webhook(client, p2)

        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 0


# ── Test 5: Three failures within window (R3) ────────────────────────────────

class TestRepeatedFailures:
    """Three payment.failed within 10 minutes → R3 fires, score=60."""

    def test_r3_fires_with_three_failures(self):
        # Use same payment_id to avoid R4 co-firing (R4 counts distinct
        # payment_ids; same id = 1 attempt < MIN_ATTEMPTS_FOR_INTENT=2).
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_a", offset_seconds=180),
            make_event("payment.failed", payment_id="pay_a", offset_seconds=360),
        ]
        result = evaluate_risk(events)
        assert result.flagged is True
        assert "R3_REPEATED_FAILURES" in result.rules_fired
        assert result.risk_score == 60

    def test_case_created_via_route(self, client, db_conn):
        ts = int(BASE_TIME.timestamp())
        # Use same payment_id for all 3 failures (simulating retries on the
        # same payment). Different event IDs ensure no deduplication.
        for i in range(3):
            p = make_razorpay_payload(
                f"evt_r3_{i}", "payment.failed", "pay_r3_same", "order_r3_1",
                created_at=ts + (i * 180),  # 0s, 180s, 360s — all within 10min
            )
            send_signed_webhook(client, p)

        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 1
        assert "R3_REPEATED_FAILURES" in cases[0]["risk_rule"]
        assert cases[0]["risk_score"] == 60


# ── Test 6: Two failed attempts, no success (R4) ─────────────────────────────

class TestMultipleAttemptsIntent:
    """Two distinct failed payment_ids, last event is failure → R4 fires."""

    def test_r4_fires(self):
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=3600),
        ]
        result = evaluate_risk(events)
        assert result.flagged is True
        assert "R4_MULTIPLE_ATTEMPTS_INTENT" in result.rules_fired
        assert result.risk_score == 50

    def test_r4_does_not_fire_with_later_success(self):
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=3600),
            make_event("payment.authorized", payment_id="pay_b", offset_seconds=3660),
        ]
        result = check_r4_multiple_attempts_intent(events)
        assert result is None

    def test_case_created_via_route(self, client, db_conn):
        ts = int(BASE_TIME.timestamp())
        p1 = make_razorpay_payload(
            "evt_r4_1", "payment.failed", "pay_r4a", "order_r4_1",
            created_at=ts,
        )
        send_signed_webhook(client, p1)

        p2 = make_razorpay_payload(
            "evt_r4_2", "payment.failed", "pay_r4b", "order_r4_1",
            created_at=ts + 3600,  # 1 hour later — outside R3 window
        )
        send_signed_webhook(client, p2)

        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 1
        assert "R4_MULTIPLE_ATTEMPTS_INTENT" in cases[0]["risk_rule"]
        assert cases[0]["risk_score"] == 50


# ── Test 7: Sliding window correctness (adversarial) ─────────────────────────

class TestSlidingWindowAdversarial:
    """Failures at minute 0, 5, and 25 with 10-min window and threshold 3.

    Only 2 of the 3 failures fall within any 10-minute window:
    - Window starting at min 0: includes min 0 and min 5 (2 failures)
    - Window starting at min 5: includes min 5 only (min 25 is outside)
    - Window starting at min 25: includes min 25 only

    R3 must NOT fire.
    """

    def test_r3_does_not_fire(self):
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=300),   # 5 min
            make_event("payment.failed", payment_id="pay_c", offset_seconds=1500),  # 25 min
        ]
        result = check_r3_repeated_failures(events)
        assert result is None

    def test_overall_evaluation_r3_not_fired(self):
        """Full evaluate_risk: R3 should not fire, but R4 may (3 attempts, last failed)."""
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=300),
            make_event("payment.failed", payment_id="pay_c", offset_seconds=1500),
        ]
        result = evaluate_risk(events)
        # R3 must not fire.
        assert "R3_REPEATED_FAILURES" not in result.rules_fired

    def test_r3_fires_when_all_within_window(self):
        """Positive control: 3 failures all within 1 minute → R3 fires."""
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=30),
            make_event("payment.failed", payment_id="pay_c", offset_seconds=50),
        ]
        result = check_r3_repeated_failures(events)
        assert result is not None
        assert result.rule_name == "R3_REPEATED_FAILURES"


# ── Test 8: Duplicate processing → exactly one case ──────────────────────────

class TestDuplicateProcessing:
    """Run detection twice against identical stored events → 1 case row."""

    def test_exactly_one_case_after_two_runs(self, app, db_conn):
        from app.db.database import get_connection, init_db
        from app.db.repositories import insert_webhook_event, upsert_payment

        db_path = app.config["DATABASE_PATH"]

        # Manually insert events that would trigger R3.
        ts = int(BASE_TIME.timestamp())
        for i, pid in enumerate(["pay_dp_a", "pay_dp_b", "pay_dp_c"]):
            payload = make_razorpay_payload(
                f"evt_dp_{i}", "payment.failed", pid, "order_dp_1",
                created_at=ts + (i * 60),
            )
            insert_webhook_event(
                conn=db_conn,
                razorpay_event_id=f"evt_dp_{i}",
                event_type="payment.failed",
                payment_id=pid,
                order_id="order_dp_1",
                payload_json=json.dumps(payload),
                event_created_at=str(ts + (i * 60)),
            )
            upsert_payment(
                conn=db_conn,
                payment_id=pid,
                order_id="order_dp_1",
                amount=50000,
                currency="INR",
                status="failed",
                method="upi",
                bank=None,
                event_type="payment.failed",
            )
        db_conn.commit()

        # Run detection twice.
        process_order_for_risk(db_conn, "order_dp_1")
        db_conn.commit()
        process_order_for_risk(db_conn, "order_dp_1")
        db_conn.commit()

        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 1


# ── Test 9: Multiple rules firing + score capping ────────────────────────────

class TestMultipleRulesFiring:
    """R1 + R3 both fire → score=100; R3+R4 → capped at 100."""

    def test_r1_and_r3_combined_score_100(self):
        events = [
            # Three failures within 10 minutes → R3 (60 points)
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=60),
            make_event("payment.failed", payment_id="pay_c", offset_seconds=120),
            # Authorization 130s after first failure → R1 (40 points)
            make_event("payment.authorized", payment_id="pay_d", offset_seconds=130),
        ]
        result = evaluate_risk(events)
        assert result.flagged is True
        assert "R1_DELAYED_AUTH" in result.rules_fired
        assert "R3_REPEATED_FAILURES" in result.rules_fired
        assert result.risk_score == 100  # 40 + 60 = 100

    def test_r3_and_r4_capped_at_100(self):
        """R3 (60) + R4 (50) = 110 → capped at 100."""
        events = [
            # Three failures within 10 minutes → R3 (60 points)
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=60),
            make_event("payment.failed", payment_id="pay_c", offset_seconds=120),
            # Last event is failure and 3 distinct attempts → R4 (50 points)
            # R4 needs last event to be failure with no success after.
            # With 3 distinct payment_ids all failed, R4 fires.
        ]
        result = evaluate_risk(events)
        assert result.flagged is True
        assert "R3_REPEATED_FAILURES" in result.rules_fired
        assert "R4_MULTIPLE_ATTEMPTS_INTENT" in result.rules_fired
        # 60 + 50 = 110 → capped at 100
        assert result.risk_score == 100

    def test_reasons_concatenated(self):
        events = [
            make_event("payment.failed", payment_id="pay_a", offset_seconds=0),
            make_event("payment.failed", payment_id="pay_b", offset_seconds=60),
            make_event("payment.failed", payment_id="pay_c", offset_seconds=120),
            make_event("payment.authorized", payment_id="pay_d", offset_seconds=130),
        ]
        result = evaluate_risk(events)
        # Reason should contain explanations from both R1 and R3.
        assert "seconds" in result.risk_reason  # R1's reason
        assert "failed attempts" in result.risk_reason  # R3's reason


# ── Test 10: Audit event written on case open ────────────────────────────────

class TestAuditEventOnCaseOpen:
    """After a flagged evaluation via the route, a detection audit row exists."""

    def test_detection_audit_event_exists(self, client, db_conn):
        ts = int(BASE_TIME.timestamp())
        # Send 3 failures to trigger R3.
        for i, pid in enumerate(["pay_au_a", "pay_au_b", "pay_au_c"]):
            p = make_razorpay_payload(
                f"evt_au_{i}", "payment.failed", pid, "order_au_1",
                created_at=ts + (i * 60),
            )
            send_signed_webhook(client, p)

        audit_rows = db_conn.execute(
            "SELECT * FROM audit_events WHERE stage = 'detection'"
        ).fetchall()
        assert len(audit_rows) >= 1
        assert audit_rows[0]["actor"] == "system"

        # Verify the detail_json contains expected fields.
        detail = json.loads(audit_rows[0]["detail_json"])
        assert "rules_fired" in detail
        assert "risk_score" in detail


# ── Test 11: Existing open case reused ───────────────────────────────────────

class TestExistingOpenCaseReused:
    """Manually insert an open case, then re-detect → no second row."""

    def test_no_duplicate_case_created(self, app, db_conn):
        from app.db.repositories import (
            insert_recovery_case,
            insert_webhook_event,
            upsert_payment,
        )

        ts = int(BASE_TIME.timestamp())

        # Insert events that would trigger R3.
        for i, pid in enumerate(["pay_reuse_a", "pay_reuse_b", "pay_reuse_c"]):
            payload = make_razorpay_payload(
                f"evt_reuse_{i}", "payment.failed", pid, "order_reuse_1",
                created_at=ts + (i * 60),
            )
            insert_webhook_event(
                conn=db_conn,
                razorpay_event_id=f"evt_reuse_{i}",
                event_type="payment.failed",
                payment_id=pid,
                order_id="order_reuse_1",
                payload_json=json.dumps(payload),
                event_created_at=str(ts + (i * 60)),
            )
            upsert_payment(
                conn=db_conn,
                payment_id=pid,
                order_id="order_reuse_1",
                amount=50000,
                currency="INR",
                status="failed",
                method="upi",
                bank=None,
                event_type="payment.failed",
            )

        # Manually insert an existing open case for the last failed payment.
        insert_recovery_case(
            conn=db_conn,
            payment_id="pay_reuse_c",
            risk_rule="R3_REPEATED_FAILURES",
            risk_score=60,
            risk_reason="Pre-existing case for test.",
        )
        db_conn.commit()

        # Now run detection — should reuse existing case, not create another.
        process_order_for_risk(db_conn, "order_reuse_1")
        db_conn.commit()

        cases = db_conn.execute("SELECT * FROM recovery_cases").fetchall()
        assert len(cases) == 1  # Still only the manually-inserted one

        # Check for the reuse audit entry.
        reuse_audits = db_conn.execute(
            "SELECT * FROM audit_events WHERE stage = 'detection' "
            "AND summary LIKE '%reused%'"
        ).fetchall()
        assert len(reuse_audits) >= 1
