"""Risk Detector — deterministic, rule-based risk evaluation.

Pure business logic module. No Flask, no sqlite3 imports at the function-
signature level. Accepts plain Python data structures (dataclasses) and
returns plain Python data structures, making it fully testable without a
running Flask app or live DB connection.

Scoring Table (verbatim — used by evaluate_risk):
    ┌──────┬────────────────────────────┬────────┬───────────────────────────────┐
    │ Rule │ Name                       │ Points │ Notes                         │
    ├──────┼────────────────────────────┼────────┼───────────────────────────────┤
    │ R1   │ Delayed Authorization      │ 40     │ Flags if gap > threshold      │
    │ R2   │ Failed → Authorized        │  0     │ Informational only, never     │
    │      │                            │        │ contributes to flagged/score  │
    │ R3   │ Repeated Failures          │ 60     │ Sliding window over failures  │
    │ R4   │ Multiple Attempts, Intent  │ 50     │ Intent signal, no success yet │
    │ R5   │ Single Isolated Failure    │  0     │ Documented non-trigger        │
    └──────┴────────────────────────────┴────────┴───────────────────────────────┘

    Final risk_score = min(sum_of_fired_rule_points, 100)
    flagged = True if risk_score > 0 (i.e., at least one of R1/R3/R4 fired)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core import risk_config

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class NormalizedEvent:
    """A single payment event, normalized from raw webhook JSON.

    This is the input unit for all rule functions. It carries only the
    fields the detector needs — no raw JSON, no Flask request objects.
    """
    event_type: str          # 'payment.failed' | 'payment.authorized' | 'payment.captured' | ...
    payment_id: str
    order_id: str
    timestamp: datetime      # parsed, not a raw string
    error_code: str | None = None


@dataclass
class RuleResult:
    """Output of a single rule function when the rule fires."""
    rule_name: str           # e.g. "R1_DELAYED_AUTH"
    score: int               # points contributed by this rule
    reason: str              # human-readable explanation
    evidence: list[NormalizedEvent] = field(default_factory=list)


@dataclass
class RiskEvaluation:
    """Final combined output of evaluate_risk()."""
    flagged: bool
    rules_fired: list[str]           # e.g. ["R3_REPEATED_FAILURES"], can be >1
    risk_score: int                   # 0-100, capped
    risk_reason: str                  # human-readable, references winning/primary rule(s)
    evidence: list[NormalizedEvent] = field(default_factory=list)


# ── Rule functions ───────────────────────────────────────────────────────────
# Each takes the full ordered event history for a single order and returns
# None if it doesn't fire, or a RuleResult if it does.

def check_r1_delayed_authorization(events: list[NormalizedEvent]) -> RuleResult | None:
    """R1: Delayed Authorization.

    Finds the first payment.failed event and the eventual payment.authorized
    event for the same order. If the time gap between them exceeds
    DELAYED_AUTH_THRESHOLD_SECONDS, fires.

    If no payment.authorized event exists yet, R1 cannot evaluate —
    returns None (no data to compare against).
    """
    threshold = risk_config.DELAYED_AUTH_THRESHOLD_SECONDS

    # Find the first failure event (or earliest event as reference point).
    first_failed = None
    for e in events:
        if e.event_type == "payment.failed":
            first_failed = e
            break

    if first_failed is None:
        return None

    # Find the first payment.authorized event that comes after the failure.
    auth_event = None
    for e in events:
        if e.event_type == "payment.authorized" and e.timestamp > first_failed.timestamp:
            auth_event = e
            break

    if auth_event is None:
        # No authorization yet — can't evaluate delay.
        return None

    gap_seconds = (auth_event.timestamp - first_failed.timestamp).total_seconds()
    if gap_seconds > threshold:
        return RuleResult(
            rule_name="R1_DELAYED_AUTH",
            score=40,
            reason=(
                f"Authorization took {int(gap_seconds)} seconds, "
                f"exceeding the {threshold}-second threshold."
            ),
            evidence=[first_failed, auth_event],
        )

    return None


def check_r2_failed_then_authorized(events: list[NormalizedEvent]) -> RuleResult | None:
    """R2: Failed then Authorized (informational/measurement signal).

    Finds any payment.failed event followed later by a payment.authorized
    or payment.captured event on the same order_id. Fires informationally
    if this sequence exists.

    IMPORTANT: R2 contributes 0 points toward risk_score and NEVER
    independently sets flagged=True. It is purely an informational/
    measurement signal indicating organic recovery occurred.
    """
    has_failed = False
    for e in events:
        if e.event_type == "payment.failed":
            has_failed = True
        elif has_failed and e.event_type in ("payment.authorized", "payment.captured"):
            # Found the failed→authorized/captured sequence.
            failed_events = [ev for ev in events if ev.event_type == "payment.failed"]
            success_events = [
                ev for ev in events
                if ev.event_type in ("payment.authorized", "payment.captured")
                and ev.timestamp > failed_events[0].timestamp
            ]
            return RuleResult(
                rule_name="R2_FAILED_THEN_AUTH",
                score=0,  # Explicitly 0 — never contributes to flagged/score
                reason="Payment failed then was later authorized/captured (organic recovery).",
                evidence=failed_events[:1] + success_events[:1],
            )

    return None


def check_r3_repeated_failures(events: list[NormalizedEvent]) -> RuleResult | None:
    """R3: Repeated Failures within a sliding window.

    Within any rolling REPEATED_FAILURE_WINDOW_MINUTES window, counts
    payment.failed events on the same order_id. If count >=
    REPEATED_FAILURE_COUNT, fires.

    Implementation: proper sliding window over sorted timestamps. For each
    failure event i, count how many subsequent failures (including i itself)
    fall within the window starting at failure i's timestamp. This correctly
    handles unevenly spaced failures — three failures where the 1st and 3rd
    are within the window but unevenly spaced still count; a failure outside
    the window relative to the anchor does not count toward the threshold.
    """
    window = timedelta(minutes=risk_config.REPEATED_FAILURE_WINDOW_MINUTES)
    threshold = risk_config.REPEATED_FAILURE_COUNT

    # Collect only failure events, already sorted by timestamp (events are
    # guaranteed sorted ascending by the caller/repository layer).
    failures = [e for e in events if e.event_type == "payment.failed"]

    if len(failures) < threshold:
        return None

    # Sliding window: for each failure[i], count failures[j] where
    # failures[j].timestamp - failures[i].timestamp <= window.
    for i in range(len(failures)):
        window_end = failures[i].timestamp + window
        count = 0
        window_events = []
        for j in range(i, len(failures)):
            if failures[j].timestamp <= window_end:
                count += 1
                window_events.append(failures[j])
            else:
                break  # Sorted, so no further events will be in window.
        if count >= threshold:
            return RuleResult(
                rule_name="R3_REPEATED_FAILURES",
                score=60,
                reason=(
                    f"{count} failed attempts occurred within "
                    f"{risk_config.REPEATED_FAILURE_WINDOW_MINUTES} minutes."
                ),
                evidence=window_events,
            )

    return None


def check_r4_multiple_attempts_intent(events: list[NormalizedEvent]) -> RuleResult | None:
    """R4: Multiple Attempts with Intent Signal.

    Fires if:
    1. attempt_count (distinct failed/authorized attempts) >= MIN_ATTEMPTS_FOR_INTENT
    2. The most recent event is payment.failed
    3. No payment.authorized/payment.captured event exists after it

    This signals a user who tried multiple times and is still stuck.
    """
    min_attempts = risk_config.MIN_ATTEMPTS_FOR_INTENT

    # Count distinct attempts: each payment.failed or payment.authorized
    # event represents an attempt. We count unique payment_ids that have
    # a failed or authorized event, or simply count failure events as
    # attempts (since each failure is an attempt).
    attempt_events = [
        e for e in events
        if e.event_type in ("payment.failed", "payment.authorized", "payment.created")
    ]

    # Count distinct payment_ids involved in failed/authorized attempts.
    failed_payment_ids = {e.payment_id for e in events if e.event_type == "payment.failed"}
    auth_payment_ids = {e.payment_id for e in events if e.event_type in ("payment.authorized", "payment.captured")}

    # Total attempt count = distinct payment_ids with failures + distinct with auth
    # but we avoid double-counting: a payment that failed then authorized is 1 attempt.
    all_attempt_ids = failed_payment_ids | auth_payment_ids
    attempt_count = len(all_attempt_ids)

    if attempt_count < min_attempts:
        return None

    # Most recent event must be a failure with no subsequent success.
    if not events:
        return None

    last_event = events[-1]
    if last_event.event_type != "payment.failed":
        return None

    # No success event after the last failure.
    last_failure_time = last_event.timestamp
    has_later_success = any(
        e.event_type in ("payment.authorized", "payment.captured")
        and e.timestamp > last_failure_time
        for e in events
    )
    if has_later_success:
        return None

    failed_evidence = [e for e in events if e.event_type == "payment.failed"]
    return RuleResult(
        rule_name="R4_MULTIPLE_ATTEMPTS_INTENT",
        score=50,
        reason=(
            f"{attempt_count} payment attempts detected with the most recent "
            f"still failed — indicates user intent but ongoing difficulty."
        ),
        evidence=failed_evidence,
    )


def check_r5_single_isolated_failure(events: list[NormalizedEvent]) -> RuleResult | None:
    """R5: Single Isolated Failure — documented NON-TRIGGER.

    This function explicitly recognizes the isolated-failure condition:
    exactly one payment.failed event total for the order, no other events.

    It ALWAYS returns None. This is intentional false-positive suppression,
    not a missing rule. A single isolated failure is extremely common in
    payment flows (user typo, temporary bank issue) and should not trigger
    a recovery case. The function exists for documentation and testability.
    """
    failures = [e for e in events if e.event_type == "payment.failed"]
    non_failures = [e for e in events if e.event_type != "payment.failed"]

    if len(failures) == 1 and len(non_failures) == 0:
        # This IS the R5 condition — single isolated failure.
        # Intentionally not flagging. This is documented false-positive suppression.
        logger.debug(
            "R5: Single isolated failure for order (payment_id=%s) — not flagging.",
            failures[0].payment_id,
        )

    # R5 NEVER returns a RuleResult. Always None.
    return None


# ── Orchestrator ─────────────────────────────────────────────────────────────

def evaluate_risk(events: list[NormalizedEvent]) -> RiskEvaluation:
    """Evaluate all risk rules against an order's event history.

    Calls R1–R5 independently, combines results:
    - rules_fired: list of every rule that fired (R1 and R3 can coexist)
    - risk_score: sum of fired rules' points, capped at 100
    - flagged: True if risk_score > 0 (at least one of R1/R3/R4 fired)
    - risk_reason: concatenated human-readable reasons from all fired rules

    R2 is included in rules_fired for transparency but contributes 0 to score
    and never independently sets flagged=True.
    R5 never fires (always returns None).

    Args:
        events: Full ordered event history for a single order, sorted by
                timestamp ascending.

    Returns:
        RiskEvaluation with the combined risk assessment.
    """
    if not events:
        return RiskEvaluation(
            flagged=False,
            rules_fired=[],
            risk_score=0,
            risk_reason="No events to evaluate.",
            evidence=[],
        )

    rule_checks = [
        check_r1_delayed_authorization,
        check_r2_failed_then_authorized,
        check_r3_repeated_failures,
        check_r4_multiple_attempts_intent,
        # R5 is called for documentation/completeness but never returns a result.
        check_r5_single_isolated_failure,
    ]

    fired_results: list[RuleResult] = []
    for check_fn in rule_checks:
        result = check_fn(events)
        if result is not None:
            fired_results.append(result)

    rules_fired = [r.rule_name for r in fired_results]
    raw_score = sum(r.score for r in fired_results)
    risk_score = min(raw_score, 100)  # Cap at 100

    # flagged is True only if at least one non-R2 rule contributed score.
    # Since R2 has score=0, this is equivalent to risk_score > 0.
    flagged = risk_score > 0

    # Build combined reason string.
    reasons = [r.reason for r in fired_results if r.reason]
    if len(reasons) == 0:
        risk_reason = "No risk rules fired."
    elif len(reasons) == 1:
        risk_reason = reasons[0]
    else:
        risk_reason = " Additionally, ".join(reasons)

    # Combine evidence from all fired rules (deduplicate by identity).
    all_evidence: list[NormalizedEvent] = []
    seen_ids: set[tuple[str, str]] = set()
    for r in fired_results:
        for e in r.evidence:
            key = (e.payment_id, e.event_type)
            if key not in seen_ids:
                seen_ids.add(key)
                all_evidence.append(e)

    return RiskEvaluation(
        flagged=flagged,
        rules_fired=rules_fired,
        risk_score=risk_score,
        risk_reason=risk_reason,
        evidence=all_evidence,
    )


# ── DB-aware orchestration ───────────────────────────────────────────────────
# This function bridges the pure-logic evaluate_risk() with the DB layer.
# It is imported by the webhook route and the CLI demo script.

def process_order_for_risk(conn, order_id: str) -> RiskEvaluation | None:
    """Fetch event history, evaluate risk, and create a case if flagged.

    This is the single entry point for detection, used by both the webhook
    route and the CLI fixture script. All detection + case-creation logic
    lives here, keeping the route focused on HTTP concerns only.

    Args:
        conn: Active sqlite3 connection (passed through, not imported).
        order_id: The Razorpay order ID to evaluate.

    Returns:
        The RiskEvaluation result, or None if no events found.
    """
    # Import DB functions here to keep module-level imports clean of sqlite3.
    from app.db.repositories import (
        get_event_history_for_order,
        get_open_case_for_payment,
        insert_audit_event,
        insert_recovery_case,
    )

    events = get_event_history_for_order(conn, order_id)
    if not events:
        return None

    evaluation = evaluate_risk(events)

    if not evaluation.flagged:
        return evaluation

    # Determine the primary payment_id for case creation.
    # Use the payment_id from the most recent failure event if available,
    # otherwise use the first event's payment_id.
    payment_id = events[-1].payment_id
    for e in reversed(events):
        if e.event_type == "payment.failed":
            payment_id = e.payment_id
            break

    # Idempotency: check if an open case already exists for this payment.
    existing_case = get_open_case_for_payment(conn, payment_id)
    if existing_case is not None:
        logger.info(
            "Existing open case (id=%s) for payment %s — not creating duplicate.",
            existing_case["id"],
            payment_id,
        )
        insert_audit_event(
            conn=conn,
            stage="detection",
            actor="system",
            summary=f"Existing open case reused for payment {payment_id}, no new case created.",
            detail_json=json.dumps({
                "existing_case_id": existing_case["id"],
                "rules_fired": evaluation.rules_fired,
                "risk_score": evaluation.risk_score,
            }),
            case_id=existing_case["id"],
        )
        return evaluation

    # Create new recovery case.
    risk_rule = ",".join(evaluation.rules_fired)
    case_id = insert_recovery_case(
        conn=conn,
        payment_id=payment_id,
        risk_rule=risk_rule,
        risk_score=evaluation.risk_score,
        risk_reason=evaluation.risk_reason,
    )

    # Write detection audit event.
    evidence_ids = [
        {"payment_id": e.payment_id, "event_type": e.event_type}
        for e in evaluation.evidence
    ]
    insert_audit_event(
        conn=conn,
        stage="detection",
        actor="system",
        summary=evaluation.risk_reason,
        detail_json=json.dumps({
            "case_id": case_id,
            "rules_fired": evaluation.rules_fired,
            "risk_score": evaluation.risk_score,
            "evidence": evidence_ids,
        }),
        case_id=case_id,
    )

    logger.info(
        "Opened recovery case %d for payment %s (score=%d, rules=%s)",
        case_id,
        payment_id,
        evaluation.risk_score,
        risk_rule,
    )

    return evaluation
