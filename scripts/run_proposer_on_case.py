"""CLI script: Run Recovery Proposer on diagnosed recovery cases.

Usage:
    python scripts/run_proposer_on_case.py [case_id] [--all]

If case_id is provided, generates a proposal for that specific case.
If --all is set or no case_id is provided, iterates over all diagnosed recovery cases.
"""

import argparse
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from app.core.proposer import propose_action
from app.db.database import get_connection, init_db


def main():
    parser = argparse.ArgumentParser(
        description="Run RecoveryOS Recovery Proposer on diagnosed recovery cases."
    )
    parser.add_argument(
        "case_id",
        nargs="?",
        type=int,
        default=None,
        help="Specific recovery case ID to evaluate for proposal.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Propose actions for all diagnosed recovery cases in the database.",
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
                "SELECT id FROM recovery_cases WHERE status = 'diagnosed' ORDER BY id ASC"
            ).fetchall()
            case_ids = [row["id"] for row in rows]

        if not case_ids:
            print("No diagnosed recovery cases found to propose actions for.")
            return

        print(f"Running Recovery Proposer on {len(case_ids)} case(s)...\n")
        print("=" * 70)

        for cid in case_ids:
            print(f"\nCase ID: #{cid}")
            print("-" * 50)
            try:
                result = propose_action(conn, cid)
                conn.commit()

                print(f"  Proposed Action:  {result.action}")
                print(f"  Reason:           {result.reason}")
                print(f"  Diagnosis Used:   {result.diagnosis_used} (conf={result.diagnosis_confidence:.2f})")
                print(f"  Proposal Status:  PROPOSED (No execution initiated)")
                print(f"  Fallback Used:    {result.fallback_used}")
                if result.fallback_used:
                    print(f"  Fallback Reason:  {result.fallback_reason}")
            except Exception as exc:
                conn.rollback()
                print(f"  ERROR proposing action for case #{cid}: {exc}")

        print("\n" + "=" * 70)
        print("Done. (Note: Proposals are recommendation-only with zero execution capability).")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
