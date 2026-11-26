"""Offline integration tests for Admin AI model lock, RBAC, quota limits, and usage audit logs."""

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from src.database import DEFAULT_DB_PATH, init_db
from src.services import ai_policy
from src.web.admin_views import AIUsageLogAdmin, SystemAIConfigAdmin, setup_admin
from src.web.app import app

pytestmark = pytest.mark.real_ai_policy

client = TestClient(app)


def _login_headers(email: str, password: str) -> dict:
    init_db(DEFAULT_DB_PATH)
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def isolated_policy_db(tmp_path, monkeypatch):
    """Route all ai_policy reads/writes to an isolated temporary SQLite database."""
    test_db = tmp_path / "ai_policy_test.db"
    init_db(test_db)
    monkeypatch.setattr(ai_policy, "DEFAULT_DB_PATH", test_db)
    return test_db


def test_config_exposes_ai_policy_and_preserves_default_engine(isolated_policy_db):
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert data["default_engine"] == "gemini"
    assert "ai_policy" in data
    assert "lock_model_for_non_admin" in data["ai_policy"]


def test_admin_ai_policy_rbac_and_persistence(isolated_policy_db):
    emp_h = _login_headers("employee@company.local", "User1234!")
    fin_h = _login_headers("finance@company.local", "Finance1234!")
    adm_h = _login_headers("admin@company.local", "Admin1234!")

    # Employee cannot read or write policy
    assert client.get("/api/admin/ai-policy", headers=emp_h).status_code == 403
    assert client.put(
        "/api/admin/ai-policy",
        headers=emp_h,
        json={"active_engine": "mock"},
    ).status_code == 403

    # Accountant can inspect policy (read-only) but cannot mutate
    fin_get = client.get("/api/admin/ai-policy", headers=fin_h)
    assert fin_get.status_code == 200
    assert fin_get.json()["can_edit"] is False
    assert client.put(
        "/api/admin/ai-policy",
        headers=fin_h,
        json={"active_engine": "mock"},
    ).status_code == 403

    # Admin can inspect and mutate policy
    adm_put = client.put(
        "/api/admin/ai-policy",
        headers=adm_h,
        json={
            "active_engine": "openrouter",
            "active_model": "nvidia/nemotron-3.5-super-vl:free",
            "enable_fallback": True,
            "lock_model_for_non_admin": True,
            "daily_limit_per_user": 15,
            "monthly_limit_total": 300,
        },
    )
    assert adm_put.status_code == 200
    policy = adm_put.json()["policy"]
    assert policy["active_engine"] == "openrouter"
    assert policy["active_model"] == "nvidia/nemotron-3.5-super-vl:free"
    assert policy["daily_limit_per_user"] == 15
    assert policy["monthly_limit_total"] == 300
    assert policy["updated_by"] == "admin@company.local"


def test_backend_hard_locks_employee_model_and_records_usage(isolated_policy_db, monkeypatch):
    adm_h = _login_headers("admin@company.local", "Admin1234!")
    emp_h = _login_headers("employee@company.local", "User1234!")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-server-secret")

    client.put(
        "/api/admin/ai-policy",
        headers=adm_h,
        json={
            "active_engine": "openrouter",
            "active_model": "google/gemma-4-31b-it:free",
            "enable_fallback": False,
            "lock_model_for_non_admin": True,
            "daily_limit_per_user": 0,
            "monthly_limit_total": 0,
        },
    )

    resp_ok = MagicMock(status_code=200)
    resp_ok.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "document_type": "commercial_invoice",
                        "invoice_number": "GOV-LOCK-001",
                        "invoice_date": "2026-11-26",
                        "seller_name": "Locked Vendor",
                        "currency": "USD",
                        "total_amount": 88.0,
                        "expense_category": "H",
                    })
                }
            }
        ]
    }

    with patch("src.extractors.openrouter_extractor.requests.post", return_value=resp_ok) as mock_post:
        # Employee maliciously/accidentally tries to pick mock or a different model + personal key
        res_emp = client.post(
            "/api/audit",
            headers=emp_h,
            data={
                "engine": "openrouter",
                "model": "nvidia/nemotron-3.5-super-vl:free",
                "api_key": "sk-or-rogue-user-key",
                "apply_template": "false",
            },
            files=[("files", ("emp.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png"))],
        )
        assert res_emp.status_code == 200
        body_emp = res_emp.json()
        assert body_emp["ai_execution"]["engine"] == "openrouter"
        assert body_emp["ai_execution"]["model"] == "google/gemma-4-31b-it:free"
        assert body_emp["ai_execution"]["policy_locked"] is True
        assert mock_post.call_args.kwargs["json"]["model"] == "google/gemma-4-31b-it:free"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-or-server-secret"

        # Admin is exempt and can freely override model
        res_adm = client.post(
            "/api/audit",
            headers=adm_h,
            data={
                "engine": "openrouter",
                "model": "nvidia/nemotron-3.5-super-vl:free",
                "apply_template": "false",
            },
            files=[("files", ("adm.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png"))],
        )
        assert res_adm.status_code == 200
        assert res_adm.json()["ai_execution"]["model"] == "nvidia/nemotron-3.5-super-vl:free"
        assert res_adm.json()["ai_execution"]["policy_locked"] is False

    # Usage endpoint aggregates both calls
    res_usage = client.get("/api/admin/ai-usage", headers=adm_h)
    assert res_usage.status_code == 200
    usage = res_usage.json()
    assert usage["today_ai_calls"] == 2
    assert usage["month_ai_calls"] == 2
    assert len(usage["by_model"]) == 2
    assert len(usage["recent_logs"]) == 2

    # Employee cannot query admin usage endpoint
    assert client.get("/api/admin/ai-usage", headers=emp_h).status_code == 403


