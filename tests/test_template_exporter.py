"""Unit tests for TemplateExpenseExporter and corporate template mapping."""

import openpyxl
from pathlib import Path
from src.models import (
    InvoiceData, AuditReport, AuditStatus, DocumentType,
    ExpenseCategory, InvoiceItem, TravelMetadata
)
from src.template_exporter import TemplateExpenseExporter, infer_expense_category, normalize_currency


def test_infer_expense_category():
    # Explicit category
    inv1 = InvoiceData(
        document_type=DocumentType.COMMERCIAL_INVOICE,
        total_amount=100.0,
        expense_category=ExpenseCategory.FOOD
    )
    rep1 = AuditReport(file_path="", data=inv1, status=AuditStatus.APPROVED, audit_timestamp="")
    assert infer_expense_category(rep1) == "F"

    # THSR ticket inference
    inv2 = InvoiceData(
        document_type=DocumentType.THSR_TICKET,
        total_amount=1490.0
    )
    rep2 = AuditReport(file_path="", data=inv2, status=AuditStatus.APPROVED, audit_timestamp="")
    assert infer_expense_category(rep2) == "T"

    # Hotel keyword inference
    inv3 = InvoiceData(
        document_type=DocumentType.COMMERCIAL_INVOICE,
        seller_name="Hilton Hotel",
        total_amount=200.0
    )
    rep3 = AuditReport(file_path="", data=inv3, status=AuditStatus.APPROVED, audit_timestamp="")
    assert infer_expense_category(rep3) == "H"


def test_normalize_currency():
    assert normalize_currency("TWD") == "NT$"
    assert normalize_currency("NTD") == "NT$"
    assert normalize_currency("台幣") == "NT$"
    assert normalize_currency("RMB") == "RMB"
    assert normalize_currency("CNY") == "RMB"
    assert normalize_currency("THB") == "THB"
    assert normalize_currency("EUR") == "EUR"
    assert normalize_currency("USD") == "USD"
    assert normalize_currency("HKD") == "HKD"


def test_template_exporter_execution(tmp_path):
    template_path = Path("Template.xlsx")
    if not template_path.exists():
        return  # skip if running in environment without template

    reports = [
        AuditReport(
            file_path="sample_data/sample_china_vat_invoice.json",
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="CN-882103",
                invoice_date="2026-08-18",
                seller_name="上海鼎泰豐",
                currency="RMB",
                total_amount=368.0,
                expense_category=ExpenseCategory.FOOD,
                purpose="上海商務餐敘",
                payment_method="Credit Card"
            ),
            status=AuditStatus.APPROVED,
            audit_timestamp="2026-08-18T12:00:00"
        ),
        AuditReport(
            file_path="sample_data/sample_thailand_grab.json",
            data=InvoiceData(
                document_type=DocumentType.TAXI_RECEIPT,
                invoice_number="GRB-TH-8271",
                invoice_date="2026-08-20",
                seller_name="GrabTaxi Thailand",
                currency="THB",
                total_amount=260.0,
                expense_category=ExpenseCategory.TRANSPORT,
                purpose="曼谷機場至客戶公司",
                payment_method="Credit Card"
            ),
            status=AuditStatus.APPROVED,
            audit_timestamp="2026-08-20T14:00:00"
        ),
        AuditReport(
            file_path="sample_data/sample_overseas_hotel.json",
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="HTL-FRA-491",
                invoice_date="2026-08-25",
                seller_name="Frankfurt Hotel",
                currency="EUR",
                total_amount=145.0,
                expense_category=ExpenseCategory.HOTEL,
                purpose="法蘭克福住宿",
                payment_method="Credit Card"
            ),
            status=AuditStatus.APPROVED,
            audit_timestamp="2026-08-25T18:00:00"
        )
    ]

    out_file = tmp_path / "Filled_Report.xlsx"
    exporter = TemplateExpenseExporter(reports, template_path=template_path)
    result_path = exporter.export(out_file)

    assert result_path.exists()

    # Verify sheet contents
    wb = openpyxl.load_workbook(result_path, data_only=False)
    assert "Report" in wb.sheetnames
    assert "Rates" in wb.sheetnames
    assert "Calc" in wb.sheetnames
    assert "E category" in wb.sheetnames

    ws = wb["Report"]

    # Verify Days Travelling (Report!F3) & Boolean Rec't compatibility with Calc!J
    assert isinstance(ws["F3"].value, int) and ws["F3"].value >= 1
    assert ws.cell(row=13, column=6).value is True

    # Verify Row 13 (First item: China RMB 368)
    assert ws.cell(row=13, column=1).value == 1
    assert ws.cell(row=13, column=2).value == "2026/08/18"
    assert ws.cell(row=13, column=3).value == 368.0
    assert ws.cell(row=13, column=4).value == "RMB"
    assert ws.cell(row=13, column=7).value == "上海商務餐敘"
    assert ws.cell(row=13, column=8).value == "F"
    assert ws.cell(row=13, column=9).value == "上海鼎泰豐"
    # Column 10 (J) formula must still be preserved and include all 5 currencies ($E$9 THB)
    formula_j13 = str(ws.cell(row=13, column=10).value)
    assert formula_j13.startswith("=")
    assert "D13" in formula_j13
    assert "$E$9" in formula_j13

    # Verify Row 14 (Second item: Grab THB 260)
    assert ws.cell(row=14, column=1).value == 2
    assert ws.cell(row=14, column=3).value == 260.0
    assert ws.cell(row=14, column=4).value == "THB"
    assert ws.cell(row=14, column=8).value == "T"
    assert "$E$9" in str(ws.cell(row=14, column=10).value)

    # Verify Row 15 (Third item: Hotel EUR 145)
    assert ws.cell(row=15, column=1).value == 3
    assert ws.cell(row=15, column=3).value == 145.0
    assert ws.cell(row=15, column=4).value == "EUR"
    assert ws.cell(row=15, column=8).value == "H"
    assert "$E$9" in str(ws.cell(row=15, column=10).value)
