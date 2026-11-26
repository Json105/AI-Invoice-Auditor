"""FastAPI routes for document upload, auditing, history, file preview, and Excel exports."""

import os
import re
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..models import AuditStatus
from ..exporter import ExpenseExporter
from ..extractors.gemini_extractor import DEFAULT_GEMINI_MODEL
from ..extractors.openrouter_extractor import DEFAULT_OPENROUTER_MODEL, OPENROUTER_FREE_VISION_MODELS
from ..template_exporter import TemplateExpenseExporter
from ..main import get_extractor
from ..database import (
    User,
    save_audit_reports,
    get_audit_history,
    get_audit_statistics,
    get_audit_record_by_id,
    get_audit_records_for_export,
    get_expense_trip_by_id,
    update_record_exchange_rate,
    DEFAULT_DB_PATH
)
from .auth_routes import get_current_user_required, is_privileged_user

router = APIRouter(prefix="/api")

SESSIONS_DIR = Path("output/web_sessions")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR = Path("data/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".tiff", ".tif", ".bmp", ".json"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB per file


def _sanitize_filename(filename: Optional[str]) -> str:
    """Strip path traversal components and keep safe filename characters."""
    raw_name = Path(filename or "uploaded_file").name
    cleaned = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", raw_name).strip("._")
    return cleaned or "uploaded_file"


def _detect_mime_type(path: Path, fallback: Optional[str] = None) -> str:
    """Resolve canonical MIME type for stored receipt files."""
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".bmp": "image/bmp",
        ".json": "application/json",
    }
    return mime_map.get(path.suffix.lower()) or fallback or "application/octet-stream"


def _resolve_effective_user_id(current_user: User, requested_user_id: Optional[int]) -> Optional[int]:
    """Enforce per-user data isolation for non-privileged employees."""
    if is_privileged_user(current_user):
        return requested_user_id
    if requested_user_id is not None and requested_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="無權限查閱其他同仁的報銷單據")
    return current_user.id


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "AI-Invoice-Auditor Enterprise Web Portal",
        "version": "1.0.0"
    }


@router.get("/config")
async def get_config():
    """Return runtime configuration and API key availability."""
    has_key = bool(os.getenv("GEMINI_API_KEY"))
    has_openrouter_key = bool(os.getenv("OPENROUTER_API_KEY"))
    return {
        "has_server_api_key": has_key,
        "has_openrouter_api_key": has_openrouter_key,
        "default_engine": "gemini",
        "default_model": DEFAULT_GEMINI_MODEL,
        "default_openrouter_model": DEFAULT_OPENROUTER_MODEL,
        "openrouter_models": OPENROUTER_FREE_VISION_MODELS,
        "template_available": Path("Template.xlsx").exists()
    }


@router.get("/stats")
async def get_stats(
    user_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user_required),
):
    """Return dashboard statistics from SQLite scoped by user role."""
    effective_user_id = _resolve_effective_user_id(current_user, user_id)
    try:
        return get_audit_statistics(DEFAULT_DB_PATH, user_id=effective_user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_history(
    limit: int = 50,
    offset: int = 0,
    user_id: Optional[int] = Query(None),
    trip_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user_required),
):
    """Return audit history records scoped by user role and filters."""
    effective_user_id = _resolve_effective_user_id(current_user, user_id)
    try:
        records = get_audit_history(
            limit=limit,
            offset=offset,
            db_path=DEFAULT_DB_PATH,
            user_id=effective_user_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            trip_id=trip_id,
        )
        return {"total": len(records), "history": records}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/export")
