"""Assignment service tests — admin-only, history maintained."""
import pytest

from app.models import EnquiryAssignment
from app.services import assignment_service
from app.services.enquiry_service import EnquiryValidationError


def test_admin_assignment_updates_enquiry_and_history(
        app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    user1 = User.query.filter_by(username="user1").first()
    enquiry = enquiry_factory()

    assignment_service.assign_enquiry(enquiry, user1.user_id, admin,
                                      remarks="handle this", ip_address="127.0.0.1")
    db_session.commit()

    assert enquiry.assigned_to_user_id == user1.user_id
    assert enquiry.status == "Assigned"
    current = (EnquiryAssignment.query
               .filter_by(enquiry_id=enquiry.enquiry_id, is_current=True).one())
    assert current.assigned_to_user_id == user1.user_id
    assert current.assigned_by_user_id == admin.user_id
    assert current.remarks == "handle this"


def test_reassignment_marks_old_rows_not_current(app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    user1 = User.query.filter_by(username="user1").first()
    user2 = User.query.filter_by(username="user2").first()
    enquiry = enquiry_factory()

    assignment_service.assign_enquiry(enquiry, user1.user_id, admin)
    assignment_service.assign_enquiry(enquiry, user2.user_id, admin, remarks="moved")
    db_session.commit()

    rows = EnquiryAssignment.query.filter_by(enquiry_id=enquiry.enquiry_id)\
        .order_by(EnquiryAssignment.assignment_id).all()
    assert len(rows) == 2
    assert [r.is_current for r in rows] == [False, True]
    assert enquiry.assigned_to_user_id == user2.user_id


def test_assignment_to_inactive_user_rejected(app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    user2 = User.query.filter_by(username="user2").first()
    user2.is_active = False
    db_session.commit()
    try:
        enquiry = enquiry_factory()
        with pytest.raises(EnquiryValidationError):
            assignment_service.assign_enquiry(enquiry, user2.user_id, admin)
    finally:
        user2.is_active = True
        db_session.commit()


def test_employee_cannot_assign_via_http(app, user1_client, enquiry_factory):
    enquiry = enquiry_factory()
    response = user1_client.post(f"/enquiries/{enquiry.enquiry_id}/assign",
                                 data={"assigned_to_user_id": "2"})
    assert response.status_code == 403
