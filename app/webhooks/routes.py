"""Webhook ingestion routes for Razorpay events.

POST /webhooks/razorpay — the sole endpoint for Day 1.

Processing flow:
1. Read raw body (never request.json before signature check)
2. Verify HMAC-SHA256 signature
3. Parse JSON only after signature passes
4. Extract event information
5. Idempotency check on razorpay_event_id
6. Store raw event immutably
7. Normalize payment state (upsert)
8. Write ingestion audit event
9. Return acknowledgement
"""

import hashlib
import json
import logging

from flask import Blueprint, current_app, jsonify, request

from app.db.database import get_connection
from app.db.repositories import (
    insert_audit_event,
    insert_webhook_event,
    is_duplicate_event,
    upsert_payment,
)
from app.core.risk_detector import process_order_for_risk
from app.webhooks.signature import verify_signature

logger = logging.getLogger(__name__)

webhook_bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")

# Day 1 event types that carry payment entity data we can normalize.
PAYMENT_EVENT_TYPES = {
    "payment.created",
    "payment.authorized",
    "payment.captured",
    "payment.failed",
}

# Events that carry order entity data.
ORDER_EVENT_TYPES = {
    "order.paid",
}

# Status mapping from event type to the payment status string we store.
EVENT_TO_STATUS = {
    "payment.created": "created",
    "payment.authorized": "authorized",
    "payment.captured": "captured",
    "payment.failed": "failed",
}


@webhook_bp.route("/razorpay", methods=["POST"])
def razorpay_webhook():
    """Handle an incoming Razorpay webhook.

    Returns:
        Tuple of (response_json, http_status_code).
    """
    # ── 1. Read raw body ───────────────────────────────────────────────
    # Use request.get_data() to capture exact bytes for signature check.
    # Do NOT touch request.json before verification.
    payload_bytes = request.get_data()

    # ── 2. Verify signature ────────────────────────────────────────────
    signature_header = request.headers.get("X-Razorpay-Signature", "")
    secret = current_app.config["RAZORPAY_WEBHOOK_SECRET"]

    if not verify_signature(payload_bytes, signature_header, secret):
        logger.warning("Webhook signature verification failed")
        return jsonify({"error": "Invalid signature"}), 400

    # ── 3. Parse JSON (only after signature succeeds) ──────────────────
    try:
        payload = json.loads(payload_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Webhook payload is not valid JSON: %s", exc)
        return jsonify({"error": "Malformed JSON payload"}), 400

    # ── 4. Extract event information ───────────────────────────────────
    event_type = payload.get("event", "")

    # Use Razorpay's top-level "id" field as the canonical event ID.
    # Fallback: if absent, construct a deterministic ID by hashing the
    # raw payload bytes. This ensures idempotency even for payloads that
    # lack a top-level id, while remaining reproducible for the same
    # payload content.
    razorpay_event_id = payload.get("id")
    if not razorpay_event_id:
        razorpay_event_id = "derived-" + hashlib.sha256(payload_bytes).hexdigest()

    # Extract payment/order entities from Razorpay's nested structure.
    payment_entity = None
    order_entity = None

    payload_data = payload.get("payload", {})

    if "payment" in payload_data:
        payment_entity = payload_data["payment"].get("entity", {})

    if "order" in payload_data:
        order_entity = payload_data["order"].get("entity", {})

    payment_id = None
    order_id = None

    if payment_entity:
        payment_id = payment_entity.get("id")
        order_id = payment_entity.get("order_id")

    if order_entity and not order_id:
        order_id = order_entity.get("id")

    event_created_at = payload.get("created_at")
    # Razorpay sends created_at as a Unix timestamp (integer).
    if event_created_at is not None:
        event_created_at = str(event_created_at)

    # ── 5–9 wrapped in try/except for clean 500 handling ──────────────
    try:
        db_path = current_app.config["DATABASE_PATH"]
        conn = get_connection(db_path)

        try:
            # ── 5. Idempotency check ──────────────────────────────────
            if is_duplicate_event(conn, razorpay_event_id):
                logger.info(
                    "Duplicate event %s — returning 200 without processing",
                    razorpay_event_id,
                )
                return jsonify({"status": "already_processed"}), 200

            # ── 6. Store raw event ────────────────────────────────────
            insert_webhook_event(
                conn=conn,
                razorpay_event_id=razorpay_event_id,
                event_type=event_type,
                payment_id=payment_id,
                order_id=order_id,
                payload_json=payload_bytes.decode("utf-8"),
                event_created_at=event_created_at,
            )

            # ── 7. Normalize payment state ────────────────────────────
            if event_type in PAYMENT_EVENT_TYPES and payment_entity and payment_id:
                status = EVENT_TO_STATUS.get(event_type, event_type)
                upsert_payment(
                    conn=conn,
                    payment_id=payment_id,
                    order_id=order_id,
                    amount=int(payment_entity.get("amount", 0)),
                    currency=payment_entity.get("currency", "INR"),
                    status=status,
                    method=payment_entity.get("method"),
                    bank=payment_entity.get("bank"),
                    event_type=event_type,
                )
            elif event_type in ORDER_EVENT_TYPES and order_entity:
                # order.paid — if a payment entity is also present, use it
                # to create/update the payment record.
                if payment_entity and payment_id:
                    upsert_payment(
                        conn=conn,
                        payment_id=payment_id,
                        order_id=order_id,
                        amount=int(payment_entity.get("amount", 0)),
                        currency=payment_entity.get("currency", "INR"),
                        status="captured",
                        method=payment_entity.get("method"),
                        bank=payment_entity.get("bank"),
                        event_type=event_type,
                    )
            # Unknown-but-signed events: stored in webhook_events above,
            # no payment state invented, no crash.

            # ── 8. Write ingestion audit event ────────────────────────
            detail = {
                "razorpay_event_id": razorpay_event_id,
                "event_type": event_type,
                "payment_id": payment_id,
                "order_id": order_id,
            }
            insert_audit_event(
                conn=conn,
                stage="ingestion",
                actor="system",
                summary=f"Received webhook: {event_type} for payment {payment_id}",
                detail_json=json.dumps(detail),
            )

            # ── Day 2: Risk Detection ─────────────────────────────────
            # After ingestion completes, evaluate the order's full event
            # history for risk signals. Case creation and audit logging
            # are handled internally by process_order_for_risk.
            if order_id:
                process_order_for_risk(conn, order_id)

            conn.commit()

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except Exception:
        logger.exception("Unexpected error processing webhook")
        return jsonify({"error": "Internal server error"}), 500

    # ── 9. Return acknowledgement ─────────────────────────────────────
    return jsonify({"status": "processed"}), 200
