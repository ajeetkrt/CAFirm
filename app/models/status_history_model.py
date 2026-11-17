"""Status history model — audit trail of every enquiry status change."""
from datetime import datetime, timezone

from app.extensions import db


class EnquiryStatusHistory(db.Model):
    __tablename__ = "enquiry_status_history"

    history_id = db.Column(db.Integer, primary_key=True)
    enquiry_id = db.Column(
        db.Integer, db.ForeignKey("enquiries.enquiry_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    previous_status = db.Column(db.String(20))
    new_status = db.Column(db.String(20), nullable=False)
    changed_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id", ondelete="SET NULL"), index=True
    )
    remarks = db.Column(db.Text)
    changed_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )

    enquiry = db.relationship("Enquiry", back_populates="status_history")
    changed_by = db.relationship("User")

    def __repr__(self) -> str:
        return f"<StatusHistory {self.previous_status} -> {self.new_status}>"
