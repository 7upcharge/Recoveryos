"""Shared test fixtures for RecoveryOS tests.

Every test gets an isolated in-memory SQLite database via the `app` and
`client` fixtures. No database state is shared between tests.
"""

import hashlib
import hmac
import json
import os
import tempfile

import pytest

from app import create_app


TEST_WEBHOOK_SECRET = "test_secret_for_unit_tests"


def compute_signature(payload_bytes: bytes, secret: str) -> str:
    """Compute the HMAC-SHA256 signature for a webhook payload.

    Helper for tests that need to send correctly signed requests.
    """
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()


@pytest.fixture
def app(tmp_path):
    """Create a Flask app with an isolated temporary database.

    Each test gets its own fresh database file in a temp directory,
    ensuring complete isolation between tests.
    """
    db_path = str(tmp_path / "test_recoveryos.db")
    test_app = create_app(
        config_overrides={
            "RAZORPAY_WEBHOOK_SECRET": TEST_WEBHOOK_SECRET,
            "DATABASE_PATH": db_path,
            "FLASK_ENV": "testing",
        }
    )
    test_app.config["TESTING"] = True
    yield test_app


@pytest.fixture
def client(app):
    """Flask test client bound to the isolated app."""
    return app.test_client()


@pytest.fixture
def db_conn(app):
    """Direct database connection for assertions in tests.

    Uses the same database file as the test app.
    """
    from app.db.database import get_connection

    conn = get_connection(app.config["DATABASE_PATH"])
    yield conn
    conn.close()


@pytest.fixture
def sample_payloads():
    """Load the sample webhook payloads fixture file."""
    fixture_path = os.path.join(
        os.path.dirname(__file__), "..", "fixtures", "sample_webhook_payloads.json"
    )
    with open(fixture_path, "r") as f:
        return json.load(f)
