"""
CAFirm application configuration.

Configuration values are read from the .env file located in the project root
so the application can be deployed on any local machine without code changes.
"""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

# Make libpq.dll visible for psycopg's pure-Python (ctypes) implementation.
# This machine blocks compiled .pyd extensions, so the binary wheel of
# psycopg cannot be used; libpq ships with the local PostgreSQL install.
# Override with PG_BIN_DIR in .env when PostgreSQL lives elsewhere.
_PG_BIN = os.getenv("PG_BIN_DIR", r"C:\Program Files\PostgreSQL\18\bin")
if os.path.isdir(_PG_BIN) and _PG_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _PG_BIN + os.pathsep + os.environ.get("PATH", "")


def _env_bool(name: str, default: str = "False") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Config:
    """Base configuration shared by every environment."""

    APP_NAME = "CAFirm"
    APP_FULL_NAME = "Chartered Accountant Enquiry & Client Service Management System"

    # Security
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-key-change-me")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.getenv("SESSION_LIFETIME_HOURS", "8")))
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 12 * 60 * 60

    # Database — real credentials come from .env (see .env.example)
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/cafarm_db",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 1800}

    # Uploads
    UPLOAD_FOLDER = BASE_DIR / os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH_MB", "10")) * 1024 * 1024
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "5"))
    ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "doc", "docx", "xls", "xlsx", "csv", "txt"}

    # Rate limiting (local, in-memory)
    LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "5"))          # attempts
    LOGIN_RATE_WINDOW = int(os.getenv("LOGIN_RATE_WINDOW", "900"))      # seconds
    PUBLIC_SUBMIT_RATE_LIMIT = int(os.getenv("PUBLIC_SUBMIT_RATE_LIMIT", "10"))
    PUBLIC_SUBMIT_RATE_WINDOW = int(os.getenv("PUBLIC_SUBMIT_RATE_WINDOW", "3600"))
    TRACK_RATE_LIMIT = int(os.getenv("TRACK_RATE_LIMIT", "30"))
    TRACK_RATE_WINDOW = int(os.getenv("TRACK_RATE_WINDOW", "600"))

    # General
    ITEMS_PER_PAGE = int(os.getenv("ITEMS_PER_PAGE", "10"))
    LOG_FOLDER = BASE_DIR / "logs"


class DevelopmentConfig(Config):
    DEBUG = True
    ENV = "development"
    # Always revalidate static files so CSS/JS edits appear after a plain refresh.
    SEND_FILE_MAX_AGE_DEFAULT = 0


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    WTF_CSRF_ENABLED = False
    # Neutralise the in-memory rate limiter so tests don't interfere.
    LOGIN_RATE_LIMIT = 10000
    PUBLIC_SUBMIT_RATE_LIMIT = 10000
    TRACK_RATE_LIMIT = 10000
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/cafarm_test",
    )


class ProductionConfig(Config):
    DEBUG = False
    ENV = "production"
    SESSION_COOKIE_SECURE = True


config_map = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}


def get_config(name: str | None = None):
    """Return the configuration class selected by FLASK_CONFIG/.env."""
    key = name or os.getenv("FLASK_CONFIG", "development")
    return config_map.get(key, DevelopmentConfig)
