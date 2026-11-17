"""Server-side validation helpers.

Every write operation validates its payload through this module — client-side
HTML validation is treated only as a convenience.
"""
import re
from pathlib import Path

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
MOBILE_RE = re.compile(r"^\+?[0-9]{10,15}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def clean_text(value) -> str:
    """Coerce any JSON scalar (str/int/bool) into trimmed text safely."""
    if value is None:
        return ""
    return str(value).strip()


def validate_full_name(value: str) -> str | None:
    value = clean_text(value)
    if not (2 <= len(value) <= 100):
        return "Full name must be between 2 and 100 characters."
    return None


def validate_mobile(value: str) -> str | None:
    value = re.sub(r"[\s\-()]", "", clean_text(value))
    if not MOBILE_RE.fullmatch(value):
        return "Mobile number must contain 10 to 15 digits (a leading + is allowed)."
    return None


def validate_email(value: str, required: bool = False) -> str | None:
    value = clean_text(value)
    if not value:
        return "Email is required." if required else None
    if len(value) > 120 or not EMAIL_RE.fullmatch(value):
        return "Please enter a valid email address."
    return None


def validate_enquiry_payload(form, require_all: bool = True, require_email: bool = False) -> tuple[dict, list[str]]:
    """Validate shared enquiry fields coming from any enquiry form.

    require_email=True is used by the public enquiry form, where the email
    address is mandatory; staff and API submissions keep it optional.
    """
    errors: list[str] = []
    data = {
        "full_name": clean_text(form.get("full_name")),
        "mobile_number": re.sub(r"[\s\-()]", "", clean_text(form.get("mobile_number"))),
        "email": clean_text(form.get("email")),
        "state": clean_text(form.get("state"))[:50] or None,
        "district": clean_text(form.get("district"))[:50] or None,
        "category_id": clean_text(form.get("category_id")),
        "subject": clean_text(form.get("subject")),
        "description": clean_text(form.get("description")),
    }

    error = validate_full_name(data["full_name"])
    if error:
        errors.append(error)

    error = validate_mobile(data["mobile_number"])
    if error:
        errors.append(error)

    error = validate_email(data["email"], required=require_email)
    if error:
        errors.append(error)

    if not data["category_id"] or not str(data["category_id"]).isdigit():
        errors.append("Please select an enquiry category.")
    else:
        data["category_id"] = int(data["category_id"])

    if not (5 <= len(data["subject"]) <= 150):
        errors.append("Subject must be between 5 and 150 characters.")

    if not (10 <= len(data["description"]) <= 5000):
        errors.append("Description must be between 10 and 5000 characters.")

    if require_all:
        pass  # reserved for future per-source differences
    return data, errors


def validate_password(value: str) -> str | None:
    value = value or ""
    if len(value) < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r"[A-Za-z]", value) or not re.search(r"[0-9]", value):
        return "Password must contain both letters and numbers."
    return None


def validate_new_user_payload(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    data = {
        "username": clean_text(form.get("username")),
        "full_name": clean_text(form.get("full_name")),
        "email": clean_text(form.get("email")),
        "password": form.get("password") or "",
        "role_id": clean_text(form.get("role_id")),
    }
    if not USERNAME_RE.fullmatch(data["username"]):
        errors.append("Username must be 3-50 characters (letters, numbers, dot, dash, underscore).")
    error = validate_full_name(data["full_name"])
    if error:
        errors.append(error)
    error = validate_email(data["email"], required=True)
    if error:
        errors.append(error)
    error = validate_password(data["password"])
    if error:
        errors.append(error)
    if not data["role_id"].isdigit():
        errors.append("Please select a role.")
    else:
        data["role_id"] = int(data["role_id"])
    return data, errors


def validate_category_payload(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    data = {
        "category_name": clean_text(form.get("category_name")),
        "description": clean_text(form.get("description")) or None,
    }
    if not (3 <= len(data["category_name"]) <= 100):
        errors.append("Category name must be between 3 and 100 characters.")
    return data, errors


def is_allowed_file(filename: str, allowed_extensions: set[str]) -> bool:
    """Check the extension of an uploaded file against the allow-list."""
    return Path(filename).suffix.lower().lstrip(".") in allowed_extensions
