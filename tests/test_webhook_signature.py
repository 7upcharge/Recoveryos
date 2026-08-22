"""Tests for Razorpay webhook HMAC-SHA256 signature verification.

Covers:
1. Valid signature → True
2. Invalid/tampered signature → False
3. Missing signature → False
4. Empty/malformed secret or payload → False
5. Bad input must not unexpectedly raise
"""

import hashlib
import hmac

from app.webhooks.signature import verify_signature


SECRET = "test_webhook_secret_key"


def _sign(payload: bytes, secret: str = SECRET) -> str:
    """Helper: produce a valid HMAC-SHA256 hex signature."""
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256,
    ).hexdigest()


class TestVerifySignature:
    """Signature verification test suite."""

    def test_valid_signature_returns_true(self):
        payload = b'{"event": "payment.failed"}'
        sig = _sign(payload)
        assert verify_signature(payload, sig, SECRET) is True

    def test_tampered_payload_returns_false(self):
        original = b'{"event": "payment.failed"}'
        tampered = b'{"event": "payment.captured"}'
        sig = _sign(original)
        assert verify_signature(tampered, sig, SECRET) is False

    def test_wrong_signature_returns_false(self):
        payload = b'{"event": "payment.failed"}'
        assert verify_signature(payload, "badhexsignature", SECRET) is False

    def test_missing_signature_returns_false(self):
        payload = b'{"event": "payment.failed"}'
        assert verify_signature(payload, "", SECRET) is False

    def test_none_signature_returns_false(self):
        payload = b'{"event": "payment.failed"}'
        assert verify_signature(payload, None, SECRET) is False

    def test_empty_payload_returns_false(self):
        assert verify_signature(b"", "somesig", SECRET) is False

    def test_none_payload_returns_false(self):
        assert verify_signature(None, "somesig", SECRET) is False

    def test_empty_secret_returns_false(self):
        payload = b'{"event": "payment.failed"}'
        sig = _sign(payload)
        assert verify_signature(payload, sig, "") is False

    def test_none_secret_returns_false(self):
        payload = b'{"event": "payment.failed"}'
        assert verify_signature(payload, "somesig", None) is False

    def test_wrong_secret_returns_false(self):
        payload = b'{"event": "payment.failed"}'
        sig = _sign(payload, secret="correct_secret")
        assert verify_signature(payload, sig, "wrong_secret") is False

    def test_binary_payload_does_not_raise(self):
        """Non-UTF8 bytes should not crash — just return False or True."""
        payload = b"\x80\x81\x82\xff"
        sig = _sign(payload)
        # Should still verify correctly because HMAC works on raw bytes
        assert verify_signature(payload, sig, SECRET) is True

    def test_extremely_long_signature_does_not_raise(self):
        payload = b'{"event": "test"}'
        long_sig = "a" * 10000
        assert verify_signature(payload, long_sig, SECRET) is False

    def test_integer_inputs_do_not_raise(self):
        """Bad types should return False, not crash."""
        assert verify_signature(12345, "sig", SECRET) is False
        assert verify_signature(b"data", 12345, SECRET) is False
