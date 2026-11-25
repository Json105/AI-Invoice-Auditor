"""Exporter for corporate travel expense template (Template.xlsx).

Loads existing enterprise Excel template, preserves formulas, formats,
and sheets ('Report', 'Calc', 'Rates', 'E category'), and populates
audited items into the 'Report' sheet.
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
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
    """Populates audit reports into corporate Template.xlsx with dynamic row expansion."""

    def __init__(
        self,
        reports: List[AuditReport],
        template_path: Union[str, Path] = "Template.xlsx",
        trip_meta: Optional[Dict[str, Any]] = None,
    ):
        self.reports = reports
        self.template_path = Path(template_path)
        self.trip_meta = trip_meta
        if not self.template_path.exists():
            raise FileNotFoundError(f"Template file not found at: {self.template_path}")

    def _compute_travel_days(self) -> int:
        """Compute business trip days from trip_meta dates or fallback to invoice dates."""
        if self.trip_meta:
            raw_start = (self.trip_meta.get("start_date") or "").strip().replace("/", "-")
            raw_end = (self.trip_meta.get("end_date") or "").strip().replace("/", "-")
            if raw_start and raw_end:
                try:
                    d1 = datetime.strptime(raw_start[:10], "%Y-%m-%d").date()
                    d2 = datetime.strptime(raw_end[:10], "%Y-%m-%d").date()
                    return max(1, (d2 - d1).days + 1)
                except ValueError:
                    pass

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
            return max(1, (max(parsed_dates) - min(parsed_dates)).days + 1)
        return max(1, len(self.reports))

    def _extend_dynamic_formulas(self, wb: openpyxl.Workbook, item_count: int) -> None:
        """Extend Report, Rates, and Calc summary/mapping formulas beyond fixed template row limits."""
        ws = wb["Report"]
        last_report_row = max(85, 12 + item_count)
        ws["C5"].value = f"=SUM(J13:J{last_report_row})"

        if "Rates" in wb.sheetnames:
            wb["Rates"]["C19"].value = f"=ROUND(SUM(Report!J13:J{last_report_row}),0)"

        if "Calc" in wb.sheetnames:
            ws_calc = wb["Calc"]
            last_calc_row = max(121, 3 + item_count)
            for idx in range(1, item_count + 1):
                c_row = 3 + idx
                r_row = 12 + idx
                ws_calc.cell(row=c_row, column=1, value=f"=Report!A{r_row}")
                ws_calc.cell(row=c_row, column=2, value=f"=Report!D{r_row}")
                ws_calc.cell(row=c_row, column=3, value=f"=Report!H{r_row}")
                ws_calc.cell(row=c_row, column=4, value=f'=IF(C{c_row}="H",Report!J{r_row},0)')
                ws_calc.cell(row=c_row, column=5, value=f'=IF(C{c_row}="S",Report!J{r_row},0)')
                ws_calc.cell(row=c_row, column=6, value=f'=IF(C{c_row}="I",Report!J{r_row},0)')
                ws_calc.cell(row=c_row, column=7, value=f'=IF(C{c_row}="T",Report!J{r_row},0)')
                ws_calc.cell(row=c_row, column=8, value=f'=IF(C{c_row}="F",Report!J{r_row},0)')
                ws_calc.cell(row=c_row, column=9, value=f'=IF(C{c_row}="E",Report!J{r_row},0)')
                ws_calc.cell(row=c_row, column=10, value=f"=IF(Report!F{r_row},0,Report!J{r_row})")
                ws_calc.cell(row=c_row, column=11, value=f'=IF(D{c_row}>0,B{c_row}, " ")')

            for col_letter in ("D", "E", "F", "G", "H", "I", "J"):
                ws_calc[f"{col_letter}1"].value = f"=ROUND(SUM({col_letter}4:{col_letter}{last_calc_row}),0)"

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

        # Sync a uniform explicit rate into the top 5-currency rate table if all items of that currency agree
        uniform_rates: Dict[str, Optional[float]] = {}
        for rpt in self.reports:
            inv_r = rpt.data
            if getattr(inv_r, "exchange_rate", None) and float(inv_r.exchange_rate) > 0:
                code = normalize_currency(inv_r.currency)
                rate_f = float(inv_r.exchange_rate)
                if code not in uniform_rates:
                    uniform_rates[code] = rate_f
                elif uniform_rates[code] is not None and abs(uniform_rates[code] - rate_f) > 1e-4:
                    uniform_rates[code] = None

        top_table_rates: Dict[str, float] = {}
        for r, (curr, default_val) in rate_rows.items():
            resolved_rate = uniform_rates.get(curr) or default_val
            rounded_top = round(float(resolved_rate), 4)
            top_table_rates[curr] = rounded_top
            ws.cell(row=r, column=5, value=curr)  # Col E
            ws.cell(row=r, column=6, value=rounded_top)  # Col F
            ws.cell(row=r, column=7, value="臺灣銀行牌告/自訂匯率")  # Col G

        # Populate trip header metadata if exporting a trip bundle
        if self.trip_meta:
            trip_code = self.trip_meta.get("trip_code") or ""
            trip_title = self.trip_meta.get("title") or ""
            applicant = self.trip_meta.get("applicant_name") or ""
            header_parts = [p for p in [f"[{trip_code}]" if trip_code else "", trip_title] if p]
            header_label = " ".join(header_parts)
            if applicant:
                header_label = f"{header_label} — {applicant}" if header_label else applicant
            if header_label:
                cell_header = ws.cell(row=2, column=1, value=header_label)
                cell_header.font = Font(name="Calibri", size=10, bold=True)

            start_d = (self.trip_meta.get("start_date") or "").strip()
            end_d = (self.trip_meta.get("end_date") or "").strip()
            if start_d or end_d:
                period_str = f"{start_d} ~ {end_d}".strip(" ~")
                cell_period = ws.cell(row=2, column=5, value=period_str)
                cell_period.font = Font(name="Calibri", size=10)

            adv_pay = float(self.trip_meta.get("advance_payment") or 0.0)
            if adv_pay > 0:
                ws.cell(row=9, column=3, value=adv_pay)

        # Populate trip days (Report!F3) and top summary formulas (Report!C8, Report!C10)
        ws.cell(row=3, column=6, value=self._compute_travel_days())  # F3: Days Travelling
        ws.cell(row=8, column=3, value="=C5")        # C8: Bonus / Subtotal
        ws.cell(row=10, column=3, value="=C5-C9")    # C10: Net Payment

        # Extend Report, Rates, and Calc formula ranges for any batch size (including 100+ items)
        self._extend_dynamic_formulas(wb, len(self.reports))

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

            # Col J (10): Use 5-currency table formula unless per-receipt custom/dated rate differs
            explicit_rate = getattr(invoice, "exchange_rate", None)
            use_per_row_rate = (
                explicit_rate is not None
                and float(explicit_rate) > 0
                and currency_code != "NT$"
                and (
                    currency_code not in top_table_rates
                    or abs(float(explicit_rate) - top_table_rates[currency_code]) >= 1e-4
                )
            )
            if use_per_row_rate:
                j_formula = f"=ROUND(C{row_idx}*{round(float(explicit_rate), 6)},2)"
            else:
                j_formula = (
                    f'=IF(D{row_idx}>0,(IF(D{row_idx}=$E$5,C{row_idx}*$F$5,'
                    f'IF(D{row_idx}=$E$6,C{row_idx}*$F$6,IF(D{row_idx}=$E$7,C{row_idx}*$F$7,'
                    f'IF(D{row_idx}=$E$8,C{row_idx}*$F$8,IF(D{row_idx}=$E$9,C{row_idx}*$F$9,C{row_idx}))))))," ")'
                )
            ws.cell(row=row_idx, column=10, value=j_formula)

        wb.save(out)
        return out
