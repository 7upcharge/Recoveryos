"""CLI Wrapper Script: Seed RecoveryOS Day 6 Synthetic Demo Dataset.

Usage:
    python scripts/seed_demo.py [--reset]

Options:
    --reset     Clear all prior demo dataset rows before seeding (default: True).
"""

import argparse
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from app.db.database import get_connection, init_db
from app.demo.seed_demo_data import seed_demo_dataset


def main():
    parser = argparse.ArgumentParser(
        description="Seed 9 synthetic demonstration scenarios into RecoveryOS SQLite database."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        default=True,
        help="Clear prior demo rows before seeding (default: True).",
    )
    parser.add_argument(
        "--no-reset",
        action="store_false",
        dest="reset",
        help="Do not clear prior demo rows before seeding.",
    )
    args = parser.parse_args()

    db_path = os.environ.get("DATABASE_PATH", "./recoveryos.db")
    init_db(db_path)
    conn = get_connection(db_path)

    try:
        print(f"Database Path: {db_path}")
        summary = seed_demo_dataset(conn, reset=args.reset)

        print("\n" + "=" * 70)
        print("Demo dataset successfully seeded!")
        print(f"  Scenarios Processed:    {summary['scenarios_seeded']}")
        print(f"  Recovery Cases Created: {summary['cases_created']}")
        print(f"  Diagnoses Created:      {summary['diagnoses_created']}")
        print(f"  Proposals Created:      {summary['proposals_created']}")
        print(f"  Verifications Created:  {summary['verifications_created']}")
        print("=" * 70)
        print("Start Flask application: python run.py")
        print("Control Center URL:      http://localhost:5000/")
        print("=" * 70)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
