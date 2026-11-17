"""Report controller — report generation and Excel/PDF exports."""
from flask import Blueprint, flash, render_template, request, send_file
from flask_login import current_user

from app.extensions import db
from app.models import Category, User
from app.services import report_service
from app.services.report_service import REPORT_TYPES, build_report, collect_filters
from app.services.enquiry_service import ENQUIRY_STATUSES
from app.utils.decorators import login_required

report_bp = Blueprint("report", __name__)

# Detailed reports page through their records (10 per page, numbered links)
# like the enquiry list; summary/grouped reports are short and render in full.
REPORT_ROWS_PER_PAGE = 10
DETAILED_REPORT_TYPES = ("total", "pending", "resolved")


def _form_choices():
    return {
        "report_types": REPORT_TYPES,
        "categories": Category.query.filter_by(is_active=True).order_by(Category.category_name).all(),
        "statuses": ENQUIRY_STATUSES,
        "sources": ("ADMIN", "USER", "VISITOR"),
        "employees": User.query.filter(User.is_active.is_(True)).order_by(User.username).all(),
    }


def _link_params(filters: dict) -> dict:
    """Raw filter values for GET pagination links (empty values dropped)."""
    params = {
        "report_type": filters["report_type"],
        "date_from": request.values.get("date_from", ""),
        "date_to": request.values.get("date_to", ""),
        "category_id": request.values.get("category_id", ""),
        "status": request.values.get("status", ""),
        "assigned_to_user_id": request.values.get("assigned_to_user_id", ""),
        "source": request.values.get("source", ""),
    }
    return {key: value for key, value in params.items() if value}


@report_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    is_post = request.method == "POST"
    filters = collect_filters(request.form if is_post else request.args)

    # POST (Generate Report) or GET with params (pagination links) render the
    # report; a plain GET from the sidebar shows the empty state.
    generate = is_post or bool(request.args)
    report = build_report(filters, current_user) if generate else None
    if is_post and report and not report["rows"]:
        flash("No records matched the selected filters.", "info")

    # Detailed reports: slice the rows for the requested page.
    page_rows = report["rows"] if report else []
    pagination = None
    if report and filters["report_type"] in DETAILED_REPORT_TYPES:
        rows = report["rows"]
        if rows:
            pages = (len(rows) + REPORT_ROWS_PER_PAGE - 1) // REPORT_ROWS_PER_PAGE
            page = min(max(1, request.args.get("page", 1, type=int) or 1), pages)
            start = (page - 1) * REPORT_ROWS_PER_PAGE
            page_rows = rows[start:start + REPORT_ROWS_PER_PAGE]
            pagination = {
                "page": page,
                "pages": pages,
                "total": len(rows),
                "start": start + 1,
                "end": start + len(page_rows),
            }

    return render_template(
        "reports/index.html",
        report=report,
        page_rows=page_rows,
        pagination=pagination,
        link_params=_link_params(filters) if generate else {},
        filters=filters,
        **_form_choices(),
    )


@report_bp.route("/export/excel", methods=["POST"])
@login_required
def export_excel():
    filters = collect_filters(request.form)
    report = build_report(filters, current_user)
    buffer = report_service.export_excel(report, filters)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="CAFirm_Report.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@report_bp.route("/export/pdf", methods=["POST"])
@login_required
def export_pdf():
    filters = collect_filters(request.form)
    report = build_report(filters, current_user)
    buffer = report_service.export_pdf(report, filters)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="CAFirm_Report.pdf",
        mimetype="application/pdf",
    )
