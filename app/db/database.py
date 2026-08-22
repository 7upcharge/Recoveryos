"""Database connection and initialization using raw sqlite3."""

import os
import sqlite3


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return a new SQLite connection with Row factory and foreign keys enabled.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A sqlite3.Connection configured with Row factory and foreign keys.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    """Initialize the database by executing schema.sql.

    Idempotent — uses CREATE TABLE IF NOT EXISTS so it is safe to call
    multiple times.

    Args:
        db_path: Path to the SQLite database file.
    """
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()

    conn = get_connection(db_path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()
