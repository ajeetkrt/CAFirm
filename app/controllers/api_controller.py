"""REST API controller (v1) — every endpoint delegates to one usp_* procedure.

URL prefix: /api/v1. JSON envelope:
    {"success": true,  "data": {...}}                       (HTTP 2xx)
    {"success": false, "code": "...", "error": "message"}   (HTTP 4xx/5xx)

Error codes -> HTTP status: VALIDATION 400, NOT_FOUND 404, FORBIDDEN 403,
CONFLICT 409, ERROR 500. CSRF is exempt for this blueprint (bearer-less JSON
API, SameSite=Lax session cookie mitigates cross-site form posts).
"""
from datetime import date, datetime as dt
from pathlib import Path
import uuid

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.services import procedure_service as proc
from app.services.procedure_service import ProcedureError
from app.utils.helpers import get_client_ip, rate_limiter
from app.utils.validators import is_allowed_file, validate_enquiry_payload

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

VALID_ENQUIRY_STATUSES = ("New", "Pending", "Assigned", "In Progress",
                          "Resolved", "Closed", "Rejected", "Reopened")
VALID_SOURCES = ("ADMIN", "USER", "VISITOR")
REPORT_TYPES = ("total", "date-wise", "status-wise", "category-wise",
                "employee-wise", "source-wise", "pending", "resolved")

ENDPOINT_PROCEDURE_MAP = [
    ("POST   /api/v1/public/enquiries", "usp_submit_visitor_enquiry"),
    ("GET    /api/v1/public/categories", "usp_list_active_categories"),
    ("GET    /api/v1/public/statistics", "usp_get_public_stats"),
    ("POST   /api/v1/public/tracking/by-reference", "usp_track_status_by_reference"),
    ("POST   /api/v1/public/tracking/by-mobile", "usp_lookup_enquiries_by_mobile"),
    ("GET    /api/v1/staff/enquiries", "usp_list_enquiries"),
    ("POST   /api/v1/staff/enquiries", "usp_create_staff_enquiry"),
    ("GET    /api/v1/staff/enquiries/<id>", "usp_get_enquiry"),
    ("PUT    /api/v1/staff/enquiries/<id>", "usp_update_enquiry"),
    ("POST   /api/v1/staff/enquiries/<id>/assignment", "usp_assign_enquiry"),
    ("POST   /api/v1/staff/enquiries/<id>/status", "usp_update_enquiry_status"),
    ("POST   /api/v1/staff/enquiries/<id>/remarks", "usp_add_enquiry_remark"),
    ("POST   /api/v1/staff/enquiries/<id>/attachments", "usp_add_enquiry_attachment"),
    ("DELETE /api/v1/staff/enquiries/attachments/<id>", "usp_delete_enquiry_attachment"),
    ("GET    /api/v1/staff/dashboard/kpis", "usp_get_dashboard_kpis"),
    ("GET    /api/v1/staff/dashboard/charts", "usp_get_dashboard_charts"),
    ("GET    /api/v1/admin/users", "usp_list_users"),
    ("POST   /api/v1/admin/users", "usp_create_user"),
    ("PATCH  /api/v1/admin/users/<id>/status", "usp_toggle_user_status"),
    ("GET    /api/v1/admin/categories", "usp_list_categories"),
    ("POST   /api/v1/admin/categories", "usp_create_category"),
    ("PATCH  /api/v1/admin/categories/<id>/status", "usp_toggle_category_status"),
    ("POST   /api/v1/reports/total", "usp_report_total_enquiries"),
    ("POST   /api/v1/reports/date-wise", "usp_report_date_wise"),
    ("POST   /api/v1/reports/status-wise", "usp_report_status_wise"),
    ("POST   /api/v1/reports/category-wise", "usp_report_category_wise"),
    ("POST   /api/v1/reports/employee-wise", "usp_report_employee_wise"),
    ("POST   /api/v1/reports/source-wise", "usp_report_source_wise"),
    ("POST   /api/v1/reports/pending", "usp_report_pending_enquiries"),
    ("POST   /api/v1/reports/resolved", "usp_report_resolved_enquiries"),
]