async def export_history_excel(
    user_id: Optional[int] = Query(None),
    trip_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user_required),
):
    """Export filtered audit history records into Excel (Template.xlsx if available)."""
    effective_user_id = _resolve_effective_user_id(current_user, user_id)
    trip_meta = None
    if trip_id is not None:
        trip_meta = get_expense_trip_by_id(trip_id, db_path=DEFAULT_DB_PATH)
        if not trip_meta:
            raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")
        if not is_privileged_user(current_user) and trip_meta.get("user_id") != current_user.id:
            raise HTTPException(status_code=403, detail="無權限匯出其他同仁的出差報銷專案")

    reports = get_audit_records_for_export(
        db_path=DEFAULT_DB_PATH,
        user_id=effective_user_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        trip_id=trip_id,
        limit=2000 if trip_id is not None else 500,
    )
    if not reports:
        raise HTTPException(status_code=404, detail="查無符合條件的報銷單據可匯出")

    export_dir = SESSIONS_DIR / f"export_{uuid.uuid4().hex[:8]}"
    export_dir.mkdir(parents=True, exist_ok=True)
    template_path = Path("Template.xlsx")
    if template_path.exists():
        out_path = export_dir / "Travel_Expenses_Report_Filtered.xlsx"
        TemplateExpenseExporter(reports, template_path=template_path, trip_meta=trip_meta).export(out_path)
    else:
        out_path = export_dir / "expense_claim_filtered.xlsx"
        ExpenseExporter(reports).to_excel(out_path)

    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/records/{record_id}/file")
async def get_record_file(
    record_id: int,
    current_user: User = Depends(get_current_user_required),
):
    """Retrieve stored original invoice image or PDF with owner/admin authorization."""
    record = get_audit_record_by_id(record_id, db_path=DEFAULT_DB_PATH)
    if not record:
        raise HTTPException(status_code=404, detail="找不到該筆報銷單據")

    if not is_privileged_user(current_user) and record.get("user_id") != current_user.id:
        raise HTTPException(status_code=403, detail="無權限檢視其他同仁的原始憑證")

    stored_path_raw = record.get("stored_file_path")
    if not stored_path_raw:
        raise HTTPException(status_code=404, detail="此單據未保存原始圖檔")

    uploads_root = UPLOADS_DIR.resolve()
    resolved = Path(stored_path_raw).resolve()
    try:
        resolved.relative_to(uploads_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="無效的檔案路徑")

    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="原始憑證檔案不存在")

    return FileResponse(
        path=str(resolved),
        filename=record.get("file_name") or resolved.name,
        media_type=record.get("file_mime_type") or "application/octet-stream",
        content_disposition_type="inline",
    )


