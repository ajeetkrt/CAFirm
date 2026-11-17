"""User model — internal application accounts (Admin / User1 / User2)."""
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class User(db.Model, UserMixin):
    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(
        db.Integer, db.ForeignKey("roles.role_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    role = db.relationship("Role", back_populates="users")
    created_enquiries = db.relationship(
        "Enquiry", foreign_keys="Enquiry.created_by_user_id", backref="created_by", lazy="dynamic"
    )
    assigned_enquiries = db.relationship(
        "Enquiry", foreign_keys="Enquiry.assigned_to_user_id", backref="assigned_to", lazy="dynamic"
    )

    # --- Flask-Login plumbing -------------------------------------------------
    def get_id(self) -> str:
        return str(self.user_id)

    # --- Password helpers -----------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # --- Role helpers -----------------------------------------------------------
    @property
    def role_name(self) -> str:
        return self.role.role_name if self.role else ""

    @property
    def is_admin(self) -> bool:
        return self.role_name == "Admin"

    def __repr__(self) -> str:
        return f"<User {self.username}>"
