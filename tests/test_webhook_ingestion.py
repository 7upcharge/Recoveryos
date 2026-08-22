"""Tests for the POST /webhooks/razorpay ingestion endpoint.

Covers:
- Valid payment.failed → 200, correct DB rows
- Existing payment.authorized → updates, no duplicate
- Invalid signature → 400, zero DB rows
- Malformed JSON → 400, zero DB rows
- Duplicate event → 200, no duplicate rows, no extra attempt_count
- Internal error → 500, no partial writes
"""

import json
from unittest.mock import patch

from tests.conftest import TEST_WEBHOOK_SECRET, compute_signature


class TestValidPaymentFailed:
    """A correctly signed payment.failed event should be fully processed."""

    def test_returns_200(self, client, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        resp = client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )
        assert resp.status_code == 200

    def test_creates_one_webhook_event(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )

        rows = db_conn.execute("SELECT * FROM webhook_events").fetchall()
        assert len(rows) == 1
        assert rows[0]["razorpay_event_id"] == "evt_TestPaymentFail004"
        assert rows[0]["event_type"] == "payment.failed"
        assert rows[0]["signature_verified"] == 1

    def test_creates_one_payment(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )

        rows = db_conn.execute("SELECT * FROM payments").fetchall()
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "pay_TestPay002"
        assert rows[0]["status"] == "failed"
        assert rows[0]["amount"] == 75000
        assert rows[0]["currency"] == "INR"
        assert rows[0]["method"] == "netbanking"
        assert rows[0]["bank"] == "HDFC"
        assert rows[0]["attempt_count"] == 1

    def test_creates_one_audit_event(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )

        rows = db_conn.execute("SELECT * FROM audit_events").fetchall()
        assert len(rows) == 1
        assert rows[0]["stage"] == "ingestion"
        assert rows[0]["actor"] == "system"
        assert rows[0]["case_id"] is None


class TestExistingPaymentAuthorized:
    """Sending payment.authorized for an existing payment should update it."""

    def test_updates_existing_payment(self, client, db_conn, sample_payloads):
        # First send payment.created
        created_payload = sample_payloads["payment_created"]
        created_body = json.dumps(created_payload).encode("utf-8")
        created_sig = compute_signature(created_body, TEST_WEBHOOK_SECRET)
        client.post(
            "/webhooks/razorpay",
            data=created_body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": created_sig},
        )

        # Then send payment.authorized for the same payment
        auth_payload = sample_payloads["payment_authorized"]
        auth_body = json.dumps(auth_payload).encode("utf-8")
        auth_sig = compute_signature(auth_body, TEST_WEBHOOK_SECRET)
        client.post(
            "/webhooks/razorpay",
            data=auth_body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": auth_sig},
        )

        # Should still be exactly one payment row, now with 'authorized' status
        rows = db_conn.execute("SELECT * FROM payments").fetchall()
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "pay_TestPay001"
        assert rows[0]["status"] == "authorized"
        # payment.authorized is a status transition, not a new attempt
        assert rows[0]["attempt_count"] == 1

    def test_no_duplicate_payment_row(self, client, db_conn, sample_payloads):
        created_payload = sample_payloads["payment_created"]
        created_body = json.dumps(created_payload).encode("utf-8")
        created_sig = compute_signature(created_body, TEST_WEBHOOK_SECRET)
        client.post(
            "/webhooks/razorpay",
            data=created_body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": created_sig},
        )

        auth_payload = sample_payloads["payment_authorized"]
        auth_body = json.dumps(auth_payload).encode("utf-8")
        auth_sig = compute_signature(auth_body, TEST_WEBHOOK_SECRET)
        client.post(
            "/webhooks/razorpay",
            data=auth_body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": auth_sig},
        )

        count = db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        assert count == 1


class TestInvalidSignature:
    """Requests with invalid signatures must be rejected with nothing written."""

    def test_returns_400(self, client, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": "invalid_signature_value"},
        )
        assert resp.status_code == 400

    def test_writes_nothing_to_db(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")

        client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": "invalid_signature_value"},
        )

        assert db_conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0

    def test_missing_signature_header(self, client, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            # No X-Razorpay-Signature header
        )
        assert resp.status_code == 400


class TestMalformedJSON:
    """Body that passes signature but is not valid JSON."""

    def test_returns_400(self, client):
        # Construct non-JSON body that will have a valid signature
        body = b"this is not json {{{{"
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        resp = client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )
        assert resp.status_code == 400

    def test_writes_nothing_to_db(self, client, db_conn):
        body = b"this is not json {{{{"
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )

        assert db_conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


class TestDuplicateEvent:
    """Sending the exact same event twice must not create duplicate state."""

    def _send_event(self, client, payload):
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)
        return client.post(
            "/webhooks/razorpay",
            data=body,
            content_type="application/json",
            headers={"X-Razorpay-Signature": sig},
        )

    def test_both_return_200(self, client, sample_payloads):
        payload = sample_payloads["payment_failed"]
        resp1 = self._send_event(client, payload)
        resp2 = self._send_event(client, payload)
        assert resp1.status_code == 200
        assert resp2.status_code == 200

    def test_exactly_one_webhook_event(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        self._send_event(client, payload)
        self._send_event(client, payload)

        count = db_conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0]
        assert count == 1

    def test_exactly_one_audit_event(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        self._send_event(client, payload)
        self._send_event(client, payload)

        count = db_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        assert count == 1

    def test_attempt_count_not_incremented(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        self._send_event(client, payload)
        self._send_event(client, payload)

        row = db_conn.execute("SELECT * FROM payments").fetchone()
        assert row["attempt_count"] == 1


class TestInternalError:
    """A database/repository exception should return 500 with no partial writes."""

    def test_returns_500_on_db_failure(self, client, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        with patch(
            "app.webhooks.routes.insert_webhook_event",
            side_effect=RuntimeError("Simulated DB crash"),
        ):
            resp = client.post(
                "/webhooks/razorpay",
                data=body,
                content_type="application/json",
                headers={"X-Razorpay-Signature": sig},
            )
        assert resp.status_code == 500

    def test_no_partial_writes_on_failure(self, client, db_conn, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        with patch(
            "app.webhooks.routes.insert_audit_event",
            side_effect=RuntimeError("Simulated audit DB crash"),
        ):
            client.post(
                "/webhooks/razorpay",
                data=body,
                content_type="application/json",
                headers={"X-Razorpay-Signature": sig},
            )

        # Transaction should have been rolled back — nothing committed
        assert db_conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0

    def test_no_stack_trace_in_response(self, client, sample_payloads):
        payload = sample_payloads["payment_failed"]
        body = json.dumps(payload).encode("utf-8")
        sig = compute_signature(body, TEST_WEBHOOK_SECRET)

        with patch(
            "app.webhooks.routes.insert_webhook_event",
            side_effect=RuntimeError("Simulated DB crash"),
        ):
            resp = client.post(
                "/webhooks/razorpay",
                data=body,
                content_type="application/json",
                headers={"X-Razorpay-Signature": sig},
            )
        data = resp.get_json()
        assert "Traceback" not in json.dumps(data)
        assert "Simulated" not in json.dumps(data)
