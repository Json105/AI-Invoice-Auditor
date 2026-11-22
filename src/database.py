"""SQLAlchemy and SQLite database module for cross-month invoice deduplication,
audit tracking, and enterprise user authentication / RBAC.
"""

import os
import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, Tuple
from datetime import datetime
import bcrypt

from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from .models import AuditReport, AuditStatus, ExpenseCategory, InvoiceData

DEFAULT_DB_PATH = Path("data/invoices.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

# SQLAlchemy Engine & Session Factory
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """Enterprise User Model with RBAC roles."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(100), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default="employee", nullable=False)  # admin, accountant, manager, employee
    department = Column(String(100), default="業務研發處", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.email} [{self.role}]>"


class AuditRecordDB(Base):
    """SQLAlchemy ORM Model for Audited Invoices, mapped to SQLAdmin."""
    __tablename__ = "audited_invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String(100), index=True)
    invoice_date = Column(String(50))
    document_type = Column(String(50))
    currency = Column(String(10))
    total_amount = Column(Float)
    buyer_tax_id = Column(String(20))
    buyer_name = Column(String(200))
    seller_tax_id = Column(String(50))
    seller_name = Column(String(200))
    expense_category = Column(String(20))
    purpose = Column(Text)
    payment_method = Column(String(50))
    audit_status = Column(String(50))
    issues_json = Column(Text)
    file_name = Column(String(255))
    user_id = Column(Integer, index=True, nullable=True)
    stored_file_path = Column(String(500), nullable=True)
    file_mime_type = Column(String(100), nullable=True)
    extracted_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Small Team Practical Approval Workflow Columns
    approval_status = Column(String(50), default="SUBMITTED", index=True)  # SUBMITTED, APPROVED, RETURNED, REJECTED, PAID
    applicant_name = Column(String(100), default="林專員 (業務同仁)")
    applicant_email = Column(String(255), default="employee@company.local")
    reviewer_name = Column(String(100), nullable=True)
    reviewer_email = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    paid_by = Column(String(100), nullable=True)

    def __repr__(self):
        return f"<AuditRecord {self.invoice_number or self.id} [{self.audit_status} / {self.approval_status}]>"


def get_db():
    """FastAPI Dependency for database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _hash_pw(password: str) -> str:
    """Helper to hash initial passwords using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed_default_users(db: Session) -> None:
    """Seed default enterprise accounts if users table is empty."""
    if db.query(User).count() == 0:
        demo_users = [
            User(
                email="admin@company.local",
                full_name="系統管理員",
                hashed_password=_hash_pw("Admin1234!"),
                role="admin",
                department="資訊工程處",
                is_active=True,
            ),
            User(
                email="finance@company.local",
                full_name="陳審計 (財務主管)",
                hashed_password=_hash_pw("Finance1234!"),
                role="accountant",
                department="財務會計處",
                is_active=True,
            ),
            User(
                email="employee@company.local",
                full_name="林專員 (業務同仁)",
                hashed_password=_hash_pw("User1234!"),
                role="employee",
                department="業務研發處",
                is_active=True,
            ),
        ]
        db.add_all(demo_users)
        db.commit()


def get_db_connection(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Establish connection to SQLite database (legacy compatibility)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_audit_columns(db_path: Union[str, Path]) -> None:
    """Safely migrate existing SQLite audited_invoices table with new columns."""
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(audited_invoices)")
        existing = {row[1] for row in cursor.fetchall()}
        if not existing:
            return
        migrations = [
            ("user_id", "INTEGER"),
            ("stored_file_path", "VARCHAR(500)"),
            ("file_mime_type", "VARCHAR(100)"),
            ("extracted_json", "TEXT"),
            ("approval_status", "TEXT DEFAULT 'SUBMITTED'"),
            ("applicant_name", "TEXT DEFAULT '林專員 (業務同仁)'"),
            ("applicant_email", "TEXT DEFAULT 'employee@company.local'"),
            ("reviewer_name", "TEXT"),
            ("reviewer_email", "TEXT"),
            ("reviewed_at", "TEXT"),
            ("review_notes", "TEXT"),
            ("paid_at", "TEXT"),
            ("paid_by", "TEXT"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE audited_invoices ADD COLUMN {col_name} {col_type}")
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> None:
    """Initialize database tables, migrate schema, and seed default users."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if str(path) != str(DEFAULT_DB_PATH):
        target_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=target_engine)
        _ensure_audit_columns(path)
        TargetSession = sessionmaker(bind=target_engine)
        with TargetSession() as session:
            seed_default_users(session)
    else:
        Base.metadata.create_all(bind=engine)
        _ensure_audit_columns(path)
        with SessionLocal() as session:
            seed_default_users(session)


