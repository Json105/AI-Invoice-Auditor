"""Enterprise compliance and business rule audit engine.

Implements:
1. Ministry of Finance (Taiwan) 8-digit Unified Business Number (統編) checksum.
2. Financial mathematics consistency verification (Subtotal + Tax == Total).
3. Specialized rule validation for THSR tickets and Taxi receipts.
4. Duplicate submission detection and risk scoring.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Union
from .models import InvoiceData, AuditReport, AuditStatus, AuditIssue, DocumentType

# Valid Taiwan High Speed Rail stations
TAIWAN_HSR_STATIONS = {
    "南港", "台北", "板橋", "桃園", "新竹", "苗栗",
    "台中", "彰化", "雲林", "嘉義", "台南", "左營"
}


def validate_taiwan_tax_id(tax_id: Optional[str]) -> bool:
    """Validate Taiwan 8-digit Unified Business Number (統一編號) checksum.

    Algorithm according to Ministry of Finance (財政部) specification:
    Weights: [1, 2, 1, 2, 1, 2, 4, 1]
    Each digit is multiplied by its weight. The sum of quotient and remainder
    divided by 10 for each product is accumulated.
    Valid if total sum % 10 == 0.
    Special case: If 7th digit is 7, digit sum can also use (2+8=10 -> 1+0=1)
    or sum - 9.
    """
    if not tax_id:
        return False

    # Clean whitespace and hyphens
    cleaned = str(tax_id).strip().replace("-", "")
    if len(cleaned) != 8 or not cleaned.isdigit() or cleaned == "00000000":
        return False

    weights = [1, 2, 1, 2, 1, 2, 4, 1]
    digits = [int(c) for c in cleaned]

    sum1 = 0
    for d, w in zip(digits, weights):
        prod = d * w
        sum1 += (prod // 10) + (prod % 10)

    if sum1 % 10 == 0:
        return True

    # Special handling when the 7th digit (0-indexed: 6) is 7
    # 7 * 4 = 28 -> (2 + 8) = 10 -> sum of digits is 1
    if digits[6] == 7:
        sum2 = sum1 - 9
        if sum2 % 10 == 0:
            return True

    return False


class InvoiceAuditor:
    """Core auditor applying enterprise accounting and anti-fraud rules."""

    def __init__(
        self,
        historical_invoice_numbers: Optional[Set[str]] = None,
        db_path: Optional[Union[str, Path]] = None
    ):
        self.seen_invoices: Set[str] = set(historical_invoice_numbers or set())
        self.db_path = Path(db_path) if db_path else None

    def audit(self, invoice: InvoiceData, file_path: str = "") -> AuditReport:
        """Run comprehensive audit checks on an invoice or receipt."""
        issues: List[AuditIssue] = []

        # 1. Duplicate submission check (Memory session + Database historical)
        if invoice.invoice_number:
            normalized_num = invoice.invoice_number.strip().upper()
            is_dup = normalized_num in self.seen_invoices
            if not is_dup and self.db_path and self.db_path.exists():
                from .database import is_invoice_seen
                if is_invoice_seen(normalized_num, db_path=self.db_path):
                    is_dup = True

            if is_dup:
                issues.append(AuditIssue(
                    code="ERR_DUPLICATE_INVOICE",
                    level="ERROR",
                    message=f"重複報銷警報：發票/收據號碼 '{invoice.invoice_number}' 先前已報銷或已存在歷史資料庫中。"
                ))
            else:
                self.seen_invoices.add(normalized_num)
        else:
            if invoice.document_type == DocumentType.TW_INVOICE:
                issues.append(AuditIssue(
                    code="ERR_MISSING_INVOICE_NUMBER",
                    level="ERROR",
                    message="發票號碼缺失：統一發票必須具有字軌發票號碼 (如：AB-12345678)。"
                ))

        # 2. Date check
        if not invoice.invoice_date:
            issues.append(AuditIssue(
                code="WARN_MISSING_DATE",
                level="WARNING",
                message="開立日期缺失或格式無法解析，建議人工查核單據實體日期。"
            ))

        # 3. Document-type specific validations
        if invoice.document_type == DocumentType.TW_INVOICE:
            self._audit_taiwan_invoice(invoice, issues)
        elif invoice.document_type == DocumentType.THSR_TICKET:
            self._audit_thsr_ticket(invoice, issues)
        elif invoice.document_type == DocumentType.TAXI_RECEIPT:
            self._audit_taxi_receipt(invoice, issues)
        elif invoice.document_type == DocumentType.COMMERCIAL_INVOICE:
            self._audit_commercial_invoice(invoice, issues)

        # 4. Low confidence score check
        if invoice.confidence_score < 0.70:
            issues.append(AuditIssue(
                code="WARN_LOW_CONFIDENCE",
                level="WARNING",
                message=f"AI 解析信賴度偏低 ({invoice.confidence_score:.2f})，可能單據模糊或歪斜，需人工複核。"
            ))

        # Determine overall status
        status = AuditStatus.APPROVED
        for issue in issues:
            if issue.level == "ERROR":
                status = AuditStatus.REJECTED
                break
            elif issue.level == "WARNING":
                status = AuditStatus.WARNING

        return AuditReport(
            file_path=file_path,
            data=invoice,
            status=status,
            issues=issues,
            audit_timestamp=datetime.now().isoformat()
        )

    def _audit_taiwan_invoice(self, inv: InvoiceData, issues: List[AuditIssue]) -> None:
        """Audit rules for Taiwan Uniform Invoices (統一發票)."""
        # Buyer Tax ID check
        if inv.buyer_tax_id:
            if not validate_taiwan_tax_id(inv.buyer_tax_id):
                issues.append(AuditIssue(
                    code="ERR_INVALID_BUYER_TAX_ID",
                    level="ERROR",
                    message=f"買方統一編號邏輯檢核失敗：'{inv.buyer_tax_id}' 不符合財政部 8 碼邏輯驗證碼規則。"
                ))
        else:
            issues.append(AuditIssue(
                code="WARN_NO_BUYER_TAX_ID",
                level="WARNING",
                message="無買方統一編號（二聯式或個人消費），如需報帳可能無法申報扣抵營業稅。"
            ))

        # Seller Tax ID check
        if inv.seller_tax_id and not validate_taiwan_tax_id(inv.seller_tax_id):
            issues.append(AuditIssue(
                code="ERR_INVALID_SELLER_TAX_ID",
                level="ERROR",
                message=f"賣方統一編號邏輯檢核失敗：'{inv.seller_tax_id}' 不符合財政部 8 碼邏輯驗證碼規則。"
            ))

        # Financial Math Check
        if inv.subtotal is not None and inv.tax_amount is not None:
            expected_total = inv.subtotal + inv.tax_amount
            if abs(expected_total - inv.total_amount) > 1.5:
                issues.append(AuditIssue(
                    code="ERR_MATH_MISMATCH",
                    level="ERROR",
                    message=(
                        f"金額數學平衡檢核失敗：未稅金額 (${inv.subtotal:,.2f}) + "
                        f"稅額 (${inv.tax_amount:,.2f}) != 總金額 (${inv.total_amount:,.2f})"
                    )
                ))

            # 5% tax check
            expected_tax = inv.subtotal * (inv.tax_rate or 0.05)
            if abs(expected_tax - inv.tax_amount) > 2.0:
                issues.append(AuditIssue(
                    code="WARN_TAX_RATE_DEVIATION",
                    level="WARNING",
                    message=(
                        f"營業稅額與 5% 基準差異較大：依未稅金額計算稅額應約為 ${expected_tax:,.0f}，"
                        f"實體單據載明為 ${inv.tax_amount:,.0f}。"
                    )
                ))

    def _audit_thsr_ticket(self, inv: InvoiceData, issues: List[AuditIssue]) -> None:
        """Audit rules for Taiwan High Speed Rail tickets."""
        details = inv.travel_details
        if not details:
            issues.append(AuditIssue(
                code="WARN_MISSING_TRAVEL_DETAILS",
                level="WARNING",
                message="高鐵票缺少行程詳細資訊 (起訖站或搭乘日期)。"
            ))
            return

        # Station check
        if details.departure and details.departure not in TAIWAN_HSR_STATIONS:
            issues.append(AuditIssue(
                code="WARN_UNKNOWN_HSR_STATION",
                level="WARNING",
                message=f"起點站名 '{details.departure}' 非標準台灣高鐵車站名稱。"
            ))
        if details.destination and details.destination not in TAIWAN_HSR_STATIONS:
            issues.append(AuditIssue(
                code="WARN_UNKNOWN_HSR_STATION",
                level="WARNING",
                message=f"訖點站名 '{details.destination}' 非標準台灣高鐵車站名稱。"
            ))

        # Positive amount check
        if inv.total_amount <= 0:
            issues.append(AuditIssue(
                code="ERR_INVALID_FARE",
                level="ERROR",
                message="高鐵車票票價不可小於或等於 0 元。"
            ))

        # Buyer tax id on ticket
        if inv.buyer_tax_id and not validate_taiwan_tax_id(inv.buyer_tax_id):
            issues.append(AuditIssue(
                code="ERR_INVALID_BUYER_TAX_ID",
                level="ERROR",
                message=f"高鐵購票證明上的買方統編 '{inv.buyer_tax_id}' 格式驗證不合規。"
            ))

    def _audit_taxi_receipt(self, inv: InvoiceData, issues: List[AuditIssue]) -> None:
        """Audit rules for Taxi receipts."""
        details = inv.travel_details

        # Fare sanity check
        if inv.total_amount <= 0:
            issues.append(AuditIssue(
                code="ERR_INVALID_FARE",
                level="ERROR",
                message="計程車乘車金額不可小於或等於 0 元。"
            ))
        elif inv.total_amount > 5000:
            issues.append(AuditIssue(
                code="WARN_HIGH_FARE",
                level="WARNING",
                message=f"計程車單趟車資 (${inv.total_amount:,.0f}) 超過常見內部報銷預警門檻 ($5,000)，需檢附主管特准證明。"
            ))

        # Vehicle number check
        if details and details.vehicle_number:
            v_num = details.vehicle_number.strip().replace(" ", "").upper()
            if "-" not in v_num and len(v_num) not in (5, 6, 7):
                issues.append(AuditIssue(
                    code="WARN_SUSPICIOUS_VEHICLE_NO",
                    level="WARNING",
                    message=f"計程車車號 '{details.vehicle_number}' 格式異常，請核對車牌。"
                ))

    def _audit_commercial_invoice(self, inv: InvoiceData, issues: List[AuditIssue]) -> None:
        """Audit rules for general commercial invoices."""
        if inv.total_amount <= 0:
            issues.append(AuditIssue(
                code="ERR_INVALID_AMOUNT",
                level="ERROR",
                message="發票總金額必須大於 0。"
            ))
