"""Enquiry controller — list, create, view, edit, assign, status, remarks, attachments."""
from sqlalchemy import func

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, send_file, url_for)
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Category, Enquiry, EnquiryAttachment, User
from app.services import assignment_service
from app.services import enquiry_service as svc
from app.utils.decorators import login_required
from app.utils.helpers import get_client_ip, log_activity
from app.utils.validators import validate_enquiry_payload

enquiry_bp = Blueprint("enquiry", __name__)

# The enquiry list shows five records per page with a five-page window
# (5 × 5) — compact, scannable and consistent across screens.
ENQUIRIES_PER_PAGE = 5


def _get_enquiry_or_404(enquiry_id: int) -> Enquiry:
    enquiry = db.session.get(Enquiry, enquiry_id)
    if not enquiry:
        abort(404)
    if not svc.can_view(current_user, enquiry):
        abort(403)
    return enquiry


def _categories_for_form():
    return Category.query.filter_by(is_active=True).order_by(Category.category_name).all()


def _employees():
    return (
        User.query.join(User.role)
        .filter(User.is_active.is_(True))
        .order_by(User.username).all()
    )


@enquiry_bp.route("/")
@login_required
def list_enquiries():
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = ENQUIRIES_PER_PAGE

    filters = {
        "q": request.args.get("q", ""),
        "status": request.args.get("status", ""),
        "category_id": int(request.args["category_id"])
        if (request.args.get("category_id") or "").isdigit() else None,
        "source": request.args.get("source", ""),
        "assigned_to_user_id": int(request.args["assigned_to_user_id"])
        if (request.args.get("assigned_to_user_id") or "").isdigit() else None,
    }

    categories = _categories_for_form()
    employees = _employees()

    query = svc.apply_filters(svc.scoped_enquiries(current_user), filters)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    # A stale ?page= beyond the last page snaps back to the last real page.
    if not pagination.items and pagination.pages and page > pagination.pages:
        pagination = query.paginate(page=pagination.pages, per_page=per_page,
                                    error_out=False)

    # Scope-wide counts for the header chips (open / resolved / total).
    scoped = svc.scoped_enquiries(current_user)
    status_counts_map = dict(
        scoped.with_entities(Enquiry.status, func.count(Enquiry.enquiry_id))
        .group_by(Enquiry.status).all())
    total_all = sum(status_counts_map.values())
    closed_like = (status_counts_map.get("Resolved", 0)
                   + status_counts_map.get("Closed", 0))
    open_count = total_all - closed_like - status_counts_map.get("Rejected", 0)

    return render_template(
        "enquiries/list.html",
        pagination=pagination,
        enquiries=pagination.items,
        categories=categories,
        employees=employees,
        statuses=svc.ENQUIRY_STATUSES,
        total_all=total_all,
        open_count=open_count,
        resolved_count=closed_like,
        per_page=per_page,
        filters=filters,
    )


@enquiry_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    """New Enquiry form. Validation failures re-render the form with every
    typed value preserved — a user never loses work because of one mistake."""
    if request.method == "POST":
        data, errors = validate_enquiry_payload(request.form, require_email=True)
        if not errors:
            try:
                source = "ADMIN" if current_user.is_admin else "USER"
                enquiry = svc.create_enquiry(
                    data, source=source, created_by=current_user,
                    ip_address=get_client_ip(),
                )
                db.session.commit()
                flash(f"Enquiry {enquiry.enquiry_number} created successfully.", "success")
                return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))
            except svc.EnquiryValidationError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
        else:
            for error in errors:
                flash(error, "danger")
        return render_template(
            "enquiries/form.html",
            enquiry=None,
            categories=_categories_for_form(),
            form=request.form,
        ), 400

    return render_template(
        "enquiries/form.html",
        enquiry=None,
        categories=_categories_for_form(),
        form=None,
    )


