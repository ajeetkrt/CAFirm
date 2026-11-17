"""Report service — builds the 8 report types and exports them to Excel/PDF."""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func

from app.extensions import db
from app.models import Category, Enquiry, User
from app.services.enquiry_service import apply_filters, parse_date, scoped_enquiries

REPORT_TYPES = {
    "total": "Total Enquiry Report",
    "datewise": "Date-wise Enquiry Report",
    "statuswise": "Status-wise Enquiry Report",
    "categorywise": "Category-wise Enquiry Report",
    "employeewise": "Employee-wise Enquiry Report",
    "sourcewise": "Source-wise Enquiry Report",
    "pending": "Pending Enquiry Report",
    "resolved": "Resolved Enquiry Report",
}

DETAILED_HEADERS = [
    "Enquiry Number", "Date", "Full Name", "Mobile", "Category", "Subject",
    "Source", "Status", "Created By", "Assigned To",
]


def collect_filters(request_form) -> dict:
    """Extract the shared report filters from a request form."""
    return {
        "date_from": parse_date(request_form.get("date_from")),
        "date_to": parse_date(request_form.get("date_to"), end_of_day=True),
        "category_id": int(request_form["category_id"])
        if (request_form.get("category_id") or "").isdigit() else None,
        "status": (request_form.get("status") or "").strip() or None,
        "assigned_to_user_id": int(request_form["assigned_to_user_id"])
        if (request_form.get("assigned_to_user_id") or "").isdigit() else None,
        "source": (request_form.get("source") or "").strip() or None,
        "report_type": (request_form.get("report_type") or "total").strip(),
    }


def _base_rows(filters: dict, user):
    """Detailed enquiry rows honouring scope + filters; returns (headers, rows)."""
    query = scoped_enquiries(user)
    query = apply_filters(query, filters)
    headers = list(DETAILED_HEADERS)
    rows = []
    for e in query.all():
        rows.append([
            e.enquiry_number,
            e.created_at.strftime("%Y-%m-%d") if e.created_at else "",
            e.full_name,
            e.mobile_number,
            e.category.category_name if e.category else "",
            e.subject,
            e.source.title(),
            e.status,
            e.created_by.full_name if e.created_by else "Visitor",
            e.assigned_to.full_name if e.assigned_to else "Unassigned",
        ])
    return headers, rows


def _count_rows(filters: dict, user, group_column) -> tuple[list[str], list[list]]:
    """Grouped-count rows for a chosen grouping column."""
    query = scoped_enquiries(user).with_entities(group_column, func.count(Enquiry.enquiry_id))
    # The grouping column itself must not be constrained by the same filter.
    query = apply_filters(query, {k: v for k, v in filters.items() if k != "status"},
                          order_by_created=False)
    counts = query.group_by(group_column).order_by(func.count(Enquiry.enquiry_id).desc()).all()
    headers = [DETAILED_HEADERS[0].replace("Enquiry Number", "Group"), "Total Enquiries"]
    if group_column is Enquiry.created_at:
        headers = ["Date", "Total Enquiries"]
    elif group_column is Enquiry.status:
        headers = ["Status", "Total Enquiries"]
    elif group_column is Enquiry.source:
        headers = ["Source", "Total Enquiries"]
    elif group_column is Enquiry.assigned_to_user_id:
        headers = ["Assigned Employee", "Total Enquiries"]
    elif group_column is Enquiry.category_id:
        headers = ["Category", "Total Enquiries"]
    rows = [[_group_label(group_column, value), count] for value, count in counts]
    return headers, rows


def _group_label(group_column, value) -> str:
    if value is None:
        return "Unassigned / Visitor" if group_column is Enquiry.assigned_to_user_id else "N/A"
    if group_column is Enquiry.created_at:
        return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value)[:10]
    if group_column is Enquiry.category_id:
        category = db.session.get(Category, value)
        return category.category_name if category else str(value)
    if group_column is Enquiry.assigned_to_user_id:
        user = db.session.get(User, value)
        return user.full_name if user else str(value)
    return str(value)


def _employee_join_rows(filters: dict, user, resolved_only: bool = False):
    """Employee-wise counts (created + assigned), optionally limited to resolved."""
    query = scoped_enquiries(user)
    if resolved_only:
        query = query.filter(Enquiry.status == "Resolved")
    assigned_counts = dict(
        query.with_entities(Enquiry.assigned_to_user_id, func.count(Enquiry.enquiry_id))
        .filter(Enquiry.assigned_to_user_id.isnot(None))
        .group_by(Enquiry.assigned_to_user_id).all()
    )
    created_counts = dict(
        query.with_entities(Enquiry.created_by_user_id, func.count(Enquiry.enquiry_id))
        .filter(Enquiry.created_by_user_id.isnot(None))
        .group_by(Enquiry.created_by_user_id).all()
    )
    headers = ["Employee", "Created", "Assigned", "Total"]
    rows = []
    user_ids = sorted(set(assigned_counts) | set(created_counts))
    for uid in user_ids:
        name = _group_label(Enquiry.assigned_to_user_id, uid)
        created = created_counts.get(uid, 0)
        assigned = assigned_counts.get(uid, 0)
        rows.append([name, created, assigned, created + assigned])
    rows.sort(key=lambda r: r[3], reverse=True)
    return headers, rows


