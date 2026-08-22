"""Flask application factory for RecoveryOS."""

import logging

from flask import Flask

from app.config import load_config
from app.db.database import init_db
from app.webhooks.routes import webhook_bp


def create_app(config_overrides: dict | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config_overrides: Optional dictionary of config values to override
            the environment-loaded defaults. Used by tests to inject
            isolated database paths and test secrets.

    Returns:
        Configured Flask application.

    Raises:
        RuntimeError: If RAZORPAY_WEBHOOK_SECRET is not set and no
            override provides it.
    """
    app = Flask(__name__)

    # ── Load configuration ────────────────────────────────────────────
    if config_overrides and "RAZORPAY_WEBHOOK_SECRET" in config_overrides:
        # When tests provide full config overrides (including the secret),
        # skip load_config() which would require a .env file.
        config = {
            "RAZORPAY_WEBHOOK_SECRET": config_overrides["RAZORPAY_WEBHOOK_SECRET"],
            "DATABASE_PATH": config_overrides.get("DATABASE_PATH", "./recoveryos.db"),
            "FLASK_ENV": config_overrides.get("FLASK_ENV", "testing"),
        }
    else:
        config = load_config()
        if config_overrides:
            config.update(config_overrides)

    app.config.update(config)

    # ── Logging ───────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG if config.get("FLASK_ENV") == "development" else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ── Initialize database ───────────────────────────────────────────
    init_db(config["DATABASE_PATH"])

    # ── Register blueprints ───────────────────────────────────────────
    app.register_blueprint(webhook_bp)

    return app
