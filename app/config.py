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
        if os.environ.get("VERCEL"):
            secret = "default_webhook_secret_for_demo"
        else:
            raise RuntimeError(
                "RAZORPAY_WEBHOOK_SECRET is not set. "
                "The application cannot start without webhook signature verification. "
                "Set this variable in your .env file or environment."
            )

    # On Vercel (serverless), only /tmp is writable. Detect via VERCEL env var.
    if os.environ.get("VERCEL"):
        default_db_path = "/tmp/recoveryos.db"
    else:
        default_db_path = "./recoveryos.db"

    return {
        "RAZORPAY_WEBHOOK_SECRET": secret,
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", default_db_path),
        "FLASK_ENV": os.environ.get("FLASK_ENV", "production"),
    }
