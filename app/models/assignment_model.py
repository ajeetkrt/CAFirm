"""Assignment model — history of enquiry assignments to employees."""
from datetime import datetime, timezone

from app.extensions import db


class EnquiryAssignment(db.Model):
    __tablename__ = "enquiry_assignments"

    assignment_id = db.Column(db.Integer, primary_key=True)
    enquiry_id = db.Column(
        db.Integer, db.ForeignKey("enquiries.enquiry_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    assigned_to_user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id", ondelete="SET NULL"), index=True
    )
    assigned_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id", ondelete="SET NULL"), index=True
    )
    assigned_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    remarks = db.Column(db.Text)
    is_current = db.Column(db.Boolean, nullable=False, default=True, index=True)

    __table_args__ = (
        db.Index("ix_assignments_enquiry_current", "enquiry_id", "is_current"),
    )

    enquiry = db.relationship("Enquiry", back_populates="assignments")
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_user_id])
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_user_id])

    def __repr__(self) -> str:
        return f"<Assignment enquiry={self.enquiry_id} to={self.assigned_to_user_id}>"
