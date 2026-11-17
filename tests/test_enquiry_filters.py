"""Functional checks for the enquiries list page — filters, pagination, scope."""
from datetime import datetime

import pytest
from sqlalchemy import text

from app.models import User


@pytest.fixture(autouse=True)
def _clean_enquiry_tables(db_session):
    """The app DB is session-scoped — wipe ALL seeded rows so every test starts
    from the exact same state, then reseed the base accounts/categories."""
    db_session.execute(text("""
        TRUNCATE enquiry_status_history, enquiry_assignments,
                 enquiry_attachments, activity_logs, enquiries,
                 users, roles, enquiry_categories
        RESTART IDENTITY CASCADE
    """))
    db_session.commit()
    from conftest import seed_base_data
    seed_base_data(db_session)
    yield


def _make(factory, db_session, **overrides):
    """Create one enquiry with optional status / assignee / created_at."""
    status = overrides.pop("status", None)
    assigned_to = overrides.pop("assigned_to", None)
    created_at = overrides.pop("created_at", None)
    enquiry = factory(**overrides)
    if status:
        enquiry.status = status
    if assigned_to is not None:
        enquiry.assigned_to_user_id = assigned_to
    if created_at:
        enquiry.created_at = created_at
    db_session.commit()
    return enquiry


def _seed(factory, db_session):
    """12 enquiries spread across statuses, categories, sources and dates."""
    user1 = User.query.filter_by(username="user1").one()
    user2 = User.query.filter_by(username="user2").one()

    _make(factory, db_session, full_name="Ravi Kumar", mobile_number="9000000001",
          subject="GST invoice mismatch", category_id=1, status="New",
          created_at=datetime(2026, 11, 1, 10, 0))
    _make(factory, db_session, full_name="Anita Sharma", mobile_number="9000000002",
          subject="Refund pending", category_id=2, status="Pending",
          assigned_to=user2.user_id, created_at=datetime(2026, 11, 3, 10, 0))
    _make(factory, db_session, full_name="Ravi Kumar", mobile_number="9000000003",
          subject="Portal not working", category_id=2, source="USER",
          created_by=user1, status="Resolved", assigned_to=user1.user_id,
          created_at=datetime(2026, 11, 5, 10, 0))
    _make(factory, db_session, full_name="Mohit Verma", mobile_number="9000000004",
          subject="Registration delay", category_id=1, status="In Progress",
          assigned_to=user2.user_id, created_at=datetime(2026, 11, 5, 11, 0))
    _make(factory, db_session, full_name="Sneha Patel", mobile_number="9000000005",
          subject="Certificate request", category_id=1, status="Closed",
          created_at=datetime(2026, 11, 6, 10, 0))
    for i in range(7):  # 7 "New" fillers -> 12 enquiries total
        _make(factory, db_session, full_name=f"Filler Person {i}",
              mobile_number=f"90000001{i:02d}", subject=f"Filler subject {i}",
              status="New", created_at=datetime(2026, 11, 7, 9, i))


def _rows(html):
    return html.count("class=\"fw-semibold enquiry-link\"")


def test_five_per_page_and_pagination_window(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    html = admin_client.get("/enquiries/").get_data(as_text=True)

    assert _rows(html) == 5                       # 5 rows per page
    assert "Showing <strong>1–5</strong>" in html  # range line
    assert "of <strong>12</strong>" in html
    assert "Page 1 of 3" in html
    # window shows up to 5 numbered pages, no ellipsis needed yet
    for page in ("1", "2", "3"):
        assert f">{page}</a>" in html
    assert "page-ellipsis" not in html


def test_page_two_and_last_page_counts(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    assert _rows(admin_client.get("/enquiries/?page=2").get_data(as_text=True)) == 5
    last = admin_client.get("/enquiries/?page=3").get_data(as_text=True)
    assert _rows(last) == 2
    assert "Showing <strong>11–12</strong>" in last


def test_stale_page_snaps_to_last(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    html = admin_client.get("/enquiries/?page=99").get_data(as_text=True)
    assert _rows(html) == 2
    assert "Page 3 of 3" in html


def test_search_matches_number_name_mobile_subject(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    # by name
    html = admin_client.get("/enquiries/?q=Anita").get_data(as_text=True)
    assert _rows(html) == 1 and "Anita Sharma" in html
    # by mobile fragment
    html = admin_client.get("/enquiries/?q=9000000004").get_data(as_text=True)
    assert _rows(html) == 1 and "Mohit Verma" in html
    # by subject fragment
    html = admin_client.get("/enquiries/?q=invoice").get_data(as_text=True)
    assert _rows(html) == 1 and "GST invoice mismatch" in html
    # no match
    html = admin_client.get("/enquiries/?q=zzzznope").get_data(as_text=True)
    assert _rows(html) == 0 and "No enquiries match" in html


def test_status_filter(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    html = admin_client.get("/enquiries/?status=Resolved").get_data(as_text=True)
    assert _rows(html) == 1 and "Ravi Kumar" in html
    html = admin_client.get("/enquiries/?status=Pending").get_data(as_text=True)
    assert _rows(html) == 1 and "Anita Sharma" in html


def test_category_filter(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    html = admin_client.get("/enquiries/?category_id=2").get_data(as_text=True)
    assert _rows(html) == 2 and "Refund pending" in html and "Portal not working" in html


def test_source_filter(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    html = admin_client.get("/enquiries/?source=USER").get_data(as_text=True)
    assert _rows(html) == 1 and "Portal not working" in html


def test_assigned_to_filter(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    user2 = User.query.filter_by(username="user2").one()
    html = admin_client.get(f"/enquiries/?assigned_to_user_id={user2.user_id}").get_data(as_text=True)
    assert _rows(html) == 2
    assert "Anita Sharma" in html and "Mohit Verma" in html


def test_combined_filters_and_minimal_bar(admin_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    html = admin_client.get("/enquiries/?status=Pending&category_id=2").get_data(as_text=True)
    assert _rows(html) == 1 and "Anita Sharma" in html
    # minimal single-row bar: no chips, no buttons, no date inputs
    assert 'class="filter-chip"' not in html
    assert "Clear all" not in html and "Apply" not in html and "Reset" not in html
    assert 'type="date"' not in html
    assert 'autocomplete="off"' in html


def test_apply_filters_date_bound_still_correct_for_reports(enquiry_factory, db_session):
    """Regression: 'to' must include the whole chosen day, nothing more.
    (The list page no longer exposes dates, but reports still filter by them.)"""
    from app.services import enquiry_service as svc
    _seed(enquiry_factory, db_session)
    query = svc.apply_filters(svc.scoped_enquiries(None), {
        "date_from": datetime(2026, 11, 1),      # midnight of the chosen day
        "date_to": datetime(2026, 11, 2),        # next-day midnight (end_of_day)
    })
    assert query.count() == 1


def test_employee_sees_only_own_scope(user1_client, enquiry_factory, db_session):
    _seed(enquiry_factory, db_session)
    # user1: created e3 (source USER) only; assigned none except own enquiry
    html = user1_client.get("/enquiries/").get_data(as_text=True)
    assert "Ravi Kumar" in html          # own enquiry
    assert "Anita Sharma" not in html    # assigned to user2, created by admin
    assert "Filler Person" not in html   # admin-created, unassigned
    assert "of <strong>1</strong>" in html