def is_invoice_seen(invoice_number: Optional[str], db_path: Union[str, Path] = DEFAULT_DB_PATH) -> bool:
    """Check if an invoice number was previously processed in the database."""
    if not invoice_number:
        return False

    normalized = invoice_number.strip().upper()
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM audited_invoices WHERE UPPER(TRIM(invoice_number)) = ? LIMIT 1",
        (normalized,)
    )
    result = cursor.fetchone()
    conn.close()
    return result is not None


def save_audit_reports(
    reports: List[AuditReport],
    applicant_name: str = "林專員 (業務同仁)",
    applicant_email: str = "employee@company.local",
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
    user_id: Optional[int] = None,
    stored_files: Optional[Dict[str, Tuple[str, str]]] = None,
) -> int:
    """Persist audit reports to the database with owner, file metadata, and initial SUBMITTED status."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    inserted = 0
    file_map = stored_files or {}
    with conn:
        for r in reports:
            inv = r.data
            file_name = Path(r.file_path).name if r.file_path else "uploaded_file"
            issues_json = json.dumps([issue.model_dump() for issue in r.issues], ensure_ascii=False)
            extracted_json = json.dumps(inv.model_dump(mode="json"), ensure_ascii=False)
            category_val = (
                inv.expense_category.value
                if isinstance(inv.expense_category, ExpenseCategory)
                else (str(inv.expense_category) if inv.expense_category else None)
            )
            stored_path, mime_type = file_map.get(str(r.file_path), (None, None))

            conn.execute("""
                INSERT INTO audited_invoices (
                    invoice_number, invoice_date, document_type, currency,
                    total_amount, buyer_tax_id, buyer_name, seller_tax_id,
                    seller_name, expense_category, purpose, payment_method,
                    audit_status, issues_json, file_name,
                    user_id, stored_file_path, file_mime_type, extracted_json,
                    created_at, approval_status, applicant_name, applicant_email
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                inv.invoice_number.strip().upper() if inv.invoice_number else None,
                inv.invoice_date,
                inv.document_type.value if hasattr(inv.document_type, "value") else str(inv.document_type),
                inv.currency,
                float(inv.total_amount),
                inv.buyer_tax_id,
                inv.buyer_name,
                inv.seller_tax_id,
                inv.seller_name,
                category_val,
                inv.purpose,
                inv.payment_method,
                r.status.value if hasattr(r.status, "value") else str(r.status),
                issues_json,
                file_name,
                user_id,
                stored_path,
                mime_type,
                extracted_json,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "SUBMITTED",
                applicant_name,
                applicant_email,
            ))
            inserted += 1
    conn.close()
    return inserted


