"""Razorpay webhook signature verification.

Implements HMAC-SHA256 verification as specified by Razorpay's webhook docs.
Never raises on bad input — returns False instead.
"""

import hashlib
import hmac


def verify_signature(
    payload_bytes: bytes,
    signature_header: str,
    secret: str,
) -> bool:
    """Verify the Razorpay webhook HMAC-SHA256 signature.

    Computes the HMAC-SHA256 digest of the raw request body using the
    webhook secret, then compares it to the signature sent in the
    X-Razorpay-Signature header using constant-time comparison.

    This function operates on the raw request bytes. It does NOT
    re-serialize JSON before verification — doing so would break
    signature checks if key ordering or whitespace differs.

    Args:
        payload_bytes: The raw HTTP request body, exactly as received.
        signature_header: Value of the X-Razorpay-Signature header.
        secret: The webhook secret configured in the Razorpay dashboard.

    Returns:
        True if the signature is valid, False otherwise.
        Returns False (never raises) for missing, empty, or malformed input.
    """
    try:
        if not payload_bytes or not signature_header or not secret:
            return False

        expected = hmac.new(
            key=secret.encode("utf-8"),
            msg=payload_bytes,
            digestmod=hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature_header)
    except Exception:
        # Never raise on bad input — a failed verification is just False
        return False
