"""Category model — enquiry categories managed by Admin."""
from datetime import datetime, timezone

from app.extensions import db


class Category(db.Model):
    __tablename__ = "enquiry_categories"

    category_id = db.Column(db.Integer, primary_key=True)
    category_name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    enquiries = db.relationship("Enquiry", back_populates="category", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Category {self.category_name}>"
