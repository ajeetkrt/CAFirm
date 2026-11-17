"""Report service and export tests."""
from app.services import report_service
from app.services.report_service import build_report, collect_filters


def _filters(**kwargs):
    base = {"date_from": None, "date_to": None, "category_id": None,
            "status": None, "assigned_to_user_id": None, "source": None,
            "report_type": "total"}
    base.update(kwargs)
    return base


def test_all_report_types_build(app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry_factory()

    for report_type in report_service.REPORT_TYPES:
        report = build_report(_filters(report_type=report_type), admin)
        assert report["title"] == report_service.REPORT_TYPES[report_type]
        assert report["headers"], report_type
        assert isinstance(report["rows"], list)


def test_pending_report_lists_only_open_enquiries(app, db_session, enquiry_factory):
    from app.models import User
    from app.services import enquiry_service as svc
    admin = User.query.filter_by(username="admin").first()
    enquiry = enquiry_factory()
    enquiry.status = "Resolved"
    db_session.commit()
    enquiry_factory()

    report = build_report(_filters(report_type="pending"), admin)
    numbers = [row[0] for row in report["rows"]]
    assert enquiry.enquiry_number not in numbers
    assert len(numbers) >= 1


def test_excel_export_is_valid_xlsx(app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry_factory()
    report = build_report(_filters(report_type="total"), admin)
    buffer = report_service.export_excel(report, _filters())
    data = buffer.read()
    assert data[:2] == b"PK"  # xlsx is a zip container
    assert len(data) > 1000


def test_pdf_export_is_valid_pdf(app, db_session, enquiry_factory):
    from app.models import User
    admin = User.query.filter_by(username="admin").first()
    enquiry_factory()
    report = build_report(_filters(report_type="total"), admin)
    buffer = report_service.export_pdf(report, _filters())
    data = buffer.read()
    assert data[:5] == b"%PDF-"
    assert len(data) > 1000


def test_report_export_via_http(app, admin_client, enquiry_factory):
    enquiry_factory()
    response = admin_client.post("/reports/export/excel",
                                 data={"report_type": "total"})
    assert response.status_code == 200
    assert response.data[:2] == b"PK"

    response = admin_client.post("/reports/export/pdf",
                                 data={"report_type": "statuswise"})
    assert response.status_code == 200
    assert response.data[:5] == b"%PDF-"


def test_employee_report_scope(app, user1_client, db_session, enquiry_factory):
    """Employees only export records within their scope."""
    from app.models import User, Enquiry
    user1 = User.query.filter_by(username="user1").first()
    user2 = User.query.filter_by(username="user2").first()
    mine = enquiry_factory(created_by=user1, source="USER")
    other = enquiry_factory(created_by=user2, source="USER")

    report = build_report(_filters(report_type="total"), user1)
    numbers = [row[0] for row in report["rows"]]
    assert mine.enquiry_number in numbers
    assert other.enquiry_number not in numbers


# ---------------------------------------------------------------------------
# Total Enquiry Report — 10 records per page with numbered pagination
# ---------------------------------------------------------------------------

def test_total_report_shows_ten_per_page(app, admin_client, db_session, enquiry_factory):
    for _ in range(15):  # guarantee more than one page regardless of prior data
        enquiry_factory()
    html = admin_client.post("/reports/", data={"report_type": "total"}).get_data(as_text=True)

    assert html.count("CAF-") == 10                       # exactly 10 rows shown
    assert "Showing <strong>1–10</strong>" in html
    assert "Page 1 of" in html
    assert "10 records per page" in html
    assert "pagination-ca" in html                        # numbered pager rendered


def test_report_pagination_links_via_get(app, admin_client, db_session, enquiry_factory):
    for _ in range(12):  # at least two pages
        enquiry_factory()

    response = admin_client.get("/reports/?report_type=total&page=2")
    assert response.status_code == 200
    page2 = response.get_data(as_text=True)
    assert "Page 2 of" in page2
    assert "Showing <strong>11–" in page2                 # rows 11 onward
    assert 0 < page2.count("CAF-") <= 10                  # partial last page allowed

    # pagination links carry the report type back into the GET URL
    import re
    links = [h for h in re.findall(r'href="(/reports/\?[^"]*)"', page2)
             if "page=" in h]
    assert links and all("report_type=total" in h for h in links), links[:3]


def test_summary_report_not_paginated(app, admin_client, db_session, enquiry_factory):
    enquiry_factory()
    html = admin_client.get("/reports/?report_type=statuswise").get_data(as_text=True)

    assert "pagination-ca" not in html                    # summaries render in full
    assert "records per page" not in html
    assert "Status-wise Enquiry Report" in html
