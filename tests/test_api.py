"""REST API (v1) tests — each endpoint delegates to one usp_* procedure."""
import pytest

from app.services import procedure_service as proc


def _submit_payload(**overrides):
    payload = {
        "full_name": "API Visitor",
        "mobile_number": "9876500011",
        "email": "apivisitor@example.com",
        "state": "Punjab",
        "district": "Ludhiana",
        "category_id": "1",
        "subject": "API submitted subject",
        "description": "Description submitted through the REST API surface.",
    }
    payload.update(overrides)
    return payload


def _submit_via_api(client):
    response = client.post("/api/v1/public/enquiries", json=_submit_payload())
    assert response.status_code == 201
    body = response.get_json()
    assert body["success"] is True
    assert "tracking_token" not in body["data"]             # token system removed
    return body["data"]["enquiry_number"]


def test_api_index_lists_endpoint_procedure_map(client):
    response = client.get("/api/v1/")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["api_version"] == "v1"
    procedures = {item["procedure"] for item in data["procedures"]}
    assert "usp_submit_visitor_enquiry" in procedures
    assert "usp_report_total_enquiries" in procedures
    assert all(p.startswith("usp_") for p in procedures)


def test_api_public_statistics_and_categories(client):
    stats = client.get("/api/v1/public/statistics").get_json()
    assert stats["success"] is True
    assert "total_enquiries" in stats["data"]
    assert "pending_enquiries" in stats["data"]
    assert "resolved_enquiries" in stats["data"]

    categories = client.get("/api/v1/public/categories").get_json()
    assert categories["success"] is True
    names = [c["category_name"] for c in categories["data"]["items"]]
    assert "GST Registration" in names
    assert "Old Category" not in names  # inactive categories stay hidden


def test_api_visitor_submit_and_track_by_reference(client):
    number = _submit_via_api(client)
    assert number.startswith("CAF-")

    ok = client.post("/api/v1/public/tracking/by-reference",
                     json={"enquiry_number": number})
    assert ok.status_code == 200
    enquiry = ok.get_json()["data"]["enquiry"]
    assert enquiry["subject"] == "API submitted subject"
    assert "full_name" not in enquiry  # visitor identity is not exposed publicly

    bad = client.post("/api/v1/public/tracking/by-reference",
                      json={"enquiry_number": "CAF-1999-999999"})
    assert bad.status_code == 404
    assert bad.get_json()["code"] == "NOT_FOUND"


def test_api_mobile_lookup(client):
    number = _submit_via_api(client)

    lookup = client.post("/api/v1/public/tracking/by-mobile", json={"mobile": "9876500011"})
    assert lookup.status_code == 200
    items = lookup.get_json()["data"]["items"]
    assert any(item["enquiry_number"] == number for item in items)

    none = client.post("/api/v1/public/tracking/by-mobile", json={"mobile": "9000000000"})
    assert none.status_code == 404

    # The token-reissue endpoint is gone with the token system.
    gone = client.post("/api/v1/public/tracking/token-reissue",
                       json={"enquiry_number": number, "mobile": "9876500011"})
    assert gone.status_code == 404


def test_api_honeypot_and_validation_rejections(client):
    spam = client.post("/api/v1/public/enquiries",
                       data={**_submit_payload(), "website": "http://spam.example"})
    assert spam.status_code == 400

    bad = client.post("/api/v1/public/enquiries", json=_submit_payload(subject="tiny"))
    assert bad.status_code == 400
    assert bad.get_json()["code"] == "VALIDATION"


def test_api_staff_endpoints_require_login(client):
    response = client.get("/api/v1/staff/enquiries")
    assert response.status_code == 401
    assert response.get_json()["success"] is False

    detail = client.get("/api/v1/staff/enquiries/1")
    assert detail.status_code == 401


def test_api_employee_forbidden_on_admin_endpoints(user1_client):
    response = user1_client.get("/api/v1/admin/users")
    assert response.status_code == 403
    assert response.get_json()["code"] == "FORBIDDEN"


