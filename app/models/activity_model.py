"""Activity log model — audit trail of important system activities."""
from datetime import datetime, timezone

from app.extensions import db


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    activity_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id", ondelete="SET NULL"), index=True
    )
    enquiry_id = db.Column(
        db.Integer, db.ForeignKey("enquiries.enquiry_id", ondelete="CASCADE"), index=True
    )
    action = db.Column(db.String(50), nullable=False, index=True)
    description = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), index=True,
    )

    user = db.relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<ActivityLog {self.action}>"
