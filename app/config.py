"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv


def load_config():
    """Load and validate configuration from environment variables.

    Raises:
        RuntimeError: If RAZORPAY_WEBHOOK_SECRET is not set.
            The application must never boot with signature verification
            silently disabled.
    """
    load_dotenv()

    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError(
            "RAZORPAY_WEBHOOK_SECRET is not set. "
            "The application cannot start without webhook signature verification. "
            "Set this variable in your .env file or environment."
        )

    return {
        "RAZORPAY_WEBHOOK_SECRET": secret,
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", "./recoveryos.db"),
        "FLASK_ENV": os.environ.get("FLASK_ENV", "development"),
    }
