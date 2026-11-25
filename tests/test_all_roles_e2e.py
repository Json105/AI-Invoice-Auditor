"""Comprehensive multi-role E2E regression tests covering Unauthenticated,
Employee, Finance (accountant), Admin + SQLAdmin views, and Frontend HTML/JS integrity.
"""

import io
import re
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen import canvas
from starlette.testclient import TestClient

from src.database import DEFAULT_DB_PATH, SessionLocal, User, init_db
from src.services.auth_service import hash_password
from src.services.fx_service import fx_service
from src.services.watch_folder import watch_folder_service
from src.web.app import app

client = TestClient(app)


def _login(email: str, password: str):
    init_db(DEFAULT_DB_PATH)
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _make_multipage_pdf_bytes(num_pages: int = 2) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for page_idx in range(1, num_pages + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 720, f"E2E Receipt Page {page_idx}")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_unauthenticated_api_and_admin_boundaries():
    """All 21 protected API routes must return 401 and /admin list views must redirect when logged out."""
    unauth_endpoints = [
        ("GET", "/api/auth/me"),
        ("GET", "/api/auth/users"),
        ("POST", "/api/auth/change-password"),
        ("GET", "/api/stats"),
        ("GET", "/api/history"),
        ("GET", "/api/history/export"),
        ("POST", "/api/audit"),
        ("GET", "/api/trips"),
        ("POST", "/api/trips"),
        ("GET", "/api/approval/summary"),
        ("GET", "/api/approval/list"),
        ("POST", "/api/approval/1/review"),
        ("POST", "/api/approval/1/mark-paid"),
        ("POST", "/api/approval/1/resubmit"),
        ("GET", "/api/storage/stats"),
        ("POST", "/api/storage/cleanup"),
        ("POST", "/api/storage/presigned-url"),
        ("GET", "/api/watch-folder/status"),
        ("POST", "/api/watch-folder/scan"),
        ("PATCH", "/api/records/1/exchange-rate"),
        ("GET", "/api/records/1/file"),
    ]
    for method, path in unauth_endpoints:
        res = client.request(method, path)
        assert res.status_code == 401, f"Expected 401 for {method} {path}, got {res.status_code}"

    res_admin = client.get("/admin/user/list", follow_redirects=False)
    assert res_admin.status_code in (302, 303, 307)
    assert "/admin/login" in res_admin.headers.get("Location", "")