# ---------------------------------------------------------------------------
# Envelope helpers + authentication
# ---------------------------------------------------------------------------
def api_success(data=None, status: int = 200):
    return jsonify({"success": True, "data": data}), status


def api_error(code: str, message: str, status: int):
    return jsonify({"success": False, "code": code, "error": message}), status


@api_bp.errorhandler(ProcedureError)
def handle_procedure_error(exc: ProcedureError):
    db.session.rollback()
    return api_error(exc.code, exc.message, exc.status)


@api_bp.errorhandler(SQLAlchemyError)
def handle_db_error(_exc):
    db.session.rollback()
    return api_error("ERROR", "A database error occurred. Please try again.", 500)


@api_bp.errorhandler(404)
def handle_not_found(_error):
    return api_error("NOT_FOUND", "The requested API endpoint does not exist.", 404)


def api_login_required():
    """Return an error response when the caller is not an authenticated user."""
    if not current_user.is_authenticated:
        return api_error("FORBIDDEN", "Authentication required. Please sign in.", 401)
    return None


def api_admin_required():
    """Return an error response when the caller is not Admin."""
    guard = api_login_required()
    if guard is not None:
        return guard
    if not current_user.is_admin:
        return api_error("FORBIDDEN", "Administrator privileges required.", 403)
    return None


def json_body() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict()


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return dt.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _viewer_scope():
    return current_user.user_id, current_user.is_admin


# ---------------------------------------------------------------------------
# API index
# ---------------------------------------------------------------------------
@api_bp.route("/")
def index():
    """Discoverable index mapping every endpoint to its stored procedure."""
    return api_success({
        "api_version": "v1",
        "procedures": [
            {"endpoint": endpoint, "procedure": procedure}
            for endpoint, procedure in ENDPOINT_PROCEDURE_MAP
        ],
    })


# ---------------------------------------------------------------------------
# Public / visitor endpoints (no authentication)
# ---------------------------------------------------------------------------
@api_bp.route("/public/enquiries", methods=["POST"])
def submit_enquiry():
    """Submit a visitor enquiry — returns the Enquiry Number for tracking."""
    ip_address = get_client_ip()
    rate_key = f"public_submit:{ip_address}"
    limit = current_app.config["PUBLIC_SUBMIT_RATE_LIMIT"]
    window = current_app.config["PUBLIC_SUBMIT_RATE_WINDOW"]
    if rate_limiter.is_blocked(rate_key, limit, window):
        return api_error("CONFLICT", "Too many submissions. Please try again later.", 429)

    # Honeypot: real users never fill the hidden "website" field.
    honeypot = (request.form.get("website") or (json_body() or {}).get("website") or "").strip()
    if honeypot:
        return api_error("VALIDATION", "Submission rejected.", 400)

    data, errors = validate_enquiry_payload(request.form if request.form else json_body())
    if errors:
        return api_error("VALIDATION", "; ".join(errors), 400)

    result = proc.submit_visitor_enquiry(data, ip_address=ip_address)
    rate_limiter.record(rate_key, window)
    db.session.commit()
    return api_success(result, status=201)


@api_bp.route("/public/categories", methods=["GET"])
def public_categories():
    result = proc.list_active_categories()
    db.session.commit()
    return api_success(result)


@api_bp.route("/public/statistics", methods=["GET"])
def public_statistics():
    result = proc.get_public_stats()
    db.session.commit()
    return api_success(result)


@api_bp.route("/public/tracking/by-reference", methods=["POST"])
def tracking_by_reference():
    """Status by Enquiry Number alone — no token required."""
    ip_address = get_client_ip()
    rate_key = f"track:{ip_address}"
    limit = current_app.config["TRACK_RATE_LIMIT"]
    window = current_app.config["TRACK_RATE_WINDOW"]
    if rate_limiter.is_blocked(rate_key, limit, window):
        return api_error("CONFLICT", "Too many tracking attempts. Please try again later.", 429)
    rate_limiter.record(rate_key, window)

    body = json_body()
    result = proc.track_status_by_reference(
        (body.get("enquiry_number") or "").strip(),
        ip_address=ip_address,
    )
    db.session.commit()
    return api_success(result)


