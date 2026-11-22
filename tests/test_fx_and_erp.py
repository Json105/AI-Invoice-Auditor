"""Integration and unit tests for FX Service and ERP Exporter (面向五)."""

import csv
import io
import json
from datetime import datetime
from pathlib import Path
import pytest
from starlette.testclient import TestClient
import openpyxl

from src.models import InvoiceData, AuditReport, AuditStatus, DocumentType
from src.services.fx_service import fx_service, FXService
from src.erp_exporter import ERPExporter
from src.template_exporter import TemplateExpenseExporter
from src.database import init_db, DEFAULT_DB_PATH
from src.web.app import app

client = TestClient(app)


def test_fx_service_rates_and_conversion():
    """1. Test FX Service rates retrieval and accurate currency conversion."""
    rates_data = fx_service.get_live_rates()
    assert isinstance(rates_data, dict)
    assert "rates" in rates_data
    assert "source" in rates_data
    assert "updated_at" in rates_data

    rates = rates_data["rates"]
    assert rates["USD"] > 25.0
    assert rates["EUR"] > 30.0
    assert rates["RMB"] > 3.5
    assert rates["THB"] > 0.5
    assert rates["NT$"] == 1.0

    # Test conversion
    converted_usd, rate_usd = fx_service.convert_to_ntd(100.0, "USD")
    assert converted_usd == round(100.0 * rates["USD"], 2)
    assert rate_usd == rates["USD"]

    converted_ntd, rate_ntd = fx_service.convert_to_ntd(500.0, "NT$")
    assert converted_ntd == 500.0
    assert rate_ntd == 1.0


def test_erp_exporter_double_entry_balance(tmp_path):
    """2. Test ERP Exporter debit/credit balancing and CSV/JSON output."""
    reports = [
        AuditReport(
            file_path="mock_inv_1.json",
            status=AuditStatus.APPROVED,
            audit_timestamp=datetime.now().isoformat(),
            data=InvoiceData(
                document_type=DocumentType.TW_INVOICE,
                invoice_number="AB-12345678",
                invoice_date="2026-08-20",
                seller_tax_id="84521098",
                seller_name="台灣高鐵公司",
                currency="NT$",
                subtotal=1490.0,
                tax_amount=75.0,
                total_amount=1565.0,
                purpose="商務拜訪高鐵交通",
            )
        ),
        AuditReport(
            file_path="mock_inv_2.json",
            status=AuditStatus.APPROVED,
            audit_timestamp=datetime.now().isoformat(),
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="HTL-88219",
                invoice_date="2026-08-22",
                seller_name="Hilton Hotel",
                currency="USD",
                total_amount=200.0,
                purpose="出差商務住宿",
            )
        )
    ]

    exporter = ERPExporter(reports, company_code="TW10", department_name="海外業務處", applicant_name="王大明")
    entries = exporter.generate_entries()
    assert len(entries) >= 3

    # Check Double-Entry Balance (借貸平衡)
    total_debit = sum(e["debit_ntd"] for e in entries)
    total_credit = sum(e["credit_ntd"] for e in entries)
    assert abs(total_debit - total_credit) < 0.01, f"Unbalanced: Debit={total_debit}, Credit={total_credit}"

    # Verify CSV Export
    csv_path = tmp_path / "test_erp.csv"
    exporter.to_csv(csv_path)
    assert csv_path.exists()
    assert csv_path.stat().st_size > 200

    # Verify UTF-8-BOM encoding
    content_bytes = csv_path.read_bytes()
    assert content_bytes.startswith(b"\xef\xbb\xbf"), "Missing UTF-8 BOM"

    # Verify JSON Export
    json_path = tmp_path / "test_erp.json"
    exporter.to_json(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    assert json_data["erp_voucher"]["is_balanced"] is True


def test_template_exporter_injects_live_rates(tmp_path):
    """3. Test that TemplateExpenseExporter injects live FX rates into Report sheet."""
    template_path = Path("Template.xlsx")
    if not template_path.exists():
        pytest.skip("Template.xlsx not present")

    reports = [
        AuditReport(
            file_path="sample.json",
            status=AuditStatus.APPROVED,
            audit_timestamp=datetime.now().isoformat(),
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="EUR-9912",
                currency="EUR",
                total_amount=150.0,
                purpose="歐洲出差住宿",
            )
        )
    ]

    out_file = tmp_path / "filled_template_with_fx.xlsx"
    exporter = TemplateExpenseExporter(reports, template_path=template_path)
    exporter.export(out_file)

    wb = openpyxl.load_workbook(out_file, data_only=False)
    ws = wb["Report"]

    # Rows 5 to 9 should contain live rates
    eur_rate = ws["F5"].value
    usd_rate = ws["F8"].value
    assert isinstance(eur_rate, (int, float)) and eur_rate > 30.0
    assert isinstance(usd_rate, (int, float)) and usd_rate > 25.0


def test_web_api_rates_and_erp_download():
    """4. Test /api/rates and /api/download/{session_id}/erp Web endpoints."""
    # Test /api/rates
    res_rates = client.get("/api/rates")
    assert res_rates.status_code == 200
    rates_json = res_rates.json()
    assert "rates" in rates_json
    assert rates_json["rates"].get("USD") > 0

    # Authenticate before calling protected audit & download endpoints
    init_db(DEFAULT_DB_PATH)
    res_login = client.post(
        "/api/auth/login",
        json={"email": "admin@company.local", "password": "Admin1234!"},
    )
    assert res_login.status_code == 200
    headers = {"Authorization": f"Bearer {res_login.json()['access_token']}"}

    # Test audit endpoint returns erp download url
    sample_file = Path("sample_data/sample_china_vat_invoice.json")
    with open(sample_file, "rb") as f:
        res_audit = client.post(
            "/api/audit",
            data={"engine": "mock", "apply_template": "false"},
            files=[("files", (sample_file.name, f, "application/json"))],
            headers=headers,
        )
    assert res_audit.status_code == 200
    data = res_audit.json()
    dl_urls = data.get("download_urls", {})
    assert "erp" in dl_urls

    # Test downloading ERP CSV
    res_erp = client.get(dl_urls["erp"], headers=headers)
    assert res_erp.status_code == 200
    assert "傳票編號" in res_erp.text or "Voucher_No" in res_erp.text
    assert len(res_erp.content) > 100
