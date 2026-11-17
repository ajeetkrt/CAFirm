"""Status workflow tests — transitions, permissions, history."""
from app.models import EnquiryStatusHistory
from app.services import enquiry_service as svc
from app.services.enquiry_service import EnquiryValidationError, EnquiryPermissionError


def test_valid_transition_records_history(app, admin_client, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry = enquiry_factory()
    svc.update_status(enquiry, "Pending", "Needs verification", admin, "127.0.0.1")
    db_session.commit()
    assert enquiry.status == "Pending"
    history = (EnquiryStatusHistory.query
               .filter_by(enquiry_id=enquiry.enquiry_id)
               .order_by(EnquiryStatusHistory.history_id.desc()).first())
    assert history.previous_status == "New"
    assert history.new_status == "Pending"
    assert history.remarks == "Needs verification"


def test_invalid_transition_rejected(app, admin_client, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry = enquiry_factory()
    try:
        svc.update_status(enquiry, "Resolved", "jump ahead", admin)  # New -> Resolved allowed; use illegal pair
        # find a truly illegal pair: Closed -> In Progress
        enquiry.status = "Closed"
        try:
            svc.update_status(enquiry, "In Progress", "illegal", admin)
            assert False, "expected EnquiryValidationError"
        except EnquiryValidationError:
            pass
    finally:
        pass


def test_employee_cannot_close_enquiry(app, enquiry_factory):
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory()
    enquiry.status = "In Progress"
    try:
        svc.update_status(enquiry, "Closed", "attempt", user1)
        assert False, "expected EnquiryPermissionError"
    except EnquiryPermissionError:
        pass


def test_reopen_requires_reason(app, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry = enquiry_factory()
    enquiry.status = "Resolved"
    try:
        svc.update_status(enquiry, "Reopened", "", admin)
        assert False, "expected EnquiryValidationError"
    except EnquiryValidationError:
        pass
    svc.update_status(enquiry, "Reopened", "Visitor reported incomplete answer", admin)
    assert enquiry.status == "Reopened"


def test_admin_can_close(app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry = enquiry_factory()
    enquiry.status = "Resolved"
    svc.update_status(enquiry, "Closed", "verified with visitor", admin)
    db_session.commit()
    assert enquiry.status == "Closed"


def test_add_remark_keeps_status(app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry = enquiry_factory()
    svc.add_remark(enquiry, "Called the visitor back", admin)
    db_session.commit()
    assert enquiry.status == "New"
    history = (EnquiryStatusHistory.query
               .filter_by(enquiry_id=enquiry.enquiry_id)
               .order_by(EnquiryStatusHistory.history_id.desc()).first())
    assert history.previous_status == history.new_status == "New"
    assert history.remarks == "Called the visitor back"


def test_status_change_via_http_by_user(app, user1_client, enquiry_factory, db_session):
    """An enquiry assigned to an employee is read-only for them: the assignee
    may NOT change its status — only the creator (or Admin) can."""
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory()
    enquiry.assigned_to_user_id = user1.user_id
    enquiry.status = "Assigned"
    from app.extensions import db
    db.session.commit()

    response = user1_client.post(f"/enquiries/{enquiry.enquiry_id}/status", data={
        "status": "In Progress", "status_remarks": "Starting work",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert ("You can only update the status of enquiries you created."
            in response.get_data(as_text=True))
    db.session.expire_all()
    assert db.session.get(svc.Enquiry, enquiry.enquiry_id).status == "Assigned"


def test_status_change_via_http_by_creator(app, user1_client, enquiry_factory, db_session):
    """The creator can still move their own enquiry through the workflow."""
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory(created_by=user1, source="USER")
    enquiry.status = "Pending"
    from app.extensions import db
    db.session.commit()

    response = user1_client.post(f"/enquiries/{enquiry.enquiry_id}/status", data={
        "status": "In Progress", "status_remarks": "Starting work",
    }, follow_redirects=True)
    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(svc.Enquiry, enquiry.enquiry_id).status == "In Progress"


def test_resolved_enquiry_locked_for_creator(app, user1_client, enquiry_factory, db_session):
    """Once Resolved (a terminal state), the creator can no longer modify the
    enquiry or update its status — only Admin still can."""
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory(created_by=user1, source="USER")
    enquiry.status = "Resolved"
    from app.extensions import db
    db.session.commit()

    fresh = db.session.get(svc.Enquiry, enquiry.enquiry_id)
    assert svc.allowed_statuses_for(fresh, user1) == set()
    assert svc.can_edit(user1, fresh) is False

    edit = user1_client.post(f"/enquiries/{enquiry.enquiry_id}/edit", data={
        "full_name": "Locked Out", "mobile_number": "9876543210",
        "category_id": "1", "subject": "Locked subject", "description": "x" * 20,
    })
    assert edit.status_code == 403

    response = user1_client.post(f"/enquiries/{enquiry.enquiry_id}/status", data={
        "status": "In Progress", "status_remarks": "trying again",
    }, follow_redirects=True)
    assert "Status cannot change from" in response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(svc.Enquiry, enquiry.enquiry_id).status == "Resolved"


# ---------------------------------------------------------------------------
# Dashboards: view + status-update quick actions on every row
# ---------------------------------------------------------------------------
def test_admin_dashboard_rows_show_details_with_status_only(app, admin_client, enquiry_factory):
    """Widget rows are detail rows with a status badge and a clean view
    affordance (chevron) — never per-row update buttons."""
    enquiry = enquiry_factory()
    html = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert enquiry.enquiry_number in html
    assert 'title="Update status of' not in html          # no update buttons
    assert "update=1" not in html
    assert 'class="wrow-go" title="View"><i class="bi bi-eye">' in html  # view icon
    assert f'title="View enquiry {enquiry.enquiry_number}"' in html


def test_admin_dashboard_rows_open_details_popup(app, admin_client, enquiry_factory):
    """Rows keep their real href but carry data-enquiry-popup so a plain click
    opens the details popup (details + status + actions) instead of navigating."""
    enquiry = enquiry_factory()
    html = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert f'data-enquiry-popup="{enquiry.enquiry_id}"' in html


def test_employee_dashboard_rows_open_details_popup(
        app, user1_client, enquiry_factory, db_session):
    from app.models import User
    enquiry = enquiry_factory()
    enquiry.assigned_to_user_id = db_session.query(User).filter_by(username="user1").one().user_id
    db_session.commit()

    html = user1_client.get("/user/dashboard").get_data(as_text=True)
    assert f'data-enquiry-popup="{enquiry.enquiry_id}"' in html


def test_dashboard_layout_has_popup_shell_and_script(app, admin_client):
    html = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert 'id="enquiryModal"' in html               # shared popup shell
    assert "js/enquiry-popup.js" in html             # popup behaviour script


def test_employee_dashboard_rows_show_details_with_status_only(
        app, user1_client, enquiry_factory, db_session):
    """Employees see the same clean detail rows on their own dashboard."""
    from app.models import User
    enquiry = enquiry_factory()
    enquiry.assigned_to_user_id = db_session.query(User).filter_by(username="user1").one().user_id
    db_session.commit()

    html = user1_client.get("/user/dashboard").get_data(as_text=True)
    assert enquiry.enquiry_number in html
    assert 'title="Update status of' not in html
    assert "Actions" not in html


def test_assigned_enquiries_tile_shows_chips_without_email(app, admin_client, db_session):
    """The Assigned tile matches the Open tile's chip legend (Assigned purple
    dot + employee name + count); no email ids appear on the dashboard tiles."""
    from app.models import User
    from app.services import procedure_service
    breakdown = procedure_service.get_dashboard_charts(None, True).get("employee_assigned") or {}
    labels = breakdown.get("labels") or []
    counts = breakdown.get("counts") or []

    html = admin_client.get("/admin/dashboard").get_data(as_text=True)
    if labels:
        assert 'kpi-chip c-assigned"><i></i>' in html          # same legend style
        top_chip = f'kpi-chip c-assigned"><i></i>{labels[0]}&nbsp;{int(counts[0])}'
        assert top_chip in html                                # heaviest assignee first
        for user in db_session.query(User).all():
            if user.email:
                assert user.email not in html                  # no email ids on tiles
    else:
        assert "No assignments yet" in html


def test_open_enquiries_tile_shows_status_chips(app, admin_client, enquiry_factory):
    """Open Enquiries bottom shows a colour-coded status legend (dot +
    label + count) instead of a raw 'New 1 · Pending 2' text dump."""
    enquiry = enquiry_factory()                        # status New → Open tile
    html = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert "kpi-chips" in html
    assert 'kpi-chip c-new"><i></i>New' in html        # New breakdown chip


# ---------------------------------------------------------------------------
# Viewport-fit workspaces — the page never scrolls down
# ---------------------------------------------------------------------------
def test_admin_dashboard_fits_viewport(app, admin_client):
    html = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert 'class="dash-fit"' in html          # flex column filling the viewport
    assert "dash-row-charts" in html           # charts flex-fill
    assert "dash-row-widgets" in html          # widgets flex-fill
    assert "New Enquiry" in html               # action button in the welcome band


def test_dashboard_widgets_paginate_4_rows_up_to_4_pages(
        app, admin_client, enquiry_factory):
    """Recent Enquiries and Pending Queue load 16 rows (4 pages × 4 rows)
    and render the widget pager for browsing without page reloads."""
    for _ in range(18):
        enquiry_factory()

    html = admin_client.get("/admin/dashboard").get_data(as_text=True)
    assert html.count('class="wrow"') == 32      # 16 recent + 16 pending
    assert html.count("data-wpager") == 2        # one pager per widget
    assert html.count("wpager-btn") >= 4         # prev/next buttons
    assert 'class="wpager-ind"' in html


def test_employee_dashboard_fits_viewport(app, user1_client):
    html = user1_client.get("/user/dashboard").get_data(as_text=True)
    assert 'class="dash-fit"' in html
    assert "dash-row-widgets" in html


def test_enquiries_list_fits_viewport(app, admin_client):
    html = admin_client.get("/enquiries/").get_data(as_text=True)
    assert 'class="fit-list"' in html