def test_all_roles_full_workflow_and_sqladmin_regression(tmp_path):
    """Verify Employee, Finance (accountant), and Admin full workflows, RBAC, and SQLAdmin list pages."""
    emp_res = _login("employee@company.local", "User1234!")
    fin_res = _login("finance@company.local", "Finance1234!")
    adm_res = _login("admin@company.local", "Admin1234!")
    assert emp_res.status_code == 200
    assert fin_res.status_code == 200
    assert adm_res.status_code == 200

    emp_h = {"Authorization": f"Bearer {emp_res.json()['access_token']}"}
    fin_h = {"Authorization": f"Bearer {fin_res.json()['access_token']}"}
    adm_h = {"Authorization": f"Bearer {adm_res.json()['access_token']}"}
    adm_id = adm_res.json()["user"]["id"]

    assert client.get("/api/auth/me", headers=emp_h).json()["role"] == "employee"
    assert client.get("/api/auth/me", headers=fin_h).json()["role"] == "accountant"
    assert client.get("/api/auth/me", headers=adm_h).json()["role"] == "admin"

    # Change password test on an isolated ephemeral user (never mutates shared demo user credentials)
    ephemeral_email = f"pwtest_{uuid.uuid4().hex[:8]}@company.local"
    with SessionLocal() as db:
        temp_user = User(
            email=ephemeral_email,
            full_name="密碼測試暫存帳號",
            hashed_password=hash_password("TempPass123!"),
            role="employee",
            department="測試部門",
            is_active=True,
        )
        db.add(temp_user)
        db.commit()
    try:
        temp_h = {"Authorization": f"Bearer {_login(ephemeral_email, 'TempPass123!').json()['access_token']}"}
        assert client.post(
            "/api/auth/change-password",
            headers=temp_h,
            json={"current_password": "WrongPassword!", "new_password": "NewUser5678!"},
        ).status_code == 400
        assert client.post(
            "/api/auth/change-password",
            headers=temp_h,
            json={"current_password": "TempPass123!", "new_password": "NewUser5678!"},
        ).status_code == 200
        assert _login(ephemeral_email, "TempPass123!").status_code == 401
        assert _login(ephemeral_email, "NewUser5678!").status_code == 200
    finally:
        with SessionLocal() as db:
            db.query(User).filter(User.email == ephemeral_email).delete()
            db.commit()

    # Mock external BOT historical lookup during offline suite execution
    def _mock_rate_lookup(currency, target_date=None, rate_type="spot_sell"):
        code = (currency or "USD").upper()
        table = {"RMB": 4.72, "CNY": 4.72, "THB": 0.965, "EUR": 36.75, "MYR": 8.271, "USD": 31.895}
        r = table.get(code, 31.895)
        return {
            "currency": currency,
            "iso_currency": "CNY" if code == "RMB" else code,
            "requested_date": target_date or "2026-10-05",
            "rate_date": "2026-10-05",
            "rate": r,
            "rate_type": "cash_sell" if code == "MYR" else rate_type,
            "source": "臺灣銀行牌告匯率 (mocked 2026-10-05)",
            "is_live": True,
        }

    with patch.object(fx_service, "get_rate_for_date", side_effect=_mock_rate_lookup):
        tag = uuid.uuid4().hex[:6].upper()
        res_trip = client.post(
            "/api/trips",
            headers=emp_h,
            json={
                "title": f"全角色驗證出差_{tag}",
                "destination": "上海/曼谷/法蘭克福",
                "start_date": "2026-10-01",
                "end_date": "2026-10-05",
                "advance_payment": 2000.0,
            },
        )
        assert res_trip.status_code == 200
        trip_id = res_trip.json()["trip"]["id"]
        trip_code = res_trip.json()["trip"]["trip_code"]

        assert client.patch(
            f"/api/trips/{trip_id}",
            headers=emp_h,
            json={"purpose": "全功能驗收測試"},
        ).json()["trip"]["purpose"] == "全功能驗收測試"

        sample_files = [
            ("files", ("sample_china_vat_invoice.json", Path("sample_data/sample_china_vat_invoice.json").read_bytes(), "application/json")),
            ("files", ("sample_thailand_grab.json", Path("sample_data/sample_thailand_grab.json").read_bytes(), "application/json")),
            ("files", ("sample_overseas_hotel.json", Path("sample_data/sample_overseas_hotel.json").read_bytes(), "application/json")),
        ]
        res_audit = client.post(
            "/api/audit",
            headers=emp_h,
            data={"engine": "mock", "apply_template": "true", "trip_id": str(trip_id), "fx_mode": "auto_bot"},
            files=sample_files,
        )
        assert res_audit.status_code == 200
        assert res_audit.json()["total"] == 3
        assert "template" in res_audit.json()["download_urls"]

        # Multi-page PDF auto-split
        pdf_bytes = _make_multipage_pdf_bytes(num_pages=2)
        res_pdf = client.post(
            "/api/audit",
            headers=emp_h,
            data={"engine": "mock", "apply_template": "false", "trip_id": str(trip_id), "split_pdf": "true"},
            files=[("files", (f"scan_{tag}.pdf", pdf_bytes, "application/pdf"))],
        )
        assert res_pdf.status_code == 200
        assert res_pdf.json()["source_files_count"] == 1
        assert res_pdf.json()["expanded_pages_count"] == 2

        # Real TW QR vs China Fapiao QR
        uniq8 = f"{int(uuid.uuid4().hex[:7], 16) % 90000000 + 10000000}"
        tw_payload = f"QA{uniq8}115100512340000014d0000015e0000000012345675ABCDEFGHIJKLMNOPQRSTUVWX:1:1:1:高鐵便當:1:350"
        cn_payload = "01,04,3100231130,88210345,368.00,20261005,12345678901234567890,ABCD"
        tw_png = tmp_path / "tw_qr.png"
        cn_png = tmp_path / "cn_qr.png"
        subprocess.run(["qrencode", "-s", "6", "-m", "4", "-o", str(tw_png), tw_payload], check=True)
        subprocess.run(["qrencode", "-s", "6", "-m", "4", "-o", str(cn_png), cn_payload], check=True)

        assert client.post(
            "/api/audit",
            headers=emp_h,
            data={"engine": "mock", "apply_template": "false", "trip_id": str(trip_id), "multi_receipt": "false"},
            files=[("files", ("tw_qr.png", tw_png.read_bytes(), "image/png"))],
        ).json()["qr_fast_decoded_count"] == 1

        assert client.post(
            "/api/audit",
            headers=emp_h,
            data={"engine": "mock", "apply_template": "false", "trip_id": str(trip_id), "multi_receipt": "false"},
            files=[("files", ("sample_china_vat_invoice.png", cn_png.read_bytes(), "image/png"))],
        ).json()["qr_fast_decoded_count"] == 0

        # Watch Folder scan into trip subfolder
        inbox = tmp_path / "watch_inbox"
        sub = inbox / trip_code
        sub.mkdir(parents=True)
        (sub / f"watch_{tag}.pdf").write_bytes(pdf_bytes)
        orig_dir = watch_folder_service.watch_dir
        watch_folder_service.watch_dir = inbox
        try:
            assert client.get("/api/watch-folder/status", headers=emp_h).json()["pending_count"] == 1
            res_scan = client.post("/api/watch-folder/scan", headers=emp_h, json={"engine": "mock", "split_pdf": True})
            assert res_scan.status_code == 200
            assert res_scan.json()["expanded_pages_count"] == 2
            assert client.get("/api/watch-folder/status", headers=emp_h).json()["pending_count"] == 0
        finally:
            watch_folder_service.watch_dir = orig_dir

        # FX rate edit (manual & bot_date) + 4 export formats + stored file preview
        trip_detail = client.get(f"/api/trips/{trip_id}", headers=emp_h).json()
        foreign_rec = next(r for r in trip_detail["records"] if (r.get("currency") or "NT$") != "NT$")
        rec_id = foreign_rec["id"]

        assert client.get("/api/rates/lookup?currency=USD&date=2026-10-04").status_code == 200
        assert client.patch(
            f"/api/records/{rec_id}/exchange-rate",
            headers=emp_h,
            json={"mode": "manual", "exchange_rate": 5.1234},
        ).json()["record"]["exchange_rate"] == 5.1234
        assert client.patch(
            f"/api/records/{rec_id}/exchange-rate",
            headers=emp_h,
            json={"mode": "bot_date", "rate_date": "2026-10-05", "rate_type": "spot_sell"},
        ).json()["record"]["exchange_rate"] > 0

        for fmt in ("template", "excel", "erp", "json"):
            assert client.get(f"/api/trips/{trip_id}/export?format={fmt}", headers=emp_h).status_code == 200
        assert client.get(f"/api/records/{rec_id}/file", headers=emp_h).status_code == 200
        assert client.get(f"/api/history/export?trip_id={trip_id}", headers=emp_h).status_code == 200

        # Admin-owned trip & record for employee cross-user isolation tests
        adm_trip_id = client.post("/api/trips", headers=adm_h, json={"title": f"AdminPrivate_{tag}"}).json()["trip"]["id"]
        client.post(
            "/api/audit",
            headers=adm_h,
            data={"engine": "mock", "apply_template": "false", "trip_id": str(adm_trip_id)},
            files=[("files", ("sample_overseas_hotel.json", Path("sample_data/sample_overseas_hotel.json").read_bytes(), "application/json"))],
        )
        adm_rec_id = client.get(f"/api/trips/{adm_trip_id}", headers=adm_h).json()["records"][0]["id"]

        for call_res in [
            client.get("/api/auth/users", headers=emp_h),
            client.get("/api/storage/stats", headers=emp_h),
            client.post("/api/storage/cleanup", headers=emp_h, json={"max_age_hours": 72}),
            client.get(f"/api/history?user_id={adm_id}", headers=emp_h),
            client.get(f"/api/stats?user_id={adm_id}", headers=emp_h),
            client.get(f"/api/trips?user_id={adm_id}", headers=emp_h),
            client.get(f"/api/trips/{adm_trip_id}", headers=emp_h),
            client.get(f"/api/records/{adm_rec_id}/file", headers=emp_h),
            client.patch(f"/api/records/{adm_rec_id}/exchange-rate", headers=emp_h, json={"mode": "manual", "exchange_rate": 30.0}),
            client.post(f"/api/trips/{trip_id}/review", headers=emp_h, json={"action": "approve"}),
            client.post(f"/api/trips/{trip_id}/mark-paid", headers=emp_h),
            client.post(f"/api/approval/{rec_id}/review", headers=emp_h, json={"action": "approve"}),
            client.post(f"/api/approval/{rec_id}/mark-paid", headers=emp_h),
            client.post(f"/api/approval/{adm_rec_id}/resubmit", headers=emp_h),
        ]:
            assert call_res.status_code == 403

        # Employee blocked from SQLAdmin
        emp_ac = TestClient(app)
        emp_ac.post("/admin/login", data={"username": "employee@company.local", "password": "User1234!"})
        assert emp_ac.get("/admin/user/list", follow_redirects=False).status_code in (302, 303, 307)

        # Employee submit -> Finance return -> Employee resubmit -> Finance approve & pay
        assert client.post(f"/api/trips/{trip_id}/submit", headers=emp_h, json={"notes": "請審核"}).json()["trip"]["status"] == "SUBMITTED"
        assert client.get("/api/auth/users", headers=fin_h).status_code == 200
        assert client.get("/api/approval/summary", headers=fin_h).status_code == 200
        assert client.get("/api/approval/list", headers=fin_h).status_code == 200
        assert client.get("/api/storage/stats", headers=fin_h).status_code == 200
        assert client.get(f"/api/records/{rec_id}/file", headers=fin_h).status_code == 200

        assert client.post(
            f"/api/approval/{rec_id}/review",
            headers=fin_h,
            json={"action": "return", "notes": "請補說明"},
        ).json()["trip_status"] == "RETURNED"
        assert client.post(
            f"/api/approval/{rec_id}/resubmit",
            headers=emp_h,
            json={"notes": "已補充"},
        ).json()["trip_status"] == "SUBMITTED"
        assert client.post(
            f"/api/trips/{trip_id}/review",
            headers=fin_h,
            json={"action": "approve", "notes": "複核無誤"},
        ).json()["trip"]["status"] == "APPROVED"
        assert client.post(
            f"/api/trips/{trip_id}/mark-paid",
            headers=fin_h,
            json={"paid_by": "出納匯款"},
        ).json()["trip"]["status"] == "PAID"

        # Paid records/trips are locked for employee edits
        assert client.patch(f"/api/records/{rec_id}/exchange-rate", headers=emp_h, json={"mode": "manual", "exchange_rate": 10.0}).status_code == 400
        assert client.delete(f"/api/trips/{trip_id}", headers=emp_h).status_code == 400

    # Regression check for Bug #1: Finance and Admin can open all 3 SQLAdmin list pages cleanly (HTTP 200)
    for email, pw in [("finance@company.local", "Finance1234!"), ("admin@company.local", "Admin1234!")]:
        ac = TestClient(app)
        res_login = ac.post("/admin/login", data={"username": email, "password": pw}, follow_redirects=False)
        assert res_login.status_code in (200, 302, 303)
        for view_url in ("/admin/user/list", "/admin/expense-trip-db/list", "/admin/audit-record-db/list"):
            assert ac.get(view_url).status_code == 200, f"{email} failed to load {view_url}"

    assert client.post("/api/storage/cleanup", headers=adm_h, json={"max_age_hours": 99999}).status_code == 200


