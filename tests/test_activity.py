"""Activity log tests — audit trail recorded for key operations."""
from app.models import ActivityLog


def test_login_and_logout_logged(app, client, db_session):
    client.post("/login", data={"username": "admin", "password": "Admin@123"})
    actions = {row[0] for row in db_session.query(ActivityLog.action).all()}
    assert "USER_LOGIN" in actions

    client.post("/logout")
    actions = {row[0] for row in db_session.query(ActivityLog.action).all()}
    assert "USER_LOGOUT" in actions


def test_enquiry_lifecycle_logged(app, admin_client, db_session, enquiry_factory):
    enquiry = enquiry_factory()
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/assign",
                      data={"assigned_to_user_id": "2", "assignment_remarks": ""})
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/status",
                      data={"status": "Pending", "status_remarks": "waiting"})
    admin_client.post(f"/enquiries/{enquiry.enquiry_id}/remark",
                      data={"remark": "audit remark"})

    actions = {row[0] for row in
               db_session.query(ActivityLog.action)
               .filter(ActivityLog.enquiry_id == enquiry.enquiry_id).all()}
    assert {"ENQUIRY_CREATED", "ENQUIRY_ASSIGNED", "STATUS_CHANGED", "REMARK_ADDED"} \
        .issubset(actions)


def test_failed_login_logged(app, client, db_session):
    client.post("/login", data={"username": "admin", "password": "bad-pass"})
    failed = db_session.query(ActivityLog).filter_by(action="LOGIN_FAILED").count()
    assert failed >= 1


def test_access_denied_logged(app, user2_client, db_session, enquiry_factory):
    """Every forbidden request must be recorded — who, what path, from where."""
    from app.models import User
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory(created_by=user1, source="USER")

    response = user2_client.get(f"/enquiries/{enquiry.enquiry_id}")
    assert response.status_code == 403

    denied = db_session.query(ActivityLog).filter_by(action="ACCESS_DENIED").all()
    assert any(f"/enquiries/{enquiry.enquiry_id}" in row.description
               for row in denied)
    # the denied user is recorded on the entry
    assert any(row.user_id == User.query.filter_by(username="user2").one().user_id
               for row in denied)


def test_attachment_download_logged(app, admin_client, db_session, enquiry_factory):
    import io
    from app.models import EnquiryAttachment
    enquiry = enquiry_factory()
    upload = admin_client.post(f"/enquiries/{enquiry.enquiry_id}/attachments",
                               data={"attachment": (io.BytesIO(b"audit me"), "audit.txt")},
                               content_type="multipart/form-data",
                               follow_redirects=True)
    assert upload.status_code == 200

    attachment = db_session.query(EnquiryAttachment) \
        .filter_by(enquiry_id=enquiry.enquiry_id).one()
    download = admin_client.get(f"/enquiries/attachments/{attachment.attachment_id}/download")
    assert download.status_code == 200
    assert download.data == b"audit me"

    logged = db_session.query(ActivityLog) \
        .filter_by(action="ATTACHMENT_DOWNLOADED", enquiry_id=enquiry.enquiry_id).count()
    assert logged == 1


def test_honeypot_reject_logged(app, client, db_session):
    """Bots filling the hidden field are rejected AND audited."""
    response = client.post("/public/enquiry", data={
        "website": "http://spam.example",
        "full_name": "Bad Bot", "mobile_number": "9999999999",
        "email": "bot@example.com", "category_id": "1",
        "subject": "spam subject here", "description": "spam text long enough.",
    })
    assert response.status_code == 400
    assert db_session.query(ActivityLog).filter_by(action="HONEYPOT_REJECT").count() == 1
