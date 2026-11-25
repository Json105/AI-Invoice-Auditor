"""Tests for Mandatory Auth Gate, RBAC hardening on Approval/Storage,
Multi-page PDF auto-splitting, and Single-photo multi-receipt extraction.
"""

import io
import json
from datetime import datetime
from unittest.mock import patch, MagicMock
from reportlab.pdfgen import canvas
from starlette.testclient import TestClient

from src.database import (
    DEFAULT_DB_PATH,
    init_db,
    save_audit_reports,
    SessionLocal,
    AuditRecordDB,
    User,
)
from src.extractors.gemini_extractor import GeminiExtractor
from src.extractors.mock_extractor import MockExtractor
from src.models import AuditReport, AuditStatus, DocumentType, InvoiceData
from src.services.document_splitter import split_pdf_to_page_images
from src.web.app import app

client = TestClient(app)


def _login_headers(email: str, password: str) -> dict:
    init_db(DEFAULT_DB_PATH)
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_multipage_pdf_bytes(num_pages: int = 3) -> bytes:
    """Create a real multi-page PDF document in memory via reportlab."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for page_idx in range(1, num_pages + 1):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 720, f"Receipt Page {page_idx}")
        c.drawString(72, 690, f"Total: TWD {page_idx * 100}")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_approval_and_storage_require_auth_and_rbac():
    """Unauthenticated gets 401; employee is blocked from finance/admin endpoints (403) and isolated."""
    init_db(DEFAULT_DB_PATH)
    # 1. Unauthenticated requests must be rejected with 401
    assert client.get("/api/approval/summary").status_code == 401
    assert client.get("/api/approval/list").status_code == 401
    assert client.post("/api/approval/1/review", json={"action": "approve"}).status_code == 401
    assert client.post("/api/approval/1/mark-paid").status_code == 401
    assert client.post("/api/approval/1/resubmit").status_code == 401
    assert client.get("/api/storage/stats").status_code == 401
    assert client.post("/api/storage/cleanup", json={"max_age_hours": 72}).status_code == 401
    assert client.post("/api/storage/presigned-url", json={"file_path": "sample_data/sample_thsr_ticket.png"}).status_code == 401

    emp_headers = _login_headers("employee@company.local", "User1234!")
    fin_headers = _login_headers("finance@company.local", "Finance1234!")
    admin_headers = _login_headers("admin@company.local", "Admin1234!")

    with SessionLocal() as db:
        admin_user = db.query(User).filter(User.email == "admin@company.local").first()
        admin_uid = admin_user.id

    # Seed a RETURNED record belonging strictly to admin
    admin_report = AuditReport(
        file_path="admin_only_taxi.png",
        status=AuditStatus.APPROVED,
        audit_timestamp=datetime.now().isoformat(),
        data=InvoiceData(
            document_type=DocumentType.TAXI_RECEIPT,
            invoice_number="ADMIN-RBAC-901",
            total_amount=500.0,
            seller_name="Admin Taxi",
        ),
    )
    save_audit_reports(
        [admin_report],
        applicant_name="系統管理員",
        applicant_email="admin@company.local",
        user_id=admin_uid,
    )
    with SessionLocal() as db:
        rec = db.query(AuditRecordDB).filter(AuditRecordDB.invoice_number == "ADMIN-RBAC-901").first()
        assert rec is not None
        admin_rec_id = rec.id

    # 2. Employee cannot review, mark-paid, check storage stats/cleanup, or resubmit admin's record
    assert client.post(f"/api/approval/{admin_rec_id}/review", headers=emp_headers, json={"action": "approve"}).status_code == 403
    assert client.post(f"/api/approval/{admin_rec_id}/mark-paid", headers=emp_headers).status_code == 403
    assert client.post(f"/api/approval/{admin_rec_id}/resubmit", headers=emp_headers).status_code == 403
    assert client.get("/api/storage/stats", headers=emp_headers).status_code == 403
    assert client.post("/api/storage/cleanup", headers=emp_headers, json={"max_age_hours": 72}).status_code == 403

    # 3. Employee's /api/approval/list must NEVER return admin's record; cross-user filter -> 403
    assert client.get(f"/api/approval/list?user_id={admin_uid}", headers=emp_headers).status_code == 403
    emp_list = client.get("/api/approval/list?status=ALL&limit=200", headers=emp_headers)
    assert emp_list.status_code == 200
    assert all(r["id"] != admin_rec_id for r in emp_list.json()["records"])

    # Employee CAN view own summary
    assert client.get("/api/approval/summary", headers=emp_headers).status_code == 200

    # Finance / Admin CAN access summary, review, and storage stats
    assert client.get("/api/storage/stats", headers=fin_headers).status_code == 200
    assert client.get("/api/storage/stats", headers=admin_headers).status_code == 200


def test_auth_gate_and_bulk_controls_in_html():
    """Verify frontend serves mandatory Auth Gate lock and batch PDF/multi-receipt options."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert 'id="auth-gate-screen"' in html
    assert 'id="app-shell"' in html
    assert "auth-locked" in html
    assert 'id="split-pdf-cb"' in html
    assert 'id="multi-receipt-cb"' in html


