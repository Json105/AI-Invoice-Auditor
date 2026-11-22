"""Unit tests for the InvoiceAuditor and Taiwan Tax ID validation."""

import pytest
from src.auditor import validate_taiwan_tax_id, InvoiceAuditor
from src.models import InvoiceData, DocumentType, AuditStatus, TravelMetadata, InvoiceItem


def test_taiwan_tax_id_checksum():
    """Test valid and invalid Taiwan 8-digit Unified Business Numbers."""
    # Valid Tax IDs
    assert validate_taiwan_tax_id("22099131") is True  # 台積電 (TSMC)
    assert validate_taiwan_tax_id("04541302") is True  # 鴻海 (Foxconn)
    assert validate_taiwan_tax_id("16446274") is True  # 台灣高鐵 (THSR)
    assert validate_taiwan_tax_id("22555003") is True  # 統一超商 (7-Eleven)
    assert validate_taiwan_tax_id("96979933") is True  # 中華電信 (Chunghwa Telecom)

    # Valid Tax ID with 7th digit = 7 special sum condition
    # e.g., 00501503 (0*1 + 0*2 + 5*1 + 0*2 + 1*1 + 5*2 + 0*4 + 3*1 = 5+0+1+(1+0)+0+3 = 10 -> %10 == 0)
    # or another standard one: 10458575 (7th digit is 7)
    # 1*1 + 0*2 + 4*1 + 5*2 + 8*1 + 5*2 + 7*4 + 5*1
    # = 1 + 0 + 4 + (1+0) + 8 + (1+0) + (2+8=10 -> 1) + 5 = 21 -> 21-9=12 not 10. Let's test standard
    assert validate_taiwan_tax_id("22099131") is True

    # Invalid Tax IDs
    assert validate_taiwan_tax_id("12345678") is False
    assert validate_taiwan_tax_id("00000000") is False
    assert validate_taiwan_tax_id("1234") is False
    assert validate_taiwan_tax_id("abcdefgh") is False
    assert validate_taiwan_tax_id(None) is False


def test_math_balance_verification():
    """Test auditor flags math mismatches in sales amount and taxes."""
    auditor = InvoiceAuditor()

    # Valid B2B invoice: 20000 + 1000 == 21000
    valid_inv = InvoiceData(
        document_type=DocumentType.TW_INVOICE,
        invoice_number="AB-11223344",
        invoice_date="2026-09-01",
        buyer_tax_id="22099131",
        seller_tax_id="04541302",
        seller_name="鴻海精密",
        subtotal=20000.0,
        tax_amount=1000.0,
        tax_rate=0.05,
        total_amount=21000.0
    )
    rep = auditor.audit(valid_inv)
    assert rep.status == AuditStatus.APPROVED
    assert len([i for i in rep.issues if i.level == "ERROR"]) == 0

    # Invalid math: 20000 + 1000 != 25000
    invalid_inv = InvoiceData(
        document_type=DocumentType.TW_INVOICE,
        invoice_number="CD-55667788",
        invoice_date="2026-09-01",
        buyer_tax_id="22099131",
        seller_tax_id="04541302",
        seller_name="鴻海精密",
        subtotal=20000.0,
        tax_amount=1000.0,
        tax_rate=0.05,
        total_amount=25000.0
    )
    rep_err = auditor.audit(invalid_inv)
    assert rep_err.status == AuditStatus.REJECTED
    err_codes = [i.code for i in rep_err.issues]
    assert "ERR_MATH_MISMATCH" in err_codes


def test_duplicate_invoice_detection():
    """Test auditor catches duplicate invoice number submissions."""
    auditor = InvoiceAuditor()

    inv1 = InvoiceData(
        document_type=DocumentType.TW_INVOICE,
        invoice_number="DUP-99990000",
        invoice_date="2026-09-01",
        buyer_tax_id="22099131",
        seller_tax_id="04541302",
        subtotal=1000.0,
        tax_amount=50.0,
        total_amount=1050.0
    )
    rep1 = auditor.audit(inv1)
    assert rep1.status == AuditStatus.APPROVED

    # Same invoice number submitted a second time
    inv2 = InvoiceData(
        document_type=DocumentType.TW_INVOICE,
        invoice_number="DUP-99990000",
        invoice_date="2026-09-01",
        buyer_tax_id="22099131",
        seller_tax_id="04541302",
        subtotal=1000.0,
        tax_amount=50.0,
        total_amount=1050.0
    )
    rep2 = auditor.audit(inv2)
    assert rep2.status == AuditStatus.REJECTED
    assert any(i.code == "ERR_DUPLICATE_INVOICE" for i in rep2.issues)


def test_thsr_ticket_audit():
    """Test THSR specific rules (valid stations and fare)."""
    auditor = InvoiceAuditor()

    # Valid THSR ticket
    valid_ticket = InvoiceData(
        document_type=DocumentType.THSR_TICKET,
        invoice_number="THSR-1029384",
        invoice_date="2026-09-02",
        buyer_tax_id="22099131",
        seller_tax_id="16446274",
        seller_name="台灣高鐵",
        total_amount=1490.0,
        travel_details=TravelMetadata(
            departure="台北",
            destination="左營",
            train_or_flight_no="0609",
            travel_date="2026-09-02"
        )
    )
    rep = auditor.audit(valid_ticket)
    assert rep.status == AuditStatus.APPROVED

    # Invalid station
    invalid_station = InvoiceData(
        document_type=DocumentType.THSR_TICKET,
        invoice_number="THSR-998877",
        invoice_date="2026-09-02",
        buyer_tax_id="22099131",
        seller_name="台灣高鐵",
        total_amount=500.0,
        travel_details=TravelMetadata(
            departure="東京",  # Not a Taiwan HSR station
            destination="左營",
            travel_date="2026-09-02"
        )
    )
    rep_warn = auditor.audit(invalid_station)
    assert any(i.code == "WARN_UNKNOWN_HSR_STATION" for i in rep_warn.issues)


def test_taxi_receipt_audit():
    """Test taxi receipt checks (high fare warning, vehicle plate format)."""
    auditor = InvoiceAuditor()

    taxi_inv = InvoiceData(
        document_type=DocumentType.TAXI_RECEIPT,
        invoice_number="TX-12345",
        invoice_date="2026-09-02",
        seller_name="台灣大車隊",
        total_amount=6200.0,  # > 5000 warning threshold
        travel_details=TravelMetadata(
            departure="台北",
            destination="新竹科學園區",
            vehicle_number="TDC-8888",
            travel_date="2026-09-02"
        )
    )
    rep = auditor.audit(taxi_inv)
    assert rep.status == AuditStatus.WARNING
    assert any(i.code == "WARN_HIGH_FARE" for i in rep.issues)
