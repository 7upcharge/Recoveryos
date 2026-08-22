#!/usr/bin/env python3
"""Standalone CLI script to initialize the RecoveryOS SQLite database.

Usage:
    python scripts/init_db.py

Creates/resets the database at the path specified by DATABASE_PATH
(defaults to ./recoveryos.db). Uses schema.sql which is idempotent
via CREATE TABLE IF NOT EXISTS.
"""

import os
import sys

# Add project root to path so we can import app modules.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from app.db.database import init_db


def main():
    load_dotenv()
    db_path = os.environ.get("DATABASE_PATH", "./recoveryos.db")
    print(f"Initializing database at: {db_path}")
    init_db(db_path)
    print("Database initialized successfully.")
    print("Tables created: webhook_events, payments, audit_events")


if __name__ == "__main__":
    main()
