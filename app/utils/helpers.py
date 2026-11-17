"""Reusable helper utilities: logging of activities, IP extraction and rate limiting."""
import sys
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone

from flask import request

from app.extensions import db

# CSS badge class for each enquiry status (used by templates via context processor).
STATUS_BADGE_CLASSES = {
    "New": "status-new",
    "Pending": "status-pending",
    "Assigned": "status-assigned",
    "In Progress": "status-inprogress",
    "Resolved": "status-resolved",
    "Closed": "status-closed",
    "Rejected": "status-rejected",
    "Reopened": "status-reopened",
}


def get_client_ip() -> str:
    """Best-effort client IP; honours X-Forwarded-For (reverse proxy).

    Safe to call outside a request context (returns "unknown").
    """
    try:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.remote_addr or "unknown"
    except RuntimeError:  # no request context (CLI, tests, background work)
        return "unknown"


def log_activity(action, description="", user=None, enquiry=None, ip_address=None, commit=True):
    """Record an activity log entry. Never raises and never rolls back the
    caller's work — auditing must not break requests."""
    from flask import current_app

    try:
        from app.models import ActivityLog

        entry = ActivityLog(
            user_id=user.user_id if user is not None else None,
            enquiry_id=enquiry.enquiry_id if enquiry is not None else None,
            action=action,
            description=description,
            ip_address=ip_address or get_client_ip(),
        )
        # A savepoint keeps an audit failure from discarding the caller's
        # pending changes; only the audit entry itself is abandoned.
        with db.session.begin_nested():
            db.session.add(entry)
        if commit:
            db.session.commit()
        return entry
    except Exception as exc:  # noqa: BLE001 — audit failures must never break the request
        try:
            current_app.logger.warning("Activity log failed for action %s: %s", action, exc)
        except Exception:  # noqa: BLE001 — even the logger may be unavailable
            print(f"Activity log failed for action {action}: {exc}", file=sys.stderr)


class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Suitable for the local, single-process CAFirm deployment; requires no
    external services or network access.
    """

    def __init__(self) -> None:
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, window_seconds: int) -> None:
        now = datetime.now(timezone.utc).timestamp()
        events = self._events[key]
        while events and now - events[0] > window_seconds:
            events.popleft()

    def record(self, key: str, window_seconds: int) -> int:
        """Record an event for `key` and return the current count inside the window."""
        with self._lock:
            self._prune(key, window_seconds)
            self._events[key].append(datetime.now(timezone.utc).timestamp())
            return len(self._events[key])

    def count(self, key: str, window_seconds: int) -> int:
        with self._lock:
            self._prune(key, window_seconds)
            return len(self._events[key])

    def is_blocked(self, key: str, limit: int, window_seconds: int) -> bool:
        return self.count(key, window_seconds) >= limit

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


rate_limiter = RateLimiter()
