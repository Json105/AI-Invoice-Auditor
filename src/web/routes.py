"""FastAPI routes for document upload, auditing, history, file preview, and Excel exports."""

import os
import re
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse

from ..models import AuditStatus
from ..auditor import InvoiceAuditor
from ..exporter import ExpenseExporter
from ..template_exporter import TemplateExpenseExporter
from ..main import get_extractor
from ..database import (
    User,
    save_audit_reports,
    get_audit_history,
    get_audit_statistics,
    get_audit_record_by_id,
    get_audit_records_for_export,
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
    return {
        "has_server_api_key": has_key,
        "default_engine": "gemini",
        "default_model": "gemini-3.7-flash",
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
        )
        return {"total": len(records), "history": records}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/export")
async def export_history_excel(
    user_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user_required),
):
    """Export filtered audit history records into Excel (Template.xlsx if available)."""
    effective_user_id = _resolve_effective_user_id(current_user, user_id)
    reports = get_audit_records_for_export(
        db_path=DEFAULT_DB_PATH,
        user_id=effective_user_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    if not reports:
        raise HTTPException(status_code=404, detail="查無符合條件的報銷單據可匯出")

    export_dir = SESSIONS_DIR / f"export_{uuid.uuid4().hex[:8]}"
    export_dir.mkdir(parents=True, exist_ok=True)
    template_path = Path("Template.xlsx")
    if template_path.exists():
        out_path = export_dir / "Travel_Expenses_Report_Filtered.xlsx"
        TemplateExpenseExporter(reports, template_path=template_path).export(out_path)
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
    model: str = Form("gemini-3.7-flash"),
    apply_template: bool = Form(True),
    current_user: User = Depends(get_current_user_required),
):
    """Upload documents, persist original receipts, run audits, save to DB, and export reports."""
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

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

    saved_paths: List[Path] = []
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
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"檔案 '{safe_name}' 超過 20 MB 上限",
            )

        target_path = month_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        target_path.write_bytes(content)
        saved_paths.append(target_path)
        stored_files[str(target_path)] = (
            str(target_path),
            _detect_mime_type(target_path, file.content_type),
        )

    auditor = InvoiceAuditor(db_path=DEFAULT_DB_PATH)
    reports = []

    for file_path in saved_paths:
        try:
            invoice_data = extractor.extract(file_path)
            report = auditor.audit(invoice_data, file_path=str(file_path))
            reports.append(report)
        except Exception as e:
            # Create a rejected audit report on extraction failure
            from ..models import InvoiceData, DocumentType, AuditIssue
            failed_inv = InvoiceData(
                document_type=DocumentType.GENERAL_RECEIPT,
                total_amount=0.0,
                notes=f"解析失敗: {str(e)}"
            )
            failed_report = auditor.audit(failed_inv, file_path=str(file_path))
            failed_report.status = AuditStatus.REJECTED
            failed_report.issues.append(
                AuditIssue(code="ERR_EXTRACTION_FAILED", level="ERROR", message=f"檔案解析失敗: {str(e)}")
            )
            reports.append(failed_report)

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
        t_exporter = TemplateExpenseExporter(reports, template_path=template_path)
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

    # Persist to SQLite
    try:
        save_audit_reports(
            reports,
            applicant_name=current_user.full_name,
            applicant_email=current_user.email,
            db_path=DEFAULT_DB_PATH,
            user_id=current_user.id,
            stored_files=stored_files,
        )
    except Exception as e:
        print(f"Warning: Failed to save to database: {e}")

    # Summary
    approved = sum(1 for r in reports if r.status == AuditStatus.APPROVED)
    warning = sum(1 for r in reports if r.status == AuditStatus.WARNING)
    rejected = sum(1 for r in reports if r.status == AuditStatus.REJECTED)

    download_urls = {
        "excel": f"/api/download/{session_id}/excel",
        "json": f"/api/download/{session_id}/json",
        "erp": f"/api/download/{session_id}/erp"
    }
    if template_filled:
        download_urls["template"] = f"/api/download/{session_id}/template"

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
