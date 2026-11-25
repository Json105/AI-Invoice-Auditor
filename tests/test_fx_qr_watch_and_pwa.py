"""Tests for Per-Receipt FX Control (BOT Date Lookup & Manual), Cross-Border-Safe TW e-Invoice QR,
MFP Watch Folder Auto-Ingestion, and PWA + Minimalist Collapsible UI.
"""

import io
import json
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import openpyxl
from PIL import Image
from reportlab.pdfgen import canvas
from starlette.testclient import TestClient

from src.database import DEFAULT_DB_PATH, AuditRecordDB, SessionLocal, User, init_db, save_audit_reports
from src.erp_exporter import ERPExporter
from src.models import AuditReport, AuditStatus, DocumentType, InvoiceData
from src.services.fx_service import FXService, fx_service
from src.services.tw_einvoice_qr import (
    classify_qr_payload,
    parse_taiwan_einvoice_qr_texts,
    try_decode_taiwan_einvoice_from_image,
)
from src.services.watch_folder import watch_folder_service
from src.template_exporter import TemplateExpenseExporter
from src.web.app import app

client = TestClient(app)


def _login_headers(email: str, password: str) -> dict:
    init_db(DEFAULT_DB_PATH)
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _make_qr_png(payload: str, out_path: Path) -> Path:
    qrencode_bin = shutil.which("qrencode")
    assert qrencode_bin, "qrencode binary must be installed for QR tests"
    subprocess.run([qrencode_bin, "-s", "6", "-m", "4", "-o", str(out_path), payload], check=True)
    return out_path


