"""Automated test suite for RecoveryOS Developer Dashboard UI.

Covers:
1. Dashboard homepage (GET /) loads with 200 OK
2. Empty database produces clean empty state ("No recovery cases yet")
3. Seeded database displays summary cards and cases table
4. Case detail page (GET /cases/<case_id>) loads with 200 OK
5. Payment event timeline renders correctly
6. Audit trail renders stage logs correctly
7. Invalid case ID returns 404
"""

import pytest

from app.db.repositories import (
    insert_audit_event,
    insert_diagnosis,
    insert_recovery_case,
    insert_webhook_event,
    upsert_payment,
)
from app.core.diagnosis_schema import DiagnosisResult


class TestDashboardHomepage:
    """Test GET / dashboard route."""

    def test_dashboard_empty_database_returns_200_and_empty_state(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Developer Dashboard" in html
        assert "No recovery cases yet." in html
        assert "Total Payments" in html

    def test_dashboard_with_seeded_data(self, client, db_conn):
        # Insert test payment, case, and diagnosis
        p_id = "pay_dash_01"
        o_id = "order_dash_01"

        insert_webhook_event(
            db_conn, "evt_dash_01", "payment.failed", p_id, o_id,
            '{"test": true}', "1724300000"
        )
        upsert_payment(db_conn, p_id, o_id, 500000, "INR", "failed", "upi", "SBI", "payment.failed")
        c_id = insert_recovery_case(db_conn, p_id, "R3_REPEATED_FAILURES", 60, "3 failures")
        
        diag = DiagnosisResult(
            diagnosis="AUTHENTICATION_FAILURE",
            confidence=0.90,
            explanation="OTP timeout.",
            evidence=["evt_dash_01"],
            limitations="None",
        )
        insert_diagnosis(db_conn, c_id, diag, "gemini-2.5-flash", "{}")
        db_conn.commit()

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Developer Dashboard" in html
        assert "#" + str(c_id) in html
        assert p_id in html
        assert "AUTHENTICATION_FAILURE" in html
        assert "R3_REPEATED_FAILURES" in html


class TestCaseDetailPage:
    """Test GET /cases/<case_id> case detail route."""

    def test_case_detail_loads_successfully(self, client, db_conn):
        p_id = "pay_det_01"
        o_id = "order_det_01"

        insert_webhook_event(
            db_conn, "evt_det_01", "payment.failed", p_id, o_id,
            '{"test": true}', "1724300000"
        )
        upsert_payment(db_conn, p_id, o_id, 100000, "INR", "failed", "card", "HDFC", "payment.failed")
        c_id = insert_recovery_case(db_conn, p_id, "R1_DELAYED_AUTH", 40, "Delayed auth by 120s")
        
        diag = DiagnosisResult(
            diagnosis="BANK_DELAY",
            confidence=0.85,
            explanation="Bank latency exceeded threshold.",
            evidence=["evt_det_01"],
            limitations="No direct telemetry.",
        )
        insert_diagnosis(db_conn, c_id, diag, "gemini-2.5-flash", "{}")
        insert_audit_event(db_conn, "detection", "system", "Risk detected R1", case_id=c_id)
        db_conn.commit()

        resp = client.get(f"/cases/{c_id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert f"Case #{c_id}" in html
        assert p_id in html
        assert o_id in html
        assert "₹1000.00" in html
        assert "BANK_DELAY" in html
        assert "R1_DELAYED_AUTH" in html
        assert "evt_det_01" in html  # Timeline event
        assert "Risk detected R1" in html  # Audit event

    def test_invalid_case_id_returns_404(self, client):
        resp = client.get("/cases/99999")
        assert resp.status_code == 404
