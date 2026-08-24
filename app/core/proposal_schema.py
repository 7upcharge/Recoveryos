"""Proposal schema and contract layer for RecoveryOS Day 4.

Defines the single source of truth for allowed proposal actions, input/output
dataclasses, and schema validation functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Single source of truth for allowed proposal action categories
ALLOWED_ACTIONS = {
    "RETRY_LATER",
    "SEND_REMINDER",
    "PROMPT_REAUTH",
    "ESCALATE_HUMAN",
    "NO_ACTION",
}


@dataclass
class ProposalInput:
    """Input payload assembled for the Recovery Proposer."""

    case_id: int
    payment_id: str
    diagnosis: str
    diagnosis_confidence: float
    diagnosis_explanation: str
    risk_score: int
    rules_fired: list[str]


@dataclass
class ProposalResult:
    """Validated output result from the proposal stage."""

    action: str  # Must be in ALLOWED_ACTIONS
    reason: str
    diagnosis_used: str
    diagnosis_confidence: float
    fallback_used: bool = False
    fallback_reason: str | None = None


def validate_proposed_action(raw: dict[str, Any]) -> ProposalResult | None:
    """Validate a raw dictionary proposal against the strict proposal schema.

    Args:
        raw: Raw dict payload containing action, reason, diagnosis_used, etc.

    Returns:
        A validated ProposalResult object if all constraints pass, or None if validation fails.
        Guaranteed to never raise an exception.
    """
    try:
        if not isinstance(raw, dict):
            logger.warning("Proposal validation failed: raw payload is not a dictionary.")
            return None

        required_keys = {"action", "reason", "diagnosis_used", "diagnosis_confidence"}
        if not required_keys.issubset(raw.keys()):
            missing = required_keys - raw.keys()
            logger.warning("Proposal validation failed: missing required keys: %s", missing)
            return None

        action_val = raw.get("action")
        if not isinstance(action_val, str) or action_val not in ALLOWED_ACTIONS:
            logger.warning("Proposal validation failed: action '%s' is not in ALLOWED_ACTIONS.", action_val)
            return None

        reason_val = raw.get("reason")
        if not isinstance(reason_val, str) or not reason_val.strip():
            logger.warning("Proposal validation failed: reason must be a non-empty string.")
            return None

        diag_used = raw.get("diagnosis_used")
        if not isinstance(diag_used, str) or not diag_used.strip():
            logger.warning("Proposal validation failed: diagnosis_used must be a non-empty string.")
            return None

        raw_conf = raw.get("diagnosis_confidence")
        try:
            conf_val = float(raw_conf)
        except (ValueError, TypeError):
            logger.warning("Proposal validation failed: diagnosis_confidence '%s' is not numeric.", raw_conf)
            return None

        if not (0.0 <= conf_val <= 1.0):
            logger.warning("Proposal validation failed: confidence %f out of bounds [0.0, 1.0].", conf_val)
            return None

        fallback_used = bool(raw.get("fallback_used", False))
        fallback_reason = raw.get("fallback_reason")
        if fallback_reason is not None and not isinstance(fallback_reason, str):
            fallback_reason = str(fallback_reason)

        return ProposalResult(
            action=action_val,
            reason=reason_val.strip(),
            diagnosis_used=diag_used,
            diagnosis_confidence=conf_val,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )
    except Exception as exc:
        logger.warning("Unexpected error during proposal validation: %s", exc)
        return None
