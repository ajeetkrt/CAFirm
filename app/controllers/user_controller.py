"""Employee (User) controller — personal dashboard for User1 / User2."""
from datetime import datetime

from flask import Blueprint, render_template
from flask_login import current_user

from app.extensions import db
from app.models import ActivityLog, Enquiry
from app.services import procedure_service
from app.utils.decorators import role_required
from app.utils.helpers import STATUS_BADGE_CLASSES

user_bp = Blueprint("user", __name__)

# Statuses the employee KPI procedure reports (its user branch has no
# Assigned/Reopened/Closed/Rejected rows — those are admin-level labels).
OPEN_STATUSES = ("New", "Pending", "In Progress")


def _decorate_user_kpis(raw_kpis, scope_total: int, month_count: int) -> list[dict]:
    """Fold the employee's raw KPIs into the same five one-line tiles the
    admin dashboard uses (icon + share bar + status chip legend) — but the
    labels are the employee's own: what they raised, what admin assigned
    them, what is open, what is completed and this month's intake."""
    raw = {k["label"]: int(k["value"]) for k in raw_kpis}
    created = raw.get("Enquiries Created by Me", 0)
    assigned = raw.get("Enquiries Assigned to Me", 0)
    resolved = raw.get("Resolved Enquiries", 0)
    base = scope_total if scope_total else 0   # share bar base: my whole scope

    def tile(label: str, value: int, status: str | None, note: str, icon: str,
             chips: list[dict] | None = None) -> dict:
        return {
            "label": label,
            "value": value,
            "status": status,
            "icon": icon,
            "percent": round(value * 100 / base) if base else 0,
            "note": note,       # plain summary — tooltip + no-chip fallback
            "chips": chips,
        }

    open_chips = [
        {"label": name, "count": raw.get(f"{name} Enquiries", 0),
         "class": STATUS_BADGE_CLASSES[name].replace("status-", "c-")}
        for name in OPEN_STATUSES if raw.get(f"{name} Enquiries", 0)
    ]
    open_count = sum(raw.get(f"{name} Enquiries", 0) for name in OPEN_STATUSES)
    open_note = (" · ".join(f"{c['label']} {c['count']}" for c in open_chips)
                 or "No open enquiries")

    completed_chips = ([{"label": "Resolved", "count": resolved, "class": "c-resolved"}]
                       if resolved else None)

    return [
        tile("Created by Me", created, None, "Enquiries raised by me",
             "bi-pencil-square"),
        tile("Assigned to Me", assigned, "Assigned", "Tasks assigned by admin",
             "bi-person-check"),
        tile("Open Enquiries", open_count, "Pending", open_note,
             "bi-hourglass-split", chips=open_chips or None),
        tile("Completed", resolved, "Resolved", "Resolved in my scope",
             "bi-check2-circle", chips=completed_chips),
        tile("This Month", month_count, None, "Created this month",
             "bi-calendar-plus"),
    ]


@user_bp.route("/dashboard")
@role_required("Admin", "User")
def dashboard():
    """Personal dashboard; every number is the employee's own scope:
    enquiries they created plus enquiries admin assigned to them."""
    user = current_user
    raw_kpis = procedure_service.get_dashboard_kpis(user.user_id, False)["kpis"]
    charts = procedure_service.get_dashboard_charts(user.user_id, False)
    db.session.commit()

    raw_counts = {k["label"]: int(k["value"]) for k in raw_kpis}
    resolved = raw_counts.get("Resolved Enquiries", 0)
    active_count = sum(raw_counts.get(f"{s} Enquiries", 0) for s in OPEN_STATUSES)

    scoped = Enquiry.query.filter(
        db.or_(Enquiry.created_by_user_id == user.user_id,
               Enquiry.assigned_to_user_id == user.user_id)
    )
    scope_total = scoped.count()
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_count = scoped.filter(Enquiry.created_at >= month_start).count()
    kpis = _decorate_user_kpis(raw_kpis, scope_total, month_count)

    # Widget browsers: 4 rows per page, up to 4 pages (4×4 = 16) — same as admin.
    recent = scoped.order_by(Enquiry.created_at.desc()).limit(16).all()
    activities = (
        ActivityLog.query.filter(ActivityLog.user_id == user.user_id)
        .order_by(ActivityLog.created_at.desc()).limit(16).all()
    )

    now = datetime.now()
    greeting = ("Good morning" if now.hour < 12
                else "Good afternoon" if now.hour < 17 else "Good evening")

    return render_template(
        "user/dashboard.html",
        kpis=kpis,
        recent=recent,
        activities=activities,
        charts=charts,
        my_total=scope_total,
        active_count=active_count,
        month_count=month_count,
        resolution_rate=round(resolved * 100 / scope_total) if scope_total else 0,
        greeting=greeting,
        today_long=now.strftime("%A, %d %B %Y"),
    )
