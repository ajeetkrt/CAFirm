"""Access-control decorators used by internal controllers."""
from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def admin_required(view):
    """Allow only logged-in users whose role is Admin."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def role_required(*role_names):
    """Allow only logged-in users holding one of the given role names."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role_name not in role_names:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
