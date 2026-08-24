"""CLI Seed Script: Populate SQLite database with synthetic demo data for RecoveryOS Developer Dashboard.

Demonstrates 5 synthetic payment scenarios:
1. Normal successful payment (no risk, no case)
2. Delayed authorization (R1_DELAYED_AUTH, Case #1, Diagnosed: BANK_DELAY)
3. Repeated failures (R3_REPEATED_FAILURES, Case #2, Diagnosed: AUTHENTICATION_FAILURE)
4. Failed then later authorized (R2_FAILED_THEN_AUTH, Case #3, Diagnosed: NETWORK_OR_GATEWAY_ERROR)
5. Multiple attempts with UNKNOWN fallback (R4_MULTIPLE_ATTEMPTS_INTENT, Case #4, Diagnosed: UNKNOWN)

Usage:
    python scripts/seed_demo_data.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from app.core.diagnosis_schema import DiagnosisResult
from app.db.database import get_connection, init_db
from app.db.repositories import (
    insert_audit_event,
    insert_diagnosis,
    insert_recovery_case,
    insert_webhook_event,
    update_case_status,
    upsert_payment,
)

BASE_TIME = datetime.now(timezone.utc) - timedelta(hours=2)


def seed():
    db_path = os.environ.get("DATABASE_PATH", "./recoveryos.db")
    init_db(db_path)
    conn = get_connection(db_path)

    print("Seeding synthetic demo data into SQLite database...")
    print(f"Database Path: {db_path}\n")

    try:
        # ── 1. Normal Successful Payment (no case) ─────────────
        t1 = BASE_TIME
        p1_id = "pay_demo_101"
        o1_id = "order_demo_101"

        insert_webhook_event(
            conn, "evt_d101_1", "payment.created", p1_id, o1_id,
            json.dumps({"event": "payment.created", "payload": {"payment": {"entity": {"id": p1_id, "amount": 150000, "status": "created"}}}}),
            str(int(t1.timestamp()))
        )
        upsert_payment(conn, p1_id, o1_id, 150000, "INR", "created", "upi", None, "payment.created")

        insert_webhook_event(
            conn, "evt_d101_2", "payment.authorized", p1_id, o1_id,
            json.dumps({"event": "payment.authorized", "payload": {"payment": {"entity": {"id": p1_id, "amount": 150000, "status": "authorized"}}}}),
            str(int((t1 + timedelta(seconds=15)).timestamp()))
        )
        upsert_payment(conn, p1_id, o1_id, 150000, "INR", "authorized", "upi", None, "payment.authorized")

        insert_webhook_event(
            conn, "evt_d101_3", "payment.captured", p1_id, o1_id,
            json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {"id": p1_id, "amount": 150000, "status": "captured"}}}}),
            str(int((t1 + timedelta(seconds=30)).timestamp()))
        )
        upsert_payment(conn, p1_id, o1_id, 150000, "INR", "captured", "upi", None, "payment.captured")

        insert_audit_event(conn, "ingestion", "system", f"Received webhook: payment.captured for {p1_id}")
        print(" [1/5] Seeded: Normal successful payment (pay_demo_101, INR 1,500.00)")

        # ── 2. Delayed Authorization (R1_DELAYED_AUTH, Case #1, Diagnosed: BANK_DELAY) ──
        t2 = BASE_TIME + timedelta(minutes=15)
        p2_id = "pay_demo_102"
        o2_id = "order_demo_102"

        insert_webhook_event(
            conn, "evt_d102_1", "payment.failed", p2_id, o2_id,
            json.dumps({"event": "payment.failed", "payload": {"payment": {"entity": {"id": p2_id, "amount": 250000, "status": "failed", "error_code": "GATEWAY_TIMEOUT"}}}}),
            str(int(t2.timestamp()))
        )
        upsert_payment(conn, p2_id, o2_id, 250000, "INR", "failed", "card", "HDFC", "payment.failed")
        insert_audit_event(conn, "ingestion", "system", f"Received webhook: payment.failed for {p2_id}")

        insert_webhook_event(
            conn, "evt_d102_2", "payment.authorized", p2_id, o2_id,
            json.dumps({"event": "payment.authorized", "payload": {"payment": {"entity": {"id": p2_id, "amount": 250000, "status": "authorized"}}}}),
            str(int((t2 + timedelta(seconds=145)).timestamp()))
        )
        upsert_payment(conn, p2_id, o2_id, 250000, "INR", "authorized", "card", "HDFC", "payment.authorized")

        c2_id = insert_recovery_case(
            conn, p2_id, "R1_DELAYED_AUTH", 40,
            "Authorization took 145 seconds, exceeding the 90-second threshold."
        )
        insert_audit_event(conn, "detection", "system", "Authorization took 145 seconds, exceeding threshold.", case_id=c2_id)

        d2_result = DiagnosisResult(
            diagnosis="BANK_DELAY",
            confidence=0.85,
            explanation="HDFC core banking authorization response latency exceeded 90s SLA during peak hour.",
            evidence=["pay_demo_102 auth delay: 145s", "error_code: GATEWAY_TIMEOUT"],
            limitations="No direct bank gateway telemetry available.",
            fallback_used=False,
        )
        insert_diagnosis(conn, c2_id, d2_result, "gemini-2.5-flash", json.dumps({"diagnosis": "BANK_DELAY", "confidence": 0.85}))
        update_case_status(conn, c2_id, "diagnosed")
        insert_audit_event(conn, "diagnosis", "gemini", "Diagnosis: BANK_DELAY (conf=0.85)", case_id=c2_id)
        print(f" [2/5] Seeded: Delayed Auth R1 (Case #{c2_id}, pay_demo_102, INR 2,500.00, Diagnosed: BANK_DELAY)")

        # ── 3. Repeated Failures (R3_REPEATED_FAILURES, Case #2, Diagnosed: AUTHENTICATION_FAILURE) ──
        t3 = BASE_TIME + timedelta(minutes=35)
        p3_id = "pay_demo_103_0"
        o3_id = "order_demo_103"

        for idx, delay in enumerate([0, 90, 210]):
            pid = f"pay_demo_103_{idx}"
            insert_webhook_event(
                conn, f"evt_d103_{idx}", "payment.failed", pid, o3_id,
                json.dumps({"event": "payment.failed", "payload": {"payment": {"entity": {"id": pid, "amount": 499900, "status": "failed", "error_code": "BAD_REQUEST_AUTHENTICATION_FAILED"}}}}),
                str(int((t3 + timedelta(seconds=delay)).timestamp()))
            )
            upsert_payment(conn, pid, o3_id, 499900, "INR", "failed", "netbanking", "ICICI", "payment.failed")
            insert_audit_event(conn, "ingestion", "system", f"Received webhook: payment.failed for {pid}")

        c3_id = insert_recovery_case(
            conn, p3_id, "R3_REPEATED_FAILURES", 60,
            "3 failed attempts occurred within 10 minutes."
        )
        insert_audit_event(conn, "detection", "system", "3 failed attempts occurred within 10 minutes.", case_id=c3_id)

        d3_result = DiagnosisResult(
            diagnosis="AUTHENTICATION_FAILURE",
            confidence=0.92,
            explanation="Customer repeatedly failed 2-factor OTP authentication on ICICI netbanking gateway.",
            evidence=["3 failed attempts within 3.5 mins", "error_code: BAD_REQUEST_AUTHENTICATION_FAILED"],
            limitations="Customer OTP entry retries not recorded at web layer.",
            fallback_used=False,
        )
        insert_diagnosis(conn, c3_id, d3_result, "gemini-2.5-flash", json.dumps({"diagnosis": "AUTHENTICATION_FAILURE", "confidence": 0.92}))
        update_case_status(conn, c3_id, "diagnosed")
        insert_audit_event(conn, "diagnosis", "gemini", "Diagnosis: AUTHENTICATION_FAILURE (conf=0.92)", case_id=c3_id)
        print(f" [3/5] Seeded: Repeated Failures R3 (Case #{c3_id}, pay_demo_103_a, INR 4,999.00, Diagnosed: AUTHENTICATION_FAILURE)")

        # ── 4. Failed then Later Authorized (R2_FAILED_THEN_AUTH, Case #3, Diagnosed: NETWORK_OR_GATEWAY_ERROR) ──
        t4 = BASE_TIME + timedelta(minutes=60)
        p4_a = "pay_demo_104_a"
        p4_b = "pay_demo_104_b"
        o4_id = "order_demo_104"

        insert_webhook_event(
            conn, "evt_d104_1", "payment.failed", p4_a, o4_id,
            json.dumps({"event": "payment.failed", "payload": {"payment": {"entity": {"id": p4_a, "amount": 1200000, "status": "failed", "error_code": "NETWORK_ERROR"}}}}),
            str(int(t4.timestamp()))
        )
        upsert_payment(conn, p4_a, o4_id, 1200000, "INR", "failed", "upi", "AXIS", "payment.failed")

        insert_webhook_event(
            conn, "evt_d104_2", "payment.failed", p4_b, o4_id,
            json.dumps({"event": "payment.failed", "payload": {"payment": {"entity": {"id": p4_b, "amount": 1200000, "status": "failed", "error_code": "NETWORK_ERROR"}}}}),
            str(int((t4 + timedelta(seconds=60)).timestamp()))
        )
        upsert_payment(conn, p4_b, o4_id, 1200000, "INR", "failed", "upi", "AXIS", "payment.failed")

        insert_webhook_event(
            conn, "evt_d104_3", "payment.authorized", p4_b, o4_id,
            json.dumps({"event": "payment.authorized", "payload": {"payment": {"entity": {"id": p4_b, "amount": 1200000, "status": "authorized"}}}}),
            str(int((t4 + timedelta(seconds=120)).timestamp()))
        )
        upsert_payment(conn, p4_b, o4_id, 1200000, "INR", "authorized", "upi", "AXIS", "payment.authorized")

        c4_id = insert_recovery_case(
            conn, p4_b, "R4_MULTIPLE_ATTEMPTS_INTENT,R2_FAILED_THEN_AUTH", 50,
            "2 payment attempts detected with organic recovery later recorded."
        )
        insert_audit_event(conn, "detection", "system", "2 attempts with organic recovery.", case_id=c4_id)

        d4_result = DiagnosisResult(
            diagnosis="NETWORK_OR_GATEWAY_ERROR",
            confidence=0.78,
            explanation="Transient network disconnect between UPI NPCI switch and Axis PSP handle during initial attempts.",
            evidence=["2 failed attempts with NETWORK_ERROR", "Subsequent authorization succeeded at t+120s"],
            limitations="NPCI switch internal status logs unavailable.",
            fallback_used=False,
        )
        insert_diagnosis(conn, c4_id, d4_result, "gemini-2.5-flash", json.dumps({"diagnosis": "NETWORK_OR_GATEWAY_ERROR", "confidence": 0.78}))
        update_case_status(conn, c4_id, "diagnosed")
        insert_audit_event(conn, "diagnosis", "gemini", "Diagnosis: NETWORK_OR_GATEWAY_ERROR (conf=0.78)", case_id=c4_id)
        print(f" [4/5] Seeded: Organic Recovery R2/R4 (Case #{c4_id}, pay_demo_104_b, INR 12,000.00, Diagnosed: NETWORK_OR_GATEWAY_ERROR)")

        # ── 5. Multiple Attempts with UNKNOWN Fallback (Case #4, Status: Open/Diagnosed Fallback) ──
        t5 = BASE_TIME + timedelta(minutes=90)
        p5_a = "pay_demo_105_a"
        p5_b = "pay_demo_105_b"
        o5_id = "order_demo_105"

        insert_webhook_event(
            conn, "evt_d105_1", "payment.failed", p5_a, o5_id,
            json.dumps({"event": "payment.failed", "payload": {"payment": {"entity": {"id": p5_a, "amount": 750000, "status": "failed", "error_code": "UNKNOWN_ERROR"}}}}),
            str(int(t5.timestamp()))
        )
        upsert_payment(conn, p5_a, o5_id, 750000, "INR", "failed", "card", "SBI", "payment.failed")

        insert_webhook_event(
            conn, "evt_d105_2", "payment.failed", p5_b, o5_id,
            json.dumps({"event": "payment.failed", "payload": {"payment": {"entity": {"id": p5_b, "amount": 750000, "status": "failed", "error_code": "UNKNOWN_ERROR"}}}}),
            str(int((t5 + timedelta(seconds=300)).timestamp()))
        )
        upsert_payment(conn, p5_b, o5_id, 750000, "INR", "failed", "card", "SBI", "payment.failed")

        c5_id = insert_recovery_case(
            conn, p5_b, "R4_MULTIPLE_ATTEMPTS_INTENT", 50,
            "2 payment attempts detected with the most recent still failed — indicates user intent but ongoing difficulty."
        )
        insert_audit_event(conn, "detection", "system", "2 payment attempts detected with recent failed.", case_id=c5_id)

        d5_result = DiagnosisResult(
            diagnosis="UNKNOWN",
            confidence=0.0,
            explanation="Diagnosis unavailable: unsupported_diagnosis",
            evidence=[],
            limitations="Diagnosis service failed, timed out, or returned invalid output.",
            fallback_used=True,
            fallback_reason="unsupported_diagnosis",
        )
        insert_diagnosis(conn, c5_id, d5_result, "gemini-2.5-flash", json.dumps({"diagnosis": "UNKNOWN", "confidence": 0.0}))
        update_case_status(conn, c5_id, "diagnosed")
        insert_audit_event(conn, "diagnosis", "system", "Diagnosis fallback: UNKNOWN (unsupported_diagnosis)", case_id=c5_id)
        print(f" [5/5] Seeded: UNKNOWN Fallback (Case #{c5_id}, pay_demo_105_b, INR 7,500.00, Diagnosed: UNKNOWN)")

        conn.commit()
        print("\n" + "=" * 70)
        print("Demo data successfully seeded! You can now start Flask and visit the dashboard.")
        print("Start command: python run.py")
        print("Dashboard URL: http://localhost:5000/")
        print("=" * 70)

    finally:
        conn.close()


if __name__ == "__main__":
    seed()
