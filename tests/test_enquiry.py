"""Enquiry management tests: creation, scoping, editing, attachments."""
import re

from app.models import Enquiry
from app.services import enquiry_service as svc
from app.services.enquiry_service import EnquiryValidationError


def test_admin_creates_enquiry_with_number(admin_client, enquiry_factory):
    enquiry = enquiry_factory(source="ADMIN")
    assert enquiry.enquiry_number.startswith("CAF-")
    assert re.fullmatch(r"CAF-\d{4}-\d{6}", enquiry.enquiry_number)
    assert enquiry.status == "New"
    assert enquiry.source == "ADMIN"


def test_employee_creation_source_user(user1_client, enquiry_factory):
    from app.models import User
    user = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory(created_by=user, source="USER")
    assert enquiry.source == "USER"
    assert enquiry.created_by_user_id == user.user_id


def test_sequence_numbers_are_unique(enquiry_factory):
    numbers = {enquiry_factory().enquiry_number for _ in range(5)}
    assert len(numbers) == 5


def test_user1_sees_own_but_user2_cannot(app, user1_client, user2_client, enquiry_factory):
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory(created_by=user1, source="USER")

    response = user1_client.get(f"/enquiries/{enquiry.enquiry_id}")
    assert response.status_code == 200

    response = user2_client.get(f"/enquiries/{enquiry.enquiry_id}")
    if response.status_code != 403:
        import re as _re
        body = response.get_data(as_text=True)
        print("DEBUG user2 GET status:", response.status_code)
        m = _re.search(r"<title>(.*?)</title>", body)
        print("DEBUG title:", m.group(1) if m else "?")
        with user2_client.session_transaction() as sess:
            print("DEBUG user2 client session:", dict(sess))
    assert response.status_code == 403


def test_assigned_employee_can_view(app, user1_client, user2_client, enquiry_factory):
    from app.extensions import db
    from app.models import User
    user2 = User.query.filter_by(username="user2").first()
    enquiry = enquiry_factory()
    enquiry.assigned_to_user_id = user2.user_id
    db.session.commit()

    assert user2_client.get(f"/enquiries/{enquiry.enquiry_id}").status_code == 200
    assert user1_client.get(f"/enquiries/{enquiry.enquiry_id}").status_code == 403


def test_admin_sees_everything(admin_client, enquiry_factory):
    enquiry = enquiry_factory()
    assert admin_client.get(f"/enquiries/{enquiry.enquiry_id}").status_code == 200


def test_employee_cannot_edit_others_enquiry(app, user2_client, enquiry_factory):
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory(created_by=user1, source="USER")
    response = user2_client.post(f"/enquiries/{enquiry.enquiry_id}/edit", data={
        "full_name": "Hacked Name", "mobile_number": "9999999999",
        "category_id": "1", "subject": "Hacked subject", "description": "x" * 20,
    })
    assert response.status_code == 403


def test_edit_updates_fields(app, admin_client, db_session, enquiry_factory):
    enquiry = enquiry_factory()
    response = admin_client.post(f"/enquiries/{enquiry.enquiry_id}/edit", data={
        "full_name": "Updated Name", "mobile_number": "9123456780",
        "email": "updated@example.com", "state": "Goa", "district": "Panaji",
        "category_id": "1", "subject": "Updated subject here",
        "description": "An updated description long enough to pass validation.",
    }, follow_redirects=True)
    assert b"updated successfully" in response.data or b"updated" in response.data.lower()
    db_session.expire_all()
    enquiry = db_session.get(Enquiry, enquiry.enquiry_id)
    assert enquiry.full_name == "Updated Name"
    assert enquiry.state == "Goa"


def test_validation_rejects_bad_mobile(app, admin_client, db_session):
    response = admin_client.post("/enquiries/new", data={
        "full_name": "Bad Mobile", "mobile_number": "abc",
        "category_id": "1", "subject": "Subject here",
        "description": "Description text here long enough.",
    }, follow_redirects=True)
    assert b"Mobile number" in response.data
    assert db_session.query(Enquiry).filter_by(full_name="Bad Mobile").count() == 0


def test_attachment_upload_and_download(app, admin_client, user1_client, enquiry_factory):
    import io
    enquiry = enquiry_factory()
    data = {"attachment": (io.BytesIO(b"hello cafirm"), "notes.txt")}
    response = admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                                 data=data, content_type="multipart/form-data",
                                 follow_redirects=True)
    assert b"uploaded" in response.data

    enquiry = db_session_get(app, enquiry.enquiry_id)
    assert len(enquiry.attachments) == 1
    attachment = enquiry.attachments[0]
    assert attachment.original_file_name == "notes.txt"
    # stored name is opaque (uuid)
    assert attachment.stored_file_name != "notes.txt"
    assert attachment.stored_file_name.endswith(".txt")

    download = admin_client.get(f"/enquiries/attachments/{attachment.attachment_id}/download")
    assert download.status_code == 200
    assert download.data == b"hello cafirm"


