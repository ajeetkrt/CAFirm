"""Enquiry service — creation, filtering, status workflow, attachments.

All business rules for enquiries live here; controllers only translate HTTP
requests into calls on this module.
"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import func, or_, text

from app.extensions import db
from app.models import (
    Category, Enquiry, EnquiryAttachment, EnquiryStatusHistory, User,
)
from app.utils.helpers import log_activity

ENQUIRY_STATUSES = (
    "New", "Pending", "Assigned", "In Progress", "Resolved", "Closed", "Rejected", "Reopened",
)

#: Allowed status transitions. Keys and values must be members of ENQUIRY_STATUSES.
STATUS_TRANSITIONS = {
    "New": {"Pending", "Assigned", "In Progress", "Resolved", "Closed", "Rejected"},
    "Pending": {"In Progress", "Resolved", "Closed", "Rejected"},
    "Assigned": {"In Progress", "Pending", "Resolved", "Closed", "Rejected"},
    "In Progress": {"Pending", "Resolved", "Closed", "Rejected"},
    "Resolved": {"Closed", "Reopened"},
    "Closed": {"Reopened"},
    "Rejected": {"Reopened"},
    "Reopened": {"In Progress", "Pending", "Resolved", "Rejected", "Closed"},
}

#: Statuses only Admin may set.
ADMIN_ONLY_STATUSES = {"Closed", "Rejected", "Reopened"}

#: Terminal states — once an enquiry lands here, employees (its creator or
#: assignee) can no longer modify it or change its status. Admin keeps full control.
TERMINAL_STATUSES = {"Resolved", "Closed", "Rejected"}

_ENQUIRY_NUMBER_SEQUENCE = "enquiry_number_seq"


class EnquiryValidationError(Exception):
    """Raised when a status transition or payload is invalid."""


class EnquiryPermissionError(Exception):
    """Raised when a user tries to perform an operation they are not allowed to."""


def ensure_enquiry_sequence(session) -> None:
    """Make sure the enquiry-number sequence exists (also needed for tests)."""
    session.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {_ENQUIRY_NUMBER_SEQUENCE} START 1"))


def generate_enquiry_number(session) -> str:
    """Generate a unique enquiry number from a PostgreSQL sequence."""
    ensure_enquiry_sequence(session)
    next_value = session.execute(text(f"SELECT nextval('{_ENQUIRY_NUMBER_SEQUENCE}')")).scalar()
    year = datetime.now(timezone.utc).year
    return f"CAF-{year}-{int(next_value):06d}"


def create_enquiry(data: dict, source: str, created_by: User | None = None,
                   ip_address: str | None = None) -> Enquiry:
    """Create an enquiry, record the initial status entry and audit log."""
    category = db.session.get(Category, data["category_id"])
    if not category or not category.is_active:
        raise EnquiryValidationError("Selected enquiry category is not available.")

    enquiry = Enquiry(
        enquiry_number=generate_enquiry_number(db.session),
        full_name=data["full_name"],
        mobile_number=data["mobile_number"],
        email=data.get("email") or None,
        state=data.get("state"),
        district=data.get("district"),
        category_id=category.category_id,
        subject=data["subject"],
        description=data["description"],
        source=source,
        created_by_user_id=created_by.user_id if created_by else None,
        assigned_to_user_id=None,
        status="New",
    )
    db.session.add(enquiry)
    db.session.flush()  # obtain enquiry_id for the history row

    db.session.add(EnquiryStatusHistory(
        enquiry_id=enquiry.enquiry_id,
        previous_status=None,
        new_status="New",
        changed_by_user_id=created_by.user_id if created_by else None,
        remarks="Enquiry created" if source == "VISITOR" else "Enquiry created internally",
    ))

    log_activity(
        "ENQUIRY_CREATED",
        f"Enquiry {enquiry.enquiry_number} created ({source})",
        user=created_by, enquiry=enquiry, ip_address=ip_address,
    )
    return enquiry


def can_view(user, enquiry: Enquiry) -> bool:
    """Authorisation check for viewing a single enquiry (prevents IDOR)."""
    if user is None:
        return False
    if user.is_admin:
        return True
    return enquiry.created_by_user_id == user.user_id or enquiry.assigned_to_user_id == user.user_id


def can_edit(user, enquiry: Enquiry) -> bool:
    """Authorisation check for editing enquiry details.

    Employees may edit only enquiries they created themselves (an enquiry
    merely assigned to them is not theirs to modify), and never once the
    enquiry reached a terminal state — only Admin can still change those.
    """
    if user is None:
        return False
    if user.is_admin:
        return True
    if enquiry.status in TERMINAL_STATUSES:
        return False
    return enquiry.created_by_user_id == user.user_id


def can_assign(user) -> bool:
    """Only Admin can (re)assign enquiries."""
    return user is not None and user.is_admin


def allowed_statuses_for(enquiry: Enquiry, user) -> set[str]:
    """Statuses `user` may set for this enquiry right now (role-aware).

    Admin shares the full workflow. Employees may only progress the status of
    enquiries **they created** (Pending, In Progress, Resolved…); enquiries
    merely assigned to them are read-only. Terminal states lock everyone but
    Admin out of further status changes.
    """
    if user is not None and user.is_admin:
        return set(STATUS_TRANSITIONS.get(enquiry.status, set()))
    if (user is None or enquiry.status in TERMINAL_STATUSES
            or enquiry.created_by_user_id != user.user_id):
        return set()
    return STATUS_TRANSITIONS.get(enquiry.status, set()) - ADMIN_ONLY_STATUSES


def status_update_map(enquiries, user) -> dict[int, bool]:
    """enquiry_id -> whether `user` may still change that enquiry's status."""
    return {e.enquiry_id: bool(allowed_statuses_for(e, user)) for e in enquiries}


