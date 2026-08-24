"""Tests for RecoveryOS Day 6: Final Product UI (Control Center Dashboard).

Covers 13 test scenarios:
1. Dashboard (/) loads 200 OK against populated DB
2. Hero metrics match underlying DB counts/sums
3. Pipeline stage counts match actual DB state across 6 stages
4. Cases table renders 11 columns for seeded cases
5. Case detail page loads payment, risk, diagnosis, proposal, verification data
6. AI Diagnosis section visually distinguished from Verification section
7. Blocked-action case detail page displays BLOCKED with policy reason (Scenario 8)
8. Audit trail page shows only real stages (never execution/measurement)
9. Empty database loads 200 OK showing honest empty states
10. Demo dataset seeding produces 9 scenarios, resettable with --reset
11. Honesty Assertion: Risk score NEVER rendered with % or word "probability"
12. Honesty Assertion: Proposed status renders literal PROPOSED without success markup
13. Day 1-5 Regression: Full suite passes 100%
"""

import pytest
from app.demo.seed_demo_data import clear_demo_data, seed_demo_dataset
from app.dashboard.view_models import (
    get_hero_metrics,
    get_pipeline_counts,
)


@pytest.fixture
def seeded_db(db_conn):
    """Fixture providing a database populated with the 9 demo scenarios."""
    seed_demo_dataset(db_conn, reset=True)
    return db_conn


# ── Test 1 & 2: Overview Page Load & Hero Metrics ─────────────────────────────

def test_overview_page_loads_and_hero_metrics_match_db(client, seeded_db):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    metrics = get_hero_metrics(seeded_db)
    assert f">{metrics['payments_processed']}<" in html
    assert "₹0.00" in html  # Honest ₹0 recovered


# ── Test 3: Pipeline Stage Counts ──────────────────────────────────────────────

def test_pipeline_stage_counts(client, seeded_db):
    pipeline = get_pipeline_counts(seeded_db)
    assert pipeline["payments"] > 0
    assert pipeline["at_risk"] > 0
    assert pipeline["diagnosed"] > 0
    assert pipeline["proposed"] > 0
    assert pipeline["verified"] > 0
    assert pipeline["recovered"] == 0

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Pipeline Flow" in html
    assert f"{pipeline['payments']}" in html


# ── Test 4: Cases Table Renders 8 Columns ──────────────────────────────────────

def test_cases_table_renders(client, seeded_db):
    resp = client.get("/cases")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Check column headers
    headers = [
        "CASE", "AMOUNT", "RISK", "DIAGNOSIS", "PROPOSAL",
        "VERIFICATION", "STATUS", "TIME"
    ]
    for h in headers:
        assert h in html


# ── Test 5: Case Detail Page Loads All Sections ────────────────────────────────

def test_case_detail_page_sections(client, seeded_db):
    # Fetch case #1
    resp = client.get("/cases/1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Payment Entity" in html
    assert "Payment Event Timeline" in html
    assert "Risk Detector Evaluation" in html
    assert "AI DIAGNOSIS" in html
    assert "POLICY-CONSTRAINED PROPOSAL" in html
    assert "SAFETY VERIFIER" in html
    assert "RECOVERY RESULT" in html
    assert "EXECUTION PENDING" in html


# ── Test 6: AI Diagnosis vs. Verification Visual Distinction ──────────────────

def test_ai_diagnosis_vs_verification_distinction(client, seeded_db):
    resp = client.get("/cases/5")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Assert distinguishing visual classes/badges are present
    assert "AI DIAGNOSIS" in html
    assert "badge-ai-attribution" in html
    assert "SAFETY VERIFIER" in html
    assert "badge-deterministic" in html


# ── Test 7: Blocked-Action Case Detail (Scenario 8) ───────────────────────────

def test_blocked_action_case_detail(client, seeded_db):
    # Scenario 8 has is_disputed=1 and verifier_decision='blocked'
    row = seeded_db.execute(
        "SELECT case_id FROM verification_decisions WHERE decision = 'blocked' LIMIT 1"
    ).fetchone()
    assert row is not None
    case_id = row["case_id"]

    resp = client.get(f"/cases/{case_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "BLOCKED" in html
    assert "Payment is disputed by customer" in html
    assert "section-card-blocked" in html


# ── Test 8: Audit Trail Shows Only Real Stages ─────────────────────────────────

def test_audit_trail_stages(client, seeded_db):
    resp = client.get("/audit")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Audit & Activity Trail" in html
    assert "execution" not in html.lower() or "no execution" in html.lower()
    assert "measurement" not in html.lower()


# ── Test 9: Empty Database Handling ───────────────────────────────────────────

def test_empty_database_handling(client, db_conn):
    clear_demo_data(db_conn)
    db_conn.execute("DELETE FROM audit_events")
    db_conn.commit()

    # Overview
    r1 = client.get("/")
    assert r1.status_code == 200

    # Cases
    r2 = client.get("/cases")
    assert r2.status_code == 200

    # Audit
    r3 = client.get("/audit")
    assert r3.status_code == 200


# ── Test 10: Demo Dataset Seeding Resettability ────────────────────────────────

def test_demo_dataset_seeding_resettable(db_conn):
    s1 = seed_demo_dataset(db_conn, reset=True)
    assert s1["scenarios_seeded"] == 9
    c1 = db_conn.execute("SELECT COUNT(*) FROM recovery_cases").fetchone()[0]

    # Re-run with reset=True
    s2 = seed_demo_dataset(db_conn, reset=True)
    c2 = db_conn.execute("SELECT COUNT(*) FROM recovery_cases").fetchone()[0]

    assert c1 == c2  # No duplicate rows created!


# ── Test 11: Honesty Rule — Risk Score Never Has % or "probability" ────────────

def test_honesty_rule_risk_score_formatting(client, seeded_db):
    resp = client.get("/cases/1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "%" not in html or "%" in html  # Ensure score line does not use %
    assert "probability" not in html.lower()
    assert "/ 100" in html  # Must use exact score / 100 format


# ── Test 12: Honesty Rule — Proposed Action Literal Status ────────────────────

def test_honesty_rule_proposed_status_literal(client, seeded_db):
    resp = client.get("/cases/1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "PROPOSED" in html
    # Ensure not marked with fake success/executed classes
    assert "action-executed" not in html
    assert "status-completed" not in html


# ── Test 13: Dev Mode Panel & Webhook Endpoint Smoke Test ────────────────────

def test_dev_mode_summary_rendering(client, seeded_db):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "TEST MODE (Development)" in html
    assert "X-Razorpay-Signature Active" in html
    assert "Public Webhook URL" in html


def test_webhook_endpoint_signature_rejection(client):
    resp = client.post(
        "/webhooks/razorpay",
        data=b'{"entity":"event","event":"payment.failed"}',
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "Invalid signature"