@api_bp.route("/public/tracking/by-mobile", methods=["POST"])
def tracking_by_mobile():
    ip_address = get_client_ip()
    rate_key = f"track:{ip_address}"
    limit = current_app.config["TRACK_RATE_LIMIT"]
    window = current_app.config["TRACK_RATE_WINDOW"]
    if rate_limiter.is_blocked(rate_key, limit, window):
        return api_error("CONFLICT", "Too many tracking attempts. Please try again later.", 429)
    rate_limiter.record(rate_key, window)

    body = json_body()
    result = proc.lookup_enquiries_by_mobile((body.get("mobile") or "").strip())
    db.session.commit()
    return api_success(result)


# ---------------------------------------------------------------------------
# Staff enquiry endpoints (authentication required)
# ---------------------------------------------------------------------------
@api_bp.route("/staff/enquiries", methods=["GET"])
def staff_list_enquiries():
    guard = api_login_required()
    if guard is not None:
        return guard

    viewer_id, is_admin = _viewer_scope()
    filters = {
        "q": request.args.get("q", type=str),
        "status": request.args.get("status", type=str),
        "category_id": request.args.get("category_id", type=int),
        "source": request.args.get("source", type=str),
        "assigned_to_user_id": request.args.get("assigned_to_user_id", type=int),
        "date_from": _parse_date(request.args.get("date_from")),
        "date_to": _parse_date(request.args.get("date_to")),
    }
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    page_size = request.args.get("page_size", 10, type=int) or 10
    result = proc.list_enquiries(viewer_id, is_admin, filters,
                                 page=page, page_size=page_size)
    db.session.commit()
    return api_success(result)


@api_bp.route("/staff/enquiries", methods=["POST"])
def staff_create_enquiry():
    guard = api_login_required()
    if guard is not None:
        return guard

    data, errors = validate_enquiry_payload(request.get_json(silent=True) or request.form)
    if errors:
        return api_error("VALIDATION", "; ".join(errors), 400)
    source = (json_body().get("source") or "ADMIN").upper()
    if source not in ("ADMIN", "USER"):
        return api_error("VALIDATION", "Source must be ADMIN or USER.", 400)
    data["source"] = source

    result = proc.create_staff_enquiry(current_user.user_id, data,
                                       ip_address=get_client_ip())
    db.session.commit()
    return api_success(result, status=201)


@api_bp.route("/staff/enquiries/<int:enquiry_id>", methods=["GET"])
def staff_get_enquiry(enquiry_id: int):
    guard = api_login_required()
    if guard is not None:
        return guard

    viewer_id, is_admin = _viewer_scope()
    result = proc.get_enquiry(viewer_id, is_admin, enquiry_id)
    db.session.commit()
    return api_success(result)


@api_bp.route("/staff/enquiries/<int:enquiry_id>", methods=["PUT"])
def staff_update_enquiry(enquiry_id: int):
    guard = api_login_required()
    if guard is not None:
        return guard

    data, errors = validate_enquiry_payload(request.get_json(silent=True) or request.form)
    if errors:
        return api_error("VALIDATION", "; ".join(errors), 400)

    viewer_id, is_admin = _viewer_scope()
    result = proc.update_enquiry(viewer_id, is_admin, enquiry_id, data,
                                 ip_address=get_client_ip())
    db.session.commit()
    return api_success(result)


@api_bp.route("/staff/enquiries/<int:enquiry_id>/assignment", methods=["POST"])
def staff_assign_enquiry(enquiry_id: int):
    guard = api_admin_required()
    if guard is not None:
        return guard

    body = json_body()
    target = body.get("assigned_to_user_id")
    if not target or not str(target).isdigit():
        return api_error("VALIDATION", "Please select an employee to assign.", 400)

    result = proc.assign_enquiry(
        current_user.user_id, enquiry_id, int(target),
        remarks=(body.get("remarks") or "").strip() or None,
        ip_address=get_client_ip(),
    )
    db.session.commit()
    return api_success(result)


