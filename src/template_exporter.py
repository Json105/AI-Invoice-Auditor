"""Exporter for corporate travel expense template (Template.xlsx).

Loads existing enterprise Excel template, preserves formulas, formats,
and sheets ('Report', 'Calc', 'Rates', 'E category'), and populates
audited items into the 'Report' sheet.
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Union, Optional
import openpyxl
from openpyxl.styles import Font, Alignment

from .models import AuditReport, DocumentType, ExpenseCategory


def infer_expense_category(report: AuditReport) -> str:
    """Infer F/I/T/H/S/E category if not explicitly set."""
    invoice = report.data
    if invoice.expense_category:
        if isinstance(invoice.expense_category, ExpenseCategory):
            return invoice.expense_category.value
        return str(invoice.expense_category)

    # Infer by document type
    if invoice.document_type in (DocumentType.THSR_TICKET, DocumentType.TAXI_RECEIPT):
        return "T"

    # Infer by seller or item text
    text_corpus = (
        (invoice.seller_name or "") + " " +
        (invoice.purpose or "") + " " +
        " ".join(item.name for item in invoice.items)
    ).lower()

    if any(k in text_corpus for k in ["hotel", "inn", "hostel", "住宿", "飯店", "酒店", "旅館", "房費"]):
        return "H"
    if any(k in text_corpus for k in ["food", "restaurant", "cafe", "餐", "食", "咖啡", "飲", "鼎泰豐", "便當"]):
        return "F"
    if any(k in text_corpus for k in ["taxi", "grab", "uber", "flight", "train", "高鐵", "車資", "機票", "捷運", "交通"]):
        return "T"
    if any(k in text_corpus for k in ["gift", "present", "禮品", "伴手禮", "花籃"]):
        return "S"
    if any(k in text_corpus for k in ["insurance", "visa", "保險", "簽證"]):
        return "I"

    # Default to Extra / administrative
    return "E"


def normalize_currency(currency_str: Optional[str]) -> str:
    """Normalize currency to match template codes (EUR, HKD, RMB, USD, THB, NT$)."""
    if not currency_str:
        return "NT$"
    c = currency_str.strip().upper()
    if c in ("TWD", "NTD", "NT$", "NT", "台幣", "新台幣"):
        return "NT$"
    if c in ("RMB", "CNY", "人民幣", "元"):
        return "RMB"
    if c in ("THB", "泰銖", "BAHT"):
        return "THB"
    if c in ("EUR", "EURO", "歐元"):
        return "EUR"
    if c in ("USD", "US$", "美元", "美金"):
        return "USD"
    if c in ("HKD", "HK$", "港幣"):
        return "HKD"
    return c


class TemplateExpenseExporter:
    """Populates audit reports into corporate Template.xlsx."""

    def __init__(self, reports: List[AuditReport], template_path: Union[str, Path] = "Template.xlsx"):
        self.reports = reports
        self.template_path = Path(template_path)
        if not self.template_path.exists():
            raise FileNotFoundError(f"Template file not found at: {self.template_path}")

    def export(self, output_path: Union[str, Path]) -> Path:
        """Populate items into template copy and save to output_path."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Copy template first to preserve all binary metadata and sheets
        shutil.copyfile(self.template_path, out)

        wb = openpyxl.load_workbook(out)
        if "Report" not in wb.sheetnames:
            raise ValueError(f"Sheet 'Report' not found in template: {self.template_path}")

        ws = wb["Report"]

        # Populate Live Exchange Rates from FX Service (Rows 5 to 9)
        from .services.fx_service import fx_service
        rates_data = fx_service.get_live_rates()
        live_rates = rates_data.get("rates", {})

        rate_rows = {
            5: ("EUR", live_rates.get("EUR", 36.75)),
            6: ("HKD", live_rates.get("HKD", 4.05)),
            7: ("RMB", live_rates.get("RMB", 4.72)),
            8: ("USD", live_rates.get("USD", 31.75)),
            9: ("THB", live_rates.get("THB", 0.96)),
        }

        for r, (curr, rate_val) in rate_rows.items():
            ws.cell(row=r, column=5, value=curr)  # Col E
            ws.cell(row=r, column=6, value=round(rate_val, 3))  # Col F
            ws.cell(row=r, column=7, value="央行/台銀牌告即時匯率")  # Col G

        # Populate trip days (Report!F3) and top summary formulas (Report!C8, Report!C10)
        parsed_dates = []
        for report in self.reports:
            raw_date = (report.data.invoice_date or "").strip()
            if raw_date:
                normalized_date = raw_date.replace("/", "-")
                try:
                    parsed_dates.append(datetime.strptime(normalized_date[:10], "%Y-%m-%d").date())
                except ValueError:
                    pass
        if parsed_dates:
            travel_days = max(1, (max(parsed_dates) - min(parsed_dates)).days + 1)
        else:
            travel_days = max(1, len(self.reports))
        ws.cell(row=3, column=6, value=travel_days)  # F3: Days Travelling
        ws.cell(row=8, column=3, value="=C5")        # C8: Bonus / Subtotal
        ws.cell(row=10, column=3, value="=C5-C9")    # C10: Net Payment

        # Base styles for data cells
        data_font = Font(name="Calibri", size=10)
        center_align = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")
        right_align = Alignment(horizontal="right", vertical="center")

        start_row = 13
        for idx, report in enumerate(self.reports, start=1):
            row_idx = start_row + idx - 1
            invoice = report.data

            # Normalize values
            date_val = invoice.invoice_date or datetime.now().strftime("%Y/%m/%d")
            date_val = date_val.replace("-", "/")

            amount_val = float(invoice.total_amount)
            currency_code = normalize_currency(invoice.currency)
            payment_val = invoice.payment_method or "Credit Card"
            # Calc!J{row} evaluates IF(Report!F{row}, 0, Report!J{row}), requiring boolean/numeric
            receipt_val = bool(invoice.invoice_number or report.file_path)

            # Purpose summary
            if invoice.purpose:
                purpose_val = invoice.purpose
            elif invoice.notes:
                purpose_val = invoice.notes
            elif invoice.items:
                purpose_val = invoice.items[0].name
            else:
                purpose_val = "差旅核銷項目"

            type_code = infer_expense_category(report)
            company_val = invoice.seller_name or "商家"

            # Set cells (Columns A to I)
            # Col A (1): No.
            cell_no = ws.cell(row=row_idx, column=1, value=idx)
            cell_no.font = data_font
            cell_no.alignment = center_align

            # Col B (2): Date
            cell_date = ws.cell(row=row_idx, column=2, value=date_val)
            cell_date.font = data_font
            cell_date.alignment = center_align

            # Col C (3): Amount (Original currency)
            cell_amount = ws.cell(row=row_idx, column=3, value=amount_val)
            cell_amount.font = data_font
            cell_amount.alignment = right_align
            cell_amount.number_format = "#,##0.00"

            # Col D (4): Cur'cy
            cell_cur = ws.cell(row=row_idx, column=4, value=currency_code)
            cell_cur.font = data_font
            cell_cur.alignment = center_align

            # Col E (5): Payment
            cell_pay = ws.cell(row=row_idx, column=5, value=payment_val)
            cell_pay.font = data_font
            cell_pay.alignment = center_align

            # Col F (6): Rec't
            cell_rec = ws.cell(row=row_idx, column=6, value=receipt_val)
            cell_rec.font = data_font
            cell_rec.alignment = center_align

            # Col G (7): Purpose
            cell_pur = ws.cell(row=row_idx, column=7, value=purpose_val)
            cell_pur.font = data_font
            cell_pur.alignment = left_align

            # Col H (8): Type (F/I/T/H/S/E)
            cell_type = ws.cell(row=row_idx, column=8, value=type_code)
            cell_type.font = data_font
            cell_type.alignment = center_align

            # Col I (9): Company
            cell_comp = ws.cell(row=row_idx, column=9, value=company_val)
            cell_comp.font = data_font
            cell_comp.alignment = left_align

            # Col J (10): Always write complete 5-currency formula (fixes missing rows 22-30 and missing $E$9 in rows 31+)
            ws.cell(
                row=row_idx,
                column=10,
                value=f'=IF(D{row_idx}>0,(IF(D{row_idx}=$E$5,C{row_idx}*$F$5,IF(D{row_idx}=$E$6,C{row_idx}*$F$6,IF(D{row_idx}=$E$7,C{row_idx}*$F$7,IF(D{row_idx}=$E$8,C{row_idx}*$F$8,IF(D{row_idx}=$E$9,C{row_idx}*$F$9,C{row_idx}))))))," ")'
            )

        wb.save(out)
        return out