def _make_multipage_pdf_bytes(num_pages: int = 2) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for page_idx in range(1, num_pages + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 720, f"Scanner Watch PDF Page {page_idx}")
        c.drawString(72, 695, f"Amount: TWD {page_idx * 250}")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_bot_historical_rate_lookup_and_sea_cash_fallback(tmp_path):
    """Test BOT date lookup (weekend rollback + SEA cash_sell fallback), PATCH FX API, and exporters."""
    service = FXService(cache_ttl_seconds=3600)
    service.historical_cache_file = tmp_path / "fx_hist_test.json"
    service._historical_cache = {}

    def _fake_urlopen(req, timeout=3):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        mock_resp = MagicMock()
        if "data_id=USD" in url:
            body = {
                "msg": "success",
                "status": 200,
                "data": [
                    {"date": "2026-10-01", "currency": "USD", "cash_buy": 31.455, "cash_sell": 32.125, "spot_buy": 31.805, "spot_sell": 31.905},
                    {"date": "2026-10-02", "currency": "USD", "cash_buy": 31.445, "cash_sell": 32.115, "spot_buy": 31.795, "spot_sell": 31.895},
                ],
            }
        elif "data_id=MYR" in url:
            body = {
                "msg": "success",
                "status": 200,
                "data": [
                    {"date": "2026-10-05", "currency": "MYR", "cash_buy": 6.746, "cash_sell": 8.271, "spot_buy": 0.0, "spot_sell": 0.0},
                ],
            }
        else:
            body = {"data": []}
        mock_resp.read.return_value = json.dumps(body).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False
        return mock_resp

    with patch("src.services.fx_service.urllib.request.urlopen", side_effect=_fake_urlopen):
        usd_quote = service.get_rate_for_date("USD", "2026-10-04", rate_type="spot_sell")
        assert usd_quote["rate"] == 31.895
        assert usd_quote["rate_date"] == "2026-10-02"
        assert usd_quote["rate_type"] == "spot_sell"
        assert "即期賣出" in usd_quote["source"]

        # SEA currency MYR has spot_sell == 0 -> must automatically fall back to cash_sell
        myr_quote = service.get_rate_for_date("MYR", "2026-10-05", rate_type="spot_sell")
        assert myr_quote["rate"] == 8.271
        assert myr_quote["rate_type"] == "cash_sell"
        assert "現金賣出" in myr_quote["source"]

    # Test /api/rates/lookup HTTP endpoint
    with patch.object(fx_service, "get_rate_for_date", return_value={
        "currency": "USD",
        "iso_currency": "USD",
        "requested_date": "2026-10-04",
        "rate_date": "2026-10-02",
        "rate": 31.895,
        "rate_type": "spot_sell",
        "source": "臺灣銀行牌告匯率 (即期賣出 2026-10-02)",
        "is_live": True,
    }):
        res_lookup = client.get("/api/rates/lookup?currency=USD&date=2026-10-04")
        assert res_lookup.status_code == 200
        assert res_lookup.json()["rate"] == 31.895

    # End-to-end DB + Trip + RBAC + Exporters
    emp_headers = _login_headers("employee@company.local", "User1234!")
    res_trip = client.post("/api/trips", headers=emp_headers, json={"title": "跨國匯率調整測試專案"})
    assert res_trip.status_code == 200
    trip_id = res_trip.json()["trip"]["id"]

    with SessionLocal() as db:
        emp_uid = db.query(User).filter(User.email == "employee@company.local").first().id
        admin_uid = db.query(User).filter(User.email == "admin@company.local").first().id

    inv_code_emp = f"FX-EMP-{uuid.uuid4().hex[:6].upper()}"
    inv_code_adm = f"FX-ADM-{uuid.uuid4().hex[:6].upper()}"
    save_audit_reports(
        [AuditReport(
            file_path="emp_hotel.json",
            status=AuditStatus.APPROVED,
            audit_timestamp=datetime.now().isoformat(),
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number=inv_code_emp,
                invoice_date="2026-10-04",
                currency="USD",
                total_amount=200.0,
                seller_name="US Airport Hotel",
            ),
        )],
        applicant_name="林專員",
        applicant_email="employee@company.local",
        user_id=emp_uid,
        trip_id=trip_id,
    )
    save_audit_reports(
        [AuditReport(
            file_path="adm_hotel.json",
            status=AuditStatus.APPROVED,
            audit_timestamp=datetime.now().isoformat(),
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number=inv_code_adm,
                invoice_date="2026-10-04",
                currency="USD",
                total_amount=100.0,
                seller_name="Admin Hotel",
            ),
        )],
        applicant_name="系統管理員",
        applicant_email="admin@company.local",
        user_id=admin_uid,
    )

    with SessionLocal() as db:
        emp_rec_id = db.query(AuditRecordDB).filter(AuditRecordDB.invoice_number == inv_code_emp).first().id
        adm_rec_id = db.query(AuditRecordDB).filter(AuditRecordDB.invoice_number == inv_code_adm).first().id

    # RBAC check: employee cannot edit admin's record
    res_403 = client.patch(
        f"/api/records/{adm_rec_id}/exchange-rate",
        headers=emp_headers,
        json={"mode": "manual", "exchange_rate": 32.0},
    )
    assert res_403.status_code == 403

    # Manual FX override on employee's own record -> updates NTD total inside trip
    res_manual = client.patch(
        f"/api/records/{emp_rec_id}/exchange-rate",
        headers=emp_headers,
        json={"mode": "manual", "exchange_rate": 32.5},
    )
    assert res_manual.status_code == 200
    assert res_manual.json()["record"]["exchange_rate"] == 32.5
    assert res_manual.json()["record"]["ntd_amount"] == 6500.0

    trip_detail = client.get(f"/api/trips/{trip_id}", headers=emp_headers).json()
    assert trip_detail["estimated_total_ntd"] == 6500.0

    # BOT date lookup mode on employee's own record
    with patch.object(fx_service, "get_rate_for_date", return_value={
        "rate": 31.895,
        "rate_date": "2026-10-02",
        "source": "臺灣銀行牌告匯率 (即期賣出 2026-10-02)",
    }):
        res_bot = client.patch(
            f"/api/records/{emp_rec_id}/exchange-rate",
            headers=emp_headers,
            json={"mode": "bot_date", "rate_date": "2026-10-04", "rate_type": "spot_sell"},
        )
        assert res_bot.status_code == 200
        assert res_bot.json()["record"]["exchange_rate"] == 31.895
        assert res_bot.json()["record"]["ntd_amount"] == 6379.0

    # Verify ERPExporter & TemplateExpenseExporter respect explicit per-receipt FX rates (including MYR)
    export_reports = [
        AuditReport(
            file_path="myr.json",
            status=AuditStatus.APPROVED,
            audit_timestamp=datetime.now().isoformat(),
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="MYR-001",
                invoice_date="2026-10-05",
                currency="MYR",
                total_amount=100.0,
                exchange_rate=8.271,
                exchange_rate_source="臺灣銀行牌告匯率 (現金賣出 2026-10-05)",
                ntd_amount=827.1,
                seller_name="KL Grab",
            ),
        ),
        AuditReport(
            file_path="usd.json",
            status=AuditStatus.APPROVED,
            audit_timestamp=datetime.now().isoformat(),
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number="USD-002",
                invoice_date="2026-10-02",
                currency="USD",
                total_amount=200.0,
                exchange_rate=31.895,
                exchange_rate_source="臺灣銀行牌告匯率 (即期賣出 2026-10-02)",
                ntd_amount=6379.0,
                seller_name="US Hotel",
            ),
        ),
    ]
    erp = ERPExporter(export_reports)
    entries = erp.generate_entries()
    assert entries[0]["fx_rate"] == 8.271
    assert entries[0]["debit_ntd"] == 827.1
    assert entries[1]["fx_rate"] == 31.895
    assert entries[1]["debit_ntd"] == 6379.0
    assert entries[-1]["credit_ntd"] == 7206.1

    template_path = Path("Template.xlsx")
    if template_path.exists():
        tpl_out = tmp_path / "custom_fx_template.xlsx"
        TemplateExpenseExporter(export_reports, template_path=template_path).export(tpl_out)
        wb = openpyxl.load_workbook(tpl_out, data_only=False)
        ws = wb["Report"]
        # MYR is outside the 5 template columns -> must generate explicit per-row NTD formula
        assert ws["J13"].value == "=ROUND(C13*8.271,2)"


