"""Integration tests for Small-Team Financial Approval Workflow & State Machine."""

import pytest
from starlette.testclient import TestClient
from pathlib import Path
from datetime import datetime

from src.web.app import app
from src.database import (
    init_db,
    save_audit_reports,
    DEFAULT_DB_PATH,
    SessionLocal,
    AuditRecordDB,
    User
)
from src.models import (
    AuditReport,
    AuditStatus,
    InvoiceData,
    DocumentType,
    ExpenseCategory
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_records():
    """Seed test audit records before tests."""
    init_db(DEFAULT_DB_PATH)


def test_approval_summary_api():
    """1. Test summary statistics endpoint for Boss / Finance dashboard."""
    res = client.get("/api/approval/summary")
    assert res.status_code == 200
    data = res.json()
    assert "submitted_count" in data
    assert "approved_count" in data
    assert "returned_count" in data
    assert "paid_count" in data
    assert "total_count" in data
    assert isinstance(data["total_amount"], (int, float))


def test_approval_workflow_happy_path():
    """2. Test Happy Path: Submitted -> Approved -> Paid."""
    # Seed a new invoice report
    test_report = AuditReport(
        file_path="mock_taxi.png",
        status=AuditStatus.APPROVED,
        audit_timestamp=datetime.now().isoformat(),
        data=InvoiceData(
            document_type=DocumentType.TAXI_RECEIPT,
            invoice_number="TX-998811",
            currency="NT$",
            total_amount=450.0,
            seller_name="台灣大車隊",
            purpose="商務拜訪交通費",
            expense_category=ExpenseCategory.TRANSPORT
        )
    )
    save_audit_reports([test_report], applicant_name="王小明", applicant_email="wang@company.local")

    # Get the inserted record id
    res_list = client.get("/api/approval/list?status=SUBMITTED")
    assert res_list.status_code == 200
    records = res_list.json()["records"]
    target = next((r for r in records if r["invoice_number"] == "TX-998811"), None)
    assert target is not None
    rec_id = target["id"]
    assert target["approval_status"] == "SUBMITTED"
    assert target["applicant_name"] == "王小明"

    # Step 1: Manager/Finance Approves
    res_review = client.post(
        f"/api/approval/{rec_id}/review",
        json={"action": "approve", "notes": "符合差旅報銷規定，核准"}
    )
    assert res_review.status_code == 200
    review_data = res_review.json()
    assert review_data["approval_status"] == "APPROVED"

    # Verify status in database
    res_approved_list = client.get("/api/approval/list?status=APPROVED")
    approved_target = next((r for r in res_approved_list.json()["records"] if r["id"] == rec_id), None)
    assert approved_target is not None
    assert approved_target["approval_status"] == "APPROVED"
    assert approved_target["reviewer_name"] is not None

    # Step 2: Cashier Marks as Paid
    res_paid = client.post(
        f"/api/approval/{rec_id}/mark-paid",
        json={"paid_by": "財務出納處 (台新網銀轉帳)"}
    )
    assert res_paid.status_code == 200
    paid_data = res_paid.json()
    assert paid_data["approval_status"] == "PAID"

    # Verify final state
    res_paid_list = client.get("/api/approval/list?status=PAID")
    paid_target = next((r for r in res_paid_list.json()["records"] if r["id"] == rec_id), None)
    assert paid_target is not None
    assert paid_target["approval_status"] == "PAID"
    assert paid_target["paid_by"] == "財務出納處 (台新網銀轉帳)"
    assert paid_target["paid_at"] is not None


def test_approval_workflow_return_and_resubmit():
    """3. Test Return with reason -> Employee Resubmission flow."""
    test_report = AuditReport(
        file_path="mock_hotel.json",
        status=AuditStatus.WARNING,
        audit_timestamp=datetime.now().isoformat(),
        data=InvoiceData(
            document_type=DocumentType.COMMERCIAL_INVOICE,
            invoice_number="HTL-554433",
            currency="USD",
            total_amount=180.0,
            seller_name="Hilton Hotel",
            purpose="出差住宿"
        )
    )
    save_audit_reports([test_report], applicant_name="林專員", applicant_email="lin@company.local")

    res_list = client.get("/api/approval/list?status=ALL")
    target = next((r for r in res_list.json()["records"] if r["invoice_number"] == "HTL-554433"), None)
    assert target is not None
    rec_id = target["id"]

    # Reject without notes should fail
    res_fail_return = client.post(
        f"/api/approval/{rec_id}/review",
        json={"action": "return", "notes": ""}
    )
    assert res_fail_return.status_code == 400

    # Return with specific reason
    res_return = client.post(
        f"/api/approval/{rec_id}/review",
        json={"action": "return", "notes": "請補附飯店水單明細以茲佐證"}
    )
    assert res_return.status_code == 200
    assert res_return.json()["approval_status"] == "RETURNED"

    # Check that record is now RETURNED
    res_ret_list = client.get("/api/approval/list?status=RETURNED")
    returned_target = next((r for r in res_ret_list.json()["records"] if r["id"] == rec_id), None)
    assert returned_target is not None
    assert "請補附飯店水單明細" in returned_target["review_notes"]

    # Employee resubmits
    res_resubmit = client.post(
        f"/api/approval/{rec_id}/resubmit",
        json={"notes": "已補上水單明細截圖，請再次覆核"}
    )
    assert res_resubmit.status_code == 200
    assert res_resubmit.json()["approval_status"] == "SUBMITTED"

    # Verify back to SUBMITTED
    res_sub_list = client.get("/api/approval/list?status=SUBMITTED")
    resubmitted_target = next((r for r in res_sub_list.json()["records"] if r["id"] == rec_id), None)
    assert resubmitted_target is not None
    assert resubmitted_target["approval_status"] == "SUBMITTED"
    assert "重新送審" in resubmitted_target["review_notes"]
