"""Export engine generating enterprise ERP expense reports and JSON audit trails."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Union
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .models import AuditReport, AuditStatus


class ExpenseExporter:
    """Exports audited invoice and expense data to Excel and JSON."""

    def __init__(self, reports: List[AuditReport]):
        self.reports = reports

    def to_json(self, output_path: Union[str, Path]) -> Path:
        """Export full audit trail to formatted JSON."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = [report.model_dump(mode="json") for report in self.reports]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return path

    def to_excel(self, output_path: Union[str, Path]) -> Path:
        """Generate high-aesthetic, corporate ERP-ready Excel expense report."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "差旅與發票報銷清冊"
        ws.views.sheetView[0].showGridLines = True

        # Styles
        title_font = Font(name="微軟正黑體", size=16, bold=True, color="1F4E79")
        header_font = Font(name="微軟正黑體", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")

        data_font = Font(name="微軟正黑體", size=10)
        bold_font = Font(name="微軟正黑體", size=10, bold=True)
        thin_border = Border(
            left=Side(style="thin", color="D9D9D9"),
            right=Side(style="thin", color="D9D9D9"),
            top=Side(style="thin", color="D9D9D9"),
            bottom=Side(style="thin", color="D9D9D9"),
        )

        # Status Fills
        fill_approved = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid") # Soft green
        fill_warning = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")  # Soft yellow
        fill_rejected = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid") # Soft red

        font_approved = Font(name="微軟正黑體", size=10, bold=True, color="385723")
        font_warning = Font(name="微軟正黑體", size=10, bold=True, color="833C0C")
        font_rejected = Font(name="微軟正黑體", size=10, bold=True, color="C00000")

        # 1. Report Title Header
        ws.merge_cells("A1:N1")
        title_cell = ws["A1"]
        title_cell.value = "🏢 企業智慧財務報銷與合規稽核清冊 (AI-Invoice-Auditor)"
        title_cell.font = title_font
        title_cell.alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[1].height = 40

        # Subtitle info
        ws["A2"] = f"產出時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  總筆數：{len(self.reports)} 筆"
        ws["A2"].font = Font(name="微軟正黑體", size=9, italic=True, color="595959")
        ws.row_dimensions[2].height = 20

        # 2. Table Headers
        headers = [
            "審核判定", "單據類型", "發票/票據號碼", "單據日期",
            "買方統編", "賣方/開立單位", "差旅起訖站", "車次/車號",
            "未稅金額", "營業稅額", "報銷總額", "幣別",
            "合規稽核說明 / 異常標記", "原始檔案"
        ]

        ws.row_dimensions[4].height = 26
        for col_idx, header_text in enumerate(headers, 1):
            cell = ws.cell(row=4, column=col_idx, value=header_text)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        # 3. Data Rows
        doc_type_labels = {
            "tw_invoice": "統一發票",
            "thsr_ticket": "台灣高鐵",
            "taxi_receipt": "計程車",
            "commercial_invoice": "商業發票",
            "general_receipt": "通用收據"
        }

        current_row = 5
        total_subtotal = 0.0
        total_tax = 0.0
        total_gross = 0.0

        for rep in self.reports:
            inv = rep.data
            ws.row_dimensions[current_row].height = 22

            # Status cell
            status_cell = ws.cell(row=current_row, column=1, value=rep.status.value)
            status_cell.alignment = Alignment(horizontal="center", vertical="center")
            if rep.status == AuditStatus.APPROVED:
                status_cell.fill = fill_approved
                status_cell.font = font_approved
            elif rep.status == AuditStatus.WARNING:
                status_cell.fill = fill_warning
                status_cell.font = font_warning
            else:
                status_cell.fill = fill_rejected
                status_cell.font = font_rejected

            # Type
            type_label = doc_type_labels.get(inv.document_type.value, inv.document_type.value)
            c2 = ws.cell(row=current_row, column=2, value=type_label)
            c2.alignment = Alignment(horizontal="center", vertical="center")

            # Invoice No
            c3 = ws.cell(row=current_row, column=3, value=inv.invoice_number or "-")
            c3.alignment = Alignment(horizontal="center", vertical="center")

            # Date
            c4 = ws.cell(row=current_row, column=4, value=inv.invoice_date or "-")
            c4.alignment = Alignment(horizontal="center", vertical="center")

            # Buyer Tax ID
            c5 = ws.cell(row=current_row, column=5, value=inv.buyer_tax_id or "-")
            c5.alignment = Alignment(horizontal="center", vertical="center")

            # Seller Name
            c6 = ws.cell(row=current_row, column=6, value=inv.seller_name or "-")
            c6.alignment = Alignment(horizontal="left", vertical="center")

            # Travel route
            route = "-"
            vehicle_info = "-"
            if inv.travel_details:
                td = inv.travel_details
                if td.departure and td.destination:
                    route = f"{td.departure} ➔ {td.destination}"
                vehicle_info = td.train_or_flight_no or td.vehicle_number or "-"

            c7 = ws.cell(row=current_row, column=7, value=route)
            c7.alignment = Alignment(horizontal="center", vertical="center")

            c8 = ws.cell(row=current_row, column=8, value=vehicle_info)
            c8.alignment = Alignment(horizontal="center", vertical="center")

            # Amounts
            subtotal_val = inv.subtotal if inv.subtotal is not None else (inv.total_amount - (inv.tax_amount or 0))
            tax_val = inv.tax_amount if inv.tax_amount is not None else 0.0
            gross_val = inv.total_amount

            total_subtotal += subtotal_val
            total_tax += tax_val
            total_gross += gross_val

            c9 = ws.cell(row=current_row, column=9, value=subtotal_val)
            c9.number_format = "#,##0"
            c9.alignment = Alignment(horizontal="right", vertical="center")

            c10 = ws.cell(row=current_row, column=10, value=tax_val)
            c10.number_format = "#,##0"
            c10.alignment = Alignment(horizontal="right", vertical="center")

            c11 = ws.cell(row=current_row, column=11, value=gross_val)
            c11.number_format = "#,##0"
            c11.font = bold_font
            c11.alignment = Alignment(horizontal="right", vertical="center")

            # Currency
            c12 = ws.cell(row=current_row, column=12, value=inv.currency)
            c12.alignment = Alignment(horizontal="center", vertical="center")

            # Audit issues description
            if rep.issues:
                issue_summary = "；".join([f"[{i.level}] {i.message}" for i in rep.issues])
            else:
                issue_summary = "✅ 檢核通過無異常"
            c13 = ws.cell(row=current_row, column=13, value=issue_summary)
            c13.alignment = Alignment(horizontal="left", vertical="center")

            # Source file
            c14 = ws.cell(row=current_row, column=14, value=Path(rep.file_path).name if rep.file_path else "-")
            c14.alignment = Alignment(horizontal="left", vertical="center")

            # Apply borders and fonts to row
            for col_idx in range(1, 15):
                cell = ws.cell(row=current_row, column=col_idx)
                cell.border = thin_border
                if col_idx != 1 and col_idx != 11:
                    cell.font = data_font

            current_row += 1

        # 4. Summary Totals Row
        ws.row_dimensions[current_row].height = 25
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=8)
        sum_label = ws.cell(row=current_row, column=1, value="合計 (Total Summary)")
        sum_label.font = bold_font
        sum_label.alignment = Alignment(horizontal="center", vertical="center")

        sum_subtotal = ws.cell(row=current_row, column=9, value=total_subtotal)
        sum_subtotal.number_format = "#,##0"
        sum_subtotal.font = bold_font

        sum_tax = ws.cell(row=current_row, column=10, value=total_tax)
        sum_tax.number_format = "#,##0"
        sum_tax.font = bold_font

        sum_gross = ws.cell(row=current_row, column=11, value=total_gross)
        sum_gross.number_format = "#,##0"
        sum_gross.font = Font(name="微軟正黑體", size=11, bold=True, color="1F4E79")

        for col_idx in range(1, 15):
            c = ws.cell(row=current_row, column=col_idx)
            c.border = thin_border
            c.fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            # Add reasonable padding
            ws.column_dimensions[col_letter].width = max(max_len * 1.5, 12)

        ws.column_dimensions["M"].width = 45 # Issues column
        ws.column_dimensions["F"].width = 24 # Seller name

        wb.save(path)
        return path
