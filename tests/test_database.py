"""Unit tests for SQLite database operations and cross-month deduplication."""

from pathlib import Path
from src.models import InvoiceData, AuditReport, AuditStatus, DocumentType, ExpenseCategory
from src.database import (
    init_db,
    is_invoice_seen,
    save_audit_reports,
    get_audit_history,
    get_audit_statistics
)


def test_database_lifecycle_and_deduplication(tmp_path):
    db_file = tmp_path / "test_invoices.db"
    init_db(db_file)
    assert db_file.exists()

    # Initially invoice is not seen
    assert not is_invoice_seen("AB-12345678", db_path=db_file)

    # Create dummy reports
    rep1 = AuditReport(
        file_path="sample.png",
        data=InvoiceData(
            document_type=DocumentType.TW_INVOICE,
            invoice_number="AB-12345678",
            invoice_date="2026-09-01",
            total_amount=5000.0,
            currency="NT$",
            seller_name="測試廠商",
            expense_category=ExpenseCategory.EXTRA,
            purpose="辦公設備"
        ),
        status=AuditStatus.APPROVED,
        audit_timestamp="2026-09-01T10:00:00"
    )

    rep2 = AuditReport(
        file_path="grab.png",
        data=InvoiceData(
            document_type=DocumentType.TAXI_RECEIPT,
            invoice_number="GRB-888999",
            invoice_date="2026-08-20",
            total_amount=250.0,
            currency="THB",
            seller_name="Grab Thailand",
            expense_category=ExpenseCategory.TRANSPORT,
            purpose="曼谷計程車"
        ),
        status=AuditStatus.APPROVED,
        audit_timestamp="2026-08-20T12:00:00"
    )

    inserted = save_audit_reports([rep1, rep2], db_path=db_file)
    assert inserted == 2

    # Now invoice must be marked as seen (case insensitive)
    assert is_invoice_seen("AB-12345678", db_path=db_file)
    assert is_invoice_seen("ab-12345678", db_path=db_file)
    assert is_invoice_seen("GRB-888999", db_path=db_file)
    assert not is_invoice_seen("NON-EXISTENT", db_path=db_file)

    # Check history retrieval
    history = get_audit_history(limit=10, db_path=db_file)
    assert len(history) == 2
    assert history[0]["invoice_number"] == "GRB-888999"
    assert history[1]["invoice_number"] == "AB-12345678"

    # Check statistics
    stats = get_audit_statistics(db_path=db_file)
    assert stats["total_invoices"] == 2
    assert stats["status_counts"]["APPROVED"] == 2
    assert "NT$" in stats["currency_totals"]
    assert "THB" in stats["currency_totals"]
    assert stats["currency_totals"]["NT$"] == 5000.0
    assert stats["currency_totals"]["THB"] == 250.0
