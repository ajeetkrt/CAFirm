"""Public controller — landing page, visitor enquiry form and status tracking.

The tracking page is action-based:
    action="status"        (default — Enquiry Number OR mobile, any one is enough)
    action="mobile_lookup" (find enquiries by mobile number)
"""
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.services import procedure_service as proc
from app.services.procedure_service import ProcedureError
from app.utils.helpers import get_client_ip, log_activity, rate_limiter
from app.utils.validators import validate_enquiry_payload

public_bp = Blueprint("public", __name__)


# Practice announcements shown in the "Latest Notes" ticker on the home page.
# Keep entries short and update them as the practice schedule changes.
LATEST_NOTES = [
    "GST returns for the month are due by the 20th — file early to avoid late fees.",
    "Advance tax instalment dates: 15 Jun / 15 Sep / 15 Dec / 15 Mar — plan your payments.",
    "ITR filing season is open — keep Form 16, bank statements and investment proofs ready.",
    "Company annual filings (AOC-4 / MGT-7) are due by 29 October — contact us in advance.",
    "TDS returns are due quarterly — share challans and ledger details on time.",
    "Track any enquiry 24x7 with just your Enquiry Number — no token needed.",
    "Misplaced your number? Find every enquiry with your registered mobile number.",
]


@public_bp.route("/")
def index():
    """Public landing page — live statistics and quick actions via procedures."""
    stats = proc.get_public_stats()
    db.session.commit()
    return render_template("public/index.html", stats=stats, latest_notes=LATEST_NOTES)


@public_bp.route("/public/enquiry", methods=["GET", "POST"])
def submit_enquiry():
    """Public enquiry submission — no account required."""
    categories = proc.list_active_categories()["items"]
    db.session.commit()
    category_options = [
        {"category_id": c["category_id"],
         "category_name": c["category_name"],
         "description": c["description"]}
        for c in categories
    ]

    if request.method == "POST":
        ip_address = get_client_ip()
        rate_key = f"public_submit:{ip_address}"
        limit = current_app.config["PUBLIC_SUBMIT_RATE_LIMIT"]
        window = current_app.config["PUBLIC_SUBMIT_RATE_WINDOW"]
        if rate_limiter.is_blocked(rate_key, limit, window):
            log_activity("RATE_LIMITED",
                         "Public enquiry form submissions blocked for this network",
                         ip_address=ip_address)
            flash("Too many submissions from this network. Please try again later.", "danger")
            return render_template("public/enquiry_form.html",
                                   categories=category_options), 429

        # Honeypot: real users never fill the hidden "website" field.
        honeypot = (request.form.get("website") or "").strip()
        if honeypot:
            log_activity("HONEYPOT_REJECT",
                         "Hidden honeypot field filled on the public enquiry form",
                         ip_address=ip_address)
            flash("Submission rejected.", "danger")
            return render_template("public/enquiry_form.html",
                                   categories=category_options), 400

        data, errors = validate_enquiry_payload(request.form, require_email=True)
        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("public/enquiry_form.html",
                                   categories=category_options, form=request.form), 400

        try:
            result = proc.submit_visitor_enquiry(data, ip_address=ip_address)
        except ProcedureError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
            return render_template("public/enquiry_form.html",
                                   categories=category_options, form=request.form), 400

        rate_limiter.record(rate_key, window)
        db.session.commit()

        # Confirmation page shows the Enquiry Number — all the visitor needs.
        return render_template("public/enquiry_success.html",
                               enquiry_number=result["enquiry_number"])

    return render_template("public/enquiry_form.html", categories=category_options)


@public_bp.route("/public/track", methods=["GET", "POST"])
def track():
    """Visitor tracking — any one identifier (Enquiry Number OR mobile)."""
    enquiry = None
    history = []
    mobile_results = None
    mobile_total = None
    status_view = False

    def _show_status(result):
        nonlocal enquiry, history, status_view
        enquiry = result["enquiry"]
        history = result["history"]
        status_view = True

    if request.method == "POST":
        ip_address = get_client_ip()
        rate_key = f"track:{ip_address}"
        limit = current_app.config["TRACK_RATE_LIMIT"]
        window = current_app.config["TRACK_RATE_WINDOW"]
        if rate_limiter.is_blocked(rate_key, limit, window):
            log_activity("RATE_LIMITED",
                         "Public tracking attempts blocked for this network",
                         ip_address=ip_address)
            flash("Too many tracking attempts. Please try again later.", "danger")
            return render_template("public/track.html"), 429

        rate_limiter.record(rate_key, window)
        action = (request.form.get("action") or "status").strip()
        enquiry_number = (request.form.get("enquiry_number") or "").strip()
        mobile = (request.form.get("mobile") or "").strip()

        try:
            if action == "mobile_lookup":
                result = proc.lookup_enquiries_by_mobile(mobile)
                mobile_results = result["items"]
                mobile_total = result["total"]
            else:
                # Any-one tracking: the Enquiry Number OR the mobile number.
                if enquiry_number:
                    _show_status(proc.track_status_by_reference(
                        enquiry_number, ip_address=ip_address))
                elif mobile:
                    result = proc.lookup_enquiries_by_mobile(mobile)
                    mobile_results = result["items"]
                    mobile_total = result["total"]
                else:
                    flash("Enter your Enquiry Reference Number or your registered "
                          "mobile number — any one is enough.", "warning")
        except ProcedureError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        else:
            db.session.commit()
    else:
        # Deep link: /public/track?number=CAF-... shows the status view directly.
        prefill = (request.args.get("number") or "").strip()
        if prefill:
            try:
                _show_status(proc.track_status_by_reference(prefill, ip_address=get_client_ip()))
                db.session.commit()
            except ProcedureError as exc:
                db.session.rollback()
                flash(exc.message, "danger")

    return render_template(
        "public/track.html",
        enquiry=enquiry,
        history=history,
        status_view=status_view,
        mobile_results=mobile_results,
        mobile_total=mobile_total,
        prefill_number=(request.form.get("enquiry_number") if request.method == "POST"
                        else request.args.get("number")) or "",
        prefill_mobile=(request.form.get("mobile") if request.method == "POST" else "") or "",
    )


@public_bp.route("/public/track/enquiry", methods=["GET"])
def track_enquiry_redirect():
    """Convenience deep link from the mobile lookup to the reference form."""
    number = (request.args.get("number") or "").strip()
    if not number:
        return redirect(url_for("public.track"))
    return redirect(url_for("public.track", number=number))
