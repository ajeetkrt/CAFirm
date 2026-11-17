"""Visitor tracking tests — tokenless (any-one identifier) tracking rules."""
import re

from app.models import User


def test_public_flow_end_to_end(client, app, db_session):
    """Visitor submits via HTTP, receives the number, then tracks by number alone."""
    response = client.post("/public/enquiry", data={
        "website": "",
        "full_name": "Public Visitor",
        "mobile_number": "9123456789",
        "email": "visitor@example.com",
        "state": "Delhi",
        "district": "New Delhi",
        "category_id": "1",
        "subject": "Public flow subject",
        "description": "Testing the complete visitor submission flow.",
    })
    assert response.status_code == 200
    assert b"Enquiry Number" in response.data
    assert b"Tracking Token" not in response.data          # token system removed

    number = re.search(rb"CAF-\d{4}-\d{6}", response.data).group(0).decode()

    # Unified form POST — the number alone is enough (no token field).
    page = client.post("/public/track", data={"enquiry_number": number}).data
    assert b"Public flow subject" in page
    assert b"Public Visitor" not in page  # visitor name is not exposed on tracking page

    # Deep link renders the status view directly.
    page = client.get(f"/public/track?number={number}").data
    assert b"Public flow subject" in page


def test_case_insensitive_enquiry_number(app, client, db_session, enquiry_factory):
    enquiry = enquiry_factory(source="VISITOR", subject="Case check subject")
    page = client.get(f"/public/track?number={enquiry.enquiry_number.lower()}").data
    assert b"Case check subject" in page


def test_unknown_number_shows_friendly_error(client):
    page = client.post("/public/track", data={
        "enquiry_number": "CAF-1999-999999"}).data
    assert b"No matching enquiry found" in page


def test_mobile_lookup_flow(client, app, db_session):
    """Tracking by mobile: the lookup lists the visitor's enquiries with deep links."""
    client.post("/public/enquiry", data={
        "website": "",
        "full_name": "Mobile Visitor",
        "mobile_number": "9555500000",
        "email": "mobile@example.com",
        "category_id": "1",
        "subject": "Mobile lookup subject",
        "description": "Enquiry used to verify tracking by mobile number.",
    })
    page = client.post("/public/track", data={
        "action": "mobile_lookup", "mobile": "9555500000"}).data
    assert b"Mobile lookup subject" in page
    assert b"View Status" in page


def test_mobile_lookup_unknown_number(client):
    page = client.post("/public/track", data={
        "action": "mobile_lookup", "mobile": "9000000000"}).data
    assert b"No enquiries were found for this mobile number" in page


def test_honeypot_rejects_bots(client):
    response = client.post("/public/enquiry", data={
        "website": "http://spam.example",
        "full_name": "Bot Bot",
        "mobile_number": "9000000000",
        "category_id": "1",
        "subject": "Spam subject",
        "description": "Spammy description text here.",
    })
    assert response.status_code == 400


def test_status_view_without_token(app, client, db_session, enquiry_factory):
    """Any-one tracking: the reference number alone shows the status view."""
    enquiry = enquiry_factory(source="VISITOR", subject="Tokenless status check")
    page = client.get(f"/public/track?number={enquiry.enquiry_number}").data
    assert b"Tokenless status check" in page
    assert b"progress-steps" in page
    assert b"CAF-" in page


def test_status_view_shows_assigned_officer(app, client, db_session, enquiry_factory):
    """The status panel names the officer handling the enquiry."""
    enquiry = enquiry_factory(source="VISITOR", subject="Officer visibility check")
    user = User.query.filter(User.username != "admin").first() or User.query.first()
    enquiry.assigned_to_user_id = user.user_id
    db_session.commit()

    page = client.get(f"/public/track?number={enquiry.enquiry_number}").data
    assert b"Assigned Officer" in page
    assert user.full_name.encode() in page
    assert (user.email or "Contact via the office").encode() in page


def test_status_view_without_assignment_shows_queue_note(app, client, db_session, enquiry_factory):
    """Unassigned enquiries show a polite in-queue note instead of an officer."""
    enquiry = enquiry_factory(source="VISITOR", subject="Queue note check")
    page = client.get(f"/public/track?number={enquiry.enquiry_number}").data
    assert b"Not yet assigned" in page
    assert b"Assigned Officer" not in page


def test_public_history_hides_remarks(app, client, db_session, enquiry_factory):
    """The public tracking page shows status history but NEVER internal remarks."""
    from app.services import enquiry_service as svc
    admin = User.query.filter_by(username="admin").first()
    enquiry = enquiry_factory(source="VISITOR", subject="Remarks privacy check")
    svc.update_status(enquiry, "In Progress", "internal note hidden", admin)
    db_session.commit()

    page = client.get(f"/public/track?number={enquiry.enquiry_number}").data
    assert b"In Progress" in page                      # status is public
    assert b"internal note hidden" not in page          # remarks are private
