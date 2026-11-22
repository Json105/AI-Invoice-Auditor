"""Tests for Phase 1 internal 10-employee portal features:
- Forced JWT auth & RBAC record/file isolation
- Receipt persistence & protected preview/download
- Filtered history Excel export
- UserAdmin plaintext password auto-hashing
- Employee password change endpoint
- TIFF/BMP scan normalization & SQLite schema migration
"""

import asyncio
import io
import sqlite3
import uuid
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient

from src.database import (
    DEFAULT_DB_PATH,
    SessionLocal,
    User,
    get_audit_history,
    init_db,
)
from src.extractors.base import prepare_document_payload
from src.services.auth_service import hash_password, verify_password
from src.web.admin_views import UserAdmin
from src.web.app import app

client = TestClient(app)


def _login_as(email: str, password: str) -> dict:
    init_db(DEFAULT_DB_PATH)
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return res.json()


def test_unauthenticated_endpoints_blocked():
    """Protected endpoints must reject unauthenticated requests with 401."""
    assert client.get("/api/history").status_code == 401
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/history/export").status_code == 401
    assert client.get("/api/records/1/file").status_code == 401
    assert client.get("/api/auth/users").status_code == 401
    res_audit = client.post(
        "/api/audit",
        data={"engine": "mock"},
        files=[("files", ("sample_thsr_ticket.png", b"fake", "image/png"))],
    )
    assert res_audit.status_code == 401


def test_rbac_upload_persistence_preview_and_export(tmp_path):
    """Employees only access own receipts; privileged roles can view all and export."""
    admin_login = _login_as("admin@company.local", "Admin1234!")
    emp_login = _login_as("employee@company.local", "User1234!")
    admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
    emp_headers = {"Authorization": f"Bearer {emp_login['access_token']}"}

    # Employee cannot list company users or filter by another user_id
    assert client.get("/api/auth/users", headers=emp_headers).status_code == 403
    assert client.get(f"/api/history?user_id={admin_login['user']['id']}", headers=emp_headers).status_code == 403

    # Admin can list users
    res_users = client.get("/api/auth/users", headers=admin_headers)
    assert res_users.status_code == 200
    assert len(res_users.json()) >= 3

    # Create a real PNG image in memory for employee upload
    img_buf = io.BytesIO()
    Image.new("RGB", (20, 20), color=(10, 80, 160)).save(img_buf, format="PNG")
    png_bytes = img_buf.getvalue()

    res_upload = client.post(
        "/api/audit",
        data={"engine": "mock", "apply_template": "true"},
        files=[("files", ("../evil_dir/sample_thsr_ticket.png", png_bytes, "image/png"))],
        headers=emp_headers,
    )
    assert res_upload.status_code == 200
    session_id = res_upload.json()["session_id"]

    # Fetch employee history and find the uploaded record
    res_hist = client.get("/api/history?limit=20", headers=emp_headers)
    assert res_hist.status_code == 200
    history = res_hist.json()["history"]
    emp_rec = next((r for r in history if r.get("user_id") == emp_login["user"]["id"] and r.get("has_file")), None)
    assert emp_rec is not None
    assert emp_rec["uploader_email"] == "employee@company.local"
    assert ".." not in (emp_rec.get("stored_file_path") or "")
    assert Path(emp_rec["stored_file_path"]).exists()

    # Employee and Admin can download the stored receipt file
    res_file_emp = client.get(f"/api/records/{emp_rec['id']}/file", headers=emp_headers)
    assert res_file_emp.status_code == 200
    assert res_file_emp.content == png_bytes

    res_file_adm = client.get(f"/api/records/{emp_rec['id']}/file", headers=admin_headers)
    assert res_file_adm.status_code == 200

    # Admin uploads a separate receipt; employee must get 403 trying to view it
    res_admin_up = client.post(
        "/api/audit",
        data={"engine": "mock", "apply_template": "false"},
        files=[("files", ("sample_taxi_receipt.png", png_bytes, "image/png"))],
        headers=admin_headers,
    )
    assert res_admin_up.status_code == 200
    admin_hist = client.get(f"/api/history?user_id={admin_login['user']['id']}&limit=5", headers=admin_headers).json()["history"]
    admin_rec = next(r for r in admin_hist if r.get("has_file"))
    assert client.get(f"/api/records/{admin_rec['id']}/file", headers=emp_headers).status_code == 403
    assert client.get(f"/api/download/{session_id}/excel", headers=emp_headers).status_code == 200

    # Filtered history Excel export works for employee
    res_export = client.get("/api/history/export", headers=emp_headers)
    assert res_export.status_code == 200
    assert len(res_export.content) > 2000