def test_api_admin_create_enquiry_and_list(admin_client):
    created = admin_client.post("/api/v1/staff/enquiries", json={
        **_submit_payload(full_name="Staff Created", source="ADMIN"),
    })
    assert created.status_code == 201
    number = created.get_json()["data"]["enquiry_number"]

    listing = admin_client.get("/api/v1/staff/enquiries?q=Staff Created")
    assert listing.status_code == 200
    items = listing.get_json()["data"]["items"]
    assert any(item["enquiry_number"] == number for item in items)

    # Admin sees the enquiry in the default list
    listing_again = admin_client.get("/api/v1/staff/enquiries")
    assert listing_again.get_json()["data"]["total"] >= 1


def test_api_status_workflow_via_procedure(admin_client, user1_client):
    created = admin_client.post("/api/v1/staff/enquiries", json=_submit_payload())
    enquiry = created.get_json()["data"]

    # Assign to user1 (admin-only endpoint)
    from app.models import User
    target = User.query.filter_by(username="user1").first()
    assigned = admin_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/assignment",
        json={"assigned_to_user_id": target.user_id, "remarks": "please check"})
    assert assigned.status_code == 200
    assert assigned.get_json()["data"]["assigned_to_name"] == "User One"

    # User1 (assignee) can see it — but cannot change its status: only the
    # creator (or Admin) may progress a status.
    seen = user1_client.get(f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}")
    assert seen.status_code == 200

    status = user1_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/status",
        json={"status": "In Progress"})
    assert status.status_code == 403

    # Admin performs the workflow instead (Assigned -> In Progress).
    status = admin_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/status",
        json={"status": "In Progress"})
    assert status.status_code == 200
    assert status.get_json()["data"]["new_status"] == "In Progress"

    # Illegal transition (In Progress -> New) -> VALIDATION
    invalid = admin_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/status",
        json={"status": "New"})
    assert invalid.status_code == 400

    # Admin-only status from a non-admin -> FORBIDDEN
    rejected_by_user = user1_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/status",
        json={"status": "Rejected"})
    assert rejected_by_user.status_code == 403

    # Admin-only status: admin resolves the enquiry.
    resolved = admin_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/status",
        json={"status": "Resolved"})
    assert resolved.status_code == 200

    # Resolved is terminal for the employee — but Admin remains unrestricted.
    reopened_by_user = user1_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/status",
        json={"status": "Reopened", "remarks": "not fixed"})
    assert reopened_by_user.status_code == 403
    reopened_by_admin = admin_client.post(
        f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/status",
        json={"status": "Reopened", "remarks": "not fixed"})
    assert reopened_by_admin.status_code == 200
    assert reopened_by_admin.get_json()["data"]["new_status"] == "Reopened"


def test_api_remarks_endpoint(admin_client):
    created = admin_client.post("/api/v1/staff/enquiries", json=_submit_payload())
    enquiry = created.get_json()["data"]

    ok = admin_client.post(f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/remarks",
                           json={"remarks": "followed up by phone"})
    assert ok.status_code == 200

    empty = admin_client.post(f"/api/v1/staff/enquiries/{enquiry['enquiry_id']}/remarks",
                              json={"remarks": "   "})
    assert empty.status_code == 400


def test_api_reports_endpoint(admin_client):
    admin_client.post("/api/v1/staff/enquiries", json=_submit_payload())
    report = admin_client.post("/api/v1/reports/total", json={})
    assert report.status_code == 200
    data = report.get_json()["data"]
    assert data["title"] == "Total Enquiry Report"
    assert data["total_records"] >= 1
    assert len(data["headers"]) == 10

    statuswise = admin_client.post("/api/v1/reports/status-wise", json={})
    assert statuswise.status_code == 200
    assert statuswise.get_json()["data"]["headers"] == ["Status", "Total Enquiries"]

    unknown = admin_client.post("/api/v1/reports/unknown", json={})
    assert unknown.status_code == 404


def test_api_dashboard_endpoints(admin_client):
    kpis = admin_client.get("/api/v1/staff/dashboard/kpis")
    assert kpis.status_code == 200
    labels = [k["label"] for k in kpis.get_json()["data"]["kpis"]]
    assert labels[0] == "Total Enquiries"

    charts = admin_client.get("/api/v1/staff/dashboard/charts")
    assert charts.status_code == 200
    data = charts.get_json()["data"]
    assert set(data) == {"status", "category", "source", "monthly_trend",
                         "employee_assigned", "employee_resolved"}
    assert set(data["status"]) == {"labels", "counts"}
    assert len(data["monthly_trend"]["labels"]) == 6
