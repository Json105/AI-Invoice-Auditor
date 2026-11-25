"""FastAPI routes for Business Trip Bundles (出差報銷專案 CRUD, 批核與合併匯出)."""

import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..database import (
    DEFAULT_DB_PATH,
    User,
    assign_records_to_trip,
    create_expense_trip,
    delete_expense_trip,
    get_audit_records_for_export,
    get_expense_trip_by_id,
    get_expense_trip_detail,
    list_expense_trips,
    mark_trip_as_paid,
    review_expense_trip,
    submit_expense_trip,
    update_expense_trip,
)
from ..erp_exporter import ERPExporter
from ..exporter import ExpenseExporter
from ..services.storage_service import storage_service
from ..template_exporter import TemplateExpenseExporter
from .auth_routes import get_current_user_required, is_privileged_user

trip_router = APIRouter(prefix="/api/trips", tags=["Expense Trips"])

TRIP_EXPORTS_DIR = Path("output/web_sessions")
TRIP_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


class TripCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    destination: Optional[str] = Field(None, max_length=120)
    start_date: Optional[str] = Field(None, max_length=20)
    end_date: Optional[str] = Field(None, max_length=20)
    purpose: Optional[str] = None
    advance_payment: float = Field(0.0, ge=0.0)


class TripUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    destination: Optional[str] = Field(None, max_length=120)
    start_date: Optional[str] = Field(None, max_length=20)
    end_date: Optional[str] = Field(None, max_length=20)
    purpose: Optional[str] = None
    advance_payment: Optional[float] = Field(None, ge=0.0)


class TripAssignRequest(BaseModel):
    record_ids: List[int] = Field(..., min_length=1)
    action: Literal["add", "remove"] = "add"


class TripSubmitRequest(BaseModel):
    notes: Optional[str] = None


class TripReviewRequest(BaseModel):
    action: Literal["approve", "return", "reject"]
    notes: Optional[str] = None


class TripMarkPaidRequest(BaseModel):
    paid_by: Optional[str] = None


def _can_access_trip(user: User, trip: Dict[str, Any]) -> bool:
    """Privileged finance/admin users see all trips; employees only their own."""
    return is_privileged_user(user) or trip.get("user_id") == user.id


def _require_accessible_trip(trip_id: int, current_user: User) -> Dict[str, Any]:
    """Load trip or raise 404/403."""
    trip = get_expense_trip_by_id(trip_id, db_path=DEFAULT_DB_PATH)
    if not trip:
        raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")
    if not _can_access_trip(current_user, trip):
        raise HTTPException(status_code=403, detail="無權限查閱或操作其他同仁的出差報銷專案")
    return trip


