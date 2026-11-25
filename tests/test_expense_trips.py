"""Comprehensive tests for Business Trip Bundles (Phase 1 + Phase 2)."""

import io
import json
from pathlib import Path
import openpyxl
from starlette.testclient import TestClient

from src.database import (
    DEFAULT_DB_PATH,
    get_audit_history,
    get_expense_trip_detail,
    init_db,
)
from src.models import (
    AuditReport,
    AuditStatus,
    DocumentType,
    ExpenseCategory,
    InvoiceData,
)
from src.template_exporter import TemplateExpenseExporter
from src.web.app import app

client = TestClient(app)


def _login(email: str, password: str) -> dict:
    init_db(DEFAULT_DB_PATH)
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _upload_mock_invoice(headers: dict, filename: str, trip_id: int = None, skip_exports: bool = False):
    payload_bytes = json.dumps({
        "document_type": "commercial_invoice",
        "invoice_number": f"TRIP-TEST-{filename}",
        "invoice_date": "2026-11-10",
        "seller_name": "Test Travel Vendor",
        "currency": "TWD",
        "subtotal": 1000.0,
        "tax_amount": 0.0,
        "tax_rate": 0.0,
        "total_amount": 1000.0,
        "expense_category": "T",
        "purpose": "出差測試交通單據",
    }).encode("utf-8")
    data = {
        "engine": "mock",
        "apply_template": "true" if not skip_exports else "false",
        "skip_exports": "true" if skip_exports else "false",
    }
    if trip_id is not None:
        data["trip_id"] = str(trip_id)
    return client.post(
        "/api/audit",
        headers=headers,
        data=data,
        files=[("files", (filename, io.BytesIO(payload_bytes), "application/json"))],
    )


