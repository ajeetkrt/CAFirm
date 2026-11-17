"""Procedure service — Python gateway to the usp_* stored procedures.

Every API endpoint funnels through :func:`call_procedure`, which executes one
named procedure and unwraps its uniform jsonb envelope:

    {"success": true,  "data": {...}}          -> returned as dict
    {"success": false, "code": ..., "error": ..} -> raises ProcedureError
"""
from sqlalchemy import text

from app.extensions import db


class ProcedureError(Exception):
    """The procedure reported a business error through its jsonb envelope."""

    HTTP_STATUS = {
        "VALIDATION": 400,
        "NOT_FOUND": 404,
        "FORBIDDEN": 403,
        "CONFLICT": 409,
    }

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self) -> int:
        return self.HTTP_STATUS.get(self.code, 500)


def call_procedure(fn_name: str, params: dict | None = None):
    """Execute ``SELECT fn_name(:named_params...)`` and return the envelope data.

    Raises ProcedureError when the procedure reports success=false.
    """
    params = params or {}
    if params:
        placeholders = ", ".join(f":{name}" for name in params)
    else:
        placeholders = ""
    statement = text(f"SELECT {fn_name}({placeholders})")
    result = db.session.execute(statement, params).scalar()
    if result is None:
        raise ProcedureError("ERROR", f"Procedure {fn_name} returned no result.")
    if not result.get("success", False):
        raise ProcedureError(result.get("code", "ERROR"), result.get("error", "Unknown error."))
    return result.get("data")


# ---------------------------------------------------------------------------
# Typed wrappers — one per API endpoint, keeping call sites self-documenting.
# ---------------------------------------------------------------------------

# Public / visitor ------------------------------------------------------------
def submit_visitor_enquiry(payload: dict, ip_address: str | None = None) -> dict:
    return call_procedure("usp_submit_visitor_enquiry", {
        "p_full_name": payload["full_name"],
        "p_mobile_number": payload["mobile_number"],
        "p_category_id": payload["category_id"],
        "p_subject": payload["subject"],
        "p_description": payload["description"],
        "p_email": payload.get("email"),
        "p_state": payload.get("state"),
        "p_district": payload.get("district"),
        "p_ip_address": ip_address,
    })


def track_status_by_reference(enquiry_number: str, ip_address: str | None = None) -> dict:
    """Status view — the Enquiry Number alone is enough (no token)."""
    return call_procedure("usp_track_status_by_reference", {
        "p_enquiry_number": enquiry_number, "p_ip_address": ip_address,
    })


def lookup_enquiries_by_mobile(mobile: str) -> dict:
    return call_procedure("usp_lookup_enquiries_by_mobile", {"p_mobile": mobile})


def get_public_stats() -> dict:
    return call_procedure("usp_get_public_stats")


def list_active_categories() -> dict:
    return call_procedure("usp_list_active_categories")


# Staff enquiries ---------------------------------------------------------------
def list_enquiries(viewer_id: int, is_admin: bool, filters: dict,
                   page: int = 1, page_size: int = 10) -> dict:
    return call_procedure("usp_list_enquiries", {
        "p_viewer_user_id": viewer_id,
        "p_is_admin": is_admin,
        "p_search": filters.get("q"),
        "p_status": filters.get("status"),
        "p_category_id": filters.get("category_id"),
        "p_source": filters.get("source"),
        "p_assigned_to_user_id": filters.get("assigned_to_user_id"),
        "p_date_from": filters.get("date_from"),
        "p_date_to": filters.get("date_to"),
        "p_page": page,
        "p_page_size": page_size,
    })


def get_enquiry(viewer_id: int, is_admin: bool, enquiry_id: int) -> dict:
    return call_procedure("usp_get_enquiry", {
        "p_viewer_user_id": viewer_id, "p_is_admin": is_admin, "p_enquiry_id": enquiry_id,
    })


def create_staff_enquiry(actor_id: int, payload: dict, ip_address: str | None = None) -> dict:
    return call_procedure("usp_create_staff_enquiry", {
        "p_actor_user_id": actor_id,
        "p_full_name": payload["full_name"],
        "p_mobile_number": payload["mobile_number"],
        "p_category_id": payload["category_id"],
        "p_subject": payload["subject"],
        "p_description": payload["description"],
        "p_email": payload.get("email"),
        "p_state": payload.get("state"),
        "p_district": payload.get("district"),
        "p_source": payload.get("source", "ADMIN"),
        "p_ip_address": ip_address,
    })


def update_enquiry(viewer_id: int, is_admin: bool, enquiry_id: int, payload: dict,
                   ip_address: str | None = None) -> dict:
    return call_procedure("usp_update_enquiry", {
        "p_viewer_user_id": viewer_id,
        "p_is_admin": is_admin,
        "p_enquiry_id": enquiry_id,
        "p_full_name": payload["full_name"],
        "p_mobile_number": payload["mobile_number"],
        "p_email": payload.get("email"),
        "p_state": payload.get("state"),
        "p_district": payload.get("district"),
        "p_category_id": payload["category_id"],
        "p_subject": payload["subject"],
        "p_description": payload["description"],
        "p_ip_address": ip_address,
    })