def test_multipage_pdf_auto_split_and_upload(tmp_path):
    """Uploading 1 multi-page PDF with split_pdf=true expands N pages into N independent claims."""
    pdf_bytes = _create_multipage_pdf_bytes(num_pages=3)
    local_pdf = tmp_path / "scanned_bundle.pdf"
    local_pdf.write_bytes(pdf_bytes)

    pages = split_pdf_to_page_images(local_pdf, tmp_path / "pages", dpi=100, max_pages=10)
    assert len(pages) == 3
    assert all(p.exists() and p.suffix == ".png" for p in pages)

    emp_headers = _login_headers("employee@company.local", "User1234!")
    res_trip = client.post(
        "/api/trips",
        headers=emp_headers,
        json={"title": "掃描機多頁 PDF 拆頁測試"},
    )
    assert res_trip.status_code == 200
    trip_id = res_trip.json()["trip"]["id"]

    res_audit = client.post(
        "/api/audit",
        headers=emp_headers,
        data={
            "engine": "mock",
            "apply_template": "true",
            "trip_id": str(trip_id),
            "split_pdf": "true",
            "multi_receipt": "false",
        },
        files=[("files", ("receipts_3pages.pdf", io.BytesIO(pdf_bytes), "application/pdf"))],
    )
    assert res_audit.status_code == 200
    body = res_audit.json()
    assert body["source_files_count"] == 1
    assert body["expanded_pages_count"] == 3
    assert body["total"] == 3
    assert len(body["reports"]) == 3

    # Each split PNG page gets its own valid 15-min presigned preview_url returning PNG image
    for rep in body["reports"]:
        preview_url = rep.get("preview_url")
        assert preview_url
        res_prev = client.get(preview_url)
        assert res_prev.status_code == 200
        assert "image/png" in res_prev.headers.get("Content-Type", "")

    detail = client.get(f"/api/trips/{trip_id}", headers=emp_headers).json()
    assert detail["receipt_count"] == 3


def test_multi_receipt_single_photo_extraction_and_trip_export(tmp_path):
    """Test extract_many for both Gemini (mocked HTTP) and MockExtractor via /api/audit."""
    # 1. Unit check: MockExtractor JSON array and desk-collage multi_receipt
    mock_ext = MockExtractor()
    items = mock_ext.extract_many("desk_multi_batch01.png", multi_receipt=True)
    assert len(items) == 3

    # 2. Unit check: GeminiExtractor.extract_many parses {"invoices": [...]} wrapper
    sample_img = tmp_path / "desk.png"
    sample_img.write_bytes(b"\x89PNG\r\n\x1a\n")
    fake_gemini_json = json.dumps({
        "invoices": [
            {
                "document_type": "tw_invoice",
                "invoice_number": "GG-11223344",
                "invoice_date": "2026-11-12",
                "total_amount": 500.0,
                "seller_name": "測試商店 A",
                "currency": "NT$",
            },
            {
                "document_type": "taxi_receipt",
                "invoice_number": "TX-55667788",
                "invoice_date": "2026-11-12",
                "total_amount": 260.0,
                "seller_name": "大車隊 B",
                "currency": "NT$",
            },
        ]
    })
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": fake_gemini_json}]}}]
    }
    gem_ext = GeminiExtractor(api_key="test-key")
    with patch("src.extractors.gemini_extractor.requests.post", return_value=mock_resp):
        parsed_list = gem_ext.extract_many(sample_img, multi_receipt=True)
    assert len(parsed_list) == 2
    assert parsed_list[0].invoice_number == "GG-11223344"
    assert parsed_list[1].invoice_number == "TX-55667788"

    # 3. End-to-end API upload of 1 collage photo -> 3 claims inside trip bundle
    emp_headers = _login_headers("employee@company.local", "User1234!")
    trip_res = client.post(
        "/api/trips",
        headers=emp_headers,
        json={"title": "多發票同框合拍專案"},
    )
    trip_id = trip_res.json()["trip"]["id"]

    res_upload = client.post(
        "/api/audit",
        headers=emp_headers,
        data={
            "engine": "mock",
            "apply_template": "true",
            "trip_id": str(trip_id),
            "split_pdf": "true",
            "multi_receipt": "true",
        },
        files=[("files", ("desk_multi_collage.png", io.BytesIO(b"fake-png-bytes"), "image/png"))],
    )
    assert res_upload.status_code == 200
    up_body = res_upload.json()
    assert up_body["source_files_count"] == 1
    assert up_body["total"] == 3

    exp_res = client.get(f"/api/trips/{trip_id}/export?format=template", headers=emp_headers)
    assert exp_res.status_code == 200
    assert len(exp_res.content) > 5000
