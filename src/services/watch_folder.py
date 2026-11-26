"""Watch Folder Auto-Ingestion Service for scanner drop-ins (MFP / Network Folder)."""

import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..database import (
    DEFAULT_DB_PATH,
    User,
    get_db_connection,
    save_audit_reports,
)
from ..main import get_extractor
from ..extractors.gemini_extractor import DEFAULT_GEMINI_MODEL
from ..models import AuditReport, AuditStatus
from .ai_policy import QuotaExceededError
from .document_splitter import expand_uploaded_document
from .ingestion_pipeline import audit_expanded_items
from .storage_service import storage_service

DEFAULT_WATCH_DIR = Path(os.getenv("WATCH_FOLDER_DIR", "data/watch_inbox"))
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".tiff", ".tif", ".bmp", ".json"}
IGNORED_SUFFIXES = {".tmp", ".part", ".crdownload"}


def _detect_mime_type(path: Path) -> str:
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
    return mime_map.get(path.suffix.lower(), "application/octet-stream")


class WatchFolderService:
    """Monitors a local/network drop-in directory for MFP scanner files."""

    def __init__(self, watch_dir: Optional[Path] = None):
        self.watch_dir = Path(watch_dir or os.getenv("WATCH_FOLDER_DIR", str(DEFAULT_WATCH_DIR)))
        self._lock = threading.Lock()

    def _ensure_dirs(self, inbox_dir: Path) -> Tuple[Path, Path]:
        inbox_dir.mkdir(parents=True, exist_ok=True)
        processed_dir = inbox_dir / "processed"
        failed_dir = inbox_dir / "failed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        failed_dir.mkdir(parents=True, exist_ok=True)
        return processed_dir, failed_dir

    def _collect_pending_files(self, inbox_dir: Path) -> List[Tuple[Path, Optional[str]]]:
        """Collect eligible files in inbox root or one level of trip subfolders."""
        pending: List[Tuple[Path, Optional[str]]] = []
        if not inbox_dir.exists():
            return pending

        for entry in sorted(inbox_dir.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith(".") or entry.name.startswith("~$"):
                continue
            if entry.is_dir():
                if entry.name.lower() in ("processed", "failed"):
                    continue
                trip_hint = entry.name if entry.name.upper().startswith("TRIP-") else None
                for sub in sorted(entry.iterdir(), key=lambda p: p.name.lower()):
                    if self._is_eligible_file(sub):
                        pending.append((sub, trip_hint))
            elif self._is_eligible_file(entry):
                pending.append((entry, None))

        return pending

    @staticmethod
    def _is_eligible_file(path: Path) -> bool:
        if not path.is_file():
            return False
        name = path.name
        if name.startswith(".") or name.startswith("~$"):
            return False
        suffix = path.suffix.lower()
        if suffix in IGNORED_SUFFIXES or suffix not in SUPPORTED_EXTENSIONS:
            return False
        return True

    def get_status(self, watch_dir: Optional[Path] = None) -> Dict[str, Any]:
        inbox_dir = Path(watch_dir) if watch_dir else self.watch_dir
        self._ensure_dirs(inbox_dir)
        pending = self._collect_pending_files(inbox_dir)
        files_info = [
            {
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "trip_code_hint": hint,
            }
            for f, hint in pending
        ]
        return {
            "watch_dir": str(inbox_dir),
            "pending_count": len(files_info),
            "pending_files": files_info,
        }

    def _resolve_trip_id_by_code(
        self,
        trip_code: Optional[str],
        user_id: int,
        is_privileged: bool,
        db_path: Path,
    ) -> Optional[int]:
        if not trip_code:
            return None
        conn = get_db_connection(db_path)
        row = conn.execute(
            "SELECT id, user_id, status FROM expense_trips WHERE UPPER(trip_code) = ? LIMIT 1",
            (trip_code.strip().upper(),),
        ).fetchone()
        conn.close()
        if not row:
            return None
        if not is_privileged and row["user_id"] != user_id:
            return None
        if (row["status"] or "").upper() in ("PAID", "REJECTED"):
            return None
        return int(row["id"])

    def scan_and_ingest(
        self,
        user: Optional[User] = None,
        engine: str = "mock",
        api_key: Optional[str] = None,
        model: str = DEFAULT_GEMINI_MODEL,
        trip_id: Optional[int] = None,
        split_pdf: bool = True,
        multi_receipt: bool = False,
        fx_mode: str = "auto_bot",
        manual_exchange_rate: Optional[float] = None,
        watch_dir: Optional[Path] = None,
        db_path: Path = DEFAULT_DB_PATH,
        uploads_dir: Path = Path("data/uploads"),
        is_privileged: bool = False,
        enable_fallback: bool = True,
    ) -> Dict[str, Any]:
        """Scan inbox directory once, process every pending document, and move to processed/failed."""
        inbox_dir = Path(watch_dir) if watch_dir else self.watch_dir
        with self._lock:
            processed_dir, failed_dir = self._ensure_dirs(inbox_dir)
            pending = self._collect_pending_files(inbox_dir)
            if not pending:
                return {
                    "watch_dir": str(inbox_dir),
                    "scanned_files_count": 0,
                    "expanded_pages_count": 0,
                    "qr_fast_decoded_count": 0,
                    "total": 0,
                    "approved": 0,
                    "warning": 0,
                    "rejected": 0,
                    "trip_id": trip_id,
                    "actual_model": model,
                    "reports": [],
                }

            extractor = get_extractor(engine, api_key=api_key, model=model, enable_fallback=enable_fallback)
            owner_id = user.id if user else 1
            owner_name = user.full_name if user else "自動監控資料夾"
            owner_email = user.email if user else "scanner@company.local"

            month_dir = uploads_dir / str(owner_id) / datetime.now().strftime("%Y-%m")
            month_dir.mkdir(parents=True, exist_ok=True)

            all_reports: List[AuditReport] = []
            total_expanded_pages = 0
            total_qr_decoded = 0
            scanned_files_count = 0

            for src_file, trip_hint in pending:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                try:
                    resolved_trip_id = trip_id
                    if resolved_trip_id is None and trip_hint:
                        resolved_trip_id = self._resolve_trip_id_by_code(
                            trip_hint,
                            user_id=owner_id,
                            is_privileged=is_privileged,
                            db_path=db_path,
                        )

                    target_path = month_dir / f"{uuid.uuid4().hex[:8]}_{src_file.name}"
                    shutil.copy2(src_file, target_path)

                    expanded_items = expand_uploaded_document(
                        file_path=target_path,
                        original_name=src_file.name,
                        split_multipage_pdf=split_pdf,
                    )
                    expanded_paths: List[Path] = []
                    stored_files: Dict[str, Tuple[str, str]] = {}
                    for item_path, _, item_mime in expanded_items:
                        resolved_mime = (
                            item_mime
                            if src_file.suffix.lower() == ".pdf" and split_pdf
                            else _detect_mime_type(item_path)
                        )
                        expanded_paths.append(item_path)
                        stored_files[str(item_path)] = (str(item_path), resolved_mime)

                    reports, qr_cnt = audit_expanded_items(
                        expanded_paths=expanded_paths,
                        extractor=extractor,
                        multi_receipt=multi_receipt,
                        fx_mode=fx_mode,
                        manual_exchange_rate=manual_exchange_rate,
                        db_path=db_path,
                    )
                    save_audit_reports(
                        reports,
                        applicant_name=owner_name,
                        applicant_email=owner_email,
                        db_path=db_path,
                        user_id=owner_id,
                        stored_files=stored_files,
                        trip_id=resolved_trip_id,
                    )

                    dest = processed_dir / f"{stamp}_{uuid.uuid4().hex[:4]}_{src_file.name}"
                    shutil.move(str(src_file), str(dest))

                    all_reports.extend(reports)
                    total_expanded_pages += len(expanded_paths)
                    total_qr_decoded += qr_cnt
                    scanned_files_count += 1
                except QuotaExceededError:
                    raise
                except Exception:
                    fail_dest = failed_dir / f"{stamp}_{uuid.uuid4().hex[:4]}_{src_file.name}"
                    try:
                        if src_file.exists():
                            shutil.move(str(src_file), str(fail_dest))
                    except Exception:
                        pass

            approved = sum(1 for r in all_reports if r.status == AuditStatus.APPROVED)
            warning = sum(1 for r in all_reports if r.status == AuditStatus.WARNING)
            rejected = sum(1 for r in all_reports if r.status == AuditStatus.REJECTED)

            reports_payload = []
            for r in all_reports:
                item = r.model_dump(mode="json")
                if r.file_path and Path(r.file_path).exists():
                    token = storage_service.generate_presigned_token(r.file_path, expires_in_seconds=900)
                    item["preview_url"] = f"/api/storage/preview/{token}"
                    item["preview_token"] = token
                reports_payload.append(item)

            actual_model = (
                getattr(extractor, "last_used_model", None)
                or getattr(extractor, "model", None)
                or model
            )

            return {
                "watch_dir": str(inbox_dir),
                "scanned_files_count": scanned_files_count,
                "expanded_pages_count": total_expanded_pages,
                "qr_fast_decoded_count": total_qr_decoded,
                "total": len(all_reports),
                "approved": approved,
                "warning": warning,
                "rejected": rejected,
                "trip_id": trip_id,
                "actual_model": actual_model,
                "reports": reports_payload,
            }


watch_folder_service = WatchFolderService()
