"""Enquiry model — the central entity of CAFirm."""
from datetime import datetime, timezone

from app.extensions import db

ENQUIRY_SOURCES = ("ADMIN", "USER", "VISITOR")


class Enquiry(db.Model):
    __tablename__ = "enquiries"

    enquiry_id = db.Column(db.Integer, primary_key=True)
    enquiry_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100), nullable=False)
    mobile_number = db.Column(db.String(15), nullable=False, index=True)
    email = db.Column(db.String(120))
    state = db.Column(db.String(50))
    district = db.Column(db.String(50))

    category_id = db.Column(
        db.Integer, db.ForeignKey("enquiry_categories.category_id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    subject = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)

    source = db.Column(db.String(10), nullable=False, default="VISITOR", index=True)
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id", ondelete="SET NULL"), index=True
    )
    assigned_to_user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id", ondelete="SET NULL"), index=True
    )
    status = db.Column(db.String(20), nullable=False, default="New", index=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.CheckConstraint("source IN ('ADMIN', 'USER', 'VISITOR')", name="ck_enquiry_source"),
        db.Index("ix_enquiries_created_status", "created_by_user_id", "status"),
        db.Index("ix_enquiries_assigned_status", "assigned_to_user_id", "status"),
    )

    category = db.relationship("Category", back_populates="enquiries")
    assignments = db.relationship(
        "EnquiryAssignment", back_populates="enquiry",
        cascade="all, delete-orphan", order_by="EnquiryAssignment.assigned_at",
    )
    status_history = db.relationship(
        "EnquiryStatusHistory", back_populates="enquiry",
        cascade="all, delete-orphan", order_by="EnquiryStatusHistory.changed_at",
    )
    attachments = db.relationship(
        "EnquiryAttachment", back_populates="enquiry", cascade="all, delete-orphan"
    )
    activities = db.relationship(
        "ActivityLog", backref="enquiry", foreign_keys="ActivityLog.enquiry_id"
    )

    def __repr__(self) -> str:
        return f"<Enquiry {self.enquiry_number} [{self.status}]>"
