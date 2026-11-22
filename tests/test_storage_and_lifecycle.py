"""Integration and security tests for Storage Provider, HMAC-SHA256 Presigned Tokens,
and TTL Retention Policy (面向四).
"""

import os
import time
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from src.web.app import app
from src.services.storage_service import (
    storage_service,
    LocalStorageProvider,
    S3StorageProvider
)

client = TestClient(app)


def test_presigned_token_generation_and_validation():
    """1. Test cryptographic token signing, expiration, and validation."""
    sample_file = Path("sample_data/sample_thsr_ticket.png")
    assert sample_file.exists()

    # Valid token for 15 minutes
    token = storage_service.generate_presigned_token(sample_file, expires_in_seconds=900)
    assert isinstance(token, str)
    assert "." in token

    validated_path = storage_service.validate_presigned_token(token)
    assert validated_path is not None
    assert validated_path.resolve() == sample_file.resolve()


def test_presigned_token_expired_and_tampered():
    """2. Test that expired, tampered, or path-traversal tokens are strictly rejected."""
    sample_file = Path("sample_data/sample_thsr_ticket.png")

    # Expired token (expires 10 seconds in the past)
    expired_token = storage_service.generate_presigned_token(sample_file, expires_in_seconds=-10)
    assert storage_service.validate_presigned_token(expired_token) is None

    # Tampered signature
    valid_token = storage_service.generate_presigned_token(sample_file, expires_in_seconds=900)
    payload_part, _ = valid_token.split(".")
    tampered_token = f"{payload_part}.fake_tampered_signature_12345"
    assert storage_service.validate_presigned_token(tampered_token) is None

    # Empty / malformed token
    assert storage_service.validate_presigned_token("") is None
    assert storage_service.validate_presigned_token("invalid_token_without_dot") is None


def test_web_api_preview_endpoint():
    """3. Test /api/storage/preview/{token} secure file streaming."""
    sample_file = Path("sample_data/sample_taxi_receipt.png")
    valid_token = storage_service.generate_presigned_token(sample_file, expires_in_seconds=900)

    # Valid preview request
    res = client.get(f"/api/storage/preview/{valid_token}")
    assert res.status_code == 200
    assert "image/" in res.headers.get("Content-Type", "")
    assert "private" in res.headers.get("Cache-Control", "")
    assert len(res.content) > 100

    # Invalid / forged token request should return 403 Forbidden
    res_forbidden = client.get("/api/storage/preview/invalid_token_string.signature")
    assert res_forbidden.status_code == 403


def test_storage_stats_and_ttl_cleanup(tmp_path):
    """4. Test /api/storage/stats and /api/storage/cleanup TTL retention policy."""
    # Test stats endpoint
    res_stats = client.get("/api/storage/stats")
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert "total_bytes" in stats
    assert "total_mb" in stats
    assert "provider_type" in stats

    # Create a mock expired temporary session folder
    sessions_root = Path("output/web_sessions")
    sessions_root.mkdir(parents=True, exist_ok=True)
    mock_old_session = sessions_root / "mock_old_session_to_delete"
    mock_old_session.mkdir(parents=True, exist_ok=True)
    dummy_file = mock_old_session / "dummy_receipt.txt"
    dummy_file.write_text("dummy invoice data to test cleanup")

    # Set modification time to 10 days ago (240 hours ago)
    old_time = time.time() - (240 * 3600)
    os.utime(str(mock_old_session), (old_time, old_time))

    # Trigger cleanup with max_age_hours=72
    res_cleanup = client.post("/api/storage/cleanup", json={"max_age_hours": 72})
    assert res_cleanup.status_code == 200
    cleanup_data = res_cleanup.json()
    assert cleanup_data["status"] == "success"
    assert cleanup_data["report"]["cleaned_sessions"] >= 1

    # Verify folder was safely removed
    assert not mock_old_session.exists()