def test_quota_limits_and_watch_folder_lock(isolated_policy_db, tmp_path):
    adm_h = _login_headers("admin@company.local", "Admin1234!")
    emp_h = _login_headers("employee@company.local", "User1234!")
    fin_h = _login_headers("finance@company.local", "Finance1234!")

    # Lock to mock engine (zero external HTTP) with daily_limit_per_user=1, monthly_limit_total=2
    client.put(
        "/api/admin/ai-policy",
        headers=adm_h,
        json={
            "active_engine": "mock",
            "active_model": "mock",
            "enable_fallback": False,
            "lock_model_for_non_admin": True,
            "daily_limit_per_user": 1,
            "monthly_limit_total": 2,
        },
    )

    # First employee call succeeds (uses 1 of employee's daily 1 and org's monthly 2)
    r1 = client.post(
        "/api/audit",
        headers=emp_h,
        data={"engine": "gemini", "apply_template": "false"},
        files=[("files", ("sample_thsr_ticket.png", io.BytesIO(b"{}"), "image/png"))],
    )
    assert r1.status_code == 200
    assert r1.json()["ai_execution"]["engine"] == "mock"
    assert r1.json()["ai_execution"]["policy_locked"] is True

    # Second employee call exceeds daily_limit_per_user=1 -> HTTP 429
    r2 = client.post(
        "/api/audit",
        headers=emp_h,
        data={"engine": "mock", "apply_template": "false"},
        files=[("files", ("sample_taxi_receipt.png", io.BytesIO(b"{}"), "image/png"))],
    )
    assert r2.status_code == 429

    # Accountant hits watch-folder lock + consumes 2nd org monthly slot
    from src.services.watch_folder import watch_folder_service

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "sample_thsr_ticket.png").write_bytes(b"{}")
    orig_dir = watch_folder_service.watch_dir
    watch_folder_service.watch_dir = inbox
    try:
        r_watch = client.post(
            "/api/watch-folder/scan",
            headers=fin_h,
            json={"engine": "gemini", "model": "gemini-3.7-flash"},
        )
        assert r_watch.status_code == 200
        assert r_watch.json()["ai_execution"]["engine"] == "mock"
        assert r_watch.json()["ai_execution"]["policy_locked"] is True

        # Now monthly_limit_total=2 is exhausted -> next non-admin watch scan returns HTTP 429
        (inbox / "sample_taxi_receipt.png").write_bytes(b"{}")
        r_watch_blocked = client.post(
            "/api/watch-folder/scan",
            headers=fin_h,
            json={"engine": "mock"},
        )
        assert r_watch_blocked.status_code == 429
    finally:
        watch_folder_service.watch_dir = orig_dir

    # Admin bypasses quota limits cleanly
    r_adm = client.post(
        "/api/audit",
        headers=adm_h,
        data={"engine": "mock", "apply_template": "false"},
        files=[("files", ("sample_thsr_ticket.png", io.BytesIO(b"{}"), "image/png"))],
    )
    assert r_adm.status_code == 200


def test_sqladmin_views_and_ui_governance_elements(isolated_policy_db):
    admin = setup_admin(app)
    model_classes = [v.model.__name__ for v in admin.views]
    assert "SystemAIConfigDB" in model_classes
    assert "AIUsageLogDB" in model_classes
    assert SystemAIConfigAdmin.can_delete is False
    assert AIUsageLogAdmin.can_edit is False

    html = client.get("/").text
    assert 'id="admin-engine-controls"' in html
    assert 'id="managed-ai-policy-banner"' in html
    assert 'id="ai-governance-card"' in html
    assert 'id="admin-ai-policy-form"' in html
