"""FastAPI routes for Scanner Watch Folder status and one-click/triggered ingestion."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..database import DEFAULT_DB_PATH, User, get_expense_trip_by_id
from ..extractors.gemini_extractor import DEFAULT_GEMINI_MODEL
from ..services.ai_policy import (
    QuotaExceededError,
    check_ai_quota,
    record_ai_usage,
    resolve_effective_ai_execution,
)
from ..services.watch_folder import watch_folder_service
from .auth_routes import get_current_user_required, is_privileged_user

watch_router = APIRouter(prefix="/api/watch-folder", tags=["Watch Folder"])


class WatchScanRequest(BaseModel):
    engine: str = "mock"
    api_key: Optional[str] = None
    model: str = DEFAULT_GEMINI_MODEL
    trip_id: Optional[int] = None
    split_pdf: bool = True
    multi_receipt: bool = False
    fx_mode: str = "auto_bot"
    manual_exchange_rate: Optional[float] = Field(None, gt=0)


@watch_router.get("/status")
async def get_watch_folder_status(
    current_user: User = Depends(get_current_user_required),
):
    """Return current watch inbox directory path and queued files awaiting ingestion."""
    return watch_folder_service.get_status()


@watch_router.post("/scan")
async def trigger_watch_folder_scan(
    req: Optional[WatchScanRequest] = None,
    current_user: User = Depends(get_current_user_required),
):
    """Scan watch inbox, ingest pending PDFs/images, and bind to trip if requested or subfolder-matched."""
    cfg = req or WatchScanRequest()
    if cfg.trip_id is not None:
        trip = get_expense_trip_by_id(cfg.trip_id, db_path=DEFAULT_DB_PATH)
        if not trip:
            raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{cfg.trip_id}")
        if not is_privileged_user(current_user) and trip.get("user_id") != current_user.id:
            raise HTTPException(status_code=403, detail="無權限匯入至其他同仁的出差專案")
        if (trip.get("status") or "").upper() in ("PAID", "REJECTED"):
            raise HTTPException(status_code=400, detail="此出差專案已撥款結案或駁回，無法再匯入單據")

    effective_ai = resolve_effective_ai_execution(
        user=current_user,
        requested_engine=cfg.engine,
        requested_model=cfg.model,
        requested_api_key=cfg.api_key,
    )

    pending_count = int(watch_folder_service.get_status().get("pending_count") or 0)
    if pending_count > 0:
        try:
            check_ai_quota(current_user, requested_units=pending_count, source="watch_folder")
        except QuotaExceededError as exc:
            raise HTTPException(status_code=429, detail=str(exc))

    try:
        result = watch_folder_service.scan_and_ingest(
            user=current_user,
            engine=effective_ai["engine"],
            api_key=effective_ai["api_key"],
            model=effective_ai["model"],
            trip_id=cfg.trip_id,
            split_pdf=cfg.split_pdf,
            multi_receipt=cfg.multi_receipt,
            fx_mode=cfg.fx_mode,
            manual_exchange_rate=cfg.manual_exchange_rate,
            is_privileged=is_privileged_user(current_user),
            enable_fallback=effective_ai["enable_fallback"],
        )
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"掃描匯入失敗: {str(exc)}")

    actual_model = result.get("actual_model") or effective_ai["model"]
    if result.get("scanned_files_count", 0) > 0:
        ai_calls = max(0, int(result.get("expanded_pages_count", 0)) - int(result.get("qr_fast_decoded_count", 0)))
        try:
            record_ai_usage(
                user=current_user,
                engine=effective_ai["engine"],
                model=actual_model,
                documents_count=int(result.get("expanded_pages_count", 0)),
                ai_calls_count=ai_calls,
                qr_fast_decoded_count=int(result.get("qr_fast_decoded_count", 0)),
                source="watch_folder",
                status="success",
            )
        except Exception:
            pass

    result["ai_execution"] = {
        "engine": effective_ai["engine"],
        "model": actual_model,
        "policy_locked": effective_ai["policy_locked"],
    }
    return result
