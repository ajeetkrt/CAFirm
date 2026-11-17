"""Authentication controller — login and logout."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.models import User
from app.utils.helpers import get_client_ip, log_activity, rate_limiter

auth_bp = Blueprint("auth", __name__)


def _dashboard_endpoint_for(user) -> str:
    return "admin.dashboard" if user.is_admin else "user.dashboard"


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(_dashboard_endpoint_for(current_user)))

    if request.method == "POST":
        ip_address = get_client_ip()
        rate_key = f"login:{ip_address}"
        limit = current_app.config["LOGIN_RATE_LIMIT"]
        window = current_app.config["LOGIN_RATE_WINDOW"]

        if rate_limiter.is_blocked(rate_key, limit, window):
            log_activity("LOGIN_BLOCKED", "Too many failed login attempts", ip_address=ip_address)
            flash("Too many failed login attempts. Please try again later.", "danger")
            return render_template("auth/login.html"), 429

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()

        if user and user.is_active and user.check_password(password):
            login_user(user, remember=False)
            rate_limiter.reset(rate_key)
            log_activity("USER_LOGIN", f"User '{username}' logged in", user=user)
            flash(f"Welcome, {user.full_name}.", "success")
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for(_dashboard_endpoint_for(user)))

        rate_limiter.record(rate_key, window)
        log_activity("LOGIN_FAILED", f"Failed login attempt for '{username}'",
                     ip_address=ip_address)
        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():
    log_activity("USER_LOGOUT", f"User '{current_user.username}' logged out", user=current_user)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("public.index"))