def _detail_context(enquiry: Enquiry) -> dict:
    """Everything the detail template needs (also reused on edit failures)."""
    current_assignment = next(
        (a for a in enquiry.assignments if a.is_current), None)
    allowed = svc.allowed_statuses_for(enquiry, current_user)
    return {
        "enquiry": enquiry,
        "categories": _categories_for_form(),
        "employees": _employees(),
        "statuses": svc.ENQUIRY_STATUSES,
        "allowed_new_statuses": sorted(allowed),
        "admin_only_statuses": svc.ADMIN_ONLY_STATUSES,
        "can_edit": svc.can_edit(current_user, enquiry),
        "can_assign": svc.can_assign(current_user),
        "can_update_status": bool(allowed),
        "current_assignment": current_assignment,
    }


@enquiry_bp.route("/<int:enquiry_id>")
@login_required
def detail(enquiry_id: int):
    enquiry = _get_enquiry_or_404(enquiry_id)
    return render_template("enquiries/detail.html", **_detail_context(enquiry))


@enquiry_bp.route("/<int:enquiry_id>/popup")
@login_required
def details_popup(enquiry_id: int):
    """Fragment for the details popup — details, status form or edit form,
    all role-aware, without leaving the page.

    `edit=1` marks popups opened from an Enquiry No. click: those carry the
    "Edit Enquiry" action in the footer (subject to edit permission).
    """
    enquiry = _get_enquiry_or_404(enquiry_id)
    view = request.args.get("view", "details")
    if view not in ("details", "status", "edit", "manage"):
        view = "details"
    if view == "edit" and not svc.can_edit(current_user, enquiry):
        abort(403)
    if view == "status" and not svc.allowed_statuses_for(enquiry, current_user):
        abort(403)
    if view == "manage" and not svc.can_assign(current_user):
        abort(403)
    context = _detail_context(enquiry)
    context["view"] = view
    context["with_edit"] = (request.args.get("edit") == "1"
                            and svc.can_edit(current_user, enquiry))
    return render_template("enquiries/_popup.html", **context)


def _document_errors(files) -> list[str]:
    """Pre-validate every uploaded document (type, size, emptiness) before
    anything is written — the whole edit stays atomic."""
    allowed = current_app.config["ALLOWED_EXTENSIONS"]
    max_size = current_app.config["MAX_FILE_SIZE_MB"] * 1024 * 1024
    max_mb = current_app.config["MAX_FILE_SIZE_MB"]
    errors = []
    for f in files:
        name = f.filename or ""
        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if not suffix or suffix not in allowed:
            errors.append(f"'{name}': this file type is not allowed.")
            continue
        size = f.seek(0, 2)
        f.seek(0)
        if size == 0:
            errors.append(f"'{name}': the file is empty.")
        elif size > max_size:
            errors.append(f"'{name}': exceeds the {max_mb} MB limit.")
    return errors


@enquiry_bp.route("/<int:enquiry_id>/edit", methods=["POST"])
@login_required
def edit(enquiry_id: int):
    enquiry = _get_enquiry_or_404(enquiry_id)
    if not svc.can_edit(current_user, enquiry):
        abort(403)

    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    with_edit = (request.form.get("popup_edit") == "1"
                 and svc.can_edit(current_user, enquiry))
    documents = [f for f in request.files.getlist("documents") if f and f.filename]
    data, errors = validate_enquiry_payload(request.form)

    if not errors and documents:
        errors = _document_errors(documents)

    # "Remove" checkboxes for existing documents. IDs are filtered to this
    # enquiry and to files the user may delete (Admin or the uploader), so a
    # tampered form can never strip someone else's attachment.
    remove_ids = {int(v) for v in request.form.getlist("remove_documents")
                  if str(v).isdigit()}
    removed = 0

    if not errors:
        try:
            svc.update_enquiry(enquiry, data, current_user, ip_address=get_client_ip())
            for f in documents:
                svc.save_attachment(enquiry, f, current_user,
                                    ip_address=get_client_ip())
            if remove_ids:
                removable = (
                    db.session.query(EnquiryAttachment)
                    .filter(EnquiryAttachment.attachment_id.in_(remove_ids),
                            EnquiryAttachment.enquiry_id == enquiry.enquiry_id)
                    .all()
                )
                for attachment in removable:
                    if (current_user.is_admin
                            or attachment.uploaded_by_user_id == current_user.user_id):
                        svc.delete_attachment(attachment, current_user)
                        removed += 1
            db.session.commit()
        except svc.EnquiryValidationError as exc:
            db.session.rollback()
            errors = [str(exc)]

    if errors:
        if ajax:
            # Keep the popup open on the edit form with the errors and every
            # typed value preserved.
            context = _detail_context(enquiry)
            context.update(view="edit", form_errors=errors, form=request.form,
                           with_edit=with_edit)
            return render_template("enquiries/_popup.html", **context), 400
        context = _detail_context(enquiry)
        context.update(form=request.form, form_errors=errors)
        return render_template("enquiries/detail.html", **context), 400

    notes = []
    if documents:
        notes.append(f"{len(documents)} document(s) attached.")
    if removed:
        notes.append(f"{removed} document(s) removed.")
    saved_note = (" " + " ".join(notes)) if notes else ""
    if ajax:
        context = _detail_context(enquiry)
        context.update(view="details",
                       popup_message=f"Enquiry details updated.{saved_note}",
                       with_edit=with_edit)
        return render_template("enquiries/_popup.html", **context)

    flash(f"Enquiry details updated.{saved_note}", "success")
    return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))