def _build_history_filters(
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    """Build parameterized SQL WHERE clause for audit history queries."""
    clauses: List[str] = []
    params: List[Any] = []
    if user_id is not None:
        clauses.append("a.user_id = ?")
        params.append(user_id)
    if status:
        clauses.append("a.audit_status = ?")
        params.append(status.strip().upper())
    if date_from:
        clauses.append("substr(COALESCE(NULLIF(a.invoice_date, ''), a.created_at), 1, 10) >= ?")
        params.append(date_from.strip())
    if date_to:
        clauses.append("substr(COALESCE(NULLIF(a.invoice_date, ''), a.created_at), 1, 10) <= ?")
        params.append(date_to.strip())
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def _hydrate_history_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert raw SQLite audit record row into API-friendly dictionary."""
    item = dict(row)
    if item.get("issues_json"):
        try:
            item["issues"] = json.loads(item["issues_json"])
        except Exception:
            item["issues"] = []
    else:
        item["issues"] = []
    item["has_file"] = bool(item.get("stored_file_path"))
    return item


def get_audit_history(
    limit: int = 50,
    offset: int = 0,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve audit history records ordered by latest first with optional filters."""
    init_db(db_path)
    where_sql, params = _build_history_filters(user_id=user_id, status=status, date_from=date_from, date_to=date_to)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT a.*, u.full_name AS uploader_name, u.email AS uploader_email, u.department AS uploader_department
        FROM audited_invoices a
        LEFT JOIN users u ON a.user_id = u.id
        {where_sql}
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
    """, (*params, limit, offset))
    rows = cursor.fetchall()
    conn.close()
    return [_hydrate_history_row(row) for row in rows]


def get_audit_record_by_id(
    record_id: int,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Retrieve a single audit record by primary key ID."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.*, u.full_name AS uploader_name, u.email AS uploader_email
        FROM audited_invoices a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE a.id = ?
        LIMIT 1
    """, (record_id,))
    row = cursor.fetchone()
    conn.close()
    return _hydrate_history_row(row) if row else None


def update_approval_status(
    record_id: int,
    status: str,
    reviewer_name: str,
    reviewer_email: str,
    review_notes: Optional[str] = None,
    db_path: Union[str, Path] = DEFAULT_DB_PATH
) -> bool:
    """Update approval status (APPROVED, RETURNED, REJECTED, SUBMITTED) with reviewer notes."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE audited_invoices
            SET approval_status = ?,
                reviewer_name = ?,
                reviewer_email = ?,
                reviewed_at = ?,
                review_notes = ?
            WHERE id = ?
        """, (status.upper(), reviewer_name, reviewer_email, now_str, review_notes, record_id))
        rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0


def mark_record_as_paid(
    record_id: int,
    paid_by: str,
    db_path: Union[str, Path] = DEFAULT_DB_PATH
) -> bool:
    """Mark an approved record as PAID with timestamp and payer info."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE audited_invoices
            SET approval_status = 'PAID',
                paid_at = ?,
                paid_by = ?
            WHERE id = ?
        """, (now_str, paid_by, record_id))
        rows_affected = cursor.rowcount
    conn.close()
    return rows_affected > 0


def get_approval_records(
    status_filter: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: Union[str, Path] = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """Retrieve records for approval workflow table with optional status filtering."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    if status_filter and status_filter.upper() != "ALL":
        cursor.execute("""
            SELECT * FROM audited_invoices
            WHERE UPPER(approval_status) = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (status_filter.upper(), limit, offset))
    else:
        cursor.execute("""
            SELECT * FROM audited_invoices
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))

    rows = cursor.fetchall()
    conn.close()
    return [_hydrate_history_row(row) for row in rows]


def get_approval_summary_stats(db_path: Union[str, Path] = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Summary metrics for Boss / Finance Approval Dashboard."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COALESCE(approval_status, 'SUBMITTED'), COUNT(*), SUM(total_amount)
        FROM audited_invoices
        GROUP BY COALESCE(approval_status, 'SUBMITTED')
    """)
    rows = cursor.fetchall()

    summary = {
        "submitted_count": 0,
        "submitted_amount": 0.0,
        "approved_count": 0,
        "approved_amount": 0.0,
        "returned_count": 0,
        "returned_amount": 0.0,
        "rejected_count": 0,
        "rejected_amount": 0.0,
        "paid_count": 0,
        "paid_amount": 0.0,
        "total_count": 0,
        "total_amount": 0.0,
    }

    for status, count, amt in rows:
        s = (status or "SUBMITTED").upper()
        amt_val = round(amt or 0.0, 2)
        summary["total_count"] += count
        summary["total_amount"] += amt_val

        if s == "SUBMITTED":
            summary["submitted_count"] = count
            summary["submitted_amount"] = amt_val
        elif s == "APPROVED":
            summary["approved_count"] = count
            summary["approved_amount"] = amt_val
        elif s == "RETURNED":
            summary["returned_count"] = count
            summary["returned_amount"] = amt_val
        elif s == "REJECTED":
            summary["rejected_count"] = count
            summary["rejected_amount"] = amt_val
        elif s == "PAID":
            summary["paid_count"] = count
            summary["paid_amount"] = amt_val

    conn.close()
    return summary