def test_attachment_rejects_disallowed_type(app, admin_client, enquiry_factory):
    import io
    enquiry = enquiry_factory()
    data = {"attachment": (io.BytesIO(b"evil"), "script.exe")}
    response = admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                                 data=data, content_type="multipart/form-data",
                                 follow_redirects=True)
    assert b"not allowed" in response.data


def db_session_get(app, enquiry_id):
    from app.extensions import db
    return db.session.get(Enquiry, enquiry_id)


# ---------------------------------------------------------------------------
# New Enquiry form flow — /enquiries/new
# ---------------------------------------------------------------------------

VALID_PAYLOAD = {
    "full_name": "Valid Person",
    "mobile_number": "9000000011",
    "email": "valid@example.com",
    "state": "Bihar",
    "district": "Patna",
    "category_id": "1",
    "subject": "ITR notice help needed",
    "description": "Received a notice under section 143(1) and need assistance.",
}


def test_new_enquiry_form_renders(admin_client):
    response = admin_client.get("/enquiries/new")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'action="/enquiries/new"' in html
    assert "Create Enquiry" in html
    # Shell styling must survive the extra_css override: dashboard.css (sidebar
    # + topbar) and enquiry.css (form) both have to be linked.
    assert "css/dashboard.css" in html
    assert "css/enquiry.css" in html


def test_create_renders_typed_values_after_validation_error(app, admin_client, db_session):
    """A validation failure must never wipe out what the user already typed."""
    payload = dict(VALID_PAYLOAD, mobile_number="abc")  # invalid mobile
    response = admin_client.post("/enquiries/new", data=payload)
    assert response.status_code == 400

    html = response.get_data(as_text=True)
    assert 'value="Valid Person"' in html
    assert 'value="valid@example.com"' in html
    assert 'value="Bihar"' in html
    assert 'value="Patna"' in html
    assert 'value="ITR notice help needed"' in html
    assert "Received a notice under section 143(1)" in html  # textarea body
    assert 'value="9000000011"' not in html                   # bad value not echoed
    assert "Mobile number" in html                            # flash explains why

    assert db_session.query(Enquiry).filter_by(full_name="Valid Person").count() == 0


def test_create_requires_email(app, admin_client, db_session):
    """Email is mandatory on the staff form, same as the public form."""
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "email"}
    response = admin_client.post("/enquiries/new", data=payload)
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Email is required" in html
    assert 'value="Valid Person"' in html   # typed data preserved
    assert db_session.query(Enquiry).filter_by(full_name="Valid Person").count() == 0


def test_create_success_redirects_to_detail(app, admin_client, db_session):
    response = admin_client.post("/enquiries/new", data=VALID_PAYLOAD,
                                 follow_redirects=True)
    assert response.status_code == 200
    assert b"created successfully" in response.data

    enquiry = db_session.query(Enquiry).filter_by(full_name="Valid Person").one()
    assert enquiry.status == "New"
    assert enquiry.source == "ADMIN"
    assert enquiry.enquiry_number.startswith("CAF-")


def test_create_renders_form_when_service_rejects(app, admin_client, db_session):
    """Service-level rejection (inactive/missing category) keeps the form usable."""
    payload = dict(VALID_PAYLOAD, category_id="999")  # no such category
    response = admin_client.post("/enquiries/new", data=payload)
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'value="Valid Person"' in html
    assert "not available" in html


# ---------------------------------------------------------------------------
# Detail page: Update Status button + role-aware modal
# ---------------------------------------------------------------------------
def test_detail_page_shows_manage_panel_for_admin(app, admin_client, enquiry_factory, db_session):
    enquiry = enquiry_factory()
    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}").get_data(as_text=True)
    assert 'id="manageModal"' in html
    assert "Manage Enquiry" in html
    assert 'id="statusModal"' not in html      # admins manage — no separate status modal
    # A New enquiry offers every next stage — Reopened is not a New→… step.
    assert '<option value="Closed"' in html
    assert '<option value="Rejected"' in html
    assert '<option value="Reopened"' not in html
    # The unified panel carries exactly ONE remarks box.
    assert 'name="manage_remarks"' in html
    assert 'name="assignment_remarks"' not in html
    assert 'name="status_remarks"' not in html

    # From Resolved, the admin-only Reopened decision becomes available.
    enquiry.status = "Resolved"
    db_session.commit()
    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}").get_data(as_text=True)
    assert '<option value="Reopened"' in html
    assert '<option value="Closed"' in html


def test_detail_update_modal_role_aware(app, user1_client, enquiry_factory, db_session):
    """Status control follows the new ownership rules: an enquiry merely
    assigned to an employee is read-only for them — the modal never renders."""
    from app.models import User
    enquiry = enquiry_factory()
    enquiry.assigned_to_user_id = db_session.query(User).filter_by(username="user1").one().user_id
    db_session.commit()

    html = user1_client.get(f"/enquiries/{enquiry.enquiry_id}").get_data(as_text=True)
    assert 'id="statusModal"' not in html             # not the creator — no modal
    assert "reserved for Admin" not in html


