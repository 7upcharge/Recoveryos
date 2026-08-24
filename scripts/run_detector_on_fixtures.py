"""CLI script: Run the Risk Detector against stored webhook events.

Usage:
    python scripts/run_detector_on_fixtures.py [order_id]

If order_id is provided, evaluates only that order.
If omitted, iterates every distinct order_id in webhook_events.

This reuses the exact same code path as the webhook route
(process_order_for_risk) — no duplicate logic.
"""

import argparse
import sys
import os

# Ensure the project root is on sys.path so imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.database import get_connection, init_db
from app.core.risk_detector import process_order_for_risk


def main():
    parser = argparse.ArgumentParser(
        description="Run the RecoveryOS Risk Detector on stored webhook events."
    )
    parser.add_argument(
        "order_id",
        nargs="?",
        default=None,
        help="Specific order_id to evaluate. If omitted, all orders are evaluated.",
    )
    args = parser.parse_args()

    db_path = os.environ.get("DATABASE_PATH", "./recoveryos.db")
    init_db(db_path)
    conn = get_connection(db_path)

    try:
        if args.order_id:
            order_ids = [args.order_id]
        else:
            rows = conn.execute(
                "SELECT DISTINCT order_id FROM webhook_events WHERE order_id IS NOT NULL"
            ).fetchall()
            order_ids = [row["order_id"] for row in rows]

        if not order_ids:
            print("No orders found in webhook_events.")
            return

        print(f"Evaluating {len(order_ids)} order(s)...\n")
        print("=" * 70)

        for oid in order_ids:
            print(f"\nOrder: {oid}")
            print("-" * 50)

            result = process_order_for_risk(conn, oid)

            if result is None:
                print("  No events found for this order.")
                continue

            print(f"  Flagged:      {result.flagged}")
            print(f"  Risk Score:   {result.risk_score}")
            print(f"  Rules Fired:  {result.rules_fired or '(none)'}")
            print(f"  Reason:       {result.risk_reason}")

            if result.evidence:
                print(f"  Evidence ({len(result.evidence)} events):")
                for ev in result.evidence:
                    print(f"    - {ev.event_type} | {ev.payment_id} | {ev.timestamp}")

            # Check if a case was created or reused.
            from app.db.repositories import get_open_case_for_payment
            # Determine the payment_id used for case lookup.
            payment_id = None
            for ev in reversed(result.evidence):
                if ev.event_type == "payment.failed":
                    payment_id = ev.payment_id
                    break
            if payment_id is None and result.evidence:
                payment_id = result.evidence[-1].payment_id

            if payment_id and result.flagged:
                case = get_open_case_for_payment(conn, payment_id)
                if case:
                    print(f"  Case:         #{case['id']} (status={case['status']})")
                else:
                    print("  Case:         (no open case)")

        conn.commit()
        print("\n" + "=" * 70)
        print("Done.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