def get_audit_records_for_export(
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 500,
) -> List[AuditReport]:
    """Reconstruct AuditReport objects from persisted DB records for Excel export."""
    rows = get_audit_history(
        limit=limit,
        offset=0,
        db_path=db_path,
        user_id=user_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    reports: List[AuditReport] = []
    for item in reversed(rows):
        inv_data: Optional[InvoiceData] = None
        if item.get("extracted_json"):
            try:
                inv_data = InvoiceData.model_validate(json.loads(item["extracted_json"]))
            except Exception:
                inv_data = None
        if inv_data is None:
            inv_data = InvoiceData(
                document_type=item.get("document_type") or "general_receipt",
                invoice_number=item.get("invoice_number"),
                invoice_date=item.get("invoice_date"),
                buyer_tax_id=item.get("buyer_tax_id"),
                buyer_name=item.get("buyer_name"),
                seller_tax_id=item.get("seller_tax_id"),
                seller_name=item.get("seller_name"),
                currency=item.get("currency") or "NT$",
                total_amount=float(item.get("total_amount") or 0.0),
                expense_category=item.get("expense_category"),
                purpose=item.get("purpose"),
                payment_method=item.get("payment_method") or "Credit Card",
            )
        try:
            audit_status = AuditStatus(item.get("audit_status") or AuditStatus.WARNING.value)
        except ValueError:
            audit_status = AuditStatus.WARNING
        reports.append(AuditReport(
            file_path=item.get("stored_file_path") or item.get("file_name") or "",
            data=inv_data,
            status=audit_status,
            issues=item.get("issues") or [],
            audit_timestamp=str(item.get("created_at") or datetime.now().isoformat()),
        ))
    return reports


def get_audit_statistics(
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Calculate summary statistics for dashboard, optionally scoped to a user."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    where_sql = "WHERE user_id = ?" if user_id is not None else ""
    params: Tuple[Any, ...] = (user_id,) if user_id is not None else ()

    cursor.execute(f"SELECT COUNT(*), audit_status FROM audited_invoices {where_sql} GROUP BY audit_status", params)
    status_rows = cursor.fetchall()
    status_counts = {"APPROVED": 0, "WARNING": 0, "REJECTED": 0}
    total = 0
    for count, status in status_rows:
        status_counts[status] = count
        total += count

    cursor.execute(f"SELECT currency, SUM(total_amount) FROM audited_invoices {where_sql} GROUP BY currency", params)
    currency_rows = cursor.fetchall()
    currency_totals = {c: round(amt, 2) for c, amt in currency_rows if c}

    cursor.execute(f"SELECT MAX(created_at) FROM audited_invoices {where_sql}", params)
    latest_time = cursor.fetchone()[0]

    conn.close()

    return {
        "total_invoices": total,
        "status_counts": status_counts,
        "currency_totals": currency_totals,
        "latest_audit_time": latest_time
    }