def test_detail_update_modal_for_creator(app, user1_client, enquiry_factory, db_session):
    """Creators still share the workflow — working stages offered, admin-only
    decisions (Closed/Reopened) hidden."""
    from app.models import User
    user1 = db_session.query(User).filter_by(username="user1").one()
    enquiry = enquiry_factory(created_by=user1, source="USER")

    html = user1_client.get(f"/enquiries/{enquiry.enquiry_id}").get_data(as_text=True)
    assert 'id="statusModal"' in html
    assert '<option value="In Progress"' in html      # working stage — allowed
    assert '<option value="Reopened"' not in html     # admin-only — hidden
    assert '<option value="Closed"' not in html
    assert "reserved for Admin" in html


def test_detail_update_modal_auto_open(app, admin_client, enquiry_factory):
    """Dashboard/list rows deep-link with ?update=1 to open the modal directly."""
    enquiry = enquiry_factory()
    deep = admin_client.get(f"/enquiries/{enquiry.enquiry_id}?update=1").get_data(as_text=True)
    assert "bootstrap.Modal.getOrCreateInstance" in deep
    plain = admin_client.get(f"/enquiries/{enquiry.enquiry_id}").get_data(as_text=True)
    assert "bootstrap.Modal.getOrCreateInstance" not in plain


def test_enquiry_list_rows_open_popup_without_icon_column(
        app, admin_client, user1_client, enquiry_factory, db_session):
    """The update/view icon column is gone: the freed width goes to the data
    columns and rows themselves open the details popup — same behaviour as
    the dashboard. Ctrl/middle-click keeps the full detail page."""
    from app.models import User
    enquiry = enquiry_factory()

    admin_html = admin_client.get("/enquiries/").get_data(as_text=True)
    assert f'data-enquiry-popup="{enquiry.enquiry_id}"' in admin_html   # row opens popup
    assert 'title="Update status of' not in admin_html                 # ⟳ column removed
    assert 'class="row-action' not in admin_html                       # no icon buttons
    assert "<th></th>" not in admin_html                               # no empty header cell

    # employee sees the same clean rows for enquiries in their scope
    enquiry.assigned_to_user_id = db_session.query(User).filter_by(username="user1").one().user_id
    db_session.commit()
    user_html = user1_client.get("/enquiries/").get_data(as_text=True)
    assert f'data-enquiry-popup="{enquiry.enquiry_id}"' in user_html


# ---------------------------------------------------------------------------
# Detail page: Edit Details moved into a popup modal
# ---------------------------------------------------------------------------
def test_detail_edit_details_button_opens_modal(app, admin_client, enquiry_factory):
    enquiry = enquiry_factory()
    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}").get_data(as_text=True)
    assert 'id="editEnquiryModal"' in html
    assert "Edit Details" in html
    assert f'action="/enquiries/{enquiry.enquiry_id}/edit"' in html
    # the old always-visible edit panel is gone — the page stays tidy
    assert 'id="editPanel"' not in html
    # enquiry values are pre-filled in the modal form
    assert f'value="{enquiry.full_name}"' in html


def test_detail_edit_error_reopens_modal_and_preserves_values(
        app, admin_client, enquiry_factory, db_session):
    enquiry = enquiry_factory()
    response = admin_client.post(f"/enquiries/{enquiry.enquiry_id}/edit", data={
        "full_name": "Valid Person",
        "mobile_number": "123",                 # invalid — guaranteed error
        "email": enquiry.email or "",
        "state": enquiry.state or "",
        "district": enquiry.district or "",
        "category_id": str(enquiry.category_id),
        "subject": enquiry.subject,
        "description": enquiry.description,
    })
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Mobile number must contain" in html           # in-modal error summary
    assert 'value="123"' in html                          # typed value preserved
    assert "bootstrap.Modal.getOrCreateInstance" in html  # modal auto-opens

    db_session.expire_all()                               # nothing was written
    fresh = db_session.query(Enquiry).filter_by(enquiry_id=enquiry.enquiry_id).one()
    assert fresh.mobile_number != "123"


# ---------------------------------------------------------------------------
# Dashboard details popup — fragment endpoint + AJAX status/edit flows
# ---------------------------------------------------------------------------
XHR = {"X-Requested-With": "XMLHttpRequest"}