def _attach_preview_urls(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Attach 15-minute HMAC presigned preview links to records with persisted files."""
    for rec in detail.get("records", []):
        stored = rec.get("stored_file_path")
        if stored and Path(stored).exists():
            token = storage_service.generate_presigned_token(stored, expires_in_seconds=900)
            rec["preview_url"] = f"/api/storage/preview/{token}"
            rec["preview_token"] = token
    return detail


@trip_router.get("")
async def list_trips_endpoint(
    status: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user_required),
):
    """List business trip expense bundles scoped by RBAC role."""
    if is_privileged_user(current_user):
        effective_user_id = user_id
    else:
        if user_id is not None and user_id != current_user.id:
            raise HTTPException(status_code=403, detail="無權限查閱其他同仁的出差報銷專案")
        effective_user_id = current_user.id

    trips = list_expense_trips(
        user_id=effective_user_id,
        status=status,
        limit=limit,
        offset=offset,
        db_path=DEFAULT_DB_PATH,
    )
    return {"total": len(trips), "trips": trips}


@trip_router.post("")
async def create_trip_endpoint(
    req: TripCreateRequest,
    current_user: User = Depends(get_current_user_required),
):
    """Create a new business trip expense bundle in DRAFT status."""
    trip = create_expense_trip(
        title=req.title,
        user_id=current_user.id,
        applicant_name=current_user.full_name,
        applicant_email=current_user.email,
        destination=req.destination,
        start_date=req.start_date,
        end_date=req.end_date,
        purpose=req.purpose,
        advance_payment=req.advance_payment,
        db_path=DEFAULT_DB_PATH,
    )
    detail = get_expense_trip_detail(trip["id"], db_path=DEFAULT_DB_PATH)
    return {"status": "success", "trip": detail or trip}


@trip_router.get("/{trip_id}")
async def get_trip_detail_endpoint(
    trip_id: int,
    current_user: User = Depends(get_current_user_required),
):
    """Retrieve a trip bundle with aggregated metrics and child invoice records."""
    _require_accessible_trip(trip_id, current_user)
    detail = get_expense_trip_detail(trip_id, db_path=DEFAULT_DB_PATH)
    if not detail:
        raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")
    return _attach_preview_urls(detail)


@trip_router.patch("/{trip_id}")
async def update_trip_endpoint(
    trip_id: int,
    req: TripUpdateRequest,
    current_user: User = Depends(get_current_user_required),
):
    """Update trip metadata (title, destination, dates, advance payment)."""
    trip = _require_accessible_trip(trip_id, current_user)
    if (trip.get("status") or "").upper() in ("PAID", "REJECTED") and not is_privileged_user(current_user):
        raise HTTPException(status_code=400, detail="已撥款結案或已駁回的專案不可再修改")

    updates = req.model_dump(exclude_unset=True)
    updated = update_expense_trip(trip_id, updates, db_path=DEFAULT_DB_PATH)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")
    detail = get_expense_trip_detail(trip_id, db_path=DEFAULT_DB_PATH)
    return {"status": "success", "trip": _attach_preview_urls(detail or updated)}


@trip_router.delete("/{trip_id}")
async def delete_trip_endpoint(
    trip_id: int,
    current_user: User = Depends(get_current_user_required),
):
    """Delete an un-paid trip bundle while preserving underlying audited invoices."""
    trip = _require_accessible_trip(trip_id, current_user)
    trip_status = (trip.get("status") or "DRAFT").upper()
    if trip_status == "PAID":
        raise HTTPException(status_code=400, detail="已撥款結案的出差專案不可刪除")
    if not is_privileged_user(current_user) and trip_status != "DRAFT":
        raise HTTPException(status_code=403, detail="僅可刪除草稿中的出差專案；已送審專案請聯繫財務處理")

    ok = delete_expense_trip(trip_id, db_path=DEFAULT_DB_PATH)
    if not ok:
        raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")
    return {"status": "success", "message": f"已刪除出差專案 {trip.get('trip_code')}，關聯單據已安全保留於歷史紀錄"}


@trip_router.post("/{trip_id}/assign")
async def assign_records_endpoint(
    trip_id: int,
    req: TripAssignRequest,
    current_user: User = Depends(get_current_user_required),
):
    """Assign existing audit records into a trip or detach them back to unbundled."""
    trip = _require_accessible_trip(trip_id, current_user)
    if (trip.get("status") or "").upper() in ("PAID", "REJECTED"):
        raise HTTPException(status_code=400, detail="已撥款結案或駁回的專案不可調整單據歸屬")

    target_trip_id: Optional[int] = trip_id if req.action == "add" else None
    updated_count = assign_records_to_trip(
        record_ids=req.record_ids,
        trip_id=target_trip_id,
        user_id=current_user.id,
        is_privileged=is_privileged_user(current_user),
        db_path=DEFAULT_DB_PATH,
    )
    detail = get_expense_trip_detail(trip_id, db_path=DEFAULT_DB_PATH)
    return {
        "status": "success",
        "updated_count": updated_count,
        "trip": _attach_preview_urls(detail) if detail else trip,
    }


@trip_router.post("/{trip_id}/submit")
async def submit_trip_endpoint(
    trip_id: int,
    req: Optional[TripSubmitRequest] = None,
    current_user: User = Depends(get_current_user_required),
):
    """Submit a trip bundle (and all draft/returned receipts inside it) for finance approval."""
    trip = _require_accessible_trip(trip_id, current_user)
    trip_status = (trip.get("status") or "DRAFT").upper()
    if trip_status in ("PAID", "REJECTED", "APPROVED"):
        raise HTTPException(status_code=400, detail=f"專案目前狀態為 {trip_status}，無法再次送審")

    try:
        detail = submit_expense_trip(
            trip_id=trip_id,
            applicant_name=current_user.full_name,
            applicant_email=current_user.email,
            notes=req.notes if req else None,
            db_path=DEFAULT_DB_PATH,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "status": "success",
        "message": f"出差專案 {detail.get('trip_code')} 已送交財務審批（共 {detail.get('receipt_count', 0)} 筆單據）",
        "trip": _attach_preview_urls(detail),
    }


@trip_router.post("/{trip_id}/review")
async def review_trip_endpoint(
    trip_id: int,
    req: TripReviewRequest,
    current_user: User = Depends(get_current_user_required),
):
    """Finance / Admin batch review for a whole trip bundle."""
    if not is_privileged_user(current_user):
        raise HTTPException(status_code=403, detail="僅財務人員或系統管理員可執行專案審批")

    if req.action == "return" and not (req.notes and req.notes.strip()):
        raise HTTPException(status_code=400, detail="退件補件時必須填寫具體退件原因")

    detail = review_expense_trip(
        trip_id=trip_id,
        action=req.action,
        reviewer_name=current_user.full_name,
        reviewer_email=current_user.email,
        review_notes=req.notes,
        db_path=DEFAULT_DB_PATH,
    )
    if not detail:
        raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")

    return {
        "status": "success",
        "message": f"出差專案 {detail.get('trip_code')} 狀態已更新為 {detail.get('status')}",
        "trip": _attach_preview_urls(detail),
    }


@trip_router.post("/{trip_id}/mark-paid")
async def mark_trip_paid_endpoint(
    trip_id: int,
    req: Optional[TripMarkPaidRequest] = None,
    current_user: User = Depends(get_current_user_required),
):
    """Finance / Admin mark an entire trip bundle (and its non-rejected claims) as PAID."""
    if not is_privileged_user(current_user):
        raise HTTPException(status_code=403, detail="僅財務人員或系統管理員可標記專案已撥款")

    paid_by = (req.paid_by if req and req.paid_by else None) or current_user.full_name
    detail = mark_trip_as_paid(
        trip_id=trip_id,
        paid_by=paid_by,
        db_path=DEFAULT_DB_PATH,
    )
    if not detail:
        raise HTTPException(status_code=404, detail=f"找不到出差報銷專案 #{trip_id}")

    return {
        "status": "success",
        "message": f"出差專案 {detail.get('trip_code')} 已完成整案撥款結案",
        "trip": _attach_preview_urls(detail),
    }


@trip_router.get("/{trip_id}/export")
async def export_trip_endpoint(
    trip_id: int,
    format: Literal["template", "excel", "erp", "json"] = Query("template"),
    current_user: User = Depends(get_current_user_required),
):
    """Export all receipts of a business trip into one consolidated Excel, ERP CSV, or JSON."""
    trip = _require_accessible_trip(trip_id, current_user)
    reports = get_audit_records_for_export(
        db_path=DEFAULT_DB_PATH,
        trip_id=trip_id,
        limit=2000,
    )
    if not reports:
        raise HTTPException(status_code=404, detail="此出差專案尚無任何單據可匯出")

    export_dir = TRIP_EXPORTS_DIR / f"trip_{trip_id}_{uuid.uuid4().hex[:8]}"
    export_dir.mkdir(parents=True, exist_ok=True)
    safe_code = trip.get("trip_code") or f"TRIP-{trip_id}"

    if format == "template":
        template_path = Path("Template.xlsx")
        if template_path.exists():
            out_path = export_dir / f"{safe_code}_Travel_Expenses_Report.xlsx"
            TemplateExpenseExporter(reports, template_path=template_path, trip_meta=trip).export(out_path)
        else:
            out_path = export_dir / f"{safe_code}_expense_claim.xlsx"
            ExpenseExporter(reports).to_excel(out_path)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "excel":
        out_path = export_dir / f"{safe_code}_expense_claim.xlsx"
        ExpenseExporter(reports).to_excel(out_path)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "erp":
        out_path = export_dir / f"{safe_code}_erp_journal_entry.csv"
        ERPExporter(reports, applicant_name=trip.get("applicant_name") or "出差報銷同仁").to_csv(out_path)
        media_type = "text/csv; charset=utf-8"
    else:
        out_path = export_dir / f"{safe_code}_audit_trail.json"
        ExpenseExporter(reports).to_json(out_path)
        media_type = "application/json"

    return FileResponse(
        path=str(out_path),
        filename=out_path.name,
        media_type=media_type,
    )
