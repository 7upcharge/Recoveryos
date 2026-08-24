"""Diagnosis schema and contract layer for RecoveryOS Day 3.

Defines the single source of truth for allowed diagnosis categories, input/output
dataclasses, and schema validation functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.risk_detector import NormalizedEvent

logger = logging.getLogger(__name__)

# Single source of truth for allowed diagnosis categories
ALLOWED_DIAGNOSES = {
    "LOW_BALANCE",
    "BANK_DELAY",
    "AUTHENTICATION_FAILURE",
    "MANDATE_OR_AUTHORIZATION_ISSUE",
    "NETWORK_OR_GATEWAY_ERROR",
    "UNKNOWN",
}


@dataclass
class DiagnosisInput:
    """Input payload assembled for Gemini diagnosis."""

    case_id: int
    payment_id: str
    order_id: str
    risk_score: int
    rules_fired: list[str]
    event_history: list[NormalizedEvent]


@dataclass
class DiagnosisResult:
    """Validated output result from the diagnosis stage."""

    diagnosis: str  # Must be in ALLOWED_DIAGNOSES
    confidence: float  # Must be between 0.0 and 1.0
    explanation: str
    evidence: list[str]
    limitations: str
    fallback_used: bool = False
    fallback_reason: str | None = None


def validate_diagnosis_output(raw: dict[str, Any]) -> DiagnosisResult | None:
    """Validate a raw dictionary output against the strict diagnosis schema.

    Args:
        raw: Raw dict parsed from model response or function call.

    Returns:
        A validated DiagnosisResult object if all constraints pass, or None if validation fails.
        Guaranteed to never raise an exception.
    """
    try:
        if not isinstance(raw, dict):
            logger.warning("Validation failed: raw output is not a dictionary.")
            return None

        # Check required fields presence
        required_keys = {"diagnosis", "confidence", "explanation", "evidence", "limitations"}
        if not required_keys.issubset(raw.keys()):
            missing = required_keys - raw.keys()
            logger.warning("Validation failed: missing required keys: %s", missing)
            return None

        diagnosis_val = raw.get("diagnosis")
        if not isinstance(diagnosis_val, str) or diagnosis_val not in ALLOWED_DIAGNOSES:
            logger.warning("Validation failed: invalid or unsupported diagnosis '%s'", diagnosis_val)
            return None

        # Confidence validation
        raw_conf = raw.get("confidence")
        try:
            confidence_val = float(raw_conf)
        except (ValueError, TypeError):
            logger.warning("Validation failed: confidence '%s' is not numeric.", raw_conf)
            return None

        if not (0.0 <= confidence_val <= 1.0):
            logger.warning("Validation failed: confidence %f out of bounds [0.0, 1.0].", confidence_val)
            return None

        # Explanation validation
        explanation_val = raw.get("explanation")
        if not isinstance(explanation_val, str) or not explanation_val.strip():
            logger.warning("Validation failed: explanation must be a non-empty string.")
            return None

        # Evidence validation
        evidence_val = raw.get("evidence")
        if not isinstance(evidence_val, list):
            logger.warning("Validation failed: evidence must be a list.")
            return None

        # Convert evidence items to strings
        evidence_list = [str(item) for item in evidence_val]

        # Limitations validation
        limitations_val = raw.get("limitations")
        if not isinstance(limitations_val, str):
            logger.warning("Validation failed: limitations must be a string.")
            return None

        return DiagnosisResult(
            diagnosis=diagnosis_val,
            confidence=confidence_val,
            explanation=explanation_val.strip(),
            evidence=evidence_list,
            limitations=limitations_val.strip(),
            fallback_used=False,
            fallback_reason=None,
        )
    except Exception as exc:
        logger.warning("Unexpected error during diagnosis output validation: %s", exc)
        return None