def _build_valid_tw_left_qr(inv_num: str = "QR88776655", roc_date: str = "1151110", sales: int = 333, total: int = 350) -> str:
    # 10 (inv) + 7 (date) + 4 (rand) + 8 (sales hex) + 8 (total hex) + 8 (buyer) + 8 (seller) + 24 (aes) = 77 chars
    return (
        f"{inv_num}"
        f"{roc_date}"
        f"1234"
        f"{sales:08x}"
        f"{total:08x}"
        f"00000000"
        f"12345675"
        f"ABCDEFGHIJKLMNOPQRSTUVWX"
        f":1:1:1:商務餐盒:1:{total}"
    )


def test_tw_einvoice_qr_decode_and_cross_border_safety(tmp_path):
    """Taiwan e-Invoice QRs fast-decode offline; China/SEA/EU QRs NEVER false-match and route to AI."""
    tw_left = _build_valid_tw_left_qr()
    tw_right = "**:飲料:1:50"
    assert classify_qr_payload(tw_left) == "tw_einvoice_left"
    assert classify_qr_payload(tw_right) == "tw_einvoice_right"

    cn_payload = "01,04,3100231130,88210345,368.00,20260818,12345678901234567890,ABCD"
    sea_payload = "00020101021129370016A000000677010111011300668123456785802TH53037646304ABCD"
    eu_url = "https://receipt.eu.example/inv/42"

    assert classify_qr_payload(cn_payload) == "cn_fapiao"
    assert classify_qr_payload(sea_payload) == "sea_or_global_payment"
    assert classify_qr_payload(eu_url) == "url"
    assert parse_taiwan_einvoice_qr_texts([cn_payload]) is None
    assert parse_taiwan_einvoice_qr_texts([sea_payload]) is None
    assert parse_taiwan_einvoice_qr_texts([eu_url]) is None

    # Generate combined Left + Right TW e-Invoice QR image via qrencode + PIL
    left_png = _make_qr_png(tw_left, tmp_path / "tw_left.png")
    right_png = _make_qr_png(tw_right, tmp_path / "tw_right.png")
    im_l = Image.open(left_png).convert("RGB")
    im_r = Image.open(right_png).convert("RGB")
    tw_combined = tmp_path / "tw_einvoice_dual.png"
    canvas_img = Image.new("RGB", (im_l.width + im_r.width + 24, max(im_l.height, im_r.height) + 16), "white")
    canvas_img.paste(im_l, (8, 8))
    canvas_img.paste(im_r, (im_l.width + 16, 8))
    canvas_img.save(tw_combined)

    decoded = try_decode_taiwan_einvoice_from_image(tw_combined)
    assert decoded is not None
    assert decoded.invoice_number == "QR-88776655"
    assert decoded.invoice_date == "2026-11-10"
    assert decoded.seller_tax_id == "12345675"
    assert decoded.total_amount == 350.0
    assert decoded.subtotal == 333.0
    assert decoded.tax_amount == 17.0

    # Upload to POST /api/audit with multi_receipt=false -> must fast-decode TW QR without invoking AI
    emp_headers = _login_headers("employee@company.local", "User1234!")
    with patch("src.extractors.mock_extractor.MockExtractor.extract_many", side_effect=AssertionError("AI extractor must be skipped for valid TW e-Invoice QR")):
        res_tw = client.post(
            "/api/audit",
            headers=emp_headers,
            data={"engine": "mock", "apply_template": "false", "multi_receipt": "false"},
            files=[("files", ("tw_einvoice.png", tw_combined.read_bytes(), "image/png"))],
        )
    assert res_tw.status_code == 200
    body_tw = res_tw.json()
    assert body_tw["qr_fast_decoded_count"] == 1
    assert body_tw["total"] == 1
    assert body_tw["reports"][0]["data"]["invoice_number"] == "QR-88776655"

    # Same TW QR image uploaded with multi_receipt=true MUST NOT short-circuit
    with patch("src.extractors.mock_extractor.MockExtractor.extract_many", return_value=[
        InvoiceData(document_type=DocumentType.GENERAL_RECEIPT, invoice_number="MULTI-A", total_amount=100.0),
        InvoiceData(document_type=DocumentType.GENERAL_RECEIPT, invoice_number="MULTI-B", total_amount=200.0),
    ]):
        res_multi = client.post(
            "/api/audit",
            headers=emp_headers,
            data={"engine": "mock", "apply_template": "false", "multi_receipt": "true"},
            files=[("files", ("tw_einvoice_multi.png", tw_combined.read_bytes(), "image/png"))],
        )
    assert res_multi.status_code == 200
    assert res_multi.json()["qr_fast_decoded_count"] == 0
    assert res_multi.json()["total"] == 2

    # China Fapiao & SEA PromptPay QRs must safely fall through to AI (qr_fast_decoded_count == 0)
    cn_png = _make_qr_png(cn_payload, tmp_path / "cn_fapiao.png")
    sea_png = _make_qr_png(sea_payload, tmp_path / "sea_promptpay.png")
    assert try_decode_taiwan_einvoice_from_image(cn_png) is None
    assert try_decode_taiwan_einvoice_from_image(sea_png) is None

    res_overseas = client.post(
        "/api/audit",
        headers=emp_headers,
        data={"engine": "mock", "apply_template": "false", "multi_receipt": "false"},
        files=[
            ("files", ("sample_china_vat_invoice.png", cn_png.read_bytes(), "image/png")),
            ("files", ("sample_thailand_grab.png", sea_png.read_bytes(), "image/png")),
        ],
    )
    assert res_overseas.status_code == 200
    assert res_overseas.json()["qr_fast_decoded_count"] == 0
    assert res_overseas.json()["total"] == 2