def test_popup_details_view_shows_details_status_and_actions(
        app, admin_client, enquiry_factory):
    enquiry = enquiry_factory()
    response = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=details",
                                headers=XHR)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'data-enquiry-id="{enquiry.enquiry_id}"' in html
    assert enquiry.enquiry_number in html
    assert enquiry.full_name in html
    assert ">New</span>" in html                  # current status badge
    assert 'data-popup-view="manage"' in html     # Manage Enquiry lives in the popup
    assert "Manage Enquiry" in html
    # Footer stays minimal: no Edit Details button, no Full Page link
    assert 'data-popup-view="edit"' not in html
    assert "Edit Details" not in html
    assert "Full Page" not in html


def test_popup_status_view_is_role_aware(
        app, admin_client, user1_client, enquiry_factory, db_session):
    from app.models import User
    enquiry = enquiry_factory()                   # created by admin
    enquiry.assigned_to_user_id = db_session.query(User).filter_by(username="user1").one().user_id
    db_session.commit()

    admin_html = admin_client.get(
        f"/enquiries/{enquiry.enquiry_id}/popup?view=status", headers=XHR
    ).get_data(as_text=True)
    assert '<option value="Closed"' in admin_html      # admin-only decision offered
    assert 'name="csrf_token"' in admin_html

    # An assigned non-creator gets no status form at all — forbidden.
    user_response = user1_client.get(
        f"/enquiries/{enquiry.enquiry_id}/popup?view=status", headers=XHR)
    assert user_response.status_code == 403


def test_popup_status_view_allowed_for_creator(
        app, user1_client, enquiry_factory, db_session):
    """The creator still gets the status form: working stages offered,
    admin-only decisions hidden."""
    from app.models import User
    user1 = db_session.query(User).filter_by(username="user1").one()
    enquiry = enquiry_factory(created_by=user1, source="USER")

    response = user1_client.get(
        f"/enquiries/{enquiry.enquiry_id}/popup?view=status", headers=XHR)
    assert response.status_code == 200
    user_html = response.get_data(as_text=True)
    assert '<option value="In Progress"' in user_html  # working stage — allowed
    assert '<option value="Closed"' not in user_html   # admin-only — hidden
    assert '<option value="Reopened"' not in user_html


def test_popup_edit_view_forbidden_for_assigned_non_creator(
        app, user1_client, enquiry_factory, db_session):
    enquiry = enquiry_factory()                   # created by admin, assigned to user1
    from app.models import User
    enquiry.assigned_to_user_id = db_session.query(User).filter_by(username="user1").one().user_id
    db_session.commit()
    response = user1_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=edit",
                                headers=XHR)
    assert response.status_code == 403


def test_popup_edit_view_allowed_for_creator(
        app, user1_client, enquiry_factory, db_session):
    from app.models import User
    user1 = db_session.query(User).filter_by(username="user1").one()
    enquiry = enquiry_factory(created_by=user1, source="USER")
    response = user1_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=edit",
                                headers=XHR)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'value="{enquiry.full_name}"' in html      # prefilled from the record
    assert f'action="/enquiries/{enquiry.enquiry_id}/edit"' in html


# ---------------------------------------------------------------------------
# Manage Enquiry — one admin panel: assignment + status + a single remark
# ---------------------------------------------------------------------------
def test_manage_popup_renders_unified_panel(app, admin_client, enquiry_factory):
    """Admins get ONE panel: assignee select, status select, one remarks box."""
    enquiry = enquiry_factory()
    response = admin_client.get(
        f"/enquiries/{enquiry.enquiry_id}/popup?view=manage", headers=XHR)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'data-enquiry-id="{enquiry.enquiry_id}"' in html
    assert f'action="/enquiries/{enquiry.enquiry_id}/manage"' in html
    assert 'name="assigned_to_user_id"' in html
    assert 'name="status"' in html
    assert 'name="manage_remarks"' in html
    # exactly one remarks field — the old three-box split is gone here
    assert html.count("<textarea") == 1
    assert 'name="assignment_remarks"' not in html
    assert 'name="status_remarks"' not in html


def test_manage_view_and_route_forbidden_for_employee(
        app, user1_client, enquiry_factory):
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory(created_by=user1, source="USER")
    get_response = user1_client.get(
        f"/enquiries/{enquiry.enquiry_id}/popup?view=manage", headers=XHR)
    assert get_response.status_code == 403
    post_response = user1_client.post(
        f"/enquiries/{enquiry.enquiry_id}/manage",
        data={"manage_remarks": "sneaky"})
    assert post_response.status_code == 403


def test_manage_route_assigns_and_updates_status(
        app, admin_client, enquiry_factory, db_session):
    """Assign + status in one apply — the remark travels with both actions."""
    from app.models import EnquiryAssignment, EnquiryStatusHistory, User
    enquiry = enquiry_factory()                       # New, unassigned
    officer = User.query.filter_by(username="user2").first()

    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/manage",
        data={"assigned_to_user_id": str(officer.user_id),
              "status": "In Progress", "manage_remarks": "Handle this one"},
        headers=XHR)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "assignment saved" in html
    assert "status updated to In Progress" in html

    db_session.expire_all()
    fresh = db_session.get(Enquiry, enquiry.enquiry_id)
    assert fresh.assigned_to_user_id == officer.user_id
    assert fresh.status == "In Progress"

    assignment = EnquiryAssignment.query.filter_by(
        enquiry_id=enquiry.enquiry_id, is_current=True).one()
    assert assignment.assigned_to_user_id == officer.user_id
    assert assignment.remarks == "Handle this one"    # the single remark

    steps = [h.new_status for h in EnquiryStatusHistory.query
             .filter_by(enquiry_id=enquiry.enquiry_id)
             .order_by(EnquiryStatusHistory.history_id)]
    assert steps[-1] == "In Progress"