def assert_status_allowed(current_status: str, new_status: str, user) -> None:
    """Validate a status transition against the workflow and the user's role."""
    if new_status not in ENQUIRY_STATUSES:
        raise EnquiryValidationError("Unknown enquiry status.")
    if current_status not in STATUS_TRANSITIONS:
        raise EnquiryValidationError("The current status does not allow any transition.")
    if new_status not in STATUS_TRANSITIONS[current_status]:
        raise EnquiryValidationError(
            f"Status cannot change from '{current_status}' to '{new_status}'."
        )
    if new_status in ADMIN_ONLY_STATUSES and not user.is_admin:
        raise EnquiryPermissionError(
            "Only Admin can mark enquiries as Closed, Rejected or Reopened."
        )


def update_status(enquiry: Enquiry, new_status: str, remarks: str, user,
                  ip_address: str | None = None) -> None:
    """Change an enquiry status, enforcing the workflow rules."""
    remarks = (remarks or "").strip()
    if new_status == "Reopened" and not remarks:
        raise EnquiryValidationError("A reason is required when reopening an enquiry.")

    if not user.is_admin and enquiry.created_by_user_id != user.user_id:
        raise EnquiryPermissionError(
            "You can only update the status of enquiries you created."
        )

    assert_status_allowed(enquiry.status, new_status, user)

    previous = enquiry.status
    db.session.add(EnquiryStatusHistory(
        enquiry_id=enquiry.enquiry_id,
        previous_status=previous,
        new_status=new_status,
        changed_by_user_id=user.user_id,
        remarks=remarks or None,
    ))
    enquiry.status = new_status

    action = "ENQUIRY_REOPENED" if new_status == "Reopened" else "STATUS_CHANGED"
    log_activity(
        action,
        f"Enquiry {enquiry.enquiry_number}: {previous} -> {new_status}",
        user=user, enquiry=enquiry, ip_address=ip_address,
    )


def add_remark(enquiry: Enquiry, remarks: str, user, ip_address: str | None = None) -> None:
    """Append a remark without changing the status."""
    remarks = (remarks or "").strip()
    if not remarks:
        raise EnquiryValidationError("Remark text is required.")
    if not can_view(user, enquiry):
        raise EnquiryPermissionError("You are not allowed to add remarks to this enquiry.")

    db.session.add(EnquiryStatusHistory(
        enquiry_id=enquiry.enquiry_id,
        previous_status=enquiry.status,
        new_status=enquiry.status,
        changed_by_user_id=user.user_id,
        remarks=remarks,
    ))
    log_activity(
        "REMARK_ADDED",
        f"Remark added on enquiry {enquiry.enquiry_number}",
        user=user, enquiry=enquiry, ip_address=ip_address,
    )


def scoped_enquiries(user):
    """Base enquiry query limited to what `user` may see."""
    query = Enquiry.query
    if user is not None and not user.is_admin:
        query = query.filter(
            or_(Enquiry.created_by_user_id == user.user_id,
                Enquiry.assigned_to_user_id == user.user_id)
        )
    return query


