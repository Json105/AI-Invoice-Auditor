"""Offline unit and API integration tests for OpenRouterExtractor and Web UI integration."""

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from src.database import DEFAULT_DB_PATH, init_db
from src.extractors.openrouter_extractor import (
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_FREE_VISION_MODELS,
    OpenRouterExtractor,
)
from src.main import get_extractor
from src.web.app import app

client = TestClient(app)


def _login_employee_headers() -> dict:
    init_db(DEFAULT_DB_PATH)
    res = client.post(
        "/api/auth/login",
        json={"email": "employee@company.local", "password": "User1234!"},
    )
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_openrouter_single_and_multi_receipt_with_think_tags(tmp_path):
    """Strip <think> tags & fences and parse both single-receipt and multi-receipt wrappers."""
    img = tmp_path / "receipt.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    single_payload = {
        "document_type": "commercial_invoice",
        "invoice_number": "OR-991100",
        "invoice_date": "2026-11-20",
        "seller_name": "Bangkok Airport Bistro",
        "currency": "THB",
        "total_amount": 480.0,
        "expense_category": "F",
    }
    multi_payload = {
        "invoices": [
            {
                "document_type": "taxi_receipt",
                "invoice_number": "OR-TAXI-01",
                "invoice_date": "2026-11-20",
                "seller_name": "City Taxi",
                "currency": "NT$",
                "total_amount": 220.0,
            },
            {
                "document_type": "tw_invoice",
                "invoice_number": "AB-90901234",
                "invoice_date": "2026-11-20",
                "seller_name": "統一超商",
                "currency": "NT$",
                "total_amount": 105.0,
            },
        ]
    }

    r1 = MagicMock(status_code=200)
    r1.json.return_value = {
        "choices": [{"message": {"content": f"<think>analyze receipt...</think>\n```json\n{json.dumps(single_payload)}\n```"}}]
    }
    r2 = MagicMock(status_code=200)
    r2.json.return_value = {
        "choices": [{"message": {"content": json.dumps(multi_payload)}}]
    }

    ext = OpenRouterExtractor(api_key="sk-or-test-key", model="google/gemma-4-31b-it:free")
    with patch("src.extractors.openrouter_extractor.requests.post", side_effect=[r1, r2]) as mock_post:
        inv = ext.extract(img)
        assert inv.invoice_number == "OR-991100"
        assert inv.currency == "THB"
        assert inv.total_amount == 480.0

        items = ext.extract_many(img, multi_receipt=True)
        assert len(items) == 2
        assert items[0].invoice_number == "OR-TAXI-01"
        assert items[1].invoice_number == "AB-90901234"
        assert mock_post.call_count == 2


def test_openrouter_rate_limit_429_automatic_fallback(tmp_path):
    """Automatically switch to the next free vision model on HTTP 429 rate-limit."""
    img = tmp_path / "slip.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    resp_429 = MagicMock(status_code=429, text="rate limit exceeded")
    resp_ok = MagicMock(status_code=200)
    resp_ok.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "document_type": "general_receipt",
                        "invoice_number": "FB-429-OK",
                        "invoice_date": "2026-11-21",
                        "seller_name": "Fallback Merchant",
                        "currency": "USD",
                        "total_amount": 42.5,
                    })
                }
            }
        ]
    }

    ext = OpenRouterExtractor(api_key="sk-or-test-key", model=DEFAULT_OPENROUTER_MODEL, enable_fallback=True)
    with patch("src.extractors.openrouter_extractor.requests.post", side_effect=[resp_429, resp_ok]) as mock_post:
        result = ext.extract(img)

    assert result.invoice_number == "FB-429-OK"
    assert mock_post.call_count == 2
    first_model = mock_post.call_args_list[0].kwargs["json"]["model"]
    second_model = mock_post.call_args_list[1].kwargs["json"]["model"]
    assert first_model == OPENROUTER_FREE_VISION_MODELS[0]["id"]
    assert second_model == OPENROUTER_FREE_VISION_MODELS[1]["id"]


