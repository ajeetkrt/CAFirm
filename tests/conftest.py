"""Pytest fixtures for CAFirm tests.

Tests run against the TEST_DATABASE_URL PostgreSQL database defined in TestingConfig.

IMPORTANT: each test gets its OWN Flask app context. A session-scoped app
context would be reused by every request that Flask-SQLAlchemy/Flask-Login
handle during the tests, which leaks request-local state (for example
``g._login_user``) across requests and tests.
"""
import pytest

from app import create_app
from app.database.procedures import drop_statements, install_statements
from app.extensions import db as _db
from app.models import Category, Role, User
from app.services.enquiry_service import ensure_enquiry_sequence

TEST_PASSWORDS = {"admin": "Admin@123", "user1": "User@123", "user2": "User@123"}


def seed_base_data(session):
    """Create roles, internal accounts and categories used by the tests."""
    admin_role = Role(role_name="Admin", description="Full access")
    user_role = Role(role_name="User", description="Employee")
    session.add_all([admin_role, user_role])
    session.flush()

    accounts = [
        ("admin", "Administrator", "admin@test.local", admin_role),
        ("user1", "User One", "user1@test.local", user_role),
        ("user2", "User Two", "user2@test.local", user_role),
    ]
    users = {}
    for username, full_name, email, role in accounts:
        user = User(username=username, full_name=full_name, email=email,
                    role_id=role.role_id, is_active=True)
        user.set_password(TEST_PASSWORDS[username])
        session.add(user)
        users[username] = user

    session.add_all([
        Category(category_name="GST Registration", is_active=True),
        Category(category_name="Complaint", is_active=True),
        Category(category_name="Old Category", is_active=False),
    ])
    session.commit()
    return users


@pytest.fixture(scope="session")
def app():
    app = create_app("testing")
    # Setup inside a short-lived app context; requests push their own.
    with app.app_context():
        _db.create_all()
        ensure_enquiry_sequence(_db.session)
        install_statements(_db.session)  # usp_* procedures for the API layer
        if not _db.session.query(Role).first():  # tolerate a leftover DB state
            seed_base_data(_db.session)
    yield app
    with app.app_context():
        _db.session.remove()
        # Functions returning SETOF enquiries pin the table type; drop them first.
        drop_statements(_db.session)
        _db.session.commit()
        _db.drop_all()


@pytest.fixture(autouse=True)
def _fresh_app_context(app):
    """Fresh app context per test — requests during the test reuse it, but it
    is never shared across tests."""
    with app.app_context():
        yield


@pytest.fixture
def db_session():
    """The scoped session active inside the test's app context."""
    return _db.session


@pytest.fixture
def client(app):
    """Test client whose every request runs in its OWN app context.

    Production servers push a fresh app context per request; under pytest an
    outer context would otherwise be reused by every request, leaking
    request-local state (e.g. Flask-Login's ``g._login_user``).
    """
    from flask.testing import FlaskClient

    class IsolatedAppClient(FlaskClient):
        def open(self, *args, **kwargs):
            with app.app_context():
                return super().open(*args, **kwargs)

    app.test_client_class = IsolatedAppClient
    return app.test_client()


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def _isolated_logged_in_client(app, username, password):
    """Build a SEPARATE client per role so session cookies never collide."""
    c = app.test_client()
    _login(c, username, password)
    return c


@pytest.fixture
def admin_client(app):
    return _isolated_logged_in_client(app, "admin", TEST_PASSWORDS["admin"])


@pytest.fixture
def user1_client(app):
    return _isolated_logged_in_client(app, "user1", TEST_PASSWORDS["user1"])


@pytest.fixture
def user2_client(app):
    return _isolated_logged_in_client(app, "user2", TEST_PASSWORDS["user2"])


@pytest.fixture
def enquiry_factory(db_session):
    """Create enquiries quickly; commits immediately."""

    def factory(**overrides):
        from app.services import enquiry_service
        source = overrides.pop("source", "ADMIN")
        created_by = overrides.pop("created_by", None)
        data = {
            "full_name": "Test Person",
            "mobile_number": "9876543210",
            "email": None,
            "state": None,
            "district": None,
            "category_id": 1,
            "subject": "Test subject here",
            "description": "A description long enough to satisfy validation rules.",
        }
        data.update(overrides)
        enquiry = enquiry_service.create_enquiry(
            data, source=source, created_by=created_by, ip_address="127.0.0.1",
        )
        db_session.commit()
        return enquiry

    return factory