def apply_filters(query, filters: dict, order_by_created: bool = True):
    """Apply the shared enquiry list filters (search, status, category, dates...)."""
    search = (filters.get("q") or "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            Enquiry.enquiry_number.ilike(like),
            Enquiry.full_name.ilike(like),
            Enquiry.mobile_number.ilike(like),
            Enquiry.subject.ilike(like),
        ))

    if filters.get("status"):
        query = query.filter(Enquiry.status == filters["status"])
    if filters.get("category_id"):
        query = query.filter(Enquiry.category_id == filters["category_id"])
    if filters.get("source"):
        query = query.filter(Enquiry.source == filters["source"])
    if filters.get("assigned_to_user_id"):
        query = query.filter(Enquiry.assigned_to_user_id == filters["assigned_to_user_id"])

    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if date_from:
        query = query.filter(Enquiry.created_at >= date_from)
    if date_to:
        # Exclusive upper bound. Callers pass next-day midnight for an
        # end-of-day date (parse_date end_of_day=True), so a plain "<" keeps
        # the chosen day inclusive without leaking into the next one.
        query = query.filter(Enquiry.created_at < date_to)
    if order_by_created:
        query = query.order_by(Enquiry.created_at.desc())
    return query


def parse_date(value: str, end_of_day: bool = False):
    """Parse an HTML date input; returns None when empty/invalid."""
    from datetime import datetime as dt

    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = dt.strptime(value, "%Y-%m-%d")
        if end_of_day:
            from datetime import timedelta
            return parsed + timedelta(days=1)
        return parsed
    except ValueError:
        return None


def update_enquiry(enquiry: Enquiry, data: dict, user, ip_address: str | None = None) -> None:
    """Update permitted enquiry fields (Admin: any enquiry, employees: own enquiries)."""
    category = db.session.get(Category, data["category_id"])
    if not category:
        raise EnquiryValidationError("Selected enquiry category does not exist.")
    if not category.is_active and category.category_id != enquiry.category_id:
        raise EnquiryValidationError("Selected enquiry category is not active.")

    enquiry.full_name = data["full_name"]
    enquiry.mobile_number = data["mobile_number"]
    enquiry.email = data.get("email") or None
    enquiry.state = data.get("state")
    enquiry.district = data.get("district")
    enquiry.category_id = category.category_id
    enquiry.subject = data["subject"]
    enquiry.description = data["description"]

    log_activity(
        "ENQUIRY_UPDATED",
        f"Enquiry {enquiry.enquiry_number} details updated",
        user=user, enquiry=enquiry, ip_address=ip_address,
    )


def count_by_status(user=None) -> dict:
    """Counts of enquiries per status (all records for Admin, own scope otherwise)."""
    query = db.session.query(Enquiry.status, func.count(Enquiry.enquiry_id))
    if user is not None and not user.is_admin:
        query = query.filter(
            or_(Enquiry.created_by_user_id == user.user_id,
                Enquiry.assigned_to_user_id == user.user_id)
        )
    counts = dict(query.group_by(Enquiry.status).all())
    return counts


def save_attachment(enquiry: Enquiry, file_storage, user, ip_address: str | None = None):
    """Validate and persist an uploaded attachment."""
    if file_storage is None or not file_storage.filename:
        raise EnquiryValidationError("No file selected.")
    original_name = file_storage.filename
    if not Path(original_name).suffix or original_name.rsplit(".", 1)[-1].lower() not in \
            current_app.config["ALLOWED_EXTENSIONS"]:
        raise EnquiryValidationError("This file type is not allowed.")
    if not can_view(user, enquiry):
        raise EnquiryPermissionError("You are not allowed to attach files to this enquiry.")

    size = file_storage.seek(0, 2)
    file_storage.seek(0)
    if size > current_app.config["MAX_FILE_SIZE_MB"] * 1024 * 1024:
        raise EnquiryValidationError(
            f"File exceeds the {current_app.config['MAX_FILE_SIZE_MB']} MB limit."
        )
    if size == 0:
        raise EnquiryValidationError("The uploaded file is empty.")

    stored_name = f"{uuid.uuid4().hex}{Path(original_name).suffix.lower()}"
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    upload_folder.mkdir(parents=True, exist_ok=True)
    target_path = upload_folder / stored_name
    # Defence in depth: ensure the resolved target stays inside the upload folder.
    if not str(target_path.resolve()).startswith(str(upload_folder.resolve())):
        raise EnquiryValidationError("Invalid storage path.")

    file_storage.save(target_path)

    attachment = EnquiryAttachment(
        enquiry_id=enquiry.enquiry_id,
        original_file_name=Path(original_name).name,
        stored_file_name=stored_name,
        file_path=str(target_path),
        file_size=int(size),
        content_type=file_storage.mimetype or "application/octet-stream",
        uploaded_by_user_id=user.user_id,
    )
    db.session.add(attachment)
    log_activity(
        "ATTACHMENT_UPLOADED",
        f"Attachment '{attachment.original_file_name}' added to {enquiry.enquiry_number}",
        user=user, enquiry=enquiry, ip_address=ip_address,
    )
    return attachment


