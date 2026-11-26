"""Pytest fixtures keeping legacy offline tests isolated from DB AI lock policy."""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_ai_policy: test against actual DB-backed AI policy & quota enforcement without mock override",
    )


@pytest.fixture(autouse=True)
def _isolate_legacy_tests_from_locked_ai_policy(request, monkeypatch):
    """Allow legacy offline tests (passing engine='mock') to run unless test opts into real_ai_policy."""
    if request.node.get_closest_marker("real_ai_policy") is not None:
        return

    monkeypatch.setattr(
        "src.services.ai_policy.get_system_ai_config",
        lambda db_path=None: {
            "active_engine": "mock",
            "active_model": "mock",
            "active_model_label": "離線展示模式 (Mock)",
            "enable_fallback": True,
            "lock_model_for_non_admin": False,
            "daily_limit_per_user": 0,
            "monthly_limit_total": 0,
            "updated_by": "pytest",
            "updated_at": None,
        },
    )
