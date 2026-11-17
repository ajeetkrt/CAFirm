"""Assignment service — admin assignment / reassignment of enquiries to employees."""
from app.extensions import db
from app.models import EnquiryAssignment, EnquiryStatusHistory, User
from app.services.enquiry_service import EnquiryValidationError, can_assign
from app.utils.helpers import log_activity

#: Statuses from which an assignment moves the enquiry into "Assigned".
ASSIGNABLE_STATUSES = {"New", "Pending", "Reopened"}


def assign_enquiry(enquiry, to_user_id: int, assigned_by, remarks: str = "",
                   ip_address: str | None = None) -> None:
    """Assign or reassign an enquiry. Only Admin may call this."""
    if not can_assign(assigned_by):
        raise PermissionError("Only Admin can assign enquiries.")

    target = db.session.get(User, to_user_id)
    if not target or not target.is_active:
        raise EnquiryValidationError("The selected employee does not exist or is inactive.")

    remarks = (remarks or "").strip()

    # Close previous assignment rows so history stays accurate.
    db.session.query(EnquiryAssignment).filter(
        EnquiryAssignment.enquiry_id == enquiry.enquiry_id,
        EnquiryAssignment.is_current.is_(True),
    ).update({"is_current": False})

    db.session.add(EnquiryAssignment(
        enquiry_id=enquiry.enquiry_id,
        assigned_to_user_id=target.user_id,
        assigned_by_user_id=assigned_by.user_id,
        remarks=remarks or None,
        is_current=True,
    ))

    previous_assignee = enquiry.assigned_to_user_id
    enquiry.assigned_to_user_id = target.user_id

    if enquiry.status in ASSIGNABLE_STATUSES:
        previous_status = enquiry.status
        enquiry.status = "Assigned"
        db.session.add(EnquiryStatusHistory(
            enquiry_id=enquiry.enquiry_id,
            previous_status=previous_status,
            new_status="Assigned",
            changed_by_user_id=assigned_by.user_id,
            remarks=remarks or f"Assigned to {target.full_name}",
        ))

    action = "ENQUIRY_REASSIGNED" if previous_assignee and previous_assignee != target.user_id \
        else "ENQUIRY_ASSIGNED"
    log_activity(
        action,
        f"Enquiry {enquiry.enquiry_number} assigned to {target.full_name}",
        user=assigned_by, enquiry=enquiry, ip_address=ip_address,
    )
