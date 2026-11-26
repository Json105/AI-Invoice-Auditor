"""Admin-managed AI engine/model lock policy, quota enforcement, and usage logging."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..database import DEFAULT_DB_PATH as _DB_DEFAULT_PATH, get_db_connection, init_db
from ..extractors.gemini_extractor import DEFAULT_GEMINI_MODEL
from ..extractors.openrouter_extractor import DEFAULT_OPENROUTER_MODEL, OPENROUTER_FREE_VISION_MODELS

# Kept at module level so tests can monkeypatch ai_policy.DEFAULT_DB_PATH cleanly
DEFAULT_DB_PATH = _DB_DEFAULT_PATH
ALLOWED_ENGINES = ("openrouter", "gemini", "ollama", "mock")


class QuotaExceededError(RuntimeError):
    """Raised when a non-admin user exceeds configured daily or monthly AI quota."""


def _model_label(engine: str, model: str) -> str:
    if engine == "openrouter":
        for item in OPENROUTER_FREE_VISION_MODELS:
            if item["id"] == model:
                return item["label"]
        return model
    if engine == "mock":
        return "離線展示模式 (Mock)"
    if engine == "ollama":
        return model or "llama3.2-vision:latest"
    return model or DEFAULT_GEMINI_MODEL


def get_system_ai_config(db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Read singleton AI configuration from SQLite, seeding defaults if empty."""
    target_db = Path(db_path) if db_path is not None else Path(DEFAULT_DB_PATH)
    init_db(target_db)
    conn = get_db_connection(target_db)
    try:
        row = conn.execute(
            """
            SELECT active_engine, active_model, enable_fallback, lock_model_for_non_admin,
                   daily_limit_per_user, monthly_limit_total, updated_by, updated_at
            FROM system_ai_config
            WHERE id = 1
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {
            "active_engine": "mock",
            "active_model": "mock",
            "active_model_label": _model_label("mock", "mock"),
            "enable_fallback": True,
            "lock_model_for_non_admin": True,
            "daily_limit_per_user": 0,
            "monthly_limit_total": 0,
            "updated_by": "system",
            "updated_at": None,
        }

    engine_val = (row["active_engine"] or "mock").strip().lower()
    model_val = (row["active_model"] or "").strip() or (
        DEFAULT_OPENROUTER_MODEL if engine_val == "openrouter" else DEFAULT_GEMINI_MODEL
    )
    return {
        "active_engine": engine_val,
        "active_model": model_val,
        "active_model_label": _model_label(engine_val, model_val),
        "enable_fallback": bool(row["enable_fallback"]),
        "lock_model_for_non_admin": bool(row["lock_model_for_non_admin"]),
        "daily_limit_per_user": int(row["daily_limit_per_user"] or 0),
        "monthly_limit_total": int(row["monthly_limit_total"] or 0),
        "updated_by": row["updated_by"] or "system",
        "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
    }


def update_system_ai_config(
    active_engine: str,
    active_model: Optional[str],
    enable_fallback: bool,
    lock_model_for_non_admin: bool,
    daily_limit_per_user: int,
    monthly_limit_total: int,
    updated_by: str,
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Validate and update singleton AI governance configuration."""
    engine_val = (active_engine or "").strip().lower()
    if engine_val not in ALLOWED_ENGINES:
        raise ValueError(f"不支援的 AI 引擎 '{active_engine}'，允許選項：{', '.join(ALLOWED_ENGINES)}")

    model_val = (active_model or "").strip()
    if not model_val:
        if engine_val == "openrouter":
            model_val = DEFAULT_OPENROUTER_MODEL
        elif engine_val == "gemini":
            model_val = DEFAULT_GEMINI_MODEL
        elif engine_val == "ollama":
            model_val = "llama3.2-vision:latest"
        else:
            model_val = "mock"

    daily_cap = max(0, int(daily_limit_per_user))
    monthly_cap = max(0, int(monthly_limit_total))

    target_db = Path(db_path) if db_path is not None else Path(DEFAULT_DB_PATH)
    init_db(target_db)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db_connection(target_db)
    with conn:
        conn.execute(
            """
            UPDATE system_ai_config
            SET active_engine = ?,
                active_model = ?,
                enable_fallback = ?,
                lock_model_for_non_admin = ?,
                daily_limit_per_user = ?,
                monthly_limit_total = ?,
                updated_by = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                engine_val,
                model_val,
                1 if enable_fallback else 0,
                1 if lock_model_for_non_admin else 0,
                daily_cap,
                monthly_cap,
                updated_by or "admin",
                now_str,
            ),
        )
    conn.close()
    return get_system_ai_config(target_db)


def resolve_effective_ai_execution(
    user: Any,
    requested_engine: Optional[str],
    requested_model: Optional[str],
    requested_api_key: Optional[str],
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Enforce admin-configured engine/model for non-admin users when lock is active."""
    policy = get_system_ai_config(db_path)
    is_admin = bool(user and getattr(user, "role", "") == "admin")

    if not is_admin and policy["lock_model_for_non_admin"]:
        return {
            "engine": policy["active_engine"],
            "model": policy["active_model"],
            "api_key": None,  # Non-admin must use server-configured credential only
            "enable_fallback": policy["enable_fallback"],
            "policy_locked": True,
        }

    engine_val = (requested_engine or "").strip().lower() or policy["active_engine"]
    model_val = (requested_model or "").strip()
    if not model_val:
        if engine_val == policy["active_engine"]:
            model_val = policy["active_model"]
        elif engine_val == "openrouter":
            model_val = DEFAULT_OPENROUTER_MODEL
        elif engine_val == "ollama":
            model_val = "llama3.2-vision:latest"
        elif engine_val == "mock":
            model_val = "mock"
        else:
            model_val = DEFAULT_GEMINI_MODEL

    api_key_val = (requested_api_key or "").strip() or None
    return {
        "engine": engine_val,
        "model": model_val,
        "api_key": api_key_val,
        "enable_fallback": policy["enable_fallback"],
        "policy_locked": False,
    }


def record_ai_usage(
    user: Any,
    engine: str,
    model: str,
    documents_count: int,
    ai_calls_count: int,
    qr_fast_decoded_count: int = 0,
    source: str = "web_audit",
    status: str = "success",
    db_path: Optional[Union[str, Path]] = None,
) -> None:
    """Persist an AI usage log entry to SQLite."""
    target_db = Path(db_path) if db_path is not None else Path(DEFAULT_DB_PATH)
    init_db(target_db)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    user_id = getattr(user, "id", None)
    user_email = getattr(user, "email", None) or "system"
    user_name = getattr(user, "full_name", None) or "系統排程"

    conn = get_db_connection(target_db)
    with conn:
        conn.execute(
            """
            INSERT INTO ai_usage_logs (
                user_id, user_email, user_name, engine, model,
                documents_count, ai_calls_count, qr_fast_decoded_count,
                source, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                user_email,
                user_name,
                (engine or "mock").lower(),
                model or "unknown",
                max(0, int(documents_count)),
                max(0, int(ai_calls_count)),
                max(0, int(qr_fast_decoded_count)),
                source,
                status,
                now_str,
            ),
        )
    conn.close()


def check_ai_quota(
    user: Any,
    requested_units: int = 1,
    source: str = "web_audit",
    db_path: Optional[Union[str, Path]] = None,
) -> None:
    """Enforce per-user daily and organization monthly AI call quotas for non-admin roles."""
    if user and getattr(user, "role", "") == "admin":
        return

    policy = get_system_ai_config(db_path)
    daily_limit = int(policy.get("daily_limit_per_user") or 0)
    monthly_limit = int(policy.get("monthly_limit_total") or 0)
    if daily_limit <= 0 and monthly_limit <= 0:
        return

    target_db = Path(db_path) if db_path is not None else Path(DEFAULT_DB_PATH)
    init_db(target_db)
    now = datetime.utcnow()
    today_prefix = now.strftime("%Y-%m-%d")
    month_prefix = now.strftime("%Y-%m")
    units = max(1, int(requested_units))
    user_id = getattr(user, "id", None)

    conn = get_db_connection(target_db)
    try:
        if daily_limit > 0 and user_id is not None:
            row_day = conn.execute(
                """
                SELECT COALESCE(SUM(ai_calls_count), 0) AS used
                FROM ai_usage_logs
                WHERE status = 'success'
                  AND user_id = ?
                  AND substr(created_at, 1, 10) = ?
                """,
                (user_id, today_prefix),
            ).fetchone()
            used_today = int(row_day["used"] or 0) if row_day else 0
            if used_today + units > daily_limit:
                record_ai_usage(
                    user=user,
                    engine=policy["active_engine"],
                    model=policy["active_model"],
                    documents_count=0,
                    ai_calls_count=0,
                    source=source,
                    status="quota_exceeded",
                    db_path=target_db,
                )
                raise QuotaExceededError(
                    f"已達今日 AI 辨識配額上限（已用 {used_today} / 每日上限 {daily_limit} 次）；請明日再試或聯繫管理員調整配額。"
                )

        if monthly_limit > 0:
            row_month = conn.execute(
                """
                SELECT COALESCE(SUM(ai_calls_count), 0) AS used
                FROM ai_usage_logs
                WHERE status = 'success'
                  AND substr(created_at, 1, 7) = ?
                """,
                (month_prefix,),
            ).fetchone()
            used_month = int(row_month["used"] or 0) if row_month else 0
            if used_month + units > monthly_limit:
                record_ai_usage(
                    user=user,
                    engine=policy["active_engine"],
                    model=policy["active_model"],
                    documents_count=0,
                    ai_calls_count=0,
                    source=source,
                    status="quota_exceeded",
                    db_path=target_db,
                )
                raise QuotaExceededError(
                    f"已達全公司本月 AI 辨識總量上限（已用 {used_month} / 每月上限 {monthly_limit} 次），請聯繫系統管理員。"
                )
    finally:
        conn.close()


def get_ai_usage_summary(db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Return aggregated AI usage KPIs, breakdown by model/user, and recent logs."""
    target_db = Path(db_path) if db_path is not None else Path(DEFAULT_DB_PATH)
    init_db(target_db)
    policy = get_system_ai_config(target_db)

    now = datetime.utcnow()
    today_prefix = now.strftime("%Y-%m-%d")
    month_prefix = now.strftime("%Y-%m")

    conn = get_db_connection(target_db)
    try:
        today_row = conn.execute(
            """
            SELECT COALESCE(SUM(ai_calls_count), 0) AS calls,
                   COALESCE(SUM(documents_count), 0) AS docs,
                   COALESCE(SUM(qr_fast_decoded_count), 0) AS qr
            FROM ai_usage_logs
            WHERE status = 'success' AND substr(created_at, 1, 10) = ?
            """,
            (today_prefix,),
        ).fetchone()
        month_row = conn.execute(
            """
            SELECT COALESCE(SUM(ai_calls_count), 0) AS calls,
                   COALESCE(SUM(documents_count), 0) AS docs,
                   COALESCE(SUM(qr_fast_decoded_count), 0) AS qr
            FROM ai_usage_logs
            WHERE status = 'success' AND substr(created_at, 1, 7) = ?
            """,
            (month_prefix,),
        ).fetchone()
        total_row = conn.execute(
            """
            SELECT COALESCE(SUM(ai_calls_count), 0) AS calls,
                   COALESCE(SUM(documents_count), 0) AS docs,
                   COALESCE(SUM(qr_fast_decoded_count), 0) AS qr
            FROM ai_usage_logs
            WHERE status = 'success'
            """
        ).fetchone()

        by_model_rows = conn.execute(
            """
            SELECT engine, model,
                   COALESCE(SUM(ai_calls_count), 0) AS ai_calls,
                   COALESCE(SUM(documents_count), 0) AS documents,
                   COUNT(*) AS request_count
            FROM ai_usage_logs
            WHERE status = 'success'
            GROUP BY engine, model
            ORDER BY ai_calls DESC, documents DESC
            """
        ).fetchall()

        by_user_rows = conn.execute(
            """
            SELECT user_email, user_name,
                   COALESCE(SUM(CASE WHEN substr(created_at, 1, 10) = ? THEN ai_calls_count ELSE 0 END), 0) AS today_calls,
                   COALESCE(SUM(CASE WHEN substr(created_at, 1, 7) = ? THEN ai_calls_count ELSE 0 END), 0) AS month_calls,
                   COALESCE(SUM(documents_count), 0) AS total_documents
            FROM ai_usage_logs
            WHERE status = 'success'
            GROUP BY user_email, user_name
            ORDER BY month_calls DESC, total_documents DESC
            LIMIT 20
            """,
            (today_prefix, month_prefix),
        ).fetchall()

        recent_rows = conn.execute(
            """
            SELECT id, user_email, user_name, engine, model, documents_count,
                   ai_calls_count, qr_fast_decoded_count, source, status, created_at
            FROM ai_usage_logs
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()
    finally:
        conn.close()

    by_model: List[Dict[str, Any]] = [
        {
            "engine": r["engine"],
            "model": r["model"],
            "ai_calls": int(r["ai_calls"] or 0),
            "documents": int(r["documents"] or 0),
            "request_count": int(r["request_count"] or 0),
        }
        for r in by_model_rows
    ]
    by_user: List[Dict[str, Any]] = [
        {
            "user_email": r["user_email"],
            "user_name": r["user_name"],
            "today_calls": int(r["today_calls"] or 0),
            "month_calls": int(r["month_calls"] or 0),
            "total_documents": int(r["total_documents"] or 0),
        }
        for r in by_user_rows
    ]
    recent_logs: List[Dict[str, Any]] = [
        {
            "id": r["id"],
            "user_email": r["user_email"],
            "user_name": r["user_name"],
            "engine": r["engine"],
            "model": r["model"],
            "documents_count": int(r["documents_count"] or 0),
            "ai_calls_count": int(r["ai_calls_count"] or 0),
            "qr_fast_decoded_count": int(r["qr_fast_decoded_count"] or 0),
            "source": r["source"],
            "status": r["status"],
            "created_at": str(r["created_at"]),
        }
        for r in recent_rows
    ]

    return {
        "today_ai_calls": int(today_row["calls"] or 0) if today_row else 0,
        "today_documents": int(today_row["docs"] or 0) if today_row else 0,
        "today_qr_saved": int(today_row["qr"] or 0) if today_row else 0,
        "month_ai_calls": int(month_row["calls"] or 0) if month_row else 0,
        "month_documents": int(month_row["docs"] or 0) if month_row else 0,
        "month_qr_saved": int(month_row["qr"] or 0) if month_row else 0,
        "total_ai_calls": int(total_row["calls"] or 0) if total_row else 0,
        "total_documents": int(total_row["docs"] or 0) if total_row else 0,
        "total_qr_saved": int(total_row["qr"] or 0) if total_row else 0,
        "policy": policy,
        "by_model": by_model,
        "by_user": by_user,
        "recent_logs": recent_logs,
    }
