"""Deterministic Mock Extractor for testing, CI/CD, and offline demos."""

import hashlib
import json
from pathlib import Path
from typing import List, Union
from .base import BaseInvoiceExtractor
from ..models import InvoiceData, DocumentType, InvoiceItem, TravelMetadata, ExpenseCategory


class MockExtractor(BaseInvoiceExtractor):
    """Mock extractor providing realistic test cases without requiring API keys."""

    def extract_many(self, file_path: Union[str, Path], multi_receipt: bool = False) -> List[InvoiceData]:
        """Support multi-receipt JSON payloads and desk-collage multi-receipt mock photos."""
        path = Path(file_path)
        stem = path.stem.lower()

        if path.suffix.lower() == ".json" and path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("invoices"), list):
                return [InvoiceData.model_validate(item) for item in raw["invoices"]]
            if isinstance(raw, list):
                return [InvoiceData.model_validate(item) for item in raw]
            return [InvoiceData.model_validate(raw)]

        if multi_receipt and any(k in stem for k in ("multi", "desk", "group", "collage")):
            tag = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:4].upper()
            return [
                InvoiceData(
                    document_type=DocumentType.TW_INVOICE,
                    invoice_number=f"MR-{tag}01",
                    invoice_date="2026-11-10",
                    buyer_tax_id="22099131",
                    buyer_name="台灣積體電路製造股份有限公司",
                    seller_tax_id="04541302",
                    seller_name="鴻海商務文具補給站",
                    currency="NT$",
                    subtotal=1000.0,
                    tax_amount=50.0,
                    tax_rate=0.05,
                    total_amount=1050.0,
                    expense_category=ExpenseCategory.EXTRA,
                    purpose="同框拍攝 - 會議簡報耗材採購",
                    payment_method="Credit Card",
                    confidence_score=0.97,
                ),
                InvoiceData(
                    document_type=DocumentType.THSR_TICKET,
                    invoice_number=f"HS-{tag}02",
                    invoice_date="2026-11-10",
                    buyer_tax_id="22099131",
                    seller_tax_id="16446274",
                    seller_name="台灣高速鐵路股份有限公司",
                    currency="NT$",
                    subtotal=667.0,
                    tax_amount=33.0,
                    tax_rate=0.05,
                    total_amount=700.0,
                    expense_category=ExpenseCategory.TRANSPORT,
                    purpose="同框拍攝 - 高鐵差旅車票 (台北-台中)",
                    payment_method="Credit Card",
                    travel_details=TravelMetadata(departure="台北", destination="台中", train_or_flight_no="0615"),
                    confidence_score=0.98,
                ),
                InvoiceData(
                    document_type=DocumentType.TAXI_RECEIPT,
                    invoice_number=f"TX-{tag}03",
                    invoice_date="2026-11-10",
                    seller_name="台灣大車隊 55688",
                    currency="NT$",
                    subtotal=280.0,
                    tax_amount=0.0,
                    tax_rate=0.0,
                    total_amount=280.0,
                    expense_category=ExpenseCategory.TRANSPORT,
                    purpose="同框拍攝 - 高鐵站至客戶園區車資",
                    payment_method="Cash",
                    travel_details=TravelMetadata(departure="台中高鐵站", destination="中科園區", vehicle_number="TDC-9910"),
                    confidence_score=0.95,
                ),
            ]

        return [self.extract(file_path)]

    def extract(self, file_path: Union[str, Path]) -> InvoiceData:
        path = Path(file_path)
        stem = path.stem.lower()

        # If it's directly a JSON fixture, load it
        if path.suffix.lower() == ".json" and path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return InvoiceData.model_validate(data)

        # Dynamic mocking based on filename keyword
        if "china" in stem or "cny" in stem or "rmb" in stem:
            return InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="CN-9823410293",
                invoice_date="2026-08-18",
                buyer_name="Enterprise Global Ltd.",
                seller_tax_id="91310101MA1FP88888",
                seller_name="上海鼎泰豐餐飲管理有限公司",
                currency="RMB",
                subtotal=347.17,
                tax_amount=20.83,
                tax_rate=0.06,
                total_amount=368.0,
                expense_category=ExpenseCategory.FOOD,
                purpose="上海客戶商務交流餐敘",
                payment_method="Credit Card",
                items=[
                    InvoiceItem(name="商務餐飲套餐", quantity=1.0, unit_price=368.0, amount=368.0)
                ],
                confidence_score=0.98
            )

        elif "grab" in stem or "thb" in stem or "thai" in stem:
            return InvoiceData(
                document_type=DocumentType.TAXI_RECEIPT,
                invoice_number="GRB-TH-8271039",
                invoice_date="2026-08-20",
                buyer_name="Enterprise Global Ltd.",
                seller_name="GrabTaxi (Thailand) Co., Ltd.",
                currency="THB",
                subtotal=243.0,
                tax_amount=17.0,
                tax_rate=0.07,
                total_amount=260.0,
                expense_category=ExpenseCategory.TRANSPORT,
                purpose="曼谷素萬那普機場至客戶辦公室 (Grab)",
                payment_method="Credit Card",
                travel_details=TravelMetadata(
                    departure="Suvarnabhumi Airport",
                    destination="Sathorn Business District",
                    vehicle_number="1Kor-8821",
                    travel_date="2026-08-20",
                    start_time="11:30",
                    end_time="12:15"
                ),
                confidence_score=0.97
            )

        elif "hotel" in stem or "eur" in stem or "lodging" in stem:
            return InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="HTL-EUR-49102",
                invoice_date="2026-08-25",
                buyer_name="Enterprise Global Ltd.",
                seller_tax_id="DE123456789",
                seller_name="Euro Plaza Business Hotel",
                currency="EUR",
                subtotal=135.51,
                tax_amount=9.49,
                tax_rate=0.07,
                total_amount=145.0,
                expense_category=ExpenseCategory.HOTEL,
                purpose="歐洲客戶拜訪出差住宿",
                payment_method="Credit Card",
                items=[
                    InvoiceItem(name="Standard Business Room (1 Night)", quantity=1.0, unit_price=145.0, amount=145.0)
                ],
                confidence_score=0.99
            )

        elif "thsr" in stem or "highspeed" in stem or "train" in stem:
            return InvoiceData(
                document_type=DocumentType.THSR_TICKET,
                invoice_number="291048201948",
                invoice_date="2026-09-02",
                buyer_tax_id="22099131",  # 台積電統編 (合規測試)
                buyer_name="台灣積體電路製造股份有限公司",
                seller_tax_id="16446274",  # 台灣高鐵統編
                seller_name="台灣高速鐵路股份有限公司",
                currency="NT$",
                subtotal=1419.0,
                tax_amount=71.0,
                tax_rate=0.05,
                total_amount=1490.0,
                expense_category=ExpenseCategory.TRANSPORT,
                purpose="高鐵商務出差 (台北-左營)",
                payment_method="Credit Card",
                items=[
                    InvoiceItem(name="台灣高鐵標準車廂車票 (台北-左營)", quantity=1.0, unit_price=1490.0, amount=1490.0)
                ],
                travel_details=TravelMetadata(
                    departure="台北",
                    destination="左營",
                    train_or_flight_no="0609",
                    seat_info="03車11A",
                    travel_date="2026-09-02",
                    start_time="08:15",
                    end_time="10:05"
                ),
                notes="商務出差高雄廠區參訪",
                confidence_score=0.99
            )

        elif "taxi" in stem or "cab" in stem or "uber" in stem:
            return InvoiceData(
                document_type=DocumentType.TAXI_RECEIPT,
                invoice_number="TX-20260902-882",
                invoice_date="2026-09-02",
                buyer_tax_id=None,
                seller_name="台灣大車隊 55688",
                currency="NT$",
                subtotal=350.0,
                tax_amount=0.0,
                tax_rate=0.0,
                total_amount=350.0,
                expense_category=ExpenseCategory.TRANSPORT,
                purpose="計程車乘車資費 (拜訪內湖客戶)",
                payment_method="Cash",
                items=[
                    InvoiceItem(name="計程車乘車資費 (台北車站 -> 內湖科技園區)", quantity=1.0, unit_price=350.0, amount=350.0)
                ],
                travel_details=TravelMetadata(
                    departure="台北車站",
                    destination="內湖科技園區",
                    vehicle_number="TDC-8821",
                    driver_name="陳大華",
                    travel_date="2026-09-02",
                    start_time="14:20",
                    end_time="14:50",
                    mileage_km=9.8
                ),
                notes="客戶緊急拜訪車資報銷",
                confidence_score=0.96
            )

        elif "error" in stem or "invalid" in stem or "fraud" in stem:
            # An invoice designed to fail math check and buyer tax ID
            return InvoiceData(
                document_type=DocumentType.TW_INVOICE,
                invoice_number="ZZ-99998888",
                invoice_date="2026-08-30",
                buyer_tax_id="12345678",  # 錯誤統編 (邏輯檢查失敗)
                buyer_name="測試假公司",
                seller_tax_id="88888888",
                seller_name="不良黑心商行",
                currency="NT$",
                subtotal=10000.0,
                tax_amount=500.0,
                tax_rate=0.05,
                total_amount=12000.0,  # 數學不符！10000 + 500 != 12000
                expense_category=ExpenseCategory.EXTRA,
                purpose="測試錯誤發票",
                payment_method="Credit Card",
                items=[
                    InvoiceItem(name="虛報耗材品項", quantity=1.0, unit_price=12000.0, amount=12000.0)
                ],
                confidence_score=0.91
            )

        else:
            # Default standard Taiwan B2B Uniform Invoice
            return InvoiceData(
                document_type=DocumentType.TW_INVOICE,
                invoice_number="AB-98765432",
                invoice_date="2026-09-01",
                buyer_tax_id="22099131",  # 台積電統編 (合規)
                buyer_name="台灣積體電路製造股份有限公司",
                seller_tax_id="04541302",  # 鴻海精密統編 (合規)
                seller_name="鴻海精密工業股份有限公司",
                currency="NT$",
                subtotal=20000.0,
                tax_amount=1000.0,
                tax_rate=0.05,
                total_amount=21000.0,
                expense_category=ExpenseCategory.EXTRA,
                purpose="伺服器機櫃備品採購",
                payment_method="Credit Card",
                items=[
                    InvoiceItem(name="伺服器機櫃備品", quantity=2.0, unit_price=10000.0, amount=20000.0)
                ],
                confidence_score=0.98
            )
