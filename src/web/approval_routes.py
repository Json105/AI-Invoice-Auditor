"""FastAPI endpoints for Financial Approval Workflow and State Machine."""

from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, status, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import (
    get_db,
    update_approval_status,
    mark_record_as_paid,
    get_approval_records,
    get_approval_summary_stats,
    DEFAULT_DB_PATH
)
from .auth_routes import get_current_user_optional, User

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
async def get_approval_summary():
    """Get high-level summary metrics for Boss / Finance Approval Dashboard."""
    try:
        return get_approval_summary_stats(DEFAULT_DB_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@approval_router.get("/list")
async def list_approval_records(
    status: Optional[str] = "ALL",
    limit: int = 100,
    offset: int = 0
):
    """List expense claims with approval status filtering (SUBMITTED, APPROVED, RETURNED, REJECTED, PAID, ALL)."""
    try:
        records = get_approval_records(status_filter=status, limit=limit, offset=offset, db_path=DEFAULT_DB_PATH)
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
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """Review an expense record: Approve, Return (with reason), or Reject."""
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

    reviewer_name = current_user.full_name if current_user else "陳審計 (財務主管)"
    reviewer_email = current_user.email if current_user else "finance@company.local"

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
        "message": f"單據 #{record_id} {action_names.get(new_status)}"
    }


@approval_router.post("/{record_id}/mark-paid")
async def mark_paid(
    record_id: int,
    req: Optional[MarkPaidRequest] = None,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """Mark an approved expense claim as PAID (撥款結案)."""
    paid_by = (req.paid_by if req and req.paid_by else None) or (current_user.full_name if current_user else "出納處 (銀行轉帳)")

    success = mark_record_as_paid(
        record_id=record_id,
        paid_by=paid_by,
        db_path=DEFAULT_DB_PATH
    )

    if not success:
        raise HTTPException(status_code=404, detail=f"找不到序號為 #{record_id} 的單據紀錄")

    return {
        "status": "success",
        "record_id": record_id,
        "approval_status": "PAID",
        "paid_by": paid_by,
        "message": f"單據 #{record_id} 已成功標記為【已完成撥款結案】"
    }


@approval_router.post("/{record_id}/resubmit")
async def resubmit_record(
    record_id: int,
    req: Optional[ResubmitRequest] = None,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """Resubmit a previously returned expense claim after correcting receipt or information."""
    applicant_name = (req.applicant_name if req and req.applicant_name else None) or (current_user.full_name if current_user else "林專員 (業務同仁)")
    applicant_email = (req.applicant_email if req and req.applicant_email else None) or (current_user.email if current_user else "employee@company.local")
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

    return {
        "status": "success",
        "record_id": record_id,
        "approval_status": "SUBMITTED",
        "message": f"單據 #{record_id} 已重新送審，待財務主管覆核"
    }