def assign_enquiry(admin_id: int, enquiry_id: int, assigned_to_user_id: int,
                   remarks: str | None = None, ip_address: str | None = None) -> dict:
    return call_procedure("usp_assign_enquiry", {
        "p_admin_user_id": admin_id,
        "p_enquiry_id": enquiry_id,
        "p_assigned_to_user_id": assigned_to_user_id,
        "p_remarks": remarks,
        "p_ip_address": ip_address,
    })


def update_enquiry_status(viewer_id: int, is_admin: bool, enquiry_id: int, new_status: str,
                          remarks: str | None = None, ip_address: str | None = None) -> dict:
    return call_procedure("usp_update_enquiry_status", {
        "p_viewer_user_id": viewer_id,
        "p_is_admin": is_admin,
        "p_enquiry_id": enquiry_id,
        "p_new_status": new_status,
        "p_remarks": remarks,
        "p_ip_address": ip_address,
    })


def add_enquiry_remark(viewer_id: int, is_admin: bool, enquiry_id: int, remarks: str,
                       ip_address: str | None = None) -> dict:
    return call_procedure("usp_add_enquiry_remark", {
        "p_viewer_user_id": viewer_id,
        "p_is_admin": is_admin,
        "p_enquiry_id": enquiry_id,
        "p_remarks": remarks,
        "p_ip_address": ip_address,
    })


def add_enquiry_attachment(viewer_id: int, is_admin: bool, enquiry_id: int, meta: dict,
                           ip_address: str | None = None) -> dict:
    return call_procedure("usp_add_enquiry_attachment", {
        "p_viewer_user_id": viewer_id,
        "p_is_admin": is_admin,
        "p_enquiry_id": enquiry_id,
        "p_original_file_name": meta["original_file_name"],
        "p_stored_file_name": meta["stored_file_name"],
        "p_file_path": meta["file_path"],
        "p_file_size": meta["file_size"],
        "p_content_type": meta.get("content_type", "application/octet-stream"),
        "p_ip_address": ip_address,
    })


def delete_enquiry_attachment(viewer_id: int, is_admin: bool, attachment_id: int,
                              ip_address: str | None = None) -> dict:
    return call_procedure("usp_delete_enquiry_attachment", {
        "p_viewer_user_id": viewer_id,
        "p_is_admin": is_admin,
        "p_attachment_id": attachment_id,
        "p_ip_address": ip_address,
    })


# Dashboards -------------------------------------------------------------------
def get_dashboard_kpis(viewer_id: int, is_admin: bool) -> dict:
    return call_procedure("usp_get_dashboard_kpis", {
        "p_viewer_user_id": viewer_id, "p_is_admin": is_admin,
    })


def get_dashboard_charts(viewer_id: int, is_admin: bool) -> dict:
    return call_procedure("usp_get_dashboard_charts", {
        "p_viewer_user_id": viewer_id, "p_is_admin": is_admin,
    })


# Admin ------------------------------------------------------------------------
def list_users(search: str | None = None) -> dict:
    return call_procedure("usp_list_users", {"p_search": search})


def create_user(actor_id: int, payload: dict, password_hash: str,
                ip_address: str | None = None) -> dict:
    return call_procedure("usp_create_user", {
        "p_username": payload["username"],
        "p_full_name": payload["full_name"],
        "p_email": payload["email"],
        "p_password_hash": password_hash,
        "p_role_id": payload["role_id"],
        "p_is_active": True,
        "p_actor_user_id": actor_id,
        "p_ip_address": ip_address,
    })


def toggle_user_status(actor_id: int, target_user_id: int,
                       ip_address: str | None = None) -> dict:
    return call_procedure("usp_toggle_user_status", {
        "p_actor_user_id": actor_id, "p_target_user_id": target_user_id,
        "p_ip_address": ip_address,
    })


def list_categories(search: str | None = None) -> dict:
    return call_procedure("usp_list_categories", {"p_search": search})


def create_category(actor_id: int, name: str, description: str | None,
                    ip_address: str | None = None) -> dict:
    return call_procedure("usp_create_category", {
        "p_category_name": name, "p_description": description,
        "p_actor_user_id": actor_id, "p_ip_address": ip_address,
    })


def toggle_category_status(actor_id: int, category_id: int,
                           ip_address: str | None = None) -> dict:
    return call_procedure("usp_toggle_category_status", {
        "p_actor_user_id": actor_id, "p_category_id": category_id,
        "p_ip_address": ip_address,
    })


# Reports ----------------------------------------------------------------------
REPORT_PROCEDURES = {
    "total": "usp_report_total_enquiries",
    "date-wise": "usp_report_date_wise",
    "status-wise": "usp_report_status_wise",
    "category-wise": "usp_report_category_wise",
    "employee-wise": "usp_report_employee_wise",
    "source-wise": "usp_report_source_wise",
    "pending": "usp_report_pending_enquiries",
    "resolved": "usp_report_resolved_enquiries",
}


def run_report(report_type: str, filters: dict) -> dict:
    fn_name = REPORT_PROCEDURES.get(report_type)
    if not fn_name:
        raise ProcedureError("VALIDATION", f"Unknown report type '{report_type}'.")
    return call_procedure(fn_name, {
        "p_status": filters.get("status"),
        "p_category_id": filters.get("category_id"),
        "p_date_from": filters.get("date_from"),
        "p_date_to": filters.get("date_to"),
        "p_source": filters.get("source"),
        "p_assigned_to_user_id": filters.get("assigned_to_user_id"),
    })