@api_bp.route("/staff/enquiries/<int:enquiry_id>/status", methods=["POST"])
def staff_update_enquiry_status(enquiry_id: int):
    guard = api_login_required()
    if guard is not None:
        return guard

    body = json_body()
    new_status = (body.get("status") or "").strip()
    if new_status not in VALID_ENQUIRY_STATUSES:
        return api_error("VALIDATION", "Unknown enquiry status.", 400)

    viewer_id, is_admin = _viewer_scope()
    result = proc.update_enquiry_status(
        viewer_id, is_admin, enquiry_id, new_status,
        remarks=(body.get("remarks") or "").strip() or None,
        ip_address=get_client_ip(),
    )
    db.session.commit()
    return api_success(result)


@api_bp.route("/staff/enquiries/<int:enquiry_id>/remarks", methods=["POST"])
def staff_add_remark(enquiry_id: int):
    guard = api_login_required()
    if guard is not None:
        return guard

    remarks = (json_body().get("remarks") or "").strip()
    viewer_id, is_admin = _viewer_scope()
    result = proc.add_enquiry_remark(viewer_id, is_admin, enquiry_id, remarks,
                                     ip_address=get_client_ip())
    db.session.commit()
    return api_success(result)


def _store_uploaded_file(file_storage) -> dict:
    """Validate and save an uploaded file; returns metadata for the procedure."""
    if file_storage is None or not file_storage.filename:
        raise ProcedureError("VALIDATION", "No file selected.")
    original_name = file_storage.filename
    if not is_allowed_file(original_name, current_app.config["ALLOWED_EXTENSIONS"]):
        raise ProcedureError("VALIDATION", "This file type is not allowed.")

    size = file_storage.seek(0, 2)
    file_storage.seek(0)
    if size > current_app.config["MAX_FILE_SIZE_MB"] * 1024 * 1024:
        raise ProcedureError(
            "VALIDATION", f"File exceeds the {current_app.config['MAX_FILE_SIZE_MB']} MB limit.")
    if size == 0:
        raise ProcedureError("VALIDATION", "The uploaded file is empty.")

    stored_name = f"{uuid.uuid4().hex}{Path(original_name).suffix.lower()}"
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    upload_folder.mkdir(parents=True, exist_ok=True)
    target_path = upload_folder / stored_name
    if not str(target_path.resolve()).startswith(str(upload_folder.resolve())):
        raise ProcedureError("VALIDATION", "Invalid storage path.")

    file_storage.save(target_path)
    return {
        "original_file_name": Path(original_name).name,
        "stored_file_name": stored_name,
        "file_path": str(target_path),
        "file_size": int(size),
        "content_type": file_storage.mimetype or "application/octet-stream",
    }


