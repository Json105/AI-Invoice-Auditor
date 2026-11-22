"""Integration tests for FastAPI endpoints."""

from fastapi.testclient import TestClient
from src.web.app import app

client = TestClient(app)


def _get_auth_headers() -> dict:
    from src.database import init_db, DEFAULT_DB_PATH
    init_db(DEFAULT_DB_PATH)
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@company.local", "password": "Admin1234!"},
    )
    assert res.status_code == 200
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_stats_endpoint():
    response = client.get("/api/stats", headers=_get_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert "total_invoices" in data
    assert "status_counts" in data


def test_history_endpoint():
    response = client.get("/api/history", headers=_get_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert "history" in data


def test_audit_upload_mock_engine():
    headers = _get_auth_headers()
    # Test uploading sample files to /api/audit
    files = [
        ("files", ("sample_thsr_ticket.png", b"fake image bytes", "image/png")),
        ("files", ("sample_china_vat.json", b'{"document_type": "commercial_invoice", "total_amount": 368, "currency": "RMB", "expense_category": "F", "invoice_number": "CN-112233"}', "application/json"))
    ]
    data = {
        "engine": "mock",
        "apply_template": "true"
    }

    response = client.post("/api/audit", files=files, data=data, headers=headers)
    assert response.status_code == 200
    result = response.json()

    assert result["total"] == 2
    assert "session_id" in result
    assert "download_urls" in result
    assert "excel" in result["download_urls"]
    assert "template" in result["download_urls"]

    session_id = result["session_id"]

    # Test downloading filled template
    template_download = client.get(f"/api/download/{session_id}/template", headers=headers)
    assert template_download.status_code == 200
    assert len(template_download.content) > 1000

    # Test downloading ERP excel
    excel_download = client.get(f"/api/download/{session_id}/excel", headers=headers)
    assert excel_download.status_code == 200
    assert len(excel_download.content) > 1000
