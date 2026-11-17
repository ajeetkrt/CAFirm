"""
CAFirm — Chartered Accountant Enquiry & Client Service Management System.

Application factory package.
"""
import logging
import os
import secrets
from datetime import datetime
from logging.handlers import RotatingFileHandler

import click
from flask import Flask, render_template, request
from werkzeug.security import generate_password_hash

from app.extensions import db, migrate, login_manager, csrf


def create_app(config_class_name: str | None = None) -> Flask:
    """Build and configure the CAFirm Flask application."""
    from config import get_config

    app = Flask(
        __name__,
        template_folder="views",
        static_folder="static",
        instance_relative_config=False,
    )

    config_class = get_config(config_class_name)
    app.config.from_object(config_class)

    # Ensure runtime folders exist (must happen before file logging starts).
    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)
    app.config["LOG_FOLDER"].mkdir(parents=True, exist_ok=True)

    _configure_logging(app)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Models must be imported so Flask-Migrate sees the full metadata.
    from app.models import (  # noqa: F401
        Role, User, Category, Enquiry, EnquiryAssignment,
        EnquiryStatusHistory, ActivityLog, EnquiryAttachment,
    )

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    # Blueprints
    from app.controllers.auth_controller import auth_bp
    from app.controllers.admin_controller import admin_bp
    from app.controllers.user_controller import user_bp
    from app.controllers.enquiry_controller import enquiry_bp
    from app.controllers.public_controller import public_bp
    from app.controllers.report_controller import report_bp
    from app.controllers.api_controller import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(user_bp, url_prefix="/user")
    app.register_blueprint(enquiry_bp, url_prefix="/enquiries")
    app.register_blueprint(public_bp)
    app.register_blueprint(report_bp, url_prefix="/reports")
    app.register_blueprint(api_bp)
    # The JSON API authenticates via the session cookie and never renders
    # forms; exempt it from CSRF token checks.
    csrf.exempt(api_bp)

    # Template globals
    from app.utils.helpers import STATUS_BADGE_CLASSES
    from app.services.enquiry_service import STATUS_TRANSITIONS, ADMIN_ONLY_STATUSES

    def static_v(filename: str) -> int:
        """Cache-busting version for a static file (its modification time).

        Appended to static asset URLs so browsers always fetch the current
        version after any CSS/JS change — no manual hard-refresh needed.
        """
        try:
            return int(os.stat(os.path.join(app.static_folder, filename)).st_mtime)
        except OSError:
            return 0

    @app.context_processor
    def inject_globals():
        return {
            "APP_NAME": app.config["APP_NAME"],
            "APP_FULL_NAME": app.config["APP_FULL_NAME"],
            "STATUS_BADGE_CLASSES": STATUS_BADGE_CLASSES,
            "STATUS_TRANSITIONS": STATUS_TRANSITIONS,
            "ADMIN_ONLY_STATUSES": ADMIN_ONLY_STATUSES,
            "static_v": static_v,
            "today_long": datetime.now().strftime("%A, %d %B %Y"),
        }

    # Security headers on every response (audit hardening).
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        # Conservative CSP: inline styles/scripts stay allowed because the
        # templates use small inline <script> blocks and style attributes;
        # everything else is same-origin only (all libraries are vendored).
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        if not app.testing and not app.debug:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    # Error pages
    @app.errorhandler(403)
    def forbidden(_error):
        # Security audit: record every denied request — who, what, from where.
        from flask_login import current_user
        from app.utils.helpers import log_activity
        user = current_user if current_user.is_authenticated else None
        log_activity(
            "ACCESS_DENIED",
            f"Access denied: {request.method} {request.path}",
            user=user,
        )
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def page_not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    # CLI commands
    _register_seed_command(app)

    return app


def _configure_logging(app: Flask) -> None:
    """Log errors to file; console keeps Flask's default setup."""
    if not app.debug and not app.testing:
        handler = RotatingFileHandler(
            app.config["LOG_FOLDER"] / "app.log",
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)


def _register_seed_command(app: Flask) -> None:
    """`flask seed` — create roles, internal accounts and default categories."""

    @app.cli.command("seed")
    @click.option("--force", is_flag=True, help="Reset passwords of existing accounts.")
    def seed(force: bool) -> None:
        from app.models import Role, User, Category

        session = db.session

        roles = {
            "Admin": Role(role_name="Admin", description="Full system access"),
            "User": Role(role_name="User", description="Internal employee account"),
        }
        for role in roles.values():
            existing = session.query(Role).filter_by(role_name=role.role_name).first()
            if not existing:
                session.add(role)
        session.flush()

        admin_role = session.query(Role).filter_by(role_name="Admin").first()
        user_role = session.query(Role).filter_by(role_name="User").first()

        # Seed passwords: defaults suit local development; on a deployed
        # instance set SEED_ADMIN_PASSWORD / SEED_STAFF_PASSWORD in the
        # environment so no guessable password ever exists.
        import os

        admin_password = os.getenv("SEED_ADMIN_PASSWORD", "Admin@123")
        staff_password = os.getenv("SEED_STAFF_PASSWORD", "User@123")

        accounts = [
            ("admin", "Administrator", "admin@cafirm.local", admin_password, admin_role),
            ("user1", "User One", "user1@cafirm.local", staff_password, user_role),
            ("user2", "User Two", "user2@cafirm.local", staff_password, user_role),
        ]
        for username, full_name, email, password, role in accounts:
            user = session.query(User).filter_by(username=username).first()
            if not user:
                user = User(
                    username=username,
                    full_name=full_name,
                    email=email,
                    role_id=role.role_id,
                    is_active=True,
                )
                user.set_password(password)
                session.add(user)
            elif force:
                user.set_password(password)

        default_categories = [
            ("GST Registration & Returns", "Enquiries about GST registration and periodic return filing"),
            ("Income Tax", "Enquiries about income tax returns, refunds and notices"),
            ("Company & Firm Registration", "Enquiries about company, LLP and partnership firm incorporation"),
            ("Accounting & Bookkeeping", "Requests for accounting, bookkeeping and payroll services"),
            ("Audit & Assurance", "Enquiries about statutory, tax and internal audits"),
            ("General Enquiry", "General questions about CA services and consultations"),
        ]
        for name, description in default_categories:
            if not session.query(Category).filter_by(category_name=name).first():
                session.add(Category(category_name=name, description=description, is_active=True))

        session.commit()
        click.echo("Seed completed: roles, internal accounts and default categories are ready.")
        if os.getenv("SEED_ADMIN_PASSWORD") or os.getenv("SEED_STAFF_PASSWORD"):
            click.echo("Logins -> admin / user1 / user2 (passwords taken from SEED_* environment variables).")
        else:
            click.echo("Default logins -> admin/Admin@123, user1/User@123, user2/User@123 (change them in production).")
