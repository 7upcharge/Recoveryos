"""CLI script: Run Gemini Diagnoser on open recovery cases.

Usage:
    python scripts/run_diagnoser_on_case.py [case_id] [--all]

If case_id is provided, diagnoses that specific case.
If --all is set or no case_id is provided, iterates over all open recovery cases.
"""

import argparse
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from app.core.diagnoser import diagnose_case
from app.db.database import get_connection, init_db


def main():
    parser = argparse.ArgumentParser(
        description="Run RecoveryOS Gemini Diagnoser on open recovery cases."
    )
    parser.add_argument(
        "case_id",
        nargs="?",
        type=int,
        default=None,
        help="Specific recovery case ID to diagnose.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Diagnose all open recovery cases in the database.",
    )
    args = parser.parse_args()

    db_path = os.environ.get("DATABASE_PATH", "./recoveryos.db")
    init_db(db_path)
    conn = get_connection(db_path)

    try:
        if args.case_id is not None:
            case_ids = [args.case_id]
        else:
            rows = conn.execute(
                "SELECT id FROM recovery_cases WHERE status = 'open' ORDER BY id ASC"
            ).fetchall()
            case_ids = [row["id"] for row in rows]

        if not case_ids:
            print("No open recovery cases found to diagnose.")
            return

        print(f"Running Gemini Diagnoser on {len(case_ids)} case(s)...\n")
        print("=" * 70)

        for cid in case_ids:
            print(f"\nCase ID: #{cid}")
            print("-" * 50)
            try:
                result = diagnose_case(conn, cid)
                conn.commit()

                print(f"  Likely Cause:    {result.diagnosis}")
                print(f"  Confidence:      {result.confidence:.2f}")
                print(f"  Explanation:     {result.explanation}")
                print(f"  Evidence:        {result.evidence or '(none)'}")
                print(f"  Limitations:     {result.limitations or '(none)'}")
                print(f"  Fallback Used:   {result.fallback_used}")
                if result.fallback_used:
                    print(f"  Fallback Reason: {result.fallback_reason}")
            except Exception as exc:
                conn.rollback()
                print(f"  ERROR diagnosing case #{cid}: {exc}")

        print("\n" + "=" * 70)
        print("Done.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
