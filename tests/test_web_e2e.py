"""End-to-End integration tests for Web Portal, API, and Audit engine."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient
import openpyxl

from src.database import init_db, DEFAULT_DB_PATH
from src.web.app import app

client = TestClient(app)


def _login_admin_headers() -> dict:
    init_db(DEFAULT_DB_PATH)
    res = client.post(
        "/api/auth/login",
        json={"email": "admin@company.local", "password": "Admin1234!"},
    )
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_server_health_and_config():
    """1. Test server health, version, and config endpoints."""
    # Health check
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    health_data = res_health.json()
    assert health_data.get("status") == "healthy"
    assert "AI-Invoice-Auditor" in health_data.get("service", "")

    # Config check
    res_cfg = client.get("/api/config")
    assert res_cfg.status_code == 200
    cfg_data = res_cfg.json()
    assert cfg_data.get("template_available") is True
    assert cfg_data.get("default_engine") == "gemini"


def test_static_html_and_branding():
    """2. Test Web UI HTML content, clean branding, 3-step workflow, and badges."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # No specific German branding
    assert "德商" not in html, "Found '德商' in HTML"

    # Scheme D System Branding
    assert "AI-Invoice Auditor | 智慧報銷平台" in html
    assert "智慧報銷平台 · Enterprise Edition" in html

    # Clean Navigation Tabs
    assert "單據辨識與稽核" in html
    assert "財務審批中心" in html
    assert "系統用量分析" in html
    assert "歷史單據記錄" in html
    assert "Stitch UI" not in html

    # 3-Step Workflow Stepper
    assert "上傳單據" in html
    assert "智慧稽核" in html
    assert "匯出報表" in html
    assert "step-num" in html
    assert "guide-steps" in html

    # 4 Audit Capability Badges
    assert "8 碼統編校驗" in html
    assert "5% 稅額平準計算" in html
    assert "跨月防重複請款" in html
    assert "多幣別即時折算" in html


def test_audit_api_workflow():
    """3. Test mock engine document ingestion, math checks, and report generation."""
    sample_dir = Path("sample_data")
    sample_files = [
        "sample_china_vat_invoice.json",
        "sample_thailand_grab.json",
        "sample_overseas_hotel.json",
        "sample_thsr_ticket.png",
        "sample_taxi_receipt.png",
        "sample_invoice_tw_b2b.json",
        "sample_invoice_error_math.json"
    ]

    files_payload = []
    opened_files = []
    try:
        for fname in sample_files:
            fpath = sample_dir / fname
            if fpath.exists():
                f = open(fpath, "rb")
                opened_files.append(f)
                files_payload.append(("files", (fname, f, "application/octet-stream")))
            else:
                files_payload.append(("files", (fname, b"{}", "application/json")))

        data_payload = {
            "engine": "mock",
            "model": "gemini-3.7-flash",
            "apply_template": "false"
        }
        headers = _login_admin_headers()

        res = client.post("/api/audit", data=data_payload, files=files_payload, headers=headers)
        assert res.status_code == 200
        result = res.json()

        # Check document summary counts
        assert result.get("total") == 7
        assert result.get("rejected") >= 1, "Expected duplicates/math errors to be flagged"

        # Check math error detection on sample_invoice_error_math.json
        math_err = next((r for r in result.get("reports", []) if "error_math" in r.get("file_path", "")), None)
        assert math_err is not None
        assert math_err.get("status") in ("WARNING", "REJECTED")

        # Check download links generated
        dl_urls = result.get("download_urls", {})
        assert bool(dl_urls.get("excel")) is True
        assert bool(dl_urls.get("json")) is True

        # Verify Excel download is valid
        res_excel = client.get(dl_urls["excel"], headers=headers)
        assert res_excel.status_code == 200
        assert len(res_excel.content) > 3000

    finally:
        for f in opened_files:
            f.close()


def test_history_and_stats_api():
    """4. Test SQLite persistence, audit history querying, and stats dashboard."""
    headers = _login_admin_headers()
    # Query history
    res_hist = client.get("/api/history?limit=10", headers=headers)
    assert res_hist.status_code == 200
    hist = res_hist.json().get("history", [])
    assert len(hist) > 0

    # Query statistics
    res_stats = client.get("/api/stats", headers=headers)
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert stats.get("total_invoices", 0) > 0

