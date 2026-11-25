"""ERP Journal Voucher Exporter for AI-Invoice Auditor.

Converts audited expense reports into standardized corporate ERP journal entries
(會計傳票分錄) compatible with SAP, Oracle NetSuite, 鼎新, and standard accounting systems.
Ensures double-entry bookkeeping balance (Debit == Credit).
"""

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .models import AuditReport, DocumentType, ExpenseCategory
from .services.fx_service import fx_service


# Standard Corporate Chart of Accounts (會計科目對照表)
ACCOUNT_CODES: Dict[str, Tuple[str, str]] = {
    "T": ("5101", "差旅交通費 (Transportation)"),
    "H": ("5102", "差旅住宿費 (Accommodation)"),
    "F": ("5103", "業務餐飲費 (Meals & Entertainment)"),
    "S": ("5104", "交際應酬費 (Social & Gifts)"),
    "I": ("5105", "保險與簽證費 (Insurance & Visa)"),
    "E": ("5109", "其他差旅雜支 (Miscellaneous Expenses)"),
    "VAT": ("1261", "進項稅額 (Input VAT - Claimable)"),
    "PAYABLE": ("2141", "應付員工代墊款 (Payable to Employees)"),
}


class ERPExporter:
    """Generates standard ERP accounting vouchers and journal entries."""

    def __init__(
        self,
        reports: List[AuditReport],
        company_code: str = "1000",
        department_name: str = "業務研發處",
        applicant_name: str = "出差報銷同仁",
    ):
        self.reports = reports
        self.company_code = company_code
        self.department_name = department_name
        self.applicant_name = applicant_name

    def generate_entries(self) -> List[Dict]:
        """Generate structured double-entry journal lines (借貸平衡會計分錄)."""
        entries = []
        voucher_id = f"V{datetime.now().strftime('%Y%m%d')}-001"
        line_num = 1

        total_payable_ntd = 0.0

        for report in self.reports:
            inv = report.data

            # Calculate NTD amount using explicit/dated per-receipt rate or live FX fallback
            original_amount = float(inv.total_amount)
            currency = inv.currency or "NT$"
            explicit_rate = getattr(inv, "exchange_rate", None)
            if explicit_rate and float(explicit_rate) > 0:
                fx_rate = float(explicit_rate)
                converted_total_ntd = round(original_amount * fx_rate, 2)
            else:
                converted_total_ntd, fx_rate = fx_service.convert_to_ntd(
                    original_amount,
                    currency,
                    target_date=inv.invoice_date,
                )

            # Determine expense category
            from .template_exporter import infer_expense_category
            cat_code = infer_expense_category(report)
            account_code, account_name = ACCOUNT_CODES.get(
                cat_code, ACCOUNT_CODES["E"]
            )

            # Handle 5% VAT if Taiwan invoice
            tax_amount_ntd = 0.0
            net_expense_ntd = converted_total_ntd

            if inv.document_type == DocumentType.TW_INVOICE and (inv.tax_amount or 0) > 0:
                tax_amount_ntd, _ = fx_service.convert_to_ntd(float(inv.tax_amount), currency, custom_rate=fx_rate)
                net_expense_ntd = round(converted_total_ntd - tax_amount_ntd, 2)

            # 1. DEBIT: Expense Line (借：費用科目)
            desc = inv.purpose or inv.notes or f"差旅{account_name}核銷"
            vendor = inv.seller_name or "商家"
            inv_no = inv.invoice_number or "無單據號碼"
            date_val = (inv.invoice_date or datetime.now().strftime("%Y-%m-%d")).replace("/", "-")

            entries.append({
                "voucher_no": voucher_id,
                "line_no": line_num,
                "date": date_val,
                "account_code": account_code,
                "account_name": account_name,
                "debit_ntd": net_expense_ntd,
                "credit_ntd": 0.0,
                "currency": currency,
                "original_amount": round(original_amount - (float(inv.tax_amount) if tax_amount_ntd > 0 else 0), 2),
                "fx_rate": fx_rate,
                "description": f"{desc} [{vendor}]",
                "reference_invoice": inv_no,
                "seller_tax_id": inv.seller_tax_id or "",
                "department": self.department_name,
            })
            line_num += 1

            # 2. DEBIT: VAT Input Tax if applicable (借：進項稅額 1261)
            if tax_amount_ntd > 0:
                entries.append({
                    "voucher_no": voucher_id,
                    "line_no": line_num,
                    "date": date_val,
                    "account_code": ACCOUNT_CODES["VAT"][0],
                    "account_name": ACCOUNT_CODES["VAT"][1],
                    "debit_ntd": tax_amount_ntd,
                    "credit_ntd": 0.0,
                    "currency": currency,
                    "original_amount": float(inv.tax_amount),
                    "fx_rate": fx_rate,
                    "description": f"進項營業稅 5% - 發票 {inv_no}",
                    "reference_invoice": inv_no,
                    "seller_tax_id": inv.seller_tax_id or "",
                    "department": self.department_name,
                })
                line_num += 1

            total_payable_ntd += converted_total_ntd

        # 3. CREDIT: Total Payable (貸：應付員工代墊款 2141)
        # Guarantees Debit == Credit balance!
        total_payable_ntd = round(total_payable_ntd, 2)
        entries.append({
            "voucher_no": voucher_id,
            "line_no": line_num,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "account_code": ACCOUNT_CODES["PAYABLE"][0],
            "account_name": ACCOUNT_CODES["PAYABLE"][1],
            "debit_ntd": 0.0,
            "credit_ntd": total_payable_ntd,
            "currency": "NT$",
            "original_amount": total_payable_ntd,
            "fx_rate": 1.0,
            "description": f"整批出差報銷款應付撥款 - {self.applicant_name} ({len(self.reports)} 筆憑證)",
            "reference_invoice": voucher_id,
            "seller_tax_id": "",
            "department": self.department_name,
        })

        return entries

    def to_csv(self, output_path: Union[str, Path]) -> Path:
        """Export to Excel-friendly UTF-8-BOM CSV for ERP import (SAP / NetSuite / 鼎新)."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        entries = self.generate_entries()
        fieldnames = [
            "傳票編號 (Voucher_No)",
            "分錄序號 (Line)",
            "記帳日期 (Date)",
            "科目代碼 (Account_Code)",
            "會計科目名稱 (Account_Name)",
            "借方金額 (Debit_NTD)",
            "貸方金額 (Credit_NTD)",
            "原始幣別 (Currency)",
            "原幣金額 (Original_Amount)",
            "換算匯率 (FX_Rate)",
            "摘要說明 (Description)",
            "單據憑證號碼 (Invoice_No)",
            "統一編號 (Tax_ID)",
            "歸屬部門 (Department)",
        ]

        # Use utf-8-sig so Excel opens it with proper Traditional Chinese encoding without garbled characters
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(fieldnames)

            for e in entries:
                writer.writerow([
                    e["voucher_no"],
                    e["line_no"],
                    e["date"],
                    e["account_code"],
                    e["account_name"],
                    f"{e['debit_ntd']:,.2f}" if e["debit_ntd"] > 0 else "0.00",
                    f"{e['credit_ntd']:,.2f}" if e["credit_ntd"] > 0 else "0.00",
                    e["currency"],
                    f"{e['original_amount']:,.2f}",
                    f"{e['fx_rate']:.4f}",
                    e["description"],
                    e["reference_invoice"],
                    e["seller_tax_id"],
                    e["department"],
                ])

        return path

    def to_json(self, output_path: Union[str, Path]) -> Path:
        """Export to structured JSON format for ERP REST API webhooks."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        entries = self.generate_entries()
        total_debit = sum(e["debit_ntd"] for e in entries)
        total_credit = sum(e["credit_ntd"] for e in entries)

        payload = {
            "erp_voucher": {
                "voucher_no": entries[0]["voucher_no"] if entries else "",
                "created_at": datetime.now().isoformat(),
                "company_code": self.company_code,
                "total_debit": round(total_debit, 2),
                "total_credit": round(total_credit, 2),
                "is_balanced": abs(total_debit - total_credit) < 0.01,
                "lines_count": len(entries),
                "journal_lines": entries,
            }
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return path