@enquiry_bp.route("/<int:enquiry_id>/assign", methods=["POST"])
@login_required
def assign(enquiry_id: int):
    enquiry = _get_enquiry_or_404(enquiry_id)
    if not svc.can_assign(current_user):
        abort(403)

    target_id = (request.form.get("assigned_to_user_id") or "").strip()
    if not target_id.isdigit():
        flash("Please select an employee to assign.", "danger")
        return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))

    try:
        assignment_service.assign_enquiry(
            enquiry, int(target_id), assigned_by=current_user,
            remarks=request.form.get("assignment_remarks", ""),
            ip_address=get_client_ip(),
        )
        db.session.commit()
        flash("Enquiry assignment saved.", "success")
    except (svc.EnquiryValidationError, PermissionError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))


@enquiry_bp.route("/<int:enquiry_id>/status", methods=["POST"])
@login_required
def change_status(enquiry_id: int):
    enquiry = _get_enquiry_or_404(enquiry_id)
    new_status = (request.form.get("status") or "").strip()
    remarks = (request.form.get("status_remarks") or "").strip()
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    with_edit = (request.form.get("popup_edit") == "1"
                 and svc.can_edit(current_user, enquiry))

    try:
        svc.update_status(enquiry, new_status, remarks, current_user,
                          ip_address=get_client_ip())
        db.session.commit()
    except (svc.EnquiryValidationError, svc.EnquiryPermissionError) as exc:
        db.session.rollback()
        if ajax:
            # Keep the popup open on the status form with the error.
            context = _detail_context(enquiry)
            context.update(view="status", form_errors=[str(exc)],
                           form=request.form, with_edit=with_edit)
            return render_template("enquiries/_popup.html", **context), 400
        flash(str(exc), "danger")
        return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))

    if ajax:
        # Success inside the popup: return the refreshed details fragment.
        context = _detail_context(enquiry)
        context.update(view="details",
                       popup_message=f"Status updated to {new_status}.",
                       with_edit=with_edit)
        return render_template("enquiries/_popup.html", **context)

    flash(f"Status updated to {new_status}.", "success")
    return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))