def test_watch_folder_scan_and_trip_binding(tmp_path):
    """Test MFP Watch Folder monitoring status, PDF split + TW QR decode, trip subfolder binding, and archiving."""
    emp_headers = _login_headers("employee@company.local", "User1234!")
    res_trip = client.post("/api/trips", headers=emp_headers, json={"title": "事務機監控匯入專案"})
    assert res_trip.status_code == 200
    trip = res_trip.json()["trip"]
    trip_id = trip["id"]
    trip_code = trip["trip_code"]

    inbox = tmp_path / "watch_inbox"
    trip_subdir = inbox / trip_code
    trip_subdir.mkdir(parents=True, exist_ok=True)

    # Drop a 2-page PDF inside the TRIP subfolder + 1 unique TW QR PNG in inbox root
    (trip_subdir / "batch_scan.pdf").write_bytes(_make_multipage_pdf_bytes(num_pages=2))
    unique_8 = f"{int(uuid.uuid4().hex[:7], 16) % 90000000 + 10000000}"
    unique_qr_payload = _build_valid_tw_left_qr(inv_num=f"WT{unique_8}", total=420, sales=400)
    _make_qr_png(unique_qr_payload, inbox / "tw_watch_qr.png")

    orig_dir = watch_folder_service.watch_dir
    watch_folder_service.watch_dir = inbox
    try:
        res_status = client.get("/api/watch-folder/status", headers=emp_headers)
        assert res_status.status_code == 200
        assert res_status.json()["pending_count"] == 2

        res_scan = client.post(
            "/api/watch-folder/scan",
            headers=emp_headers,
            json={"engine": "mock", "split_pdf": True},
        )
        assert res_scan.status_code == 200
        scan_data = res_scan.json()
        assert scan_data["scanned_files_count"] == 2
        assert scan_data["expanded_pages_count"] == 3  # 2 PDF pages + 1 QR PNG
        assert scan_data["qr_fast_decoded_count"] == 1
        assert scan_data["total"] == 3

        # Originals must be archived under processed/ so second scan processes 0
        assert not (trip_subdir / "batch_scan.pdf").exists()
        assert not (inbox / "tw_watch_qr.png").exists()
        processed_files = list((inbox / "processed").iterdir())
        assert len(processed_files) == 2

        res_status_after = client.get("/api/watch-folder/status", headers=emp_headers)
        assert res_status_after.json()["pending_count"] == 0

        res_trip_after = client.get(f"/api/trips/{trip_id}", headers=emp_headers)
        assert res_trip_after.status_code == 200
        assert res_trip_after.json()["receipt_count"] == 2
    finally:
        watch_folder_service.watch_dir = orig_dir