def test_unsupported_extension_rejected():
    """Uploading disallowed extensions (e.g., .exe or .docx) must return 400."""
    emp_login = _login_as("employee@company.local", "User1234!")
    headers = {"Authorization": f"Bearer {emp_login['access_token']}"}
    res = client.post(
        "/api/audit",
        data={"engine": "mock"},
        files=[("files", ("malware.exe", b"MZ", "application/octet-stream"))],
        headers=headers,
    )
    assert res.status_code == 400


def test_user_admin_auto_hashes_plaintext_password():
    """UserAdmin.on_model_change must hash plaintext password and normalize email."""
    view = UserAdmin()
    dummy_user = User(email="NewStaff@Company.Local", full_name="新同仁", hashed_password="PlainPassword123")
    data = {"email": " NewStaff@Company.Local ", "hashed_password": "PlainPassword123"}

    asyncio.run(view.on_model_change(data, dummy_user, True, None))

    assert data["email"] == "newstaff@company.local"
    assert dummy_user.email == "newstaff@company.local"
    assert dummy_user.hashed_password != "PlainPassword123"
    assert verify_password("PlainPassword123", dummy_user.hashed_password)

    # Existing bcrypt hash must not be double-hashed on edit
    existing_hash = dummy_user.hashed_password
    edit_data = {"email": "newstaff@company.local", "hashed_password": existing_hash}
    asyncio.run(view.on_model_change(edit_data, dummy_user, False, None))
    assert dummy_user.hashed_password == existing_hash


def test_change_password_lifecycle():
    """Employee can change own password after verifying current password."""
    init_db(DEFAULT_DB_PATH)
    temp_email = f"temp_{uuid.uuid4().hex[:8]}@company.local"
    with SessionLocal() as db:
        temp_user = User(
            email=temp_email,
            full_name="臨時測試員",
            hashed_password=hash_password("OldPass123"),
            role="employee",
            department="測試處",
            is_active=True,
        )
        db.add(temp_user)
        db.commit()

    try:
        login_res = _login_as(temp_email, "OldPass123")
        headers = {"Authorization": f"Bearer {login_res['access_token']}"}

        bad_res = client.post(
            "/api/auth/change-password",
            json={"current_password": "WrongPassword", "new_password": "NewPass456"},
            headers=headers,
        )
        assert bad_res.status_code == 400

        ok_res = client.post(
            "/api/auth/change-password",
            json={"current_password": "OldPass123", "new_password": "NewPass456"},
            headers=headers,
        )
        assert ok_res.status_code == 200

        # New password succeeds, old password fails
        _login_as(temp_email, "NewPass456")
        fail_old = client.post("/api/auth/login", json={"email": temp_email, "password": "OldPass123"})
        assert fail_old.status_code == 401
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.email == temp_email).delete()
            db.commit()


def test_tiff_and_bmp_normalized_to_png(tmp_path):
    """TIFF and BMP scanned receipts are normalized to PNG bytes for vision extractors."""
    for ext in ("tiff", "bmp"):
        fpath = tmp_path / f"scan.{ext}"
        Image.new("RGB", (32, 32), color=(200, 100, 50)).save(fpath)
        payload_bytes, mime_type = prepare_document_payload(fpath)
        assert mime_type == "image/png"
        assert payload_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_sqlite_schema_migration_preserves_legacy_rows(tmp_path):
    """init_db must migrate legacy audited_invoices tables without dropping rows."""
    legacy_db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.execute("""
        CREATE TABLE audited_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number VARCHAR(100),
            invoice_date VARCHAR(50),
            document_type VARCHAR(50),
            currency VARCHAR(10),
            total_amount FLOAT,
            buyer_tax_id VARCHAR(20),
            buyer_name VARCHAR(200),
            seller_tax_id VARCHAR(50),
            seller_name VARCHAR(200),
            expense_category VARCHAR(20),
            purpose TEXT,
            payment_method VARCHAR(50),
            audit_status VARCHAR(50),
            issues_json TEXT,
            file_name VARCHAR(255),
            created_at DATETIME
        )
    """)
    conn.execute("""
        INSERT INTO audited_invoices (invoice_number, currency, total_amount, audit_status, file_name)
        VALUES ('LEG-0001', 'NT$', 1200.0, 'APPROVED', 'legacy.jpg')
    """)
    conn.commit()
    conn.close()

    init_db(legacy_db)
    rows = get_audit_history(limit=10, db_path=legacy_db)
    assert len(rows) == 1
    assert rows[0]["invoice_number"] == "LEG-0001"
    assert rows[0]["has_file"] is False