@api_bp.route("/staff/enquiries/<int:enquiry_id>/attachments", methods=["POST"])
def staff_add_attachment(enquiry_id: int):
    guard = api_login_required()
    if guard is not None:
        return guard

    meta = _store_uploaded_file(request.files.get("file"))
    try:
        viewer_id, is_admin = _viewer_scope()
        result = proc.add_enquiry_attachment(viewer_id, is_admin, enquiry_id, meta,
                                             ip_address=get_client_ip())
    except Exception:
        try:
            Path(meta["file_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    db.session.commit()
    return api_success(result, status=201)


@api_bp.route("/staff/enquiries/attachments/<int:attachment_id>", methods=["DELETE"])
def staff_delete_attachment(attachment_id: int):
    guard = api_login_required()
    if guard is not None:
        return guard

    viewer_id, is_admin = _viewer_scope()
    result = proc.delete_enquiry_attachment(viewer_id, is_admin, attachment_id,
                                            ip_address=get_client_ip())
    db.session.commit()
    try:
        Path(result["file_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return api_success(result)


# ---------------------------------------------------------------------------
# Dashboard endpoints (authentication required; employees see their own scope)
# ---------------------------------------------------------------------------
@api_bp.route("/staff/dashboard/kpis", methods=["GET"])
def dashboard_kpis():
    guard = api_login_required()
    if guard is not None:
        return guard

    viewer_id, is_admin = _viewer_scope()
    result = proc.get_dashboard_kpis(viewer_id, is_admin)
    db.session.commit()
    return api_success(result)


@api_bp.route("/staff/dashboard/charts", methods=["GET"])
def dashboard_charts():
    guard = api_login_required()
    if guard is not None:
        return guard

    viewer_id, is_admin = _viewer_scope()
    result = proc.get_dashboard_charts(viewer_id, is_admin)
    db.session.commit()
    return api_success(result)


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------
@api_bp.route("/admin/users", methods=["GET"])
def admin_list_users():
    guard = api_admin_required()
    if guard is not None:
        return guard

    result = proc.list_users(search=(request.args.get("q") or "").strip() or None)
    db.session.commit()
    return api_success(result)


@api_bp.route("/admin/users", methods=["POST"])
def admin_create_user():
    guard = api_admin_required()
    if guard is not None:
        return guard

    from werkzeug.security import generate_password_hash
    from app.utils.validators import validate_new_user_payload

    body = request.get_json(silent=True) or request.form
    data, errors = validate_new_user_payload(body)
    if errors:
        return api_error("VALIDATION", "; ".join(errors), 400)

    result = proc.create_user(current_user.user_id, data,
                              password_hash=generate_password_hash(data["password"]),
                              ip_address=get_client_ip())
    db.session.commit()
    return api_success(result, status=201)


@api_bp.route("/admin/users/<int:user_id>/status", methods=["PATCH"])
def admin_toggle_user(user_id: int):
    guard = api_admin_required()
    if guard is not None:
        return guard

    result = proc.toggle_user_status(current_user.user_id, user_id,
                                     ip_address=get_client_ip())
    db.session.commit()
    return api_success(result)


@api_bp.route("/admin/categories", methods=["GET"])
def admin_list_categories():
    guard = api_admin_required()
    if guard is not None:
        return guard

    result = proc.list_categories(search=(request.args.get("q") or "").strip() or None)
    db.session.commit()
    return api_success(result)


@api_bp.route("/admin/categories", methods=["POST"])
def admin_create_category():
    guard = api_admin_required()
    if guard is not None:
        return guard

    body = request.get_json(silent=True) or request.form
    name = (body.get("category_name") or "").strip()
    description = (body.get("description") or "").strip() or None
    result = proc.create_category(current_user.user_id, name, description,
                                  ip_address=get_client_ip())
    db.session.commit()
    return api_success(result, status=201)


@api_bp.route("/admin/categories/<int:category_id>/status", methods=["PATCH"])
def admin_toggle_category(category_id: int):
    guard = api_admin_required()
    if guard is not None:
        return guard

    result = proc.toggle_category_status(current_user.user_id, category_id,
                                         ip_address=get_client_ip())
    db.session.commit()
    return api_success(result)


# ---------------------------------------------------------------------------
# Report endpoints — one route per report type
# ---------------------------------------------------------------------------
@api_bp.route("/reports/<report_type>", methods=["POST"])
def run_report(report_type: str):
    guard = api_login_required()
    if guard is not None:
        return guard

    if report_type not in REPORT_TYPES:
        return api_error("NOT_FOUND", f"Unknown report type '{report_type}'.", 404)

    body = request.get_json(silent=True) or request.form
    filters = {
        "status": (body.get("status") or "").strip() or None,
        "category_id": int(body["category_id"])
        if str(body.get("category_id") or "").isdigit() else None,
        "date_from": _parse_date(body.get("date_from")),
        "date_to": _parse_date(body.get("date_to")),
        "source": (body.get("source") or "").strip() or None,
        "assigned_to_user_id": int(body["assigned_to_user_id"])
        if str(body.get("assigned_to_user_id") or "").isdigit() else None,
    }
    result = proc.run_report(report_type, filters)
    result["generated_at"] = datetime_now_string()
    result["generated_by"] = current_user.full_name
    result["scope"] = "All enquiries" if current_user.is_admin else "My accessible enquiries"
    db.session.commit()
    return api_success(result)


def datetime_now_string() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
