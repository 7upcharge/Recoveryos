"""Flask Dashboard Routes for RecoveryOS Control Center Day 6.

All route handlers delegate data fetching and shaping to `app/dashboard/view_models.py`.
No SQL or direct DB manipulation is performed inside route functions.
"""

import logging
from flask import Blueprint, current_app, render_template, abort

from app.db.database import get_connection
from app import dashboard
from app.dashboard.view_models import (
    get_audit_trail,
    get_case_detail,
    get_cases_table_rows,
    get_dev_mode_summary,
    get_diagnosis_insights_summary,
    get_hero_metrics,
    get_organic_vs_assisted_summary,
    get_pipeline_counts,
)

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/", methods=["GET"])
def index():
    """Render the Control Center homepage (Overview dashboard)."""
    db_path = current_app.config["DATABASE_PATH"]
    conn = get_connection(db_path)
    try:
        metrics = get_hero_metrics(conn)
        pipeline = get_pipeline_counts(conn)
        cases = get_cases_table_rows(conn)
        organic_summary = get_organic_vs_assisted_summary(conn)
        dev_summary = get_dev_mode_summary(conn)
        diagnosis_insights = get_diagnosis_insights_summary(conn)
        return render_template(
            "overview.html",
            metrics=metrics,
            pipeline=pipeline,
            cases=cases,
            organic_summary=organic_summary,
            dev_summary=dev_summary,
            diagnosis_insights=diagnosis_insights,
        )
    finally:
        conn.close()


@dashboard_bp.route("/cases", methods=["GET"])
def cases():
    """Render the full Recovery Cases list table view."""
    db_path = current_app.config["DATABASE_PATH"]
    conn = get_connection(db_path)
    try:
        cases_data = get_cases_table_rows(conn)
        return render_template("cases.html", cases=cases_data)
    finally:
        conn.close()


@dashboard_bp.route("/cases/<int:case_id>", methods=["GET"])
def case_detail_view(case_id: int):
    """Render the 7-section case detail view for a specific recovery case."""
    db_path = current_app.config["DATABASE_PATH"]
    conn = get_connection(db_path)
    try:
        data = get_case_detail(conn, case_id)
        if not data:
            abort(404, description=f"Recovery case #{case_id} not found.")
        return render_template("case_detail.html", data=data)
    finally:
        conn.close()


@dashboard_bp.route("/audit", methods=["GET"])
def audit():
    """Render the chronological Audit & Activity Trail view."""
    db_path = current_app.config["DATABASE_PATH"]
    conn = get_connection(db_path)
    try:
        audit_events = get_audit_trail(conn)
        return render_template("audit_trail.html", audit_events=audit_events)
    finally:
        conn.close()
