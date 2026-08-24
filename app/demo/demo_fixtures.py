"""Synthetic Demo Fixtures & Scenario Definitions for RecoveryOS Day 6.

Defines the 9 required demonstration scenarios using clearly synthetic identifiers
(pay_DEMO_001..009) to drive through real Day 1-5 pipeline code paths.
"""

from __future__ import annotations

from typing import Any

# Synthetic prefix guaranteeing demo data can be identified & reset safely
DEMO_PAYMENT_PREFIX = "pay_DEMO_"
DEMO_ORDER_PREFIX = "order_DEMO_"
DEMO_EVENT_PREFIX = "evt_DEMO_"

DEMO_SCENARIOS: list[dict[str, Any]] = [
    # ── Scenario 1: Normal Payment (Clean Success, No Risk Case) ──────────────
    {
        "id": 1,
        "name": "Normal Payment Success",
        "description": "Clean authorized & captured payment. No risk rules fire.",
        "order_id": "order_DEMO_001",
        "payment_id": "pay_DEMO_001",
        "amount": 150000,  # INR 1,500.00
        "currency": "INR",
        "method": "card",
        "bank": "HDFC",
        "events": [
            {
                "event_type": "payment.authorized",
                "event_id": "evt_DEMO_001_auth",
                "delay_minutes": 0,
            }
        ],
        "expect_risk_case": False,
    },
    # ── Scenario 2: Delayed Authorization (R1_DELAYED_AUTH) ───────────────────
    {
        "id": 2,
        "name": "Delayed Authorization (R1)",
        "description": "Payment authorization arrived after 45 minute delay.",
        "order_id": "order_DEMO_002",
        "payment_id": "pay_DEMO_002",
        "amount": 250000,  # INR 2,500.00
        "currency": "INR",
        "method": "netbanking",
        "bank": "ICICI",
        "events": [
            {
                "event_type": "payment.authorized",
                "event_id": "evt_DEMO_002_auth",
                "delay_minutes": 45,  # > 30 min threshold
            }
        ],
        "expect_risk_case": True,
        "diagnosis": "BANK_DELAY",
        "diagnosis_conf": 0.85,
        "proposal_action": "RETRY_LATER",
    },
    # ── Scenario 3: Repeated Failures (R3_REPEATED_FAILURES) ──────────────────
    {
        "id": 3,
        "name": "Repeated Failures (R3)",
        "description": "3 consecutive failed attempts within 5 minutes.",
        "order_id": "order_DEMO_003",
        "payment_id": "pay_DEMO_003_0",
        "amount": 499900,  # INR 4,999.00
        "currency": "INR",
        "method": "card",
        "bank": "SBI",
        "events": [
            {"event_type": "payment.failed", "event_id": "evt_DEMO_003_0", "delay_minutes": 0, "pay_id": "pay_DEMO_003_0"},
            {"event_type": "payment.failed", "event_id": "evt_DEMO_003_1", "delay_minutes": 2, "pay_id": "pay_DEMO_003_1"},
            {"event_type": "payment.failed", "event_id": "evt_DEMO_003_2", "delay_minutes": 4, "pay_id": "pay_DEMO_003_2"},
        ],
        "expect_risk_case": True,
        "diagnosis": "AUTHENTICATION_FAILURE",
        "diagnosis_conf": 0.92,
        "proposal_action": "PROMPT_REAUTH",
    },
    # ── Scenario 4: Organic Recovery (R2_FAILED_THEN_AUTH) ────────────────────
    {
        "id": 4,
        "name": "Organic Recovery (R2)",
        "description": "Payment failed initially, then authorized organically without system intervention.",
        "order_id": "order_DEMO_004",
        "payment_id": "pay_DEMO_004",
        "amount": 1200000,  # INR 12,000.00
        "currency": "INR",
        "method": "upi",
        "bank": "AXIS",
        "events": [
            {"event_type": "payment.failed", "event_id": "evt_DEMO_004_fail", "delay_minutes": 0},
            {"event_type": "payment.authorized", "event_id": "evt_DEMO_004_auth", "delay_minutes": 10},
        ],
        "expect_risk_case": False,
    },
    # ── Scenario 5: UNKNOWN Diagnosis Fallback ────────────────────────────────
    {
        "id": 5,
        "name": "UNKNOWN Diagnosis Fallback",
        "description": "Inconclusive telemetry returns UNKNOWN diagnosis -> ESCALATE_HUMAN.",
        "order_id": "order_DEMO_005",
        "payment_id": "pay_DEMO_005_0",
        "amount": 750000,  # INR 7,500.00
        "currency": "INR",
        "method": "card",
        "bank": "KOTAK",
        "events": [
            {"event_type": "payment.failed", "event_id": "evt_DEMO_005_0", "delay_minutes": 0, "pay_id": "pay_DEMO_005_0"},
            {"event_type": "payment.failed", "event_id": "evt_DEMO_005_1", "delay_minutes": 3, "pay_id": "pay_DEMO_005_1"},
        ],
        "expect_risk_case": True,
        "diagnosis": "UNKNOWN",
        "diagnosis_conf": 0.00,
        "proposal_action": "ESCALATE_HUMAN",
    },
    # ── Scenario 6: Safe Proposal (RETRY_LATER) ────────────────────────────────
    {
        "id": 6,
        "name": "Safe Proposal (RETRY_LATER)",
        "description": "Network error diagnosis leads to bounded RETRY_LATER proposal.",
        "order_id": "order_DEMO_006",
        "payment_id": "pay_DEMO_006_0",
        "amount": 320000,  # INR 3,200.00
        "currency": "INR",
        "method": "netbanking",
        "bank": "PNB",
        "events": [
            {"event_type": "payment.failed", "event_id": "evt_DEMO_006_0", "delay_minutes": 0, "pay_id": "pay_DEMO_006_0"},
            {"event_type": "payment.failed", "event_id": "evt_DEMO_006_1", "delay_minutes": 2, "pay_id": "pay_DEMO_006_1"},
        ],
        "expect_risk_case": True,
        "diagnosis": "NETWORK_OR_GATEWAY_ERROR",
        "diagnosis_conf": 0.78,
        "proposal_action": "RETRY_LATER",
    },
    # ── Scenario 7: Approved Verification Decision (APPROVED) ──────────────────
    {
        "id": 7,
        "name": "Approved Verification Decision",
        "description": "Safe proposal verified & approved by deterministic verifier policy.",
        "order_id": "order_DEMO_007",
        "payment_id": "pay_DEMO_007_0",
        "amount": 890000,  # INR 8,900.00
        "currency": "INR",
        "method": "upi",
        "bank": "HDFC",
        "events": [
            {"event_type": "payment.failed", "event_id": "evt_DEMO_007_0", "delay_minutes": 0, "pay_id": "pay_DEMO_007_0"},
            {"event_type": "payment.failed", "event_id": "evt_DEMO_007_1", "delay_minutes": 2, "pay_id": "pay_DEMO_007_1"},
        ],
        "expect_risk_case": True,
        "diagnosis": "LOW_BALANCE",
        "diagnosis_conf": 0.88,
        "proposal_action": "SEND_REMINDER",
        "verifier_decision": "approved",
        "verifier_reason": "SEND_REMINDER action compliant with anti-nag policy (< 2 reminders sent).",
        "policies_checked": ["max_retry_limit", "dispute_check", "anti_nag_policy"],
    },
    # ── Scenario 8: Blocked Verification Decision (BLOCKED - Mandatory) ────────
    {
        "id": 8,
        "name": "Blocked Verification Decision (Mandatory)",
        "description": "Disputed payment triggers deterministic verifier block rule.",
        "order_id": "order_DEMO_008",
        "payment_id": "pay_DEMO_008_0",
        "amount": 1500000,  # INR 15,000.00
        "currency": "INR",
        "method": "card",
        "bank": "ICICI",
        "is_disputed": 1,
        "events": [
            {"event_type": "payment.failed", "event_id": "evt_DEMO_008_0", "delay_minutes": 0, "pay_id": "pay_DEMO_008_0"},
            {"event_type": "payment.failed", "event_id": "evt_DEMO_008_1", "delay_minutes": 2, "pay_id": "pay_DEMO_008_1"},
        ],
        "expect_risk_case": True,
        "diagnosis": "AUTHENTICATION_FAILURE",
        "diagnosis_conf": 0.91,
        "proposal_action": "PROMPT_REAUTH",
        "verifier_decision": "blocked",
        "verifier_reason": "Payment is disputed by customer (is_disputed=1); safety policy blocks automated recovery actions.",
        "policies_checked": ["max_retry_limit", "dispute_check_policy", "safety_stopping_rule"],
        "stopping_rules": ["DISPUTE_ACTIVE_STOP_ALL_ACTION"],
    },
    # ── Scenario 9: Distinct Organic Recovery Example ──────────────────────────
    {
        "id": 9,
        "name": "Distinct Organic Recovery Example",
        "description": "High-value enterprise order recovered organically after single bank retry.",
        "order_id": "order_DEMO_009",
        "payment_id": "pay_DEMO_009",
        "amount": 2500000,  # INR 25,000.00
        "currency": "INR",
        "method": "netbanking",
        "bank": "SBI",
        "events": [
            {"event_type": "payment.failed", "event_id": "evt_DEMO_009_fail", "delay_minutes": 0},
            {"event_type": "payment.authorized", "event_id": "evt_DEMO_009_auth", "delay_minutes": 15},
        ],
        "expect_risk_case": False,
    },
]
