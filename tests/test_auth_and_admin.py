"""Integration and unit tests for Enterprise Authentication (JWT + RBAC) and SQLAdmin."""

from datetime import datetime
import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from src.web.app import app
from src.database import engine, SessionLocal, User, AuditRecordDB, init_db, DEFAULT_DB_PATH
from src.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    authenticate_user,
)

client = TestClient(app)


def test_auth_service_hash_and_token():
    """1. Test bcrypt password hashing and JWT token lifecycle."""
    plain = "SuperSecret2026!"
    hashed = hash_password(plain)
    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False

    # Token test
    payload = {"sub": "test@company.local", "role": "admin"}
    token = create_access_token(payload)
    assert isinstance(token, str)

    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "test@company.local"
    assert decoded["role"] == "admin"


def test_database_default_users_seeded():
    """2. Test that default demo users are seeded properly."""
    init_db(DEFAULT_DB_PATH)
    with SessionLocal() as db:
        admin = db.query(User).filter(User.email == "admin@company.local").first()
        finance = db.query(User).filter(User.email == "finance@company.local").first()
        employee = db.query(User).filter(User.email == "employee@company.local").first()

        assert admin is not None
        assert admin.role == "admin"
        assert verify_password("Admin1234!", admin.hashed_password) is True

        assert finance is not None
        assert finance.role == "accountant"

        assert employee is not None
        assert employee.role == "employee"


def test_api_auth_login_and_me():
    """3. Test /api/auth/login and /api/auth/me workflow."""
    # Test valid login
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@company.local", "password": "Admin1234!"}
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["user"]["email"] == "admin@company.local"
    assert data["user"]["role"] == "admin"
    token = data["access_token"]

    # Test /api/auth/me with valid Bearer token
    res_me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_me.status_code == 200
    me_data = res_me.json()
    assert me_data["email"] == "admin@company.local"

    # Test invalid login
    res_invalid = client.post(
        "/api/auth/login",
        json={"email": "admin@company.local", "password": "WrongPassword!"}
    )
    assert res_invalid.status_code == 401

    # Test /api/auth/me without token
    res_no_token = client.get("/api/auth/me")
    assert res_no_token.status_code == 401


def test_api_oauth_status():
    """4. Test pluggable OAuth feature flag status."""
    res = client.get("/api/auth/oauth-status")
    assert res.status_code == 200
    data = res.json()
    assert "oauth_enabled" in data
    assert isinstance(data["providers"], list)


def test_sqladmin_protected_and_mounted():
    """5. Test SQLAdmin is mounted and protected by authentication."""
    res = client.get("/admin/", follow_redirects=False)
    # Protected by authentication backend: should redirect to login
    assert res.status_code in (302, 303, 307)
    assert "/admin/login" in res.headers.get("Location", "")