def test_trip_crud_and_rbac_isolation():
    """Employees create/see their own trips; cross-user access and employee review are 403."""
    emp_headers = _login("employee@company.local", "User1234!")
    admin_headers = _login("admin@company.local", "Admin1234!")

    # Admin creates a trip belonging to admin
    res_admin_trip = client.post(
        "/api/trips",
        headers=admin_headers,
        json={
            "title": "Admin Confidential Trip",
            "destination": "Osaka",
            "start_date": "2026-11-01",
            "end_date": "2026-11-03",
            "advance_payment": 5000.0,
        },
    )
    assert res_admin_trip.status_code == 200
    admin_trip_id = res_admin_trip.json()["trip"]["id"]

    # Employee creates own trip
    res_emp_trip = client.post(
        "/api/trips",
        headers=emp_headers,
        json={
            "title": "高雄三日業務拜訪",
            "destination": "Kaohsiung",
            "start_date": "2026-11-10",
            "end_date": "2026-11-12",
            "advance_payment": 3000.0,
        },
    )
    assert res_emp_trip.status_code == 200
    emp_trip = res_emp_trip.json()["trip"]
    emp_trip_id = emp_trip["id"]
    assert emp_trip["trip_code"].startswith("TRIP-")
    assert emp_trip["status"] == "DRAFT"

    # Employee cannot view Admin's trip, nor call review / mark-paid
    assert client.get(f"/api/trips/{admin_trip_id}", headers=emp_headers).status_code == 403
    assert client.post(
        f"/api/trips/{emp_trip_id}/review",
        headers=emp_headers,
        json={"action": "approve"},
    ).status_code == 403
    assert client.post(f"/api/trips/{emp_trip_id}/mark-paid", headers=emp_headers).status_code == 403

    # Update employee trip metadata
    res_patch = client.patch(
        f"/api/trips/{emp_trip_id}",
        headers=emp_headers,
        json={"title": "高雄三日業務拜訪 (已更新)", "advance_payment": 4500.0},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["trip"]["title"] == "高雄三日業務拜訪 (已更新)"
    assert res_patch.json()["trip"]["advance_payment"] == 4500.0


def test_multi_batch_upload_draft_isolation_and_assign():
    """Multiple sequential uploads converge into one trip; DRAFT receipts stay out of approval queue."""
    emp_headers = _login("employee@company.local", "User1234!")
    fin_headers = _login("finance@company.local", "Finance1234!")

    res_trip = client.post(
        "/api/trips",
        headers=emp_headers,
        json={
            "title": "台北-新竹出差收據測試",
            "start_date": "2026-11-15",
            "end_date": "2026-11-16",
        },
    )
    assert res_trip.status_code == 200
    trip_id = res_trip.json()["trip"]["id"]

    before_summary = client.get("/api/approval/summary", headers=fin_headers).json()

    # Simulate Phase 2 chunked multi-batch upload (intermediate batch skip_exports=True, final skip_exports=False)
    for i in range(6):
        is_intermediate = i < 5
        res_up = _upload_mock_invoice(
            emp_headers,
            f"batch_{trip_id}_{i}.json",
            trip_id=trip_id,
            skip_exports=is_intermediate,
        )
        assert res_up.status_code == 200
        body = res_up.json()
        assert body["trip_id"] == trip_id
        if is_intermediate:
            assert body["download_urls"] == {}
        else:
            assert "template" in body["download_urls"]

    detail = client.get(f"/api/trips/{trip_id}", headers=emp_headers).json()
    assert detail["receipt_count"] == 6
    assert detail["status"] == "DRAFT"
    assert all(r["approval_status"] == "DRAFT" for r in detail["records"])

    # DRAFT receipts must NOT inflate submitted_count or appear in ALL approval list
    after_summary = client.get("/api/approval/summary", headers=fin_headers).json()
    assert after_summary["submitted_count"] == before_summary["submitted_count"]
    appr_all = client.get("/api/approval/list?status=ALL&limit=200", headers=fin_headers).json()["records"]
    assert all(r.get("trip_id") != trip_id for r in appr_all)

    # Detach one record via /assign remove and re-add it
    detach_id = detail["records"][0]["id"]
    res_det = client.post(
        f"/api/trips/{trip_id}/assign",
        headers=emp_headers,
        json={"record_ids": [detach_id], "action": "remove"},
    )
    assert res_det.status_code == 200
    assert res_det.json()["trip"]["receipt_count"] == 5

    res_readd = client.post(
        f"/api/trips/{trip_id}/assign",
        headers=emp_headers,
        json={"record_ids": [detach_id], "action": "add"},
    )
    assert res_readd.status_code == 200
    assert res_readd.json()["trip"]["receipt_count"] == 6
    # Cleanup reference check with finance
    assert client.get(f"/api/trips/{trip_id}", headers=fin_headers).status_code == 200


def test_trip_submit_single_return_sync_and_bulk_pay():
    """Verify submit, single-receipt return -> parent RETURNED -> resubmit -> SUBMITTED, then bulk pay."""
    emp_headers = _login("employee@company.local", "User1234!")
    fin_headers = _login("finance@company.local", "Finance1234!")

    res_trip = client.post(
        "/api/trips",
        headers=emp_headers,
        json={
            "title": "狀態同步與退件測試出差案",
            "start_date": "2026-11-18",
            "end_date": "2026-11-20",
            "advance_payment": 500.0,
        },
    )
    trip_id = res_trip.json()["trip"]["id"]

    # Empty trip cannot be submitted
    res_empty_sub = client.post(f"/api/trips/{trip_id}/submit", headers=emp_headers, json={})
    assert res_empty_sub.status_code == 400

    # Upload 2 receipts into this trip
    for idx in range(2):
        assert _upload_mock_invoice(
            emp_headers,
            f"sync_{trip_id}_{idx}.json",
            trip_id=trip_id,
        ).status_code == 200

    # Submit trip bundle
    res_sub = client.post(
        f"/api/trips/{trip_id}/submit",
        headers=emp_headers,
        json={"notes": "已完成差旅單據整理"},
    )
    assert res_sub.status_code == 200
    submitted_trip = res_sub.json()["trip"]
    assert submitted_trip["status"] == "SUBMITTED"
    assert all(r["approval_status"] == "SUBMITTED" for r in submitted_trip["records"])

    # Finance returns ONLY ONE receipt inside the trip -> parent trip auto-syncs to RETURNED
    rec_one_id = submitted_trip["records"][0]["id"]
    rec_two_id = submitted_trip["records"][1]["id"]
    res_ret = client.post(
        f"/api/approval/{rec_one_id}/review",
        headers=fin_headers,
        json={"action": "return", "notes": "請補附高鐵車票正本照片"},
    )
    assert res_ret.status_code == 200
    assert res_ret.json()["trip_id"] == trip_id
    assert res_ret.json()["trip_status"] == "RETURNED"

    # Finance approves second receipt (stays RETURNED because first is still RETURNED)
    res_app_two = client.post(
        f"/api/approval/{rec_two_id}/review",
        headers=fin_headers,
        json={"action": "approve", "notes": "第二筆無誤"},
    )
    assert res_app_two.status_code == 200
    assert res_app_two.json()["trip_status"] == "RETURNED"

    # Employee resubmits the returned first receipt -> parent auto-syncs back to SUBMITTED
    res_resub = client.post(
        f"/api/approval/{rec_one_id}/resubmit",
        headers=emp_headers,
        json={"notes": "已補上傳清晰車票正本"},
    )
    assert res_resub.status_code == 200
    assert res_resub.json()["trip_status"] == "SUBMITTED"

    # Finance batch-approves the entire trip
    res_trip_app = client.post(
        f"/api/trips/{trip_id}/review",
        headers=fin_headers,
        json={"action": "approve", "notes": "整案複核通過"},
    )
    assert res_trip_app.status_code == 200
    assert res_trip_app.json()["trip"]["status"] == "APPROVED"

    # Finance batch mark-paid for the entire trip
    res_trip_paid = client.post(
        f"/api/trips/{trip_id}/mark-paid",
        headers=fin_headers,
        json={"paid_by": "出納組-銀行匯款"},
    )
    assert res_trip_paid.status_code == 200
    paid_trip = res_trip_paid.json()["trip"]
    assert paid_trip["status"] == "PAID"
    assert all(r["approval_status"] == "PAID" for r in paid_trip["records"])


def test_trip_export_all_formats():
    """Export /api/trips/{id}/export across template, excel, erp, and json formats."""
    emp_headers = _login("employee@company.local", "User1234!")
    res_trip = client.post(
        "/api/trips",
        headers=emp_headers,
        json={
            "title": "匯出格式全測專案",
            "destination": "Tokyo",
            "start_date": "2026-11-01",
            "end_date": "2026-11-05",
            "advance_payment": 12000.0,
        },
    )
    trip = res_trip.json()["trip"]
    trip_id = trip["id"]

    assert _upload_mock_invoice(emp_headers, f"exp_{trip_id}_1.json", trip_id=trip_id).status_code == 200
    assert _upload_mock_invoice(emp_headers, f"exp_{trip_id}_2.json", trip_id=trip_id).status_code == 200

    for fmt in ("template", "excel", "erp", "json"):
        res_exp = client.get(f"/api/trips/{trip_id}/export?format={fmt}", headers=emp_headers)
        assert res_exp.status_code == 200
        assert len(res_exp.content) > 100
        if fmt == "template" and Path("Template.xlsx").exists():
            wb = openpyxl.load_workbook(io.BytesIO(res_exp.content), data_only=False)
            ws = wb["Report"]
            assert trip["trip_code"] in str(ws["A2"].value)
            assert ws["F3"].value == 5
            assert float(ws["C9"].value) == 12000.0


def test_template_exporter_dynamic_expansion_over_100_rows(tmp_path):
    """Ensure TemplateExpenseExporter extends Report, Rates, and Calc formulas past 100+ rows."""
    template_path = Path("Template.xlsx")
    if not template_path.exists():
        return

    reports = [
        AuditReport(
            file_path=f"receipt_{i:03d}.jpg",
            data=InvoiceData(
                document_type=DocumentType.COMMERCIAL_INVOICE,
                invoice_number=f"BULK-{i:04d}",
                invoice_date="2026-11-10",
                seller_name=f"Vendor {i}",
                currency="TWD",
                total_amount=100.0,
                expense_category=ExpenseCategory.FOOD,
                purpose=f"Meal {i}",
            ),
            status=AuditStatus.APPROVED,
            audit_timestamp="2026-11-10T12:00:00",
        )
        for i in range(1, 131)
    ]

    out_file = tmp_path / "Bulk_130_Rows.xlsx"
    trip_meta = {
        "trip_code": "TRIP-202611-999",
        "title": "130筆大批量測試",
        "applicant_name": "林專員",
        "start_date": "2026-11-01",
        "end_date": "2026-11-07",
        "advance_payment": 8000.0,
    }
    TemplateExpenseExporter(reports, template_path=template_path, trip_meta=trip_meta).export(out_file)

    wb = openpyxl.load_workbook(out_file, data_only=False)
    ws_rep = wb["Report"]
    ws_rates = wb["Rates"]
    ws_calc = wb["Calc"]

    # Report has 130 items from row 13..142
    assert ws_rep["F3"].value == 7
    assert float(ws_rep["C9"].value) == 8000.0
    assert ws_rep["C5"].value == "=SUM(J13:J142)"
    assert ws_rates["C19"].value == "=ROUND(SUM(Report!J13:J142),0)"
    assert ws_rep.cell(row=142, column=1).value == 130
    assert "$E$9" in str(ws_rep.cell(row=142, column=10).value)

    # Calc has 130 items from row 4..133 and dynamic D1..J1 SUM ranges + fixed "F" category formula
    assert ws_calc["D1"].value == "=ROUND(SUM(D4:D133),0)"
    assert ws_calc["J1"].value == "=ROUND(SUM(J4:J133),0)"
    assert ws_calc["A133"].value == "=Report!A142"
    assert ws_calc["H133"].value == '=IF(C133="F",Report!J142,0)'


def test_delete_draft_trip_preserves_audit_records():
    """Deleting a DRAFT trip detaches its invoices without deleting audit/dedup records."""
    emp_headers = _login("employee@company.local", "User1234!")
    res_trip = client.post(
        "/api/trips",
        headers=emp_headers,
        json={"title": "待刪除草稿專案"},
    )
    trip_id = res_trip.json()["trip"]["id"]
    assert _upload_mock_invoice(emp_headers, f"del_{trip_id}_1.json", trip_id=trip_id).status_code == 200

    detail = get_expense_trip_detail(trip_id, db_path=DEFAULT_DB_PATH)
    rec_id = detail["records"][0]["id"]

    res_del = client.delete(f"/api/trips/{trip_id}", headers=emp_headers)
    assert res_del.status_code == 200
    assert get_expense_trip_detail(trip_id, db_path=DEFAULT_DB_PATH) is None

    # Invoice still exists in history with trip_id cleared
    history = get_audit_history(limit=200, db_path=DEFAULT_DB_PATH)
    preserved = next((r for r in history if r["id"] == rec_id), None)
    assert preserved is not None
    assert preserved["trip_id"] is None