def delete_attachment(attachment: EnquiryAttachment, user) -> None:
    """Remove an attachment (Admin or the employee who uploaded it)."""
    if not user.is_admin and attachment.uploaded_by_user_id != user.user_id:
        raise EnquiryPermissionError("You are not allowed to delete this attachment.")
    try:
        Path(attachment.file_path).unlink(missing_ok=True)
    except OSError:
        pass
    enquiry = attachment.enquiry
    db.session.delete(attachment)
    log_activity(
        "ATTACHMENT_DELETED",
        f"Attachment '{attachment.original_file_name}' removed from {enquiry.enquiry_number}",
        user=user, enquiry=enquiry,
    )


# --------------------------------------------------------------------------
# Dashboard aggregates
# --------------------------------------------------------------------------

def _monthly_trend(query) -> list[tuple[str, int]]:
    """Count enquiries per month for the last 6 months."""
    six_months_ago = datetime.now(timezone.utc) - timedelta(days=183)
    month = func.to_char(func.date_trunc("month", Enquiry.created_at), "YYYY-MM").label("month")
    rows = (
        query.filter(Enquiry.created_at >= six_months_ago)
        .with_entities(month, func.count(Enquiry.enquiry_id))
        .group_by(month)
        .order_by(month)
        .all()
    )
    # Fill gaps so the chart always shows six consecutive months.
    labels, counts = [], []
    today = datetime.now(timezone.utc)
    buckets = {label: count for label, count in rows}
    for offset in range(5, -1, -1):
        month_date = today.replace(day=1) - timedelta(days=30 * offset)
        key = month_date.strftime("%Y-%m")
        labels.append(month_date.strftime("%b %Y"))
        counts.append(int(buckets.get(key, 0)))
    return labels, counts


def _by_category(query) -> tuple[list[str], list[int]]:
    rows = (
        query.join(Category, Enquiry.category_id == Category.category_id)
        .with_entities(Category.category_name, func.count(Enquiry.enquiry_id))
        .group_by(Category.category_name)
        .order_by(func.count(Enquiry.enquiry_id).desc())
        .all()
    )
    return [r[0] for r in rows], [int(r[1]) for r in rows]


def _by_source(query) -> tuple[list[str], list[int]]:
    rows = (
        query.with_entities(Enquiry.source, func.count(Enquiry.enquiry_id))
        .group_by(Enquiry.source).all()
    )
    labels = [f"{r[0].title()}" for r in rows]
    return labels, [int(r[1]) for r in rows]


def _employee_stats(query, statuses: tuple[str, ...] | None = None) -> tuple[list[str], list[int]]:
    """Counts grouped by the assigned employee (role User)."""
    sub = query.filter(Enquiry.assigned_to_user_id.isnot(None))
    if statuses is not None:
        sub = sub.filter(Enquiry.status.in_(statuses))
    rows = (
        sub.join(User, Enquiry.assigned_to_user_id == User.user_id)
        .with_entities(User.full_name, func.count(Enquiry.enquiry_id))
        .group_by(User.full_name)
        .order_by(func.count(Enquiry.enquiry_id).desc())
        .all()
    )
    return [r[0] for r in rows], [int(r[1]) for r in rows]


def dashboard_charts(user=None) -> dict:
    """Chart datasets for Admin (all data) or an employee (own scope)."""
    base = scoped_enquiries(user)
    status_rows = (
        base.with_entities(Enquiry.status, func.count(Enquiry.enquiry_id))
        .group_by(Enquiry.status).all()
    )
    trend_labels, trend_counts = _monthly_trend(base)
    assigned_labels, assigned_counts = _employee_stats(base)
    resolved_labels, resolved_counts = _employee_stats(base, statuses=("Resolved",))

    return {
        "status": {"labels": [r[0] for r in status_rows], "counts": [int(r[1]) for r in status_rows]},
        "category": _by_category(base),
        "source": _by_source(base),
        "monthly_trend": {"labels": trend_labels, "counts": trend_counts},
        "employee_assigned": {"labels": assigned_labels, "counts": assigned_counts},
        "employee_resolved": {"labels": resolved_labels, "counts": resolved_counts},
    }
