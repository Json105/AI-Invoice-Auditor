"""Data models for AI-Invoice-Auditor.

Defines schemas for multi-document extraction (Taiwan Invoices, THSR Tickets,
Taxi Receipts, Commercial Invoices) and structured audit reports.
"""

from enum import Enum
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    """Supported expense and document categories."""
    TW_INVOICE = "tw_invoice"              # 台灣統一發票 (二聯/三聯/電子發票)
    THSR_TICKET = "thsr_ticket"            # 台灣高鐵票 / 購票證明
    TAXI_RECEIPT = "taxi_receipt"          # 計程車收據 / 乘車證明
    COMMERCIAL_INVOICE = "commercial_invoice" # 跨國商業發票
    GENERAL_RECEIPT = "general_receipt"    # 通用收據 / 免用統一發票收據


class ExpenseCategory(str, Enum):
    """Expense categories corresponding to corporate template (F/I/T/H/S/E)."""
    FOOD = "F"          # 餐飲膳食 (Food & Dining)
    INSURANCE = "I"     # 保險 / 簽證 (Insurance / Visa)
    TRANSPORT = "T"     # 交通 / 機票 / 高鐵 / 計程車 (Transport / Taxi / Train)
    HOTEL = "H"         # 住宿 / 飯店 (Hotel & Accommodation)
    SOCIAL = "S"        # 社交禮品 / 交際 (Social Gift / Business Entertainment)
    EXTRA = "E"         # 行政雜支 (Extra: Wifi, Phone, Overweight luggage, etc.)


class AuditStatus(str, Enum):
    """Status indicating the compliance result of an expense item."""
    APPROVED = "APPROVED"   # 合規通過，可直接匯入 ERP 報銷
    WARNING = "WARNING"     # 警示提醒，建議人工複核
    REJECTED = "REJECTED"   # 重大違規 / 數學不符，阻擋報銷


class InvoiceItem(BaseModel):
    """Detailed item breakdown from invoice or receipt."""
    name: str = Field(..., description="品名或消費項目")
    quantity: Optional[float] = Field(1.0, description="數量")
    unit_price: Optional[float] = Field(None, description="單價")
    amount: float = Field(..., description="小計金額")


class TravelMetadata(BaseModel):
    """Metadata specific to travel and transportation expenses."""
    departure: Optional[str] = Field(None, description="起點 (如：台北)")
    destination: Optional[str] = Field(None, description="訖點 (如：左營)")
    train_or_flight_no: Optional[str] = Field(None, description="車次 / 車班號碼 (如：0609)")
    seat_info: Optional[str] = Field(None, description="座位資訊 (如：03車11A)")
    vehicle_number: Optional[str] = Field(None, description="計程車牌照號碼 (如：TDC-8888)")
    driver_name: Optional[str] = Field(None, description="駕駛人姓名或司機編號")
    travel_date: Optional[str] = Field(None, description="搭乘日期 (YYYY-MM-DD)")
    start_time: Optional[str] = Field(None, description="出發時間 (HH:MM)")
    end_time: Optional[str] = Field(None, description="抵達時間 (HH:MM)")
    mileage_km: Optional[float] = Field(None, description="行車里程 (公里)")


class InvoiceData(BaseModel):
    """Structured extraction output from multimodal AI."""
    document_type: DocumentType = Field(..., description="單據類型")
    invoice_number: Optional[str] = Field(None, description="發票號碼、票號或收據單號 (例：AB-12345678)")
    invoice_date: Optional[str] = Field(None, description="開立日期 (格式：YYYY-MM-DD)")
    buyer_tax_id: Optional[str] = Field(None, description="買方統一編號 (8碼數字)")
    buyer_name: Optional[str] = Field(None, description="買方公司名稱或抬頭")
    seller_tax_id: Optional[str] = Field(None, description="賣方統一編號 (8碼數字)")
    seller_name: Optional[str] = Field(None, description="賣方名稱、車隊名稱或商號")
    currency: str = Field("NT$", description="幣別代碼 (例如：NT$, RMB, THB, EUR, USD, HKD)")
    subtotal: Optional[float] = Field(None, description="未稅銷售額 (Sales Amount)")
    tax_amount: Optional[float] = Field(None, description="營業稅額 (VAT/Tax)")
    tax_rate: Optional[float] = Field(0.05, description="稅率 (預設台灣 5%)")
    total_amount: float = Field(..., description="含稅總金額 (Total Amount / Fare)")
    expense_category: Optional[ExpenseCategory] = Field(None, description="企業報銷類別代碼 (F/I/T/H/S/E)")
    purpose: Optional[str] = Field(None, description="出差消費用途摘要")
    payment_method: Optional[str] = Field("Credit Card", description="付款方式 (Credit Card, Cash 等)")
    exchange_rate: Optional[float] = Field(None, gt=0, description="實際套用匯率 (1 原幣 = ? TWD)")
    exchange_rate_source: Optional[str] = Field(None, description="匯率來源 (臺灣銀行牌告匯率 / 員工自訂匯率)")
    exchange_rate_date: Optional[str] = Field(None, description="匯率牌告基準日 (YYYY-MM-DD)")
    ntd_amount: Optional[float] = Field(None, description="折合新台幣金額 (TWD)")
    items: List[InvoiceItem] = Field(default_factory=list, description="品項明細清單")
    travel_details: Optional[TravelMetadata] = Field(None, description="差旅額外資訊 (高鐵/計程車專用)")
    notes: Optional[str] = Field(None, description="備註或特殊說明")
    confidence_score: float = Field(1.0, ge=0.0, le=1.0, description="AI 解析信賴度分數 (0~1)")


class AuditIssue(BaseModel):
    """An identified compliance or validation issue."""
    code: str = Field(..., description="異常代碼 (如：ERR_MATH_MISMATCH)")
    level: Literal["ERROR", "WARNING", "INFO"] = Field(..., description="嚴重層級")
    message: str = Field(..., description="問題詳細描述與修正指引")


class AuditReport(BaseModel):
    """Consolidated audit report for a processed document."""
    file_path: str = Field(..., description="原始單據檔案路徑")
    data: InvoiceData = Field(..., description="結構化抽取資料")
    status: AuditStatus = Field(..., description="最終審核判定")
    issues: List[AuditIssue] = Field(default_factory=list, description="發現的異常清單")
    audit_timestamp: str = Field(..., description="稽核時間戳記")