def test_openrouter_401_fails_fast_without_fallback(tmp_path):
    """Invalid OpenRouter API key (401/403) must fail immediately without fallback retries."""
    img = tmp_path / "auth_fail.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    resp_401 = MagicMock(status_code=401, text="Invalid API key")
    ext = OpenRouterExtractor(api_key="sk-or-bad-key", enable_fallback=True)
    with patch("src.extractors.openrouter_extractor.requests.post", return_value=resp_401) as mock_post:
        with pytest.raises(RuntimeError, match="authentication failed"):
            ext.extract(img)
    assert mock_post.call_count == 1


def test_openrouter_api_config_audit_and_ui():
    """Verify /api/config, factory, POST /api/audit with engine=openrouter, and UI controls."""
    res_cfg = client.get("/api/config")
    assert res_cfg.status_code == 200
    cfg = res_cfg.json()
    assert cfg["default_openrouter_model"] == DEFAULT_OPENROUTER_MODEL
    assert any(m["id"] == "google/gemma-4-31b-it:free" for m in cfg["openrouter_models"])

    ext = get_extractor("openrouter", api_key="sk-or-factory")
    assert isinstance(ext, OpenRouterExtractor)
    assert ext.model == DEFAULT_OPENROUTER_MODEL

    resp_ok = MagicMock(status_code=200)
    resp_ok.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "document_type": "commercial_invoice",
                        "invoice_number": "OR-WEB-7788",
                        "invoice_date": "2026-10-05",
                        "seller_name": "KL Sentral Hotel",
                        "currency": "MYR",
                        "total_amount": 320.0,
                        "expense_category": "H",
                    })
                }
            }
        ]
    }

    emp_headers = _login_employee_headers()
    with patch("src.extractors.openrouter_extractor.requests.post", return_value=resp_ok) as mock_post:
        res_audit = client.post(
            "/api/audit",
            headers=emp_headers,
            data={
                "engine": "openrouter",
                "api_key": "sk-or-user-key",
                "model": "nvidia/nemotron-3.5-super-vl:free",
                "apply_template": "false",
            },
            files=[("files", ("kl_hotel.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png"))],
        )
    assert res_audit.status_code == 200
    body = res_audit.json()
    assert body["total"] == 1
    assert body["reports"][0]["data"]["invoice_number"] == "OR-WEB-7788"
    assert mock_post.call_args.kwargs["json"]["model"] == "nvidia/nemotron-3.5-super-vl:free"

    res_html = client.get("/")
    assert res_html.status_code == 200
    assert 'value="openrouter"' in res_html.text
    assert 'id="openrouter-model-select"' in res_html.text


def test_env_loader_parses_dotenv_without_overwriting_existing_env(tmp_path, monkeypatch):
    """load_env_file parses comments, export, and quotes while respecting override flag."""
    from src.services.env_loader import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n"
        "# comment line\n"
        "PLAIN_KEY=plain_val # trailing inline comment\n"
        "QUOTED_DOUBLE=\"hello world # kept\"\n"
        "QUOTED_SINGLE='single value'\n"
        "export EXPORTED_KEY=exported_val\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("PLAIN_KEY", raising=False)
    monkeypatch.delenv("QUOTED_DOUBLE", raising=False)
    monkeypatch.delenv("QUOTED_SINGLE", raising=False)
    monkeypatch.setenv("EXPORTED_KEY", "existing_val")

    assert load_env_file(env_file) is True
    assert Path(env_file).exists()
    import os

    assert os.environ["PLAIN_KEY"] == "plain_val"
    assert os.environ["QUOTED_DOUBLE"] == "hello world # kept"
    assert os.environ["QUOTED_SINGLE"] == "single value"
    assert os.environ["EXPORTED_KEY"] == "existing_val"

    assert load_env_file(env_file, override=True) is True
    assert os.environ["EXPORTED_KEY"] == "exported_val"
    assert load_env_file(tmp_path / "missing.env") is False
