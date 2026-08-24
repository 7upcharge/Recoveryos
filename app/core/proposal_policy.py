"""Deterministic Diagnosis-to-Action Proposal Policy Engine for RecoveryOS Day 4.

Mapping Table:
    ┌────────────────────────────────┬─────────────────┬────────────────────────────────────────────────────────┐
    │ Diagnosis Category             │ Default Action  │ Policy Rationale                                       │
    ├────────────────────────────────┼─────────────────┼────────────────────────────────────────────────────────┤
    │ LOW_BALANCE                    │ SEND_REMINDER   │ Notify customer to top up funds before re-attempt      │
    │ BANK_DELAY                     │ RETRY_LATER     │ Transient bank processing queue; wait for resolution   │
    │ AUTHENTICATION_FAILURE         │ PROMPT_REAUTH   │ Customer 2FA/OTP issue; prompt customer re-auth        │
    │ MANDATE_OR_AUTHORIZATION_ISSUE │ PROMPT_REAUTH   │ Standing instruction failure; prompt mandate re-auth   │
    │ NETWORK_OR_GATEWAY_ERROR       │ RETRY_LATER     │ Temporary gateway network disconnect; retry later      │
    │ UNKNOWN                        │ ESCALATE_HUMAN  │ Unclassified cause; requires human operator review     │
    └────────────────────────────────┴─────────────────┴────────────────────────────────────────────────────────┘

Confidence Overrides:
    1. MANDATE_OR_AUTHORIZATION_ISSUE with diagnosis_confidence < 0.6 forces ESCALATE_HUMAN.
    2. Universal low-confidence override: Any diagnosis category with confidence < 0.4 forces ESCALATE_HUMAN.
"""

from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

DIAGNOSIS_TO_DEFAULT_ACTION: dict[str, str] = {
    "LOW_BALANCE": "SEND_REMINDER",
    "BANK_DELAY": "RETRY_LATER",
    "AUTHENTICATION_FAILURE": "PROMPT_REAUTH",
    "MANDATE_OR_AUTHORIZATION_ISSUE": "PROMPT_REAUTH",
    "NETWORK_OR_GATEWAY_ERROR": "RETRY_LATER",
    "UNKNOWN": "ESCALATE_HUMAN",
}


def get_policy_default_action(diagnosis: str, confidence: float) -> Tuple[str, str]:
    """Compute the deterministic recommended action and rationale for a diagnosis.

    Args:
        diagnosis: Diagnosis category string (from ALLOWED_DIAGNOSES).
        confidence: Diagnosis confidence float value [0.0, 1.0].

    Returns:
        Tuple of (action, reason).
    """
    # ── Universal Low-Confidence Override Rule ─────────────────────────────
    if confidence < 0.4:
        reason = (
            f"Diagnosis confidence ({confidence:.2f}) is below the universal 0.4 threshold; "
            f"overriding default policy to ESCALATE_HUMAN."
        )
        logger.info("Policy override triggered (conf=%.2f < 0.4) -> ESCALATE_HUMAN", confidence)
        return "ESCALATE_HUMAN", reason

    # ── Mandate Specific Low-Confidence Override Rule ─────────────────────
    if diagnosis == "MANDATE_OR_AUTHORIZATION_ISSUE" and confidence < 0.6:
        reason = (
            f"Mandate or authorization issue diagnosis confidence ({confidence:.2f}) is below the 0.6 threshold; "
            f"overriding default policy to ESCALATE_HUMAN."
        )
        logger.info("Policy override triggered (MANDATE conf=%.2f < 0.6) -> ESCALATE_HUMAN", confidence)
        return "ESCALATE_HUMAN", reason

    # ── Standard Policy Mapping Table ──────────────────────────────────────
    action = DIAGNOSIS_TO_DEFAULT_ACTION.get(diagnosis, "ESCALATE_HUMAN")
    
    if diagnosis == "LOW_BALANCE":
        reason = "Diagnosis indicates insufficient balance; recommend sending payment reminder to customer."
    elif diagnosis == "BANK_DELAY":
        reason = "Diagnosis indicates core banking delay; recommend retrying payment later after delay window."
    elif diagnosis == "AUTHENTICATION_FAILURE":
        reason = "Diagnosis indicates 2FA/OTP authentication failure; recommend prompting customer for re-authentication."
    elif diagnosis == "MANDATE_OR_AUTHORIZATION_ISSUE":
        reason = "Diagnosis indicates mandate or standing instruction issue; recommend prompting customer to re-authorize mandate."
    elif diagnosis == "NETWORK_OR_GATEWAY_ERROR":
        reason = "Diagnosis indicates transient network disconnect or gateway error; recommend retrying payment later."
    elif diagnosis == "UNKNOWN":
        reason = "Diagnosis cause is UNKNOWN; recommend escalating case to human ops team."
    else:
        reason = f"Unmapped diagnosis category '{diagnosis}'; default policy recommends ESCALATE_HUMAN."

    return action, reason