@enquiry_bp.route("/<int:enquiry_id>/manage", methods=["POST"])
@login_required
def manage_enquiry(enquiry_id: int):
    """One admin panel for one enquiry: assignment, status change and a
    single remark — applied together or partially, always atomically.

    Assignment runs first (so a New/Pending/Reopened enquiry steps into
    "Assigned"), then the chosen status transition; a remark typed in the
    shared box travels with both actions, and a lone remark is recorded
    exactly once.
    """
    enquiry = _get_enquiry_or_404(enquiry_id)
    if not svc.can_assign(current_user):
        abort(403)

    assignee_raw = (request.form.get("assigned_to_user_id") or "").strip()
    new_status = (request.form.get("status") or "").strip()
    remarks = (request.form.get("manage_remarks") or "").strip()
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    with_edit = (request.form.get("popup_edit") == "1"
                 and svc.can_edit(current_user, enquiry))

    try:
        actions = []

        # 1) Assignment (optional).
        if assignee_raw:
            if not assignee_raw.isdigit():
                raise svc.EnquiryValidationError("Please select an employee to assign.")
            if int(assignee_raw) != enquiry.assigned_to_user_id:
                assignment_service.assign_enquiry(
                    enquiry, int(assignee_raw), assigned_by=current_user,
                    remarks=remarks, ip_address=get_client_ip())
                actions.append("assignment saved")

        # 2) Status change (optional) — against the possibly new status.
        if new_status and new_status != enquiry.status:
            svc.update_status(enquiry, new_status, remarks, current_user,
                              ip_address=get_client_ip())
            actions.append(f"status updated to {new_status}")

        # 3) A lone remark — recorded once, without touching status.
        if remarks and not actions:
            svc.add_remark(enquiry, remarks, current_user,
                           ip_address=get_client_ip())
            actions.append("remark added")

        if not actions:
            raise svc.EnquiryValidationError(
                "Choose an employee, pick a new status, or write a remark — "
                "there is nothing to apply yet.")

        db.session.commit()
    except (svc.EnquiryValidationError, svc.EnquiryPermissionError,
            PermissionError) as exc:
        db.session.rollback()
        if ajax:
            # Keep the panel open with every typed value preserved.
            context = _detail_context(enquiry)
            context.update(view="manage", form_errors=[str(exc)],
                           form=request.form, with_edit=with_edit)
            return render_template("enquiries/_popup.html", **context), 400
        flash(str(exc), "danger")
        return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))

    message = "Enquiry updated — " + ", ".join(actions) + "."
    if ajax:
        context = _detail_context(enquiry)
        context.update(view="details", popup_message=message, with_edit=with_edit)
        return render_template("enquiries/_popup.html", **context)

    flash(message, "success")
    return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))


@enquiry_bp.route("/<int:enquiry_id>/remark", methods=["POST"])
@login_required
def add_remark(enquiry_id: int):
    enquiry = _get_enquiry_or_404(enquiry_id)
    try:
        svc.add_remark(enquiry, request.form.get("remark", ""), current_user,
                       ip_address=get_client_ip())
        db.session.commit()
        flash("Remark added.", "success")
    except (svc.EnquiryValidationError, svc.EnquiryPermissionError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))


@enquiry_bp.route("/<int:enquiry_id>/attachments", methods=["POST"])
@login_required
def upload_attachment(enquiry_id: int):
    enquiry = _get_enquiry_or_404(enquiry_id)
    try:
        svc.save_attachment(enquiry, request.files.get("attachment"), current_user,
                            ip_address=get_client_ip())
        db.session.commit()
        flash("Attachment uploaded.", "success")
    except (svc.EnquiryValidationError, svc.EnquiryPermissionError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("enquiry.detail", enquiry_id=enquiry.enquiry_id))


@enquiry_bp.route("/attachments/<int:attachment_id>/download")
@login_required
def download_attachment(attachment_id: int):
    attachment = db.session.get(EnquiryAttachment, attachment_id)
    if not attachment:
        abort(404)
    enquiry = _get_enquiry_or_404(attachment.enquiry_id)  # authorisation check
    try:
        log_activity(
            "ATTACHMENT_DOWNLOADED",
            f"Attachment '{attachment.original_file_name}' downloaded from "
            f"{enquiry.enquiry_number}",
            user=current_user, enquiry=enquiry, ip_address=get_client_ip(),
        )
        db.session.commit()
        return send_file(attachment.file_path, as_attachment=True,
                         download_name=attachment.original_file_name)
    except (FileNotFoundError, OSError):
        abort(404)


@enquiry_bp.route("/attachments/<int:attachment_id>/delete", methods=["POST"])
@login_required
def delete_attachment(attachment_id: int):
    attachment = db.session.get(EnquiryAttachment, attachment_id)
    if not attachment:
        abort(404)
    _get_enquiry_or_404(attachment.enquiry_id)
    try:
        svc.delete_attachment(attachment, current_user)
        db.session.commit()
        flash("Attachment deleted.", "success")
    except svc.EnquiryPermissionError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("enquiry.detail", enquiry_id=attachment.enquiry_id))
