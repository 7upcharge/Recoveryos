"""Developer Dashboard Blueprint for RecoveryOS.

Provides web routes GET / (dashboard homepage) and GET /cases/<case_id> (case detail page).
All data is dynamically fetched from SQLite via repository helper functions.
"""

import logging
from flask import Blueprint, current_app, render_template, abort

from app.db.database import get_connection
from app.db.repositories import (
    get_all_recovery_cases_summary,
    get_case_detail_full,
    get_dashboard_summary,
)

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/", methods=["GET"])
def index():
    """Render the main developer dashboard homepage.

    Fetches summary metrics (total payments, payments at risk, open cases,
    diagnosed cases, total value at risk) and recent recovery cases table data.
    """
    db_path = current_app.config["DATABASE_PATH"]
    conn = get_connection(db_path)
    try:
        summary = get_dashboard_summary(conn)
        cases = get_all_recovery_cases_summary(conn)
        return render_template("dashboard.html", summary=summary, cases=cases)
    finally:
        conn.close()


@dashboard_bp.route("/cases/<int:case_id>", methods=["GET"])
def case_detail(case_id: int):
    """Render the detail view page for a specific recovery case.

    Includes payment overview, risk rule explanation, Gemini diagnosis breakdown,
    payment event timeline, and stage audit events.
    """
    db_path = current_app.config["DATABASE_PATH"]
    conn = get_connection(db_path)
    try:
        data = get_case_detail_full(conn, case_id)
        if not data:
            abort(404, description=f"Recovery case #{case_id} not found.")
        return render_template("case_detail.html", data=data)
    finally:
        conn.close()