def test_pwa_manifest_sw_and_minimalist_collapsible_ui():
    """Verify PWA endpoints/assets and minimalist UI controls (collapsed filters & FX modal)."""
    res_manifest = client.get("/static/manifest.json")
    assert res_manifest.status_code == 200
    manifest = res_manifest.json()
    assert manifest["display"] == "standalone"
    assert any(icon["src"] == "/static/icon-192.png" for icon in manifest["icons"])

    res_sw = client.get("/sw.js")
    assert res_sw.status_code == 200
    assert res_sw.headers.get("Service-Worker-Allowed") == "/"
    assert "/api/" in res_sw.text

    for icon_path in ("/static/icon.svg", "/static/icon-192.png", "/static/icon-512.png"):
        assert client.get(icon_path).status_code == 200

    res_html = client.get("/")
    assert res_html.status_code == 200
    html = res_html.text
    for marker in (
        'rel="manifest"',
        'id="fx-edit-modal"',
        'id="fx-mode-select"',
        'id="manual-fx-rate-input"',
        'id="history-filter-panel"',
        'id="trips-filter-panel"',
        'id="approval-filter-panel"',
        "collapsible-filter-panel",
        "guide-drawer",
        'id="btn-watch-scan"',
    ):
        assert marker in html, f"Missing expected minimalist/PWA/FX marker: {marker}"
