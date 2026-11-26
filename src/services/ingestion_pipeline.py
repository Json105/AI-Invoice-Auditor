"""Shared ingestion pipeline: TW e-Invoice QR pre-decode, AI extraction, FX resolution, and audit."""

from pathlib import Path
from typing import List, Optional, Tuple

from ..auditor import InvoiceAuditor
from ..database import DEFAULT_DB_PATH
from ..models import AuditIssue, AuditReport, AuditStatus, DocumentType, InvoiceData
from .ai_policy import QuotaExceededError
from .fx_service import fx_service, normalize_fx_code
from .tw_einvoice_qr import try_decode_taiwan_einvoice_from_image

_QUOTA_ERROR_MARKERS = (
    "http 429",
    "rate limit",
    "resource_exhausted",
    "insufficient_quota",
    "quota exceeded",
    "too many requests",
)


def _is_upstream_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _QUOTA_ERROR_MARKERS)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp"}


def apply_exchange_rate_to_invoice(
    invoice: InvoiceData,
    fx_mode: str = "auto_bot",
    manual_exchange_rate: Optional[float] = None,
) -> InvoiceData:
    """Resolve and attach exchange_rate, source, date, and ntd_amount onto InvoiceData."""
    iso_curr = normalize_fx_code(invoice.currency)
    total_amt = float(invoice.total_amount or 0.0)

    if iso_curr == "TWD":
        invoice.exchange_rate = 1.0
        invoice.exchange_rate_source = "新台幣本位幣"
        invoice.exchange_rate_date = invoice.invoice_date
        invoice.ntd_amount = round(total_amt, 2)
        return invoice

    if fx_mode == "manual" and manual_exchange_rate is not None and float(manual_exchange_rate) > 0:
        rate_val = round(float(manual_exchange_rate), 6)
        invoice.exchange_rate = rate_val
        invoice.exchange_rate_source = "員工自訂匯率"
        invoice.exchange_rate_date = invoice.invoice_date
        invoice.ntd_amount = round(total_amt * rate_val, 2)
        return invoice

    # Preserve pre-populated explicit rate unless auto_bot lookup should fill it
    if invoice.exchange_rate is not None and float(invoice.exchange_rate) > 0 and fx_mode != "auto_bot":
        rate_val = round(float(invoice.exchange_rate), 6)
        invoice.ntd_amount = round(total_amt * rate_val, 2)
        return invoice

    quote = fx_service.get_rate_for_date(invoice.currency, target_date=invoice.invoice_date)
    rate_val = round(float(quote.get("rate") or 1.0), 6)
    invoice.exchange_rate = rate_val
    invoice.exchange_rate_source = quote.get("source") or "臺灣銀行牌告匯率"
    invoice.exchange_rate_date = quote.get("rate_date") or invoice.invoice_date
    invoice.ntd_amount = round(total_amt * rate_val, 2)
    return invoice


def audit_expanded_items(
    expanded_paths: List[Path],
    extractor,
    multi_receipt: bool = False,
    fx_mode: str = "auto_bot",
    manual_exchange_rate: Optional[float] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> Tuple[List[AuditReport], int]:
    """Run TW e-Invoice QR fast decode (single-receipt images) or AI extract_many + FX + Audit."""
    auditor = InvoiceAuditor(db_path=db_path)
    reports: List[AuditReport] = []
    qr_fast_decoded_count = 0

    for item_path in expanded_paths:
        try:
            extracted_list: Optional[List[InvoiceData]] = None
            if not multi_receipt and item_path.suffix.lower() in _IMAGE_SUFFIXES:
                qr_invoice = try_decode_taiwan_einvoice_from_image(item_path)
                if qr_invoice is not None:
                    extracted_list = [qr_invoice]
                    qr_fast_decoded_count += 1

            if extracted_list is None:
                extracted_list = extractor.extract_many(item_path, multi_receipt=multi_receipt)

            for invoice_data in extracted_list:
                apply_exchange_rate_to_invoice(
                    invoice_data,
                    fx_mode=fx_mode,
                    manual_exchange_rate=manual_exchange_rate,
                )
                report = auditor.audit(invoice_data, file_path=str(item_path))
                reports.append(report)
        except QuotaExceededError:
            raise
        except Exception as e:
            if _is_upstream_quota_error(e):
                raise QuotaExceededError(
                    "系統辨識服務目前額度已用完，請稍後再試或聯繫系統管理員處理。"
                ) from e
            failed_inv = InvoiceData(
                document_type=DocumentType.GENERAL_RECEIPT,
                total_amount=0.0,
                notes=f"解析失敗: {str(e)}",
            )
            failed_report = auditor.audit(failed_inv, file_path=str(item_path))
            failed_report.status = AuditStatus.REJECTED
            failed_report.issues.append(
                AuditIssue(code="ERR_EXTRACTION_FAILED", level="ERROR", message=f"檔案解析失敗: {str(e)}")
            )
            reports.append(failed_report)

    return reports, qr_fast_decoded_count
