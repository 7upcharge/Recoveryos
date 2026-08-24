"""Recovery Proposer Orchestrator for RecoveryOS Day 4.

Main public entrypoint `propose_action(conn, case_id)` evaluates diagnosed recovery cases
against strict policy mappings and confidence rules, validates proposed actions against
an allowlist, persists proposed actions with status='PROPOSED', updates case status to
'proposed', and logs proposal audit events.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.proposal_policy import get_policy_default_action
from app.core.proposal_schema import (
    ALLOWED_ACTIONS,
    ProposalInput,
    ProposalResult,
    validate_proposed_action,
)
from app.db.repositories import (
    get_active_proposal_for_case,
    get_latest_diagnosis_for_case,
    get_recovery_case_by_id,
    insert_audit_event,
    insert_proposed_action,
    update_case_status,
)

logger = logging.getLogger(__name__)


def _reconstruct_result_from_db_row(row: dict[str, Any]) -> ProposalResult:
    """Reconstruct a ProposalResult dataclass from a proposed_actions database row."""
    return ProposalResult(
        action=row["proposed_action"],
        reason=row["reason"],
        diagnosis_used=row["diagnosis_used"],
        diagnosis_confidence=float(row["diagnosis_confidence"]),
        fallback_used=bool(row.get("fallback_used")),
        fallback_reason=row.get("fallback_reason"),
    )


def propose_action(
    conn: Any,
    case_id: int,
    raw_override_action: str | None = None,
) -> ProposalResult:
    """Evaluate a diagnosed recovery case and generate a proposed recovery action.

    Args:
        conn: Active database connection.
        case_id: Primary key ID of the recovery case.
        raw_override_action: Optional raw action string (used by tests/models to evaluate validation/rejection).

    Returns:
        Validated ProposalResult with status='PROPOSED'.

    Raises:
        ValueError: If case_id does not exist or case is not in 'diagnosed' status (and has no existing proposal).
    """
    # ── 1. Fetch case & check preconditions / idempotency ─────────────────────
    case = get_recovery_case_by_id(conn, case_id)
    if not case:
        raise ValueError(f"Recovery case #{case_id} not found.")

    # Idempotency check: if already proposed, return existing proposal result without duplicating
    existing_proposal = get_active_proposal_for_case(conn, case_id)
    if existing_proposal:
        logger.info("Case #%d already has a proposal; returning stored proposal result.", case_id)
        return _reconstruct_result_from_db_row(existing_proposal)

    if case["status"] != "diagnosed":
        raise ValueError(f"Recovery case #{case_id} is not in 'diagnosed' status (status='{case['status']}').")

    # ── 2. Fetch diagnosis ────────────────────────────────────────────────────
    diagnosis_row = get_latest_diagnosis_for_case(conn, case_id)
    if not diagnosis_row:
        raise ValueError(f"No diagnosis record found for case #{case_id}.")

    diagnosis_id = diagnosis_row["id"]
    diagnosis_used = diagnosis_row["likely_cause"]
    diagnosis_confidence = float(diagnosis_row["confidence"])

    # ── 3. Compute Policy Default Action & Reason ─────────────────────────────
    policy_action, policy_reason = get_policy_default_action(diagnosis_used, diagnosis_confidence)

    # ── 4. Evaluate Allowlist Validation / Fallback ───────────────────────────
    final_action = policy_action
    final_reason = policy_reason
    fallback_used = False
    fallback_reason: str | None = None
    rejected_raw_action: str | None = None

    if raw_override_action is not None:
        # Test/model override provided — validate against ALLOWED_ACTIONS
        raw_dict = {
            "action": raw_override_action,
            "reason": f"Evaluated raw action '{raw_override_action}'",
            "diagnosis_used": diagnosis_used,
            "diagnosis_confidence": diagnosis_confidence,
        }
        validated = validate_proposed_action(raw_dict)
        if validated is not None:
            final_action = validated.action
            final_reason = validated.reason
        else:
            # Action was rejected (e.g. DISCOUNT_50_PERCENT or malformed)
            rejected_raw_action = raw_override_action
            fallback_used = True
            if raw_override_action == "":
                fallback_reason = "malformed_output"
            elif raw_override_action not in ALLOWED_ACTIONS:
                fallback_reason = "invalid_model_action"
            else:
                fallback_reason = "malformed_output"
            logger.warning(
                "Raw action '%s' rejected for case #%d; falling back to policy default '%s'",
                raw_override_action,
                case_id,
                policy_action,
            )

    result = ProposalResult(
        action=final_action,
        reason=final_reason,
        diagnosis_used=diagnosis_used,
        diagnosis_confidence=diagnosis_confidence,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )

    # ── 5. Persist Proposal with status='PROPOSED' & Update Case Status ────────
    model_name = diagnosis_row.get("model_name", "policy-engine")
    insert_proposed_action(
        conn=conn,
        case_id=case_id,
        diagnosis_id=diagnosis_id,
        result=result,
        model_name=model_name,
    )

    update_case_status(conn, case_id, "proposed")

    # ── 6. Write Audit Trail Event ─────────────────────────────────────────────
    actor = "system"
    summary = (
        f"Proposal fallback: {result.action} ({result.fallback_reason})"
        if result.fallback_used
        else f"Proposed Action: {result.action}"
    )

    audit_detail = {
        "case_id": case_id,
        "diagnosis_id": diagnosis_id,
        "proposed_action": result.action,
        "reason": result.reason,
        "diagnosis_used": result.diagnosis_used,
        "diagnosis_confidence": result.diagnosis_confidence,
        "fallback_used": result.fallback_used,
        "fallback_reason": result.fallback_reason,
        "rejected_raw_action": rejected_raw_action,
        "status": "PROPOSED",
    }

    insert_audit_event(
        conn=conn,
        stage="proposal",
        actor=actor,
        summary=summary,
        detail_json=json.dumps(audit_detail),
        case_id=case_id,
    )

    logger.info("Completed proposal for case #%d: %s", case_id, summary)
    return result