def test_manage_route_lone_remark_recorded_once(
        app, admin_client, enquiry_factory, db_session):
    """Only a remark typed: status and assignee untouched, one history row."""
    from app.models import EnquiryAssignment, EnquiryStatusHistory
    enquiry = enquiry_factory()                       # New, unassigned

    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/manage",
        data={"manage_remarks": "Checked the documents today"},
        headers=XHR)
    assert response.status_code == 200
    assert "remark added" in response.get_data(as_text=True)

    db_session.expire_all()
    fresh = db_session.get(Enquiry, enquiry.enquiry_id)
    assert fresh.status == "New"
    assert EnquiryAssignment.query.filter_by(
        enquiry_id=enquiry.enquiry_id).count() == 0   # no assignment was created
    history = EnquiryStatusHistory.query.filter_by(
        enquiry_id=enquiry.enquiry_id).all()
    assert len(history) == 2                          # creation row + ONE remark row
    remark_row = history[-1]
    assert remark_row.previous_status == remark_row.new_status == "New"
    assert remark_row.remarks == "Checked the documents today"


def test_manage_route_empty_form_rejected(app, admin_client, enquiry_factory):
    """Nothing selected and nothing typed — the panel says so."""
    enquiry = enquiry_factory()
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/manage", data={}, headers=XHR)
    assert response.status_code == 400
    assert "nothing to apply" in response.get_data(as_text=True)


def test_popup_status_ajax_success_returns_details_fragment(
        app, admin_client, enquiry_factory, db_session):
    enquiry = enquiry_factory()                   # New
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/status",
        data={"status": "Pending", "status_remarks": "Under review"},
        headers=XHR,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Status updated to Pending." in html   # success message in the popup
    assert ">Pending</span>" in html              # refreshed badge in the fragment
    assert 'data-popup-view="manage"' in html     # still actionable in place

    db_session.expire_all()
    fresh = db_session.query(Enquiry).filter_by(enquiry_id=enquiry.enquiry_id).one()
    assert fresh.status == "Pending"


def test_popup_status_ajax_error_stays_on_form(
        app, admin_client, enquiry_factory, db_session):
    enquiry = enquiry_factory()                   # New → Reopened is not a valid step
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/status",
        data={"status": "Reopened", "status_remarks": "why"},
        headers=XHR,
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Status cannot change from" in html
    assert "&#39;Reopened&#39;" in html           # error quoted (Jinja-escaped)
    assert "data-popup-form" in html              # the form stays open in the popup

    db_session.expire_all()
    fresh = db_session.query(Enquiry).filter_by(enquiry_id=enquiry.enquiry_id).one()
    assert fresh.status == "New"                  # nothing was written


def test_popup_edit_ajax_success_returns_details_fragment(
        app, admin_client, enquiry_factory, db_session):
    enquiry = enquiry_factory()
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/edit",
        data={
            "full_name": "Renamed Person",
            "mobile_number": enquiry.mobile_number,
            "email": enquiry.email or "",
            "state": enquiry.state or "",
            "district": enquiry.district or "",
            "category_id": str(enquiry.category_id),
            "subject": enquiry.subject,
            "description": enquiry.description,
        },
        headers=XHR,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Enquiry details updated." in html     # success message in the popup
    assert "Renamed Person" in html               # refreshed details fragment
    assert 'data-popup-view="manage"' in html     # status still actionable in place

    db_session.expire_all()
    fresh = db_session.query(Enquiry).filter_by(enquiry_id=enquiry.enquiry_id).one()
    assert fresh.full_name == "Renamed Person"


def test_popup_edit_ajax_error_preserves_values(
        app, admin_client, enquiry_factory, db_session):
    enquiry = enquiry_factory()
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/edit",
        data={
            "full_name": "Typed Name",
            "mobile_number": "123",               # invalid — guaranteed error
            "email": enquiry.email or "",
            "state": enquiry.state or "",
            "district": enquiry.district or "",
            "category_id": str(enquiry.category_id),
            "subject": enquiry.subject,
            "description": enquiry.description,
        },
        headers=XHR,
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Mobile number must contain" in html   # in-popup error summary
    assert 'value="Typed Name"' in html           # typed values preserved
    assert "data-popup-form" in html              # still the editable form

    db_session.expire_all()
    fresh = db_session.query(Enquiry).filter_by(enquiry_id=enquiry.enquiry_id).one()
    assert fresh.full_name != "Typed Name"        # nothing was written


