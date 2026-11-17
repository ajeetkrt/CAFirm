"""Authentication and authorization tests."""
from app.models import Category, User


def test_login_success_redirects_to_dashboard(client):
    response = client.post("/login", data={"username": "admin", "password": "Admin@123"})
    assert response.status_code == 302
    assert "/admin/dashboard" in response.headers["Location"]


def test_login_wrong_password_shows_error(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong"},
                           follow_redirects=True)
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_login_unknown_user_shows_error(client):
    response = client.post("/login", data={"username": "ghost", "password": "whatever"},
                           follow_redirects=True)
    assert b"Invalid username or password" in response.data


def test_anonymous_redirects_to_login(client):
    response = client.get("/admin/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_employee_cannot_access_admin_dashboard(user1_client):
    response = user1_client.get("/admin/dashboard")
    assert response.status_code == 403


def test_employee_cannot_access_user_management(user1_client):
    assert user1_client.get("/admin/users").status_code == 403
    assert user1_client.get("/admin/categories").status_code == 403


def test_admin_users_page_renders_compact_workspace(admin_client):
    response = admin_client.get("/admin/users")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "User Administration" in html
    # create-user now lives in a modal, keeping the page fixed-height
    assert "createUserModal" in html
    assert "New User" in html
    # summary chips render with seeded accounts
    assert "users</span>" in html
    assert "admin" in html.lower()


def test_admin_users_page_paginates_above_five(admin_client, db_session):
    for i in range(4):  # 3 seeded accounts + 4 new = 7 total
        username = f"pageduser{i}"
        user = User(username=username, full_name=username.title(),
                    email=f"{username}@example.com", role_id=2, is_active=True)
        user.set_password("User@123")
        db_session.add(user)
    db_session.commit()
    total = db_session.query(User).count()
    assert total >= 7

    page1 = admin_client.get("/admin/users").get_data(as_text=True)
    assert page1.count('class="user-avatar') == 5        # five accounts per page
    assert "Page 1 of" in page1
    assert "Showing <strong>1–5</strong>" in page1
    assert "pagination-ca" in page1                      # numbered pager shows

    page2 = admin_client.get("/admin/users?page=2").get_data(as_text=True)
    assert page2.count('class="user-avatar') == total - 5

    # a stale page number snaps back to the last real page
    stale = admin_client.get("/admin/users?page=99").get_data(as_text=True)
    assert stale.count('class="user-avatar') == total - 5


def test_admin_creates_user_via_form(app, admin_client, db_session):
    """Regression: the create endpoint used to crash (missing import) with a 500."""
    from app.models import ActivityLog
    response = admin_client.post("/admin/users/create", data={
        "username": "newstaff9", "full_name": "New Staff Nine",
        "email": "newstaff9@example.com", "role_id": "2", "password": "Staff@123",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"created successfully" in response.data

    user = db_session.query(User).filter_by(username="newstaff9").one()
    assert user.is_active is True
    assert db_session.query(ActivityLog).filter_by(action="USER_CREATED").count() >= 1


def test_admin_create_user_preserves_form_on_error(app, admin_client, db_session):
    response = admin_client.post("/admin/users/create", data={
        "username": "modaluser", "full_name": "Modal User",
        "email": "modaluser@example.com", "role_id": "2", "password": "short",
    })
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Password must be" in html                     # in-modal error summary
    assert 'value="modaluser"' in html                    # typed data preserved
    assert 'value="modaluser@example.com"' in html
    assert 'value="short"' not in html                    # password never echoed
    assert db_session.query(User).filter_by(username="modaluser").count() == 0


def test_admin_create_user_rejects_duplicate(app, admin_client, db_session):
    payload = {
        "username": "dupstaff", "full_name": "Dup Staff",
        "email": "dupstaff@example.com", "role_id": "2", "password": "Staff@123",
    }
    first = admin_client.post("/admin/users/create", data=payload, follow_redirects=True)
    assert first.status_code == 200
    second = admin_client.post("/admin/users/create", data=payload)
    assert second.status_code == 400
    assert b"already exists" in second.data
    assert db_session.query(User).filter_by(username="dupstaff").count() == 1


def test_admin_toggle_user_flips_status_and_audits(app, admin_client, db_session):
    """HTTP mutations commit in the request's own session (each request runs in
    a fresh app context with its own scoped session), so the test must re-read
    the database after the POST instead of trusting pre-request objects."""
    from app.models import ActivityLog

    target = db_session.query(User).filter_by(username="user1").one()
    target.is_active = True  # normalize — never depend on leftovers from other runs
    db_session.commit()

    try:
        response = admin_client.post(f"/admin/users/{target.user_id}/toggle",
                                     follow_redirects=True)
        assert response.status_code == 200

        db_session.expire_all()  # drop pre-request identity-map state
        target = db_session.query(User).filter_by(user_id=target.user_id).one()
        assert target.is_active is False
        assert db_session.query(ActivityLog).filter_by(
            action="USER_DEACTIVATED").count() >= 1
    finally:
        # restore user1 whatever the asserts did, so later modules can log in
        try:
            db_session.rollback()
            restored = db_session.query(User).filter_by(username="user1").one()
            restored.is_active = True
            db_session.commit()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass

    # the admin cannot deactivate their own account
    admin_user = db_session.query(User).filter_by(username="admin").one()
    self_toggle = admin_client.post(f"/admin/users/{admin_user.user_id}/toggle",
                                    follow_redirects=True)
    assert b"cannot deactivate your own" in self_toggle.data
    assert admin_user.is_active is True


# ---------------------------------------------------------------------------
# Categories page — the Manage-Users style workspace
# ---------------------------------------------------------------------------
def test_admin_categories_page_renders_workspace(admin_client):
    response = admin_client.get("/admin/categories")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Category Management" in html
    # create-category lives in a modal, keeping the page fixed-height
    assert "createCategoryModal" in html
    assert "New Category" in html
    assert "GST Registration" in html       # a seeded category is listed
    assert "Showing <strong>" in html       # range footer


def test_admin_categories_paginate_above_five(admin_client, db_session):
    for i in range(4):  # 3 seeded categories + 4 new = 7 total
        db_session.add(Category(category_name=f"Paged Cat {i}", is_active=True,
                                description="pagination filler"))
    db_session.commit()
    total = db_session.query(Category).count()
    assert total >= 7

    page1 = admin_client.get("/admin/categories").get_data(as_text=True)
    assert page1.count('class="user-avatar cat-avatar"') == 5   # five per page
    assert "Page 1 of" in page1
    assert "Showing <strong>1–5</strong>" in page1
    assert "pagination-ca" in page1                             # numbered pager

    page2 = admin_client.get("/admin/categories?page=2").get_data(as_text=True)
    assert page2.count('class="user-avatar cat-avatar"') == total - 5

    # a stale page number snaps back to the last real page
    stale = admin_client.get("/admin/categories?page=99").get_data(as_text=True)
    assert stale.count('class="user-avatar cat-avatar"') == total - 5


def test_admin_create_category_preserves_form_on_error(app, admin_client, db_session):
    response = admin_client.post("/admin/categories/create", data={
        "category_name": "ab",  # too short — validator requires 3-100 chars
        "description": "Should not be created",
    })
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Category name must be" in html                 # in-modal error summary
    assert 'value="ab"' in html                            # typed data preserved
    assert "Should not be created" in html
    assert db_session.query(Category).filter_by(category_name="ab").count() == 0


def test_admin_create_category_rejects_duplicate(app, admin_client, db_session):
    payload = {"category_name": "Dup Category", "description": "first"}
    first = admin_client.post("/admin/categories/create", data=payload,
                              follow_redirects=True)
    assert first.status_code == 200
    second = admin_client.post("/admin/categories/create", data=payload)
    assert second.status_code == 400
    assert b"already exists" in second.data
    assert db_session.query(Category).filter_by(category_name="Dup Category").count() == 1


def test_admin_toggle_category_stays_on_page(admin_client, db_session):
    for i in range(4):  # 3 seeded + 4 = 7 → page 2 exists
        db_session.add(Category(category_name=f"Toggle Cat {i}", is_active=True))
    db_session.commit()
    cat = db_session.query(Category).filter_by(category_name="Toggle Cat 0").one()

    response = admin_client.post(f"/admin/categories/{cat.category_id}/toggle",
                                 data={"page": 2}, follow_redirects=False)
    assert response.status_code == 302
    assert "page=2" in response.headers["Location"]        # the admin stays on page 2

    db_session.expire_all()
    cat = db_session.query(Category).filter_by(category_id=cat.category_id).one()
    assert cat.is_active is False                          # the flip persisted


def test_logout_redirects_to_home(admin_client):
    """Logout ends the session and lands the user on the public home page."""
    from urllib.parse import urlparse
    response = admin_client.post("/logout")
    assert response.status_code == 302
    assert urlparse(response.headers["Location"]).path == "/"

    home = admin_client.get("/", follow_redirects=True)
    assert b"logged out" in home.data

    # The session really ended — the staff portal is gated again.
    assert admin_client.get("/admin/dashboard").status_code == 302


def test_passwords_are_hashed(app, db_session):
    user = db_session.query(User).filter_by(username="admin").first()
    assert user.password_hash != "Admin@123"
    assert user.password_hash.startswith(("pbkdf2:", "scrypt:"))
    assert user.check_password("Admin@123")


def test_deactivated_user_cannot_login(app, client, db_session):
    user = db_session.query(User).filter_by(username="user2").first()
    user.is_active = False
    db_session.commit()
    try:
        response = client.post("/login", data={"username": "user2", "password": "User@123"},
                               follow_redirects=True)
        assert b"Invalid username or password" in response.data
    finally:
        user.is_active = True
        db_session.commit()