@router.post("/audit")
async def audit_documents(
    files: List[UploadFile] = File(...),
    engine: str = Form("mock"),
    api_key: Optional[str] = Form(None),
    model: str = Form(DEFAULT_GEMINI_MODEL),
    apply_template: bool = Form(True),
    trip_id: Optional[int] = Form(None),
    skip_exports: bool = Form(False),
    split_pdf: bool = Form(True),
    multi_receipt: bool = Form(False),
    fx_mode: str = Form("auto_bot"),
    manual_exchange_rate: Optional[float] = Form(None),
    current_user: User = Depends(get_current_user_required),
):
    """Upload documents, split multi-page PDFs if enabled, extract single or multi-receipt claims, audit, and export."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    trip_meta: Optional[Dict] = None
    if trip_id is not None:
        trip_meta = get_expense_trip_by_id(trip_id, db_path=DEFAULT_DB_PATH)
        if not trip_meta:
            raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")
        if not is_privileged_user(current_user) and trip_meta.get("user_id") != current_user.id:
            raise HTTPException(status_code=403, detail="無權限上傳單據至其他同仁的出差專案")
        if (trip_meta.get("status") or "").upper() in ("PAID", "REJECTED"):
            raise HTTPException(status_code=400, detail="此出差專案已撥款結案或駁回，無法再新增單據")

    # Instantiate extractor before persisting files so invalid engine fails fast
    try:
        extractor = get_extractor(engine, api_key=api_key, model=model)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid extractor configuration: {str(e)}")

    session_id = str(uuid.uuid4())[:8]
    session_dir = SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session_meta.json").write_text(
        json.dumps({"owner_id": current_user.id}),
        encoding="utf-8",
    )

    month_dir = UPLOADS_DIR / str(current_user.id) / datetime.now().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)

    from ..services.document_splitter import expand_uploaded_document

    expanded_paths: List[Path] = []
    stored_files: Dict[str, Tuple[str, str]] = {}
    for file in files:
        safe_name = _sanitize_filename(file.filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支援的檔案格式 '{suffix}'（僅支援 JPG/PNG/WEBP/PDF/TIFF/BMP）",
            )

        content = await file.read()
        max_bytes = 50 * 1024 * 1024 if suffix == ".pdf" else MAX_UPLOAD_BYTES
        max_mb = 50 if suffix == ".pdf" else 20
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"檔案 '{safe_name}' 超過 {max_mb} MB 上限",
            )

        target_path = month_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        target_path.write_bytes(content)

        expanded_items = expand_uploaded_document(
            file_path=target_path,
            original_name=safe_name,
            split_multipage_pdf=split_pdf,
        )
        for item_path, _, item_mime in expanded_items:
            resolved_mime = item_mime if suffix == ".pdf" and split_pdf else _detect_mime_type(item_path, file.content_type)
            expanded_paths.append(item_path)
            stored_files[str(item_path)] = (str(item_path), resolved_mime)

    from ..services.ingestion_pipeline import audit_expanded_items

    reports, qr_fast_decoded_count = audit_expanded_items(
        expanded_paths=expanded_paths,
        extractor=extractor,
        multi_receipt=multi_receipt,
        fx_mode=fx_mode,
        manual_exchange_rate=manual_exchange_rate,
        db_path=DEFAULT_DB_PATH,
    )

    download_urls: Dict[str, str] = {}
    if not skip_exports:
        # Generate Generic Reports
        exporter = ExpenseExporter(reports)
        excel_path = session_dir / "expense_claim_report.xlsx"
        json_path = session_dir / "audit_trail.json"
        exporter.to_excel(excel_path)
        exporter.to_json(json_path)

        # Generate Corporate Template Report if Template.xlsx exists
        template_path = Path("Template.xlsx")
        template_filled = False
        if apply_template and template_path.exists():
            t_exporter = TemplateExpenseExporter(reports, template_path=template_path, trip_meta=trip_meta)
            template_out = session_dir / "Travel_Expenses_Report_Filled.xlsx"
            t_exporter.export(template_out)
            template_filled = True

        # Generate Standard Corporate ERP Journal Voucher (會計傳票分錄)
        from ..erp_exporter import ERPExporter
        erp_exporter = ERPExporter(reports)
        erp_csv_path = session_dir / "erp_journal_entry.csv"
        erp_json_path = session_dir / "erp_journal_entry.json"
        erp_exporter.to_csv(erp_csv_path)
        erp_exporter.to_json(erp_json_path)

        download_urls = {
            "excel": f"/api/download/{session_id}/excel",
            "json": f"/api/download/{session_id}/json",
            "erp": f"/api/download/{session_id}/erp"
        }
        if template_filled:
            download_urls["template"] = f"/api/download/{session_id}/template"

    # Persist to SQLite
    try:
        save_audit_reports(
            reports,
            applicant_name=current_user.full_name,
            applicant_email=current_user.email,
            db_path=DEFAULT_DB_PATH,
            user_id=current_user.id,
            stored_files=stored_files,
            trip_id=trip_id,
        )
    except Exception as e:
        print(f"Warning: Failed to save to database: {e}")

    # Summary
    approved = sum(1 for r in reports if r.status == AuditStatus.APPROVED)
    warning = sum(1 for r in reports if r.status == AuditStatus.WARNING)
    rejected = sum(1 for r in reports if r.status == AuditStatus.REJECTED)

    from ..services.storage_service import storage_service
    reports_payload = []
    for r in reports:
        item = r.model_dump(mode="json")
        if r.file_path and Path(r.file_path).exists():
            token = storage_service.generate_presigned_token(r.file_path, expires_in_seconds=900)
            item["preview_url"] = f"/api/storage/preview/{token}"
            item["preview_token"] = token
        reports_payload.append(item)

    return {
        "session_id": session_id,
        "trip_id": trip_id,
        "source_files_count": len(files),
        "expanded_pages_count": len(expanded_paths),
        "qr_fast_decoded_count": qr_fast_decoded_count,
        "total": len(reports),
        "approved": approved,
        "warning": warning,
        "rejected": rejected,
        "download_urls": download_urls,
        "reports": reports_payload
    }


@router.get("/rates")
async def get_exchange_rates(refresh: bool = False):
    """Retrieve live foreign exchange rates from Bank of Taiwan / Taifex."""
    from ..services.fx_service import fx_service
    return fx_service.get_live_rates(force_refresh=refresh)


@router.get("/rates/lookup")
async def lookup_historical_exchange_rate(
    currency: str = Query(...),
    date: Optional[str] = Query(None),
    rate_type: Literal["spot_sell", "cash_sell"] = Query("spot_sell"),
):
    """Lookup Bank of Taiwan exchange rate on a specific transaction date."""
    from ..services.fx_service import fx_service
    return fx_service.get_rate_for_date(currency, target_date=date, rate_type=rate_type)


class RecordExchangeRateUpdate(BaseModel):
    mode: Literal["manual", "bot_date"] = "manual"
    exchange_rate: Optional[float] = Field(None, gt=0)
    rate_date: Optional[str] = None
    rate_type: Literal["spot_sell", "cash_sell"] = "spot_sell"


@router.patch("/records/{record_id}/exchange-rate")
async def update_record_exchange_rate_endpoint(
    record_id: int,
    req: RecordExchangeRateUpdate,
    current_user: User = Depends(get_current_user_required),
):
    """Update a record's exchange rate either manually or via BOT historical date lookup."""
    record = get_audit_record_by_id(record_id, db_path=DEFAULT_DB_PATH)
    if not record:
        raise HTTPException(status_code=404, detail="找不到該筆報銷單據")

    if not is_privileged_user(current_user) and record.get("user_id") != current_user.id:
        raise HTTPException(status_code=403, detail="無權限修改其他同仁單據的匯率")

    if not is_privileged_user(current_user) and (record.get("approval_status") or "").upper() in ("PAID", "REJECTED"):
        raise HTTPException(status_code=400, detail="已撥款結案或駁回的單據不可再調整匯率")

    if req.mode == "manual":
        if req.exchange_rate is None or float(req.exchange_rate) <= 0:
            raise HTTPException(status_code=400, detail="自訂匯率必須大於 0")
        rate_val = float(req.exchange_rate)
        source_str = "員工自訂匯率"
        resolved_date = req.rate_date or record.get("invoice_date")
    else:
        from ..services.fx_service import fx_service
        target_date = req.rate_date or record.get("invoice_date")
        quote = fx_service.get_rate_for_date(
            record.get("currency") or "USD",
            target_date=target_date,
            rate_type=req.rate_type,
        )
        rate_val = float(quote.get("rate") or 1.0)
        source_str = str(quote.get("source") or "臺灣銀行牌告匯率")
        resolved_date = str(quote.get("rate_date") or target_date or "")

    updated = update_record_exchange_rate(
        record_id=record_id,
        exchange_rate=rate_val,
        exchange_rate_source=source_str,
        exchange_rate_date=resolved_date,
        db_path=DEFAULT_DB_PATH,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="找不到該筆報銷單據")

    return {"status": "success", "record": updated}


@router.get("/download/{session_id}/{file_type}")
async def download_file(
    session_id: str,
    file_type: str,
    current_user: User = Depends(get_current_user_required),
):
    """Download generated Excel, ERP CSV, or JSON reports."""
    session_dir = SESSIONS_DIR / session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    meta_file = session_dir / "session_meta.json"
    if meta_file.exists() and not is_privileged_user(current_user):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("owner_id") != current_user.id:
                raise HTTPException(status_code=403, detail="無權限下載其他同仁的報表")
        except HTTPException:
            raise
        except Exception:
            pass

    if file_type == "template":
        file_path = session_dir / "Travel_Expenses_Report_Filled.xlsx"
        filename = "Travel_Expenses_Report_Filled.xlsx"
    elif file_type == "excel":
        file_path = session_dir / "expense_claim_report.xlsx"
        filename = "expense_claim_report.xlsx"
    elif file_type == "erp":
        file_path = session_dir / "erp_journal_entry.csv"
        filename = "erp_journal_entry.csv"
    elif file_type == "json":
        file_path = session_dir / "audit_trail.json"
        filename = "audit_trail.json"
    else:
        raise HTTPException(status_code=400, detail="Invalid file type.")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Requested file not generated.")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream"
    )
