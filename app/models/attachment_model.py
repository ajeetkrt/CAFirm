"""Attachment model — files uploaded against enquiries."""
from datetime import datetime, timezone

from app.extensions import db


class EnquiryAttachment(db.Model):
    __tablename__ = "enquiry_attachments"

    attachment_id = db.Column(db.Integer, primary_key=True)
    enquiry_id = db.Column(
        db.Integer, db.ForeignKey("enquiries.enquiry_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    original_file_name = db.Column(db.String(255), nullable=False)
    stored_file_name = db.Column(db.String(255), nullable=False, unique=True)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    content_type = db.Column(db.String(100))
    uploaded_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id", ondelete="SET NULL"), index=True
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    enquiry = db.relationship("Enquiry", back_populates="attachments")
    uploaded_by = db.relationship("User")

    def __repr__(self) -> str:
        return f"<Attachment {self.original_file_name}>"
