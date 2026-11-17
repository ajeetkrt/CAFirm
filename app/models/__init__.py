"""SQLAlchemy models for CAFirm.

Table and relationship overview:

    roles 1 --- * users
    enquiry_categories 1 --- * enquiries
    users 1 --- * enquiries (created_by / assigned_to)
    enquiries 1 --- * enquiry_assignments
    enquiries 1 --- * enquiry_status_history
    enquiries 1 --- * enquiry_attachments
    users 1 --- * activity_logs
"""
from app.models.user_model import User
from app.models.role_model import Role
from app.models.category_model import Category
from app.models.enquiry_model import Enquiry
from app.models.assignment_model import EnquiryAssignment
from app.models.status_history_model import EnquiryStatusHistory
from app.models.activity_model import ActivityLog
from app.models.attachment_model import EnquiryAttachment

__all__ = [
    "User",
    "Role",
    "Category",
    "Enquiry",
    "EnquiryAssignment",
    "EnquiryStatusHistory",
    "ActivityLog",
    "EnquiryAttachment",
]