def test_frontend_inline_handlers_and_dom_integrity():
    """Regression check for Bug #2: ensure no raw JSON.stringify quoting bugs and no dangling HTML handlers."""
    html = Path("src/web/static/index.html").read_text(encoding="utf-8")
    js = Path("src/web/static/app.js").read_text(encoding="utf-8")

    # 1. No raw JSON.stringify inside double-quoted inline HTML attributes
    for src_name, content in [("index.html", html), ("app.js", js)]:
        for idx, line in enumerate(content.splitlines(), 1):
            for m in re.finditer(r'(on(?:click|change|submit|input))="([^"]*)"', line):
                assert "JSON.stringify" not in m.group(2), f"Unescaped JSON.stringify in {src_name}:{idx}"

    # 2. Every inline handler function in index.html must be defined in app.js
    defined_fns = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(", js))
    builtins = {"event", "return", "this", "alert", "confirm", "prompt", "console", "JSON", "Number", "String", "parseInt", "parseFloat"}
    for idx, line in enumerate(html.splitlines(), 1):
        for m in re.finditer(r'on(?:click|change|submit|input)="([^"]*)"', line):
            expr = m.group(1)
            for fn in re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", expr):
                if fn in builtins or fn in ("stopPropagation", "preventDefault"):
                    continue
                assert fn in defined_fns, f"index.html:{idx} references undefined JS handler '{fn}'"
