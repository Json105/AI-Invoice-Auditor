"""FastAPI endpoints for Storage Lifecycle, Presigned Previews, and Retention Policy."""

import mimetypes
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services.storage_service import storage_service
from .auth_routes import get_current_user_required, is_privileged_user, User

storage_router = APIRouter(prefix="/api/storage", tags=["Storage & File Lifecycle"])


class GeneratePresignedRequest(BaseModel):
    file_path: str
    expires_in_seconds: Optional[int] = 900


class CleanupRequest(BaseModel):
    max_age_hours: Optional[int] = 72


@storage_router.get("/preview/{token}")
async def preview_file(token: str):
    """Securely stream/preview an invoice image or PDF using an expiring HMAC presigned token."""
    target_file = storage_service.validate_presigned_token(token)
    if not target_file:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="憑證預覽連結已過期或簽名無效 (Invalid or expired preview token)"
        )

    mime_type, _ = mimetypes.guess_type(str(target_file))
    if not mime_type:
        mime_type = "application/octet-stream"

    return FileResponse(
        path=str(target_file),
        media_type=mime_type,
        filename=target_file.name,
        headers={
            "Cache-Control": "private, max-age=900",
            "Content-Disposition": f"inline; filename={target_file.name}"
        }
    )


@storage_router.post("/presigned-url")
async def create_presigned_url(
    req: GeneratePresignedRequest,
    current_user: User = Depends(get_current_user_required),
):
    """Generate a 15-minute expiring presigned URL scoped by RBAC ownership."""
    project_root = Path(".").resolve()
    target_path = Path(req.file_path).resolve()

    try:
        target_path.relative_to(project_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="無效的檔案路徑")

    if not is_privileged_user(current_user):
        user_uploads_root = (Path("data/uploads") / str(current_user.id)).resolve()
        try:
            target_path.relative_to(user_uploads_root)
        except ValueError:
            raise HTTPException(status_code=403, detail="無權限簽發此檔案的預覽連結")

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="找不到指定的憑證檔案")

    token = storage_service.generate_presigned_token(
        file_path=target_path,
        expires_in_seconds=req.expires_in_seconds or 900
    )
    return {
        "token": token,
        "preview_url": f"/api/storage/preview/{token}",
        "expires_in_seconds": req.expires_in_seconds or 900,
        "file_name": target_path.name
    }


@storage_router.get("/stats")
async def get_storage_stats(
    current_user: User = Depends(get_current_user_required),
):
    """Retrieve storage usage metrics, active sessions count, and disk footprint (Privileged)."""
    if not is_privileged_user(current_user):
        raise HTTPException(status_code=403, detail="僅財務主管或系統管理員可檢視系統儲存空間統計")
    try:
        metrics = storage_service.get_storage_metrics()
        return metrics
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@storage_router.post("/cleanup")
async def trigger_cleanup(
    req: Optional[CleanupRequest] = None,
    current_user: User = Depends(get_current_user_required),
):
    """Trigger TTL Retention Policy cleanup for temporary upload sessions (Privileged)."""
    if not is_privileged_user(current_user):
        raise HTTPException(status_code=403, detail="僅財務主管或系統管理員可執行系統暫存清理")
    max_age = req.max_age_hours if req and req.max_age_hours else 72
    try:
        report = storage_service.cleanup_expired_sessions(max_age_hours=max_age)
        return {
            "status": "success",
            "message": f"已完成過期暫存檔清理，共清除 {report['cleaned_sessions']} 個暫存工作階段，釋放 {report['freed_mb']} MB 磁碟空間。",
            "report": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