def test_non_ajax_status_change_still_redirects(app, admin_client, enquiry_factory, db_session):
    """Full-page fallback: no XHR header → classic flash + redirect."""
    enquiry = enquiry_factory()
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/status",
        data={"status": "Pending", "status_remarks": ""},
    )
    assert response.status_code == 302
    db_session.expire_all()
    fresh = db_session.query(Enquiry).filter_by(enquiry_id=enquiry.enquiry_id).one()
    assert fresh.status == "Pending"


def test_popup_details_shows_assignee_with_email(
        app, admin_client, enquiry_factory, db_session):
    """The popup status strip names the assignee professionally:
    'Assigned to Full Name (email)'."""
    from app.models import User
    user1 = db_session.query(User).filter_by(username="user1").one()
    enquiry = enquiry_factory()
    enquiry.assigned_to_user_id = user1.user_id
    db_session.commit()

    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=details",
                            headers=XHR).get_data(as_text=True)
    assert f"Assigned to {user1.full_name} ({user1.email})" in html


# ---------------------------------------------------------------------------
# Edit Enquiry action — offered when the popup is opened from the Enquiry No.
# ---------------------------------------------------------------------------
def test_list_enquiry_number_link_is_the_edit_trigger(app, admin_client, enquiry_factory):
    """The Enquiry No. link is the edit trigger; the row body keeps the
    plain details popup."""
    enquiry = enquiry_factory()
    html = admin_client.get("/enquiries/").get_data(as_text=True)
    assert 'data-enquiry-popup-edit="1"' in html                     # number link
    assert f'data-enquiry-popup="{enquiry.enquiry_id}"' in html      # row trigger


def test_popup_details_edit_button_only_from_enquiry_no(app, admin_client, enquiry_factory):
    enquiry = enquiry_factory()
    plain = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=details",
                             headers=XHR).get_data(as_text=True)
    assert "Edit Enquiry" not in plain                    # row-opened popup: no edit

    from_no = admin_client.get(
        f"/enquiries/{enquiry.enquiry_id}/popup?view=details&edit=1",
        headers=XHR).get_data(as_text=True)
    assert "Edit Enquiry" in from_no
    assert 'data-popup-view="edit"' in from_no


def test_popup_edit_button_is_role_aware(app, user1_client, enquiry_factory, db_session):
    """Only users who may edit see the button — assigned non-creators don't."""
    from app.models import User
    user1 = db_session.query(User).filter_by(username="user1").one()

    assigned = enquiry_factory()                          # created by admin
    assigned.assigned_to_user_id = user1.user_id
    db_session.commit()
    html = user1_client.get(
        f"/enquiries/{assigned.enquiry_id}/popup?view=details&edit=1",
        headers=XHR).get_data(as_text=True)
    assert "Edit Enquiry" not in html

    own = enquiry_factory(created_by=user1, source="USER")
    html = user1_client.get(
        f"/enquiries/{own.enquiry_id}/popup?view=details&edit=1",
        headers=XHR).get_data(as_text=True)
    assert "Edit Enquiry" in html


def test_popup_details_shows_created_on_and_status_flow(
        app, admin_client, enquiry_factory, db_session):
    """Created On sits after Created By in the details; the right side shows
    the Assigned Status Workflow with arrow connectors and dates."""
    from app.models import User
    enquiry = enquiry_factory()
    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=details",
                            headers=XHR).get_data(as_text=True)
    assert "Created On" in html
    assert enquiry.created_at.strftime('%d %b %Y') in html
    assert "Assigned Status Workflow" in html
    assert "flow-row" in html
    assert "flow-arrow" not in html               # single step — no arrow yet

    admin = db_session.query(User).filter_by(username="admin").one()
    svc.update_status(enquiry, "Pending", "moving on", admin)
    db_session.commit()

    db_session.expire_all()
    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=details",
                            headers=XHR).get_data(as_text=True)
    assert "flow-arrow" in html                   # New → Pending
    assert "Pending" in html                      # second step rendered


def test_popup_workflow_wraps_three_statuses_per_line(
        app, admin_client, enquiry_factory, db_session):
    """The Assigned Status Workflow legend lays out exactly three statuses
    per line, arrow connectors inside each line only."""
    from app.models import User
    admin = db_session.query(User).filter_by(username="admin").one()
    enquiry = enquiry_factory()
    for status in ("Pending", "In Progress", "Resolved"):
        svc.update_status(enquiry, status, "", admin)
        db_session.commit()
        db_session.expire_all()

    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=details",
                            headers=XHR).get_data(as_text=True)
    assert html.count('class="flow-row"') == 2    # 3 steps + 1 step
    assert html.count("flow-arrow") == 2          # two arrows inside the first line


