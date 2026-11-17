"""Admin controller — dashboard, user management, categories, activity logs."""
from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import desc, or_

from app.extensions import db
from app.models import Category, Enquiry, Role, User
from app.services import procedure_service
from app.utils.decorators import admin_required
from app.utils.helpers import STATUS_BADGE_CLASSES, log_activity
from app.utils.validators import validate_category_payload, validate_new_user_payload

admin_bp = Blueprint("admin", __name__)

PENDING_STATUSES = ("New", "Pending", "Assigned", "In Progress", "Reopened")

# Manage Users shows five accounts per page; the pager appears only above five.
USERS_PER_PAGE = 5
# Categories follow the same five-per-page pattern for a consistent workspace.
CATEGORIES_PER_PAGE = 5

# Presentation metadata for the five dashboard KPI tiles (icons + status tint).
# The dashboard shows exactly five tiles in one line; the eight raw status KPIs
# are folded into these groups so every enquiry is still accounted for.
KPI_TILE_ICONS = {
    "Total Enquiries": "bi-inboxes",
    "Open Enquiries": "bi-hourglass-split",
    "Completed Enquiries": "bi-check2-circle",
    "Assigned Enquiries": "bi-person-check",
    "Rejected Enquiries": "bi-x-octagon",
}


def _decorate_kpis(raw_kpis, assigned_breakdown=None) -> list[dict]:
    """Fold the raw status KPIs into five one-line dashboard tiles.

    Open = New + Pending + Assigned + In Progress (+ Reopened if present) and
    Completed = Resolved + Closed. Their breakdowns — and the per-employee
    Assigned workload — are delivered as colour coded mini chips (status dot
    + label + count) so every tile foot reads as a clean status legend.
    """
    raw = {k["label"]: int(k["value"]) for k in raw_kpis}
    total = raw.get("Total Enquiries", 0)
    resolved = raw.get("Resolved Enquiries", 0)
    closed = raw.get("Closed Enquiries", 0)

    def tile(label: str, value: int, status: str | None, note: str,
             chips: list[dict] | None = None) -> dict:
        return {
            "label": label,
            "value": value,
            "status": status,
            "icon": KPI_TILE_ICONS.get(label, "bi-clipboard-data"),
            "percent": round(value * 100 / total) if total else 0,
            "note": note,       # plain summary — tooltip + no-chip fallback
            "chips": chips,
        }

    active = ("New", "Pending", "Assigned", "In Progress", "Reopened")
    open_chips = [
        {"label": name, "count": raw.get(f"{name} Enquiries", 0),
         "class": STATUS_BADGE_CLASSES[name].replace("status-", "c-")}
        for name in active if raw.get(f"{name} Enquiries", 0)
    ]
    open_count = sum(raw.get(f"{name} Enquiries", 0) for name in active)
    open_note = (" · ".join(f"{c['label']} {c['count']}" for c in open_chips)
                 or "No open enquiries")

    completed_chips = [
        {"label": label, "count": count, "class": css}
        for label, count, css in
        (("Resolved", resolved, "c-resolved"), ("Closed", closed, "c-closed"))
        if count
    ]
    completed_note = (" · ".join(f"{c['label']} {c['count']}" for c in completed_chips)
                      or "Nothing completed yet")

    assigned_labels = (assigned_breakdown or {}).get("labels") or []
    assigned_counts = (assigned_breakdown or {}).get("counts") or []
    # Workload truth: every enquiry currently owned by an employee — rendered
    # as the same chip legend as the Open/Completed tiles (Assigned purple dot
    # + employee name + count). Emails stay off the tile.
    assigned_total = sum(int(c) for c in assigned_counts)
    assigned_note = (" · ".join(f"{n} {int(c)}" for n, c in
                                zip(assigned_labels, assigned_counts))
                     or "No assignments yet")
    assigned_chips = None
    if assigned_labels:
        assigned_chips = [
            {"label": name, "count": int(count), "class": "c-assigned"}
            for name, count in zip(assigned_labels[:3], assigned_counts[:3])
        ]
        if len(assigned_labels) > 3:
            assigned_chips.append({"label": f"+{len(assigned_labels) - 3} more",
                                   "count": "", "class": "c-assigned"})

    return [
        tile("Total Enquiries", total, None, "All enquiries received"),
        tile("Open Enquiries", open_count, "Pending", open_note,
             chips=open_chips or None),
        tile("Completed Enquiries", resolved + closed, "Closed",
             completed_note, chips=completed_chips or None),
        tile("Assigned Enquiries", assigned_total, "Assigned",
             assigned_note, chips=assigned_chips),
        tile("Rejected Enquiries", raw.get("Rejected Enquiries", 0),
             "Rejected", "of all enquiries"),
    ]


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    charts = procedure_service.get_dashboard_charts(None, True)
    raw_kpis = procedure_service.get_dashboard_kpis(None, True)["kpis"]
    kpis = _decorate_kpis(raw_kpis, charts.get("employee_assigned"))
    db.session.commit()

    raw_counts = {k["label"]: int(k["value"]) for k in raw_kpis}
    total = raw_counts.get("Total Enquiries", 0)
    resolved = raw_counts.get("Resolved Enquiries", 0)
    active_count = Enquiry.query.filter(Enquiry.status.in_(PENDING_STATUSES)).count()
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_count = Enquiry.query.filter(Enquiry.created_at >= month_start).count()

    now = datetime.now()
    greeting = ("Good morning" if now.hour < 12
                else "Good afternoon" if now.hour < 17 else "Good evening")

    # Widget browsers: 4 rows per page, up to 4 pages (4×4 = 16) — paged
    # client-side so the widgets keep one stable height.
    recent = Enquiry.query.order_by(Enquiry.created_at.desc()).limit(16).all()
    pending = (
        Enquiry.query.filter(Enquiry.status.in_(PENDING_STATUSES))
        .order_by(Enquiry.created_at.asc()).limit(16).all()
    )

    return render_template(
        "admin/dashboard.html",
        kpis=kpis,
        charts=charts,
        total_enquiries=total,
        active_count=active_count,
        month_count=month_count,
        resolution_rate=round(resolved * 100 / total) if total else 0,
        greeting=greeting,
        today_long=now.strftime("%A, %d %B %Y"),
        recent=recent,
        pending=pending,
        now_utc=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------
def _users_page_context() -> dict:
    """Shared context for the Manage Users page (list, chips, 5-per-page slice)."""
    users_list = User.query.order_by(User.username).all()
    roles = Role.query.order_by(Role.role_id).all()
    counts = {
        "total": len(users_list),
        "active": sum(1 for u in users_list if u.is_active),
        "admins": sum(1 for u in users_list if u.role and u.role.role_name == "Admin"),
        "employees": sum(1 for u in users_list if u.role and u.role.role_name != "Admin"),
    }

    # Five accounts per page — the pager only appears above five users.
    total = len(users_list)
    pages = (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    page = min(max(1, request.args.get("page", 1, type=int) or 1), max(pages, 1))
    start = (page - 1) * USERS_PER_PAGE
    page_users = users_list[start:start + USERS_PER_PAGE]
    pagination = None
    if total:
        pagination = {
            "page": page,
            "pages": pages,
            "total": total,
            "start": start + 1,
            "end": start + len(page_users),
        }

    return {
        "users": page_users,
        "roles": roles,
        "counts": counts,
        "pagination": pagination,
    }


@admin_bp.route("/users")
@admin_required
def users():
    return render_template("admin/users.html", **_users_page_context())


@admin_bp.route("/users/create", methods=["POST"])
@admin_required
def create_user():
    data, errors = validate_new_user_payload(request.form)

    if not errors and User.query.filter(
        or_(User.username == data["username"],
            User.email == data["email"] if data["email"] else False)
    ).first():
        errors.append("Username or email already exists.")

    role = None
    if not errors:
        role = db.session.get(Role, data["role_id"])
        if not role:
            errors.append("Selected role does not exist.")

    if errors:
        # Re-render with the modal open and every typed value preserved — the
        # administrator fixes the mistake without retyping the whole form.
        # (Passwords are never echoed back.)
        context = _users_page_context()
        context.update(form=request.form, form_errors=errors)
        return render_template("admin/users.html", **context), 400

    user = User(
        username=data["username"],
        full_name=data["full_name"],
        email=data["email"],
        role_id=role.role_id,
        is_active=True,
    )
    user.set_password(data["password"])
    db.session.add(user)
    log_activity("USER_CREATED", f"User '{user.username}' created with role {role.role_name}",
                 user=current_user)
    db.session.commit()
    flash(f"User '{user.username}' created successfully.", "success")

    # Land on the page that shows the freshly created account.
    ordered = [row[0] for row in User.query.order_by(User.username)
               .with_entities(User.username).all()]
    target_page = ordered.index(user.username) // USERS_PER_PAGE + 1
    return redirect(url_for("admin.users", page=target_page))


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users"))
    if user.user_id == current_user.user_id:
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.users"))

    user.is_active = not user.is_active
    action = "USER_ACTIVATED" if user.is_active else "USER_DEACTIVATED"
    log_activity(action, f"User '{user.username}' {'activated' if user.is_active else 'deactivated'}",
                 user=current_user)
    flash(f"User '{user.username}' {'activated' if user.is_active else 'deactivated'}.", "success")
    # Keep the administrator on the page they were working on.
    page = request.form.get("page", type=int)
    return redirect(url_for("admin.users", page=page) if page and page > 1
                    else url_for("admin.users"))


# ---------------------------------------------------------------------------
# Category management
# ---------------------------------------------------------------------------
def _categories_page_context() -> dict:
    """Shared context for the Categories page (list, chips, 5-per-page slice)."""
    categories_list = Category.query.order_by(Category.category_name).all()
    counts = {
        "total": len(categories_list),
        "active": sum(1 for c in categories_list if c.is_active),
        "inactive": sum(1 for c in categories_list if not c.is_active),
    }

    # Five categories per page — the pager only appears above five.
    total = len(categories_list)
    pages = (total + CATEGORIES_PER_PAGE - 1) // CATEGORIES_PER_PAGE
    page = min(max(1, request.args.get("page", 1, type=int) or 1), max(pages, 1))
    start = (page - 1) * CATEGORIES_PER_PAGE
    page_categories = categories_list[start:start + CATEGORIES_PER_PAGE]
    pagination = None
    if total:
        pagination = {
            "page": page,
            "pages": pages,
            "total": total,
            "start": start + 1,
            "end": start + len(page_categories),
        }

    return {
        "categories": page_categories,
        "counts": counts,
        "pagination": pagination,
    }


@admin_bp.route("/categories")
@admin_required
def categories():
    return render_template("admin/categories.html", **_categories_page_context())


@admin_bp.route("/categories/create", methods=["POST"])
@admin_required
def create_category():
    data, errors = validate_category_payload(request.form)

    if not errors and Category.query.filter_by(
        category_name=data["category_name"]
    ).first():
        errors.append("A category with this name already exists.")

    if errors:
        # Re-render with the modal open and every typed value preserved.
        context = _categories_page_context()
        context.update(form=request.form, form_errors=errors)
        return render_template("admin/categories.html", **context), 400

    category = Category(category_name=data["category_name"],
                        description=data["description"], is_active=True)
    db.session.add(category)
    log_activity("CATEGORY_CREATED", f"Category '{category.category_name}' created",
                 user=current_user)
    db.session.commit()
    flash(f"Category '{category.category_name}' created.", "success")

    # Land on the page that shows the freshly created category.
    ordered = [row[0] for row in Category.query.order_by(Category.category_name)
               .with_entities(Category.category_name).all()]
    target_page = ordered.index(category.category_name) // CATEGORIES_PER_PAGE + 1
    return redirect(url_for("admin.categories", page=target_page))


@admin_bp.route("/categories/<int:category_id>/toggle", methods=["POST"])
@admin_required
def toggle_category(category_id: int):
    category = db.session.get(Category, category_id)
    if not category:
        flash("Category not found.", "danger")
        return redirect(url_for("admin.categories"))

    category.is_active = not category.is_active
    log_activity("CATEGORY_UPDATED",
                 f"Category '{category.category_name}' "
                 f"{'activated' if category.is_active else 'deactivated'}",
                 user=current_user)
    flash(f"Category '{category.category_name}' "
          f"{'activated' if category.is_active else 'deactivated'}.", "success")
    # Keep the administrator on the page they were working on.
    page = request.form.get("page", type=int)
    return redirect(url_for("admin.categories", page=page) if page and page > 1
                    else url_for("admin.categories"))