def build_report(filters: dict, user) -> dict:
    """Build the selected report. Returns title/headers/rows ready for rendering/export."""
    report_type = filters.get("report_type", "total")
    title = REPORT_TYPES.get(report_type, REPORT_TYPES["total"])
    scope_label = "All enquiries" if (user is None or user.is_admin) else "My accessible enquiries"

    if report_type == "total":
        headers, rows = _base_rows(filters, user)
    elif report_type == "datewise":
        headers, rows = _count_rows(filters, user, Enquiry.created_at)
    elif report_type == "statuswise":
        headers, rows = _count_rows(filters, user, Enquiry.status)
    elif report_type == "sourcewise":
        headers, rows = _count_rows(filters, user, Enquiry.source)
    elif report_type == "categorywise":
        headers, rows = _count_rows(filters, user, Enquiry.category_id)
    elif report_type == "employeewise":
        headers, rows = _employee_join_rows(filters, user, resolved_only=False)
    elif report_type == "pending":
        filters = {**filters, "status": None}
        query = scoped_enquiries(user).filter(Enquiry.status.in_(["New", "Pending", "Assigned", "In Progress", "Reopened"]))
        query = apply_filters(query, filters)
        rows = [[
            e.enquiry_number, e.created_at.strftime("%Y-%m-%d"), e.full_name, e.mobile_number,
            e.category.category_name if e.category else "", e.subject, e.status,
            e.assigned_to.full_name if e.assigned_to else "Unassigned",
        ] for e in query.order_by(Enquiry.created_at).all()]
        headers = ["Enquiry Number", "Date", "Full Name", "Mobile", "Category", "Subject", "Status", "Assigned To"]
    elif report_type == "resolved":
        filters = {**filters, "status": None}
        query = scoped_enquiries(user).filter(Enquiry.status.in_(["Resolved"]))
        query = apply_filters(query, filters)
        rows = [[
            e.enquiry_number, e.created_at.strftime("%Y-%m-%d"), e.full_name, e.mobile_number,
            e.category.category_name if e.category else "", e.subject, e.status,
            e.assigned_to.full_name if e.assigned_to else "Unassigned",
        ] for e in query.order_by(Enquiry.created_at).all()]
        headers = ["Enquiry Number", "Date", "Full Name", "Mobile", "Category", "Subject", "Status", "Assigned To"]
    else:
        headers, rows = _base_rows(filters, user)

    return {
        "title": title,
        "headers": headers,
        "rows": rows,
        "scope": scope_label,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "generated_by": user.full_name if user is not None else "System",
    }


def _filter_description(filters: dict) -> str:
    parts = []
    if filters.get("date_from"):
        parts.append(f"From: {filters['date_from'].strftime('%Y-%m-%d')}")
    if filters.get("date_to"):
        parts.append(f"To: {(filters['date_to'] - __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d')}")
    if filters.get("status"):
        parts.append(f"Status: {filters['status']}")
    if filters.get("category_id"):
        category = db.session.get(Category, filters["category_id"])
        if category:
            parts.append(f"Category: {category.category_name}")
    if filters.get("source"):
        parts.append(f"Source: {filters['source']}")
    return " | ".join(parts) if parts else "No filters applied"


def export_excel(report: dict, filters: dict) -> io.BytesIO:
    """Render a report dict into an .xlsx workbook and return the bytes."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Summary"
    for row in (
        ("Report", report["title"]),
        ("Scope", report["scope"]),
        ("Generated At", report["generated_at"]),
        ("Generated By", report["generated_by"]),
        ("Filters", _filter_description(filters)),
        ("Total Records", len(report["rows"])),
    ):
        summary_sheet.append(row)
    summary_sheet["A1"].font = Font(bold=True)

    data_sheet = workbook.create_sheet("Report")
    header_fill = PatternFill("solid", fgColor="2E7D32")
    header_font = Font(bold=True, color="FFFFFF")

    data_sheet.append(report["headers"])
    for cell in data_sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")

    for record in report["rows"]:
        data_sheet.append(list(record))

    for index, header in enumerate(report["headers"], start=1):
        width = max(
            [len(str(header))] + [len(str(row[index - 1])) for row in report["rows"]] or [10]
        )
        data_sheet.column_dimensions[get_column_letter(index)].width = min(max(width + 2, 12), 60)
        data_sheet.cell(row=1, column=index).alignment = Alignment(vertical="center")

    data_sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def export_pdf(report: dict, filters: dict) -> io.BytesIO:
    """Render a report dict into a PDF document and return the bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=1 * cm, rightMargin=1 * cm, topMargin=1.2 * cm, bottomMargin=1.2 * cm,
        title=report["title"],
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"{report['title']}", styles["Title"]),
        Paragraph(
            f"CAFirm — {report['scope']} | Generated: {report['generated_at']} "
            f"by {report['generated_by']} | {_filter_description(filters)}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]

    data = [report["headers"]] + [[str(cell) for cell in row] for row in report["rows"]]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2e7d32")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f8f1")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Total records: {len(report['rows'])}", styles["Normal"]))
    doc.build(story)
    buffer.seek(0)
    return buffer
