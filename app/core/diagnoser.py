"""Diagnosis Orchestrator for RecoveryOS Day 3.

Main public entrypoint `diagnose_case(conn, case_id)` fetches case data and event history,
calls Gemini for structured root-cause diagnosis, validates output, applies UNKNOWN
fallback on any error/invalid output, updates case status to 'diagnosed', and logs audit events.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.core.diagnosis_schema import (
    ALLOWED_DIAGNOSES,
    DiagnosisInput,
    DiagnosisResult,
    validate_diagnosis_output,
)
from app.core.gemini_client import GeminiUnavailableError, call_gemini_for_diagnosis
from app.db.repositories import (
    get_event_history_for_order,
    get_latest_diagnosis_for_case,
    get_recovery_case_by_id,
    insert_audit_event,
    insert_diagnosis,
    update_case_status,
)

logger = logging.getLogger(__name__)


def _reconstruct_result_from_db_row(row: dict[str, Any]) -> DiagnosisResult:
    """Reconstruct a DiagnosisResult dataclass from a database row dict."""
    evidence = []
    if row.get("evidence_json"):
        try:
            evidence = json.loads(row["evidence_json"])
        except Exception:
            evidence = []

    return DiagnosisResult(
        diagnosis=row["likely_cause"],
        confidence=float(row["confidence"]),
        explanation=row["explanation"],
        evidence=evidence,
        limitations=row.get("limitations") or "",
        fallback_used=bool(row.get("fallback_used")),
        fallback_reason=row.get("fallback_reason"),
    )


def diagnose_case(conn: Any, case_id: int) -> DiagnosisResult:
    """Diagnose an open recovery case using Gemini with strict fallback guarantees.

    Args:
        conn: Active sqlite3 connection.
        case_id: Primary key ID of the recovery case.

    Returns:
        DiagnosisResult with validated diagnosis or UNKNOWN fallback.

    Raises:
        ValueError: If case_id does not exist or case is not in open status (and has no existing diagnosis).
    """
    # ── 1. Fetch case & check idempotency ──────────────────────────────────────
    case = get_recovery_case_by_id(conn, case_id)
    if not case:
        raise ValueError(f"Recovery case #{case_id} not found.")

    # Idempotency check: if already diagnosed, return existing result without re-calling API
    existing_diagnosis = get_latest_diagnosis_for_case(conn, case_id)
    if existing_diagnosis:
        logger.info("Case #%d already has a diagnosis; returning stored diagnosis.", case_id)
        return _reconstruct_result_from_db_row(existing_diagnosis)

    if case["status"] != "open":
        raise ValueError(f"Recovery case #{case_id} is not in 'open' status (status='{case['status']}').")

    # ── 2. Fetch order & event history for DiagnosisInput ──────────────────────
    payment_id = case["payment_id"]
    payment_row = conn.execute(
        "SELECT order_id FROM payments WHERE payment_id = ?", (payment_id,)
    ).fetchone()
    order_id = payment_row["order_id"] if payment_row and payment_row["order_id"] else payment_id

    events = get_event_history_for_order(conn, order_id)
    rules_fired = [r.strip() for r in case["risk_rule"].split(",") if r.strip()]

    diagnosis_input = DiagnosisInput(
        case_id=case_id,
        payment_id=payment_id,
        order_id=order_id,
        risk_score=case["risk_score"],
        rules_fired=rules_fired,
        event_history=events,
    )

    # Format events for Gemini prompt payload
    event_list_payload = []
    for ev in events:
        event_list_payload.append({
            "event_type": ev.event_type,
            "payment_id": ev.payment_id,
            "order_id": ev.order_id,
            "timestamp": ev.timestamp.isoformat(),
            "error_code": ev.error_code,
        })

    prompt_payload = {
        "case_id": case_id,
        "payment_id": payment_id,
        "order_id": order_id,
        "risk_score": case["risk_score"],
        "rules_fired": rules_fired,
        "risk_reason": case["risk_reason"],
        "event_history": event_list_payload,
    }

    # ── 3. Invoke Gemini & Validate / Fallback ─────────────────────────────────
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    raw_model_output_json = ""
    result: DiagnosisResult | None = None
    fallback_reason: str | None = None

    try:
        raw_output = call_gemini_for_diagnosis(prompt_payload)
        raw_model_output_json = json.dumps(raw_output)

        validated = validate_diagnosis_output(raw_output)
        if validated is not None:
            result = validated
        else:
            # Determine specific fallback reason code for invalid schema output
            diag_val = raw_output.get("diagnosis") if isinstance(raw_output, dict) else None
            conf_val = raw_output.get("confidence") if isinstance(raw_output, dict) else None

            if diag_val is not None and diag_val not in ALLOWED_DIAGNOSES:
                fallback_reason = "unsupported_diagnosis"
            elif conf_val is not None:
                try:
                    c = float(conf_val)
                    if not (0.0 <= c <= 1.0):
                        fallback_reason = "invalid_confidence"
                    else:
                        fallback_reason = "schema_validation_failed"
                except (ValueError, TypeError):
                    fallback_reason = "invalid_confidence"
            else:
                fallback_reason = "schema_validation_failed"

    except GeminiUnavailableError as exc:
        fallback_reason = exc.reason
        raw_model_output_json = json.dumps({"error": str(exc), "reason": exc.reason})
    except Exception as exc:
        logger.warning("Unexpected error during diagnosis execution: %s", exc)
        fallback_reason = "api_unavailable"
        raw_model_output_json = json.dumps({"error": str(exc)})

    # If result was not set due to error or failed validation, construct fallback result
    if result is None:
        if not fallback_reason:
            fallback_reason = "malformed_output"

        result = DiagnosisResult(
            diagnosis="UNKNOWN",
            confidence=0.0,
            explanation=f"Diagnosis unavailable: {fallback_reason}",
            evidence=[],
            limitations="Diagnosis service failed, timed out, or returned invalid output.",
            fallback_used=True,
            fallback_reason=fallback_reason,
        )

    # ── 4. Persist result, update case status, and log audit event ─────────────
    insert_diagnosis(
        conn=conn,
        case_id=case_id,
        result=result,
        model_name=model_name,
        raw_model_output=raw_model_output_json,
    )

    update_case_status(conn, case_id, "diagnosed")

    actor = "system" if result.fallback_used else "gemini"
    summary = (
        f"Diagnosis fallback: UNKNOWN ({result.fallback_reason})"
        if result.fallback_used
        else f"Diagnosis: {result.diagnosis} (conf={result.confidence:.2f})"
    )

    audit_detail = {
        "case_id": case_id,
        "diagnosis": result.diagnosis,
        "confidence": result.confidence,
        "explanation": result.explanation,
        "evidence": result.evidence,
        "limitations": result.limitations,
        "fallback_used": result.fallback_used,
        "fallback_reason": result.fallback_reason,
        "model_name": model_name,
    }

    insert_audit_event(
        conn=conn,
        stage="diagnosis",
        actor=actor,
        summary=summary,
        detail_json=json.dumps(audit_detail),
        case_id=case_id,
    )

    logger.info("Completed diagnosis for case #%d: %s", case_id, summary)
    return result