# ---------------------------------------------------------------------------
# Additional documents — attach Excel/PDF (one or more) while editing; view
# them in the details popup
# ---------------------------------------------------------------------------
def test_edit_popup_offers_document_upload(app, admin_client, enquiry_factory):
    enquiry = enquiry_factory()
    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=edit",
                            headers=XHR).get_data(as_text=True)
    assert 'name="documents"' in html
    assert "multiple" in html                     # one and more files
    assert ".pdf" in html and ".xlsx" in html     # Excel and PDF accepted


def test_edit_ajax_attaches_multiple_documents(
        app, admin_client, enquiry_factory, db_session):
    import io
    from app.models import EnquiryAttachment
    enquiry = enquiry_factory()
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/edit",
        data={
            "full_name": enquiry.full_name,
            "mobile_number": enquiry.mobile_number,
            "email": enquiry.email or "",
            "state": enquiry.state or "",
            "district": enquiry.district or "",
            "category_id": str(enquiry.category_id),
            "subject": enquiry.subject,
            "description": enquiry.description,
            "documents": [(io.BytesIO(b"%PDF-fake"), "notice.pdf"),
                          (io.BytesIO(b"a,b,c"), "ledger.xlsx")],
        },
        headers=XHR, content_type="multipart/form-data")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "2 document(s) attached" in html       # success message counts them
    assert "notice.pdf" in html                   # listed in the refreshed details
    assert "ledger.xlsx" in html

    db_session.expire_all()
    files = db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=enquiry.enquiry_id).all()
    assert {a.original_file_name for a in files} == {"notice.pdf", "ledger.xlsx"}


def test_edit_ajax_rejects_bad_document_type(
        app, admin_client, enquiry_factory, db_session):
    import io
    from app.models import EnquiryAttachment
    enquiry = enquiry_factory()
    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/edit",
        data={
            "full_name": enquiry.full_name,
            "mobile_number": enquiry.mobile_number,
            "email": enquiry.email or "",
            "state": enquiry.state or "",
            "district": enquiry.district or "",
            "category_id": str(enquiry.category_id),
            "subject": enquiry.subject,
            "description": enquiry.description,
            "documents": [(io.BytesIO(b"evil"), "virus.exe")],
        },
        headers=XHR, content_type="multipart/form-data")
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "not allowed" in html
    assert "virus.exe" in html                    # the offending file is named

    db_session.expire_all()                       # nothing was written
    assert db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=enquiry.enquiry_id).count() == 0


def test_popup_details_lists_documents(app, admin_client, enquiry_factory):
    import io
    enquiry = enquiry_factory()
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                      data={"attachment": (io.BytesIO(b"1,2,3"), "budget.xlsx")},
                      content_type="multipart/form-data", follow_redirects=True)

    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=details",
                            headers=XHR).get_data(as_text=True)
    assert "Documents · 1" in html                # section with count
    assert "budget.xlsx" in html                  # file name shown
    assert "download" in html                     # one-click download
    assert "doc-row" in html                      # professional row layout


# ---------------------------------------------------------------------------
# Edit popup: compact description + remove previously attached documents
# ---------------------------------------------------------------------------
def test_edit_popup_lists_existing_documents_with_remove(
        app, admin_client, enquiry_factory, db_session):
    """The edit form lists every attached document, each with a Remove
    option, and uses the compact 2-row description box."""
    import io
    enquiry = enquiry_factory()
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                      data={"attachment": (io.BytesIO(b"%PDF-x"), "notice.pdf")},
                      content_type="multipart/form-data", follow_redirects=True)
    db_session.expire_all()

    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}/popup?view=edit",
                            headers=XHR).get_data(as_text=True)
    assert "Attached Documents · 1" in html       # existing files listed
    assert "notice.pdf" in html
    assert 'name="remove_documents"' in html      # per-document Remove option
    assert "doc-remove" in html                   # professional Remove pill
    assert 'rows="2"' in html                     # compact description box


def test_edit_ajax_removes_document(app, admin_client, enquiry_factory, db_session):
    """Ticking Remove and saving deletes the attachment row and the stored
    file, and reports it in the success message."""
    import io
    from pathlib import Path
    from app.models import EnquiryAttachment
    enquiry = enquiry_factory()
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                      data={"attachment": (io.BytesIO(b"stale report"), "old_notes.txt")},
                      content_type="multipart/form-data", follow_redirects=True)
    db_session.expire_all()
    doc = db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=enquiry.enquiry_id).one()
    attachment_id, stored_path = doc.attachment_id, Path(doc.file_path)
    assert stored_path.exists()                   # sanity: file is on disk

    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/edit",
        data={
            "full_name": enquiry.full_name,
            "mobile_number": enquiry.mobile_number,
            "email": enquiry.email or "",
            "state": enquiry.state or "",
            "district": enquiry.district or "",
            "category_id": str(enquiry.category_id),
            "subject": enquiry.subject,
            "description": enquiry.description,
            "remove_documents": str(attachment_id),
        },
        headers=XHR, content_type="multipart/form-data")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "1 document(s) removed" in html
    assert "old_notes.txt" not in html            # gone from the refreshed details

    db_session.expire_all()
    assert db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=enquiry.enquiry_id).count() == 0
    assert not stored_path.exists()               # stored file unlinked too


