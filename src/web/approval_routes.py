"""FastAPI endpoints for Financial Approval Workflow and State Machine."""

from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import (
    get_db,
    get_audit_record_by_id,
    update_approval_status,
    mark_record_as_paid,
    get_approval_records,
    get_approval_summary_stats,
    sync_trip_status_from_records,
    DEFAULT_DB_PATH
)
from .auth_routes import get_current_user_required, is_privileged_user, User

approval_router = APIRouter(prefix="/api/approval", tags=["Approval Workflow"])


class ReviewActionRequest(BaseModel):
    action: Literal["approve", "return", "reject"]
    notes: Optional[str] = None


class MarkPaidRequest(BaseModel):
    paid_by: Optional[str] = None


class ResubmitRequest(BaseModel):
    applicant_name: Optional[str] = None
    applicant_email: Optional[str] = None
    notes: Optional[str] = None


@approval_router.get("/summary")
async def get_approval_summary(
    current_user: User = Depends(get_current_user_required),
):
    """Get summary metrics scoped by RBAC (privileged sees all; employee sees own)."""
    effective_user_id = None if is_privileged_user(current_user) else current_user.id
    try:
        return get_approval_summary_stats(DEFAULT_DB_PATH, user_id=effective_user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sync_parent_trip(record_id: int) -> tuple[Optional[int], Optional[str]]:
    """Sync parent ExpenseTrip status when a child receipt transitions."""
    rec = get_audit_record_by_id(record_id, db_path=DEFAULT_DB_PATH)
    trip_id = rec.get("trip_id") if rec else None
    trip_status: Optional[str] = None
    if trip_id:
        trip_status = sync_trip_status_from_records(trip_id, db_path=DEFAULT_DB_PATH)
    return trip_id, trip_status


@approval_router.get("/list")
async def list_approval_records(
    status: Optional[str] = "ALL",
    trip_id: Optional[int] = None,
    user_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    current_user: User = Depends(get_current_user_required),
):
    """List expense claims scoped by RBAC (employees only see their own claims)."""
    if is_privileged_user(current_user):
        effective_user_id = user_id
    else:
        if user_id is not None and user_id != current_user.id:
            raise HTTPException(status_code=403, detail="無權限查閱其他同仁的審批單據")
        effective_user_id = current_user.id

    try:
        records = get_approval_records(
            status_filter=status,
            limit=limit,
            offset=offset,
            db_path=DEFAULT_DB_PATH,
            trip_id=trip_id,
            user_id=effective_user_id,
        )
        return {
            "total": len(records),
            "status_filter": status,
            "records": records
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@approval_router.post("/{record_id}/review")
async def review_record(
    record_id: int,
    req: ReviewActionRequest,
    current_user: User = Depends(get_current_user_required),
):
    """Review an expense record (Finance / Admin only): Approve, Return, or Reject."""
    if not is_privileged_user(current_user):
        raise HTTPException(status_code=403, detail="僅財務主管或系統管理員可執行審批核准/退件")

    status_map = {
        "approve": "APPROVED",
        "return": "RETURNED",
        "reject": "REJECTED"
    }
    new_status = status_map.get(req.action)
    if not new_status:
        raise HTTPException(status_code=400, detail=f"不支援的審核動作: {req.action}")

    if req.action == "return" and not (req.notes and req.notes.strip()):
        raise HTTPException(status_code=400, detail="退件補件時必須填寫具體退件原因 (Review notes required for return)")

    reviewer_name = current_user.full_name
    reviewer_email = current_user.email

    success = update_approval_status(
        record_id=record_id,
        status=new_status,
        reviewer_name=reviewer_name,
        reviewer_email=reviewer_email,
        review_notes=req.notes,
        db_path=DEFAULT_DB_PATH
    )

    if not success:
        raise HTTPException(status_code=404, detail=f"找不到序號為 #{record_id} 的單據紀錄")

    trip_id, trip_status = _sync_parent_trip(record_id)

    action_names = {
        "APPROVED": "已核准通過，待出納撥款",
        "RETURNED": "已退件補件，已通知同仁修改",
        "REJECTED": "已駁回報銷"
    }

    return {
        "status": "success",
        "record_id": record_id,
        "approval_status": new_status,
        "reviewer_name": reviewer_name,
        "trip_id": trip_id,
        "trip_status": trip_status,
        "message": f"單據 #{record_id} {action_names.get(new_status)}"
    }


@approval_router.post("/{record_id}/mark-paid")
async def mark_paid(
    record_id: int,
    req: Optional[MarkPaidRequest] = None,
    current_user: User = Depends(get_current_user_required),
):
    """Mark an approved expense claim as PAID (Finance / Admin only)."""
    if not is_privileged_user(current_user):
        raise HTTPException(status_code=403, detail="僅財務主管或系統管理員可標記撥款結案")

    paid_by = (req.paid_by if req and req.paid_by else None) or current_user.full_name

    success = mark_record_as_paid(
        record_id=record_id,
        paid_by=paid_by,
        db_path=DEFAULT_DB_PATH
    )

    if not success:
        raise HTTPException(status_code=404, detail=f"找不到序號為 #{record_id} 的單據紀錄")

    trip_id, trip_status = _sync_parent_trip(record_id)

    return {
        "status": "success",
        "record_id": record_id,
        "approval_status": "PAID",
        "paid_by": paid_by,
        "trip_id": trip_id,
        "trip_status": trip_status,
        "message": f"單據 #{record_id} 已成功標記為【已完成撥款結案】"
    }


@approval_router.post("/{record_id}/resubmit")
async def resubmit_record(
    record_id: int,
    req: Optional[ResubmitRequest] = None,
    current_user: User = Depends(get_current_user_required),
):
    """Resubmit a previously returned expense claim (owner or privileged user only)."""
    existing = get_audit_record_by_id(record_id, db_path=DEFAULT_DB_PATH)
    if not existing:
        raise HTTPException(status_code=404, detail=f"找不到序號為 #{record_id} 的單據紀錄")

    if not is_privileged_user(current_user):
        rec_uid = existing.get("user_id")
        is_owner = (rec_uid == current_user.id) or (
            rec_uid is None and existing.get("applicant_email") == current_user.email
        )
        if not is_owner:
            raise HTTPException(status_code=403, detail="無權限代其他同仁重送單據")

    notes = req.notes if req and req.notes else "同仁已補齊憑證重送"

    success = update_approval_status(
        record_id=record_id,
        status="SUBMITTED",
        reviewer_name="",
        reviewer_email="",
        review_notes=f"【重新送審】{notes}",
        db_path=DEFAULT_DB_PATH
    )

    if not success:
        raise HTTPException(status_code=404, detail=f"找不到序號為 #{record_id} 的單據紀錄")

    trip_id, trip_status = _sync_parent_trip(record_id)

    return {
        "status": "success",
        "record_id": record_id,
        "approval_status": "SUBMITTED",
        "trip_id": trip_id,
        "trip_status": trip_status,
        "message": f"單據 #{record_id} 已重新送審，待財務主管覆核"
    }