def test_edit_ajax_ignores_foreign_or_unknown_remove_id(
        app, admin_client, enquiry_factory, db_session):
    """Removal ids are scoped to this enquiry: documents of other enquiries
    and bogus ids are silently ignored, nothing is deleted."""
    import io
    from app.models import EnquiryAttachment
    enquiry = enquiry_factory()
    other = enquiry_factory()
    for target in (enquiry, other):
        admin_client.post(f"/enquiries/{target.enquiry_id}/attachments",
                          data={"attachment": (io.BytesIO(b"keep"), "keep_me.txt")},
                          content_type="multipart/form-data", follow_redirects=True)
    db_session.expire_all()
    other_doc = db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=other.enquiry_id).one()

    response = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/edit",
        data={
            "full_name": enquiry.full_name,
            "mobile_number": enquiry.mobile_number,
            "email": enquiry.email or "",
            "state": enquiry.state or "",
            "district": enquiry.district or "",
            "category_id": str(enquiry.category_id),
            "subject": enquiry.subject,
            "description": enquiry.description,
            "remove_documents": [str(other_doc.attachment_id), "99999"],
        },
        headers=XHR, content_type="multipart/form-data")
    assert response.status_code == 200            # no error, just ignored
    assert "document(s) removed" not in response.get_data(as_text=True)

    db_session.expire_all()
    assert db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=enquiry.enquiry_id).count() == 1
    assert db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=other.enquiry_id).count() == 1


def test_employee_edit_cannot_remove_others_document(
        app, user1_client, admin_client, enquiry_factory, db_session):
    """An employee who may edit their enquiry still cannot strip documents
    uploaded by someone else — only Admin or the uploader can."""
    import io
    from app.models import EnquiryAttachment, User
    user1 = db_session.query(User).filter_by(username="user1").one()
    enquiry = enquiry_factory(created_by=user1, source="USER")

    # Admin attaches a file to the employee's enquiry.
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                      data={"attachment": (io.BytesIO(b"admin file"), "admin_file.txt")},
                      content_type="multipart/form-data", follow_redirects=True)
    db_session.expire_all()
    doc = db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=enquiry.enquiry_id).one()
    assert doc.uploaded_by_user_id != user1.user_id

    # The creator may edit the enquiry, but the Remove is refused silently.
    response = user1_client.post(
        f"/enquiries/{enquiry.enquiry_id}/edit",
        data={
            "full_name": enquiry.full_name,
            "mobile_number": enquiry.mobile_number,
            "email": enquiry.email or "",
            "state": enquiry.state or "",
            "district": enquiry.district or "",
            "category_id": str(enquiry.category_id),
            "subject": enquiry.subject,
            "description": enquiry.description,
            "remove_documents": str(doc.attachment_id),
        },
        headers=XHR, content_type="multipart/form-data")
    assert response.status_code == 200
    assert "document(s) removed" not in response.get_data(as_text=True)

    db_session.expire_all()
    assert db_session.query(EnquiryAttachment).filter_by(
        enquiry_id=enquiry.enquiry_id).count() == 1


def test_detail_page_edit_modal_lists_documents_with_remove(
        app, admin_client, enquiry_factory, db_session):
    """The full-page detail view's edit modal shows the same Attached
    Documents section with Remove options."""
    import io
    enquiry = enquiry_factory()
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                      data={"attachment": (io.BytesIO(b"%PDF-y"), "notice.pdf")},
                      content_type="multipart/form-data", follow_redirects=True)
    db_session.expire_all()

    html = admin_client.get(f"/enquiries/{enquiry.enquiry_id}").get_data(as_text=True)
    assert "Attached Documents · 1" in html
    assert "notice.pdf" in html
    assert 'name="remove_documents"' in html
    assert 'rows="2"' in html


def test_popup_status_success_keeps_edit_button(app, admin_client, enquiry_factory):
    """The edit flag survives the AJAX roundtrip: popups opened from the
    Enquiry No. keep the Edit Enquiry button after a status change."""
    enquiry = enquiry_factory()
    plain = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/status",
        data={"status": "Pending", "status_remarks": ""},
        headers=XHR).get_data(as_text=True)
    assert "Edit Enquiry" not in plain

    with_edit = admin_client.post(
        f"/enquiries/{enquiry.enquiry_id}/status",
        data={"status": "In Progress", "status_remarks": "", "popup_edit": "1"},
        headers=XHR).get_data(as_text=True)
    assert "Edit Enquiry" in with_edit
