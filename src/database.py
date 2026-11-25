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


class ExpenseTripDB(Base):
    """SQLAlchemy ORM Model grouping multi-day business trip receipts together."""
    __tablename__ = "expense_trips"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trip_code = Column(String(40), unique=True, index=True, nullable=False)
    title = Column(String(200), nullable=False)
    destination = Column(String(120), nullable=True)
    start_date = Column(String(20), nullable=True)
    end_date = Column(String(20), nullable=True)
    purpose = Column(Text, nullable=True)
    advance_payment = Column(Float, default=0.0, nullable=False)
    user_id = Column(Integer, index=True, nullable=True)
    applicant_name = Column(String(100), default="林專員 (業務同仁)")
    applicant_email = Column(String(255), default="employee@company.local")
    status = Column(String(30), default="DRAFT", index=True, nullable=False)  # DRAFT, SUBMITTED, APPROVED, RETURNED, REJECTED, PAID
    reviewer_name = Column(String(100), nullable=True)
    reviewer_email = Column(String(255), nullable=True)
    review_notes = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    paid_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ExpenseTrip {self.trip_code} [{self.status}]>"


class AuditRecordDB(Base):
    """SQLAlchemy ORM Model for Audited Invoices, mapped to SQLAdmin."""
    __tablename__ = "audited_invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trip_id = Column(Integer, index=True, nullable=True)
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
    exchange_rate = Column(Float, nullable=True)
    exchange_rate_source = Column(String(120), nullable=True)
    exchange_rate_date = Column(String(20), nullable=True)
    ntd_amount = Column(Float, nullable=True)
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
            ("trip_id", "INTEGER"),
            ("user_id", "INTEGER"),
            ("stored_file_path", "VARCHAR(500)"),
            ("file_mime_type", "VARCHAR(100)"),
            ("extracted_json", "TEXT"),
            ("exchange_rate", "FLOAT"),
            ("exchange_rate_source", "VARCHAR(120)"),
            ("exchange_rate_date", "VARCHAR(20)"),
            ("ntd_amount", "FLOAT"),
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
    trip_id: Optional[int] = None,
    initial_approval_status: Optional[str] = None,
) -> int:
    """Persist audit reports to the database with optional trip bundle binding."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    inserted = 0
    file_map = stored_files or {}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with conn:
        resolved_status = initial_approval_status
        if not resolved_status:
            if trip_id is not None:
                row = conn.execute(
                    "SELECT status FROM expense_trips WHERE id = ? LIMIT 1",
                    (trip_id,),
                ).fetchone()
                trip_status = (row[0] or "DRAFT").upper() if row else "DRAFT"
                # Draft trips keep newly scanned receipts in DRAFT until user submits the bundle
                resolved_status = "DRAFT" if trip_status == "DRAFT" else "SUBMITTED"
            else:
                resolved_status = "SUBMITTED"

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
            ex_rate = float(inv.exchange_rate) if getattr(inv, "exchange_rate", None) else None
            ex_source = getattr(inv, "exchange_rate_source", None)
            ex_date = getattr(inv, "exchange_rate_date", None)
            ntd_val = float(inv.ntd_amount) if getattr(inv, "ntd_amount", None) is not None else (
                round(float(inv.total_amount) * ex_rate, 2) if ex_rate else None
            )

            conn.execute("""
                INSERT INTO audited_invoices (
                    trip_id, invoice_number, invoice_date, document_type, currency,
                    total_amount, buyer_tax_id, buyer_name, seller_tax_id,
                    seller_name, expense_category, purpose, payment_method,
                    exchange_rate, exchange_rate_source, exchange_rate_date, ntd_amount,
                    audit_status, issues_json, file_name,
                    user_id, stored_file_path, file_mime_type, extracted_json,
                    created_at, approval_status, applicant_name, applicant_email
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trip_id,
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
                ex_rate,
                ex_source,
                ex_date,
                ntd_val,
                r.status.value if hasattr(r.status, "value") else str(r.status),
                issues_json,
                file_name,
                user_id,
                stored_path,
                mime_type,
                extracted_json,
                now_str,
                resolved_status,
                applicant_name,
                applicant_email,
            ))
            inserted += 1

        if trip_id is not None and inserted > 0:
            conn.execute(
                "UPDATE expense_trips SET updated_at = ? WHERE id = ?",
                (now_str, trip_id),
            )
    conn.close()
    return inserted


def _build_history_filters(
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    trip_id: Optional[int] = None,
) -> Tuple[str, List[Any]]:
    """Build parameterized SQL WHERE clause for audit history queries."""
    clauses: List[str] = []
    params: List[Any] = []
    if user_id is not None:
        clauses.append("a.user_id = ?")
        params.append(user_id)
    if trip_id is not None:
        clauses.append("a.trip_id = ?")
        params.append(trip_id)
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
    trip_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Retrieve audit history records ordered by latest first with optional filters."""
    init_db(db_path)
    where_sql, params = _build_history_filters(
        user_id=user_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        trip_id=trip_id,
    )
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT a.*,
               u.full_name AS uploader_name,
               u.email AS uploader_email,
               u.department AS uploader_department,
               t.trip_code AS trip_code,
               t.title AS trip_title,
               t.status AS trip_status
        FROM audited_invoices a
        LEFT JOIN users u ON a.user_id = u.id
        LEFT JOIN expense_trips t ON a.trip_id = t.id
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
        SELECT a.*,
               u.full_name AS uploader_name,
               u.email AS uploader_email,
               t.trip_code AS trip_code,
               t.title AS trip_title,
               t.status AS trip_status
        FROM audited_invoices a
        LEFT JOIN users u ON a.user_id = u.id
        LEFT JOIN expense_trips t ON a.trip_id = t.id
        WHERE a.id = ?
        LIMIT 1
    """, (record_id,))
    row = cursor.fetchone()
    conn.close()
    return _hydrate_history_row(row) if row else None


def update_record_exchange_rate(
    record_id: int,
    exchange_rate: float,
    exchange_rate_source: str,
    exchange_rate_date: Optional[str] = None,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Persist per-record exchange rate override (manual or dated BOT lookup) and sync NTD amount."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    row = conn.execute(
        "SELECT id, trip_id, total_amount, extracted_json FROM audited_invoices WHERE id = ? LIMIT 1",
        (record_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None

    rate_val = round(float(exchange_rate), 6)
    amt_val = float(row["total_amount"] or 0.0)
    ntd_val = round(amt_val * rate_val, 2)

    updated_json = row["extracted_json"]
    if updated_json:
        try:
            payload = json.loads(updated_json)
            if isinstance(payload, dict):
                payload["exchange_rate"] = rate_val
                payload["exchange_rate_source"] = exchange_rate_source
                payload["exchange_rate_date"] = exchange_rate_date
                payload["ntd_amount"] = ntd_val
                updated_json = json.dumps(payload, ensure_ascii=False)
        except Exception:
            pass

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        conn.execute(
            """
            UPDATE audited_invoices
            SET exchange_rate = ?,
                exchange_rate_source = ?,
                exchange_rate_date = ?,
                ntd_amount = ?,
                extracted_json = ?
            WHERE id = ?
            """,
            (rate_val, exchange_rate_source, exchange_rate_date, ntd_val, updated_json, record_id),
        )
        if row["trip_id"] is not None:
            conn.execute(
                "UPDATE expense_trips SET updated_at = ? WHERE id = ?",
                (now_str, row["trip_id"]),
            )
    conn.close()
    return get_audit_record_by_id(record_id, db_path=db_path)


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
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
    trip_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Retrieve records for approval workflow table with optional status, user, and trip filtering."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    clauses: List[str] = []
    params: List[Any] = []
    if user_id is not None:
        clauses.append("a.user_id = ?")
        params.append(user_id)
    if trip_id is not None:
        clauses.append("a.trip_id = ?")
        params.append(trip_id)

    if status_filter and status_filter.upper() != "ALL":
        clauses.append("UPPER(COALESCE(a.approval_status, 'SUBMITTED')) = ?")
        params.append(status_filter.strip().upper())
    elif trip_id is None:
        # Exclude unsubmitted DRAFT trip receipts from the finance review queue
        clauses.append("UPPER(COALESCE(a.approval_status, 'SUBMITTED')) != 'DRAFT'")

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor.execute(f"""
        SELECT a.*,
               t.trip_code AS trip_code,
               t.title AS trip_title,
               t.status AS trip_status
        FROM audited_invoices a
        LEFT JOIN expense_trips t ON a.trip_id = t.id
        {where_sql}
        ORDER BY a.id DESC
        LIMIT ? OFFSET ?
    """, (*params, limit, offset))

    rows = cursor.fetchall()
    conn.close()
    return [_hydrate_history_row(row) for row in rows]


def get_approval_summary_stats(
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Summary metrics for Approval Dashboard (ignores unsubmitted DRAFTs, optionally scoped)."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    if user_id is not None:
        cursor.execute("""
            SELECT COALESCE(approval_status, 'SUBMITTED'), COUNT(*), SUM(total_amount)
            FROM audited_invoices
            WHERE UPPER(COALESCE(approval_status, 'SUBMITTED')) != 'DRAFT'
              AND user_id = ?
            GROUP BY COALESCE(approval_status, 'SUBMITTED')
        """, (user_id,))
    else:
        cursor.execute("""
            SELECT COALESCE(approval_status, 'SUBMITTED'), COUNT(*), SUM(total_amount)
            FROM audited_invoices
            WHERE UPPER(COALESCE(approval_status, 'SUBMITTED')) != 'DRAFT'
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
    trip_id: Optional[int] = None,
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
        trip_id=trip_id,
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
        if item.get("exchange_rate") is not None:
            inv_data.exchange_rate = float(item["exchange_rate"])
        if item.get("exchange_rate_source") is not None:
            inv_data.exchange_rate_source = item["exchange_rate_source"]
        if item.get("exchange_rate_date") is not None:
            inv_data.exchange_rate_date = item["exchange_rate_date"]
        if item.get("ntd_amount") is not None:
            inv_data.ntd_amount = float(item["ntd_amount"])
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


# ==========================================================================
# Expense Trip Bundle (出差報銷專案) Operations
# ==========================================================================

def _generate_trip_code(conn: sqlite3.Connection) -> str:
    """Generate a sequential monthly trip code such as TRIP-202611-001."""
    prefix = f"TRIP-{datetime.now().strftime('%Y%m')}-"
    rows = conn.execute(
        "SELECT trip_code FROM expense_trips WHERE trip_code LIKE ?",
        (f"{prefix}%",),
    ).fetchall()
    max_seq = 0
    for row in rows:
        code = str(row[0] or "")
        tail = code[len(prefix):]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    seq = max_seq + 1
    while True:
        candidate = f"{prefix}{seq:03d}"
        exists = conn.execute(
            "SELECT 1 FROM expense_trips WHERE trip_code = ? LIMIT 1",
            (candidate,),
        ).fetchone()
        if not exists:
            return candidate
        seq += 1


def _enrich_trip_summary(trip_dict: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Attach receipt counts, approval breakdown, currency totals, and NTD estimate."""
    from .services.fx_service import fx_service

    approved_audit = 0
    warning_audit = 0
    rejected_audit = 0
    submitted_rec = 0
    returned_rec = 0
    approved_rec = 0
    paid_rec = 0
    currency_totals: Dict[str, float] = {}
    estimated_ntd = 0.0

    for rec in records:
        audit_st = (rec.get("audit_status") or "").upper()
        if audit_st == "APPROVED":
            approved_audit += 1
        elif audit_st == "WARNING":
            warning_audit += 1
        elif audit_st == "REJECTED":
            rejected_audit += 1

        appr_st = (rec.get("approval_status") or "SUBMITTED").upper()
        if appr_st == "SUBMITTED":
            submitted_rec += 1
        elif appr_st == "RETURNED":
            returned_rec += 1
        elif appr_st == "APPROVED":
            approved_rec += 1
        elif appr_st == "PAID":
            paid_rec += 1

        cur = rec.get("currency") or "NT$"
        amt = float(rec.get("total_amount") or 0.0)
        currency_totals[cur] = round(currency_totals.get(cur, 0.0) + amt, 2)
        if rec.get("exchange_rate") is not None and float(rec["exchange_rate"]) > 0:
            ntd_val = round(amt * float(rec["exchange_rate"]), 2)
        elif rec.get("ntd_amount") is not None:
            ntd_val = float(rec["ntd_amount"])
        else:
            ntd_val, _ = fx_service.convert_to_ntd(amt, cur)
        estimated_ntd += ntd_val

    enriched = dict(trip_dict)
    enriched["receipt_count"] = len(records)
    enriched["approved_audit_count"] = approved_audit
    enriched["warning_audit_count"] = warning_audit
    enriched["rejected_audit_count"] = rejected_audit
    enriched["submitted_receipt_count"] = submitted_rec
    enriched["returned_receipt_count"] = returned_rec
    enriched["approved_receipt_count"] = approved_rec
    enriched["paid_receipt_count"] = paid_rec
    enriched["currency_totals"] = currency_totals
    enriched["estimated_total_ntd"] = round(estimated_ntd, 2)
    return enriched


def create_expense_trip(
    title: str,
    user_id: Optional[int],
    applicant_name: str,
    applicant_email: str,
    destination: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    purpose: Optional[str] = None,
    advance_payment: float = 0.0,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Create a new ExpenseTripDB record in DRAFT status."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        trip_code = _generate_trip_code(conn)
        cursor = conn.execute("""
            INSERT INTO expense_trips (
                trip_code, title, destination, start_date, end_date,
                purpose, advance_payment, user_id, applicant_name, applicant_email,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)
        """, (
            trip_code,
            title.strip(),
            destination.strip() if destination else None,
            start_date.strip() if start_date else None,
            end_date.strip() if end_date else None,
            purpose.strip() if purpose else None,
            float(advance_payment or 0.0),
            user_id,
            applicant_name,
            applicant_email,
            now_str,
            now_str,
        ))
        new_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM expense_trips WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    return _enrich_trip_summary(dict(row), [])


def get_expense_trip_by_id(
    trip_id: int,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Fetch raw expense trip row as dict without child records."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    row = conn.execute("SELECT * FROM expense_trips WHERE id = ? LIMIT 1", (trip_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_expense_trips(
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """List expense trips ordered newest first, enriched with receipt totals."""
    init_db(db_path)
    conn = get_db_connection(db_path)

    clauses: List[str] = []
    params: List[Any] = []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if status and status.strip().upper() != "ALL":
        clauses.append("UPPER(status) = ?")
        params.append(status.strip().upper())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    trip_rows = conn.execute(
        f"SELECT * FROM expense_trips {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()

    trips = [dict(r) for r in trip_rows]
    records_by_trip: Dict[int, List[Dict[str, Any]]] = {}
    if trips:
        trip_ids = [t["id"] for t in trips]
        placeholders = ",".join("?" for _ in trip_ids)
        rec_rows = conn.execute(
            f"SELECT * FROM audited_invoices WHERE trip_id IN ({placeholders}) ORDER BY id ASC",
            trip_ids,
        ).fetchall()
        for rr in rec_rows:
            d = _hydrate_history_row(rr)
            records_by_trip.setdefault(d["trip_id"], []).append(d)

    conn.close()
    return [_enrich_trip_summary(t, records_by_trip.get(t["id"], [])) for t in trips]


def get_expense_trip_detail(
    trip_id: int,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Fetch a single trip with full summary metrics and chronological child records."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    trip_row = conn.execute("SELECT * FROM expense_trips WHERE id = ? LIMIT 1", (trip_id,)).fetchone()
    if not trip_row:
        conn.close()
        return None

    rec_rows = conn.execute("""
        SELECT a.*,
               u.full_name AS uploader_name,
               u.email AS uploader_email
        FROM audited_invoices a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE a.trip_id = ?
        ORDER BY a.id ASC
    """, (trip_id,)).fetchall()
    conn.close()

    records = [_hydrate_history_row(r) for r in rec_rows]
    detail = _enrich_trip_summary(dict(trip_row), records)
    detail["records"] = records
    return detail


def update_expense_trip(
    trip_id: int,
    updates: Dict[str, Any],
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Update whitelisted metadata fields on an expense trip."""
    init_db(db_path)
    allowed = {"title", "destination", "start_date", "end_date", "purpose", "advance_payment"}
    fields: List[str] = []
    values: List[Any] = []
    for key, val in updates.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        if key == "advance_payment":
            values.append(float(val or 0.0))
        elif isinstance(val, str):
            cleaned = val.strip()
            values.append(cleaned if cleaned or key == "title" else None)
        else:
            values.append(val)

    if not fields:
        return get_expense_trip_detail(trip_id, db_path=db_path)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields.append("updated_at = ?")
    values.append(now_str)
    values.append(trip_id)

    conn = get_db_connection(db_path)
    with conn:
        cur = conn.execute(
            f"UPDATE expense_trips SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        updated = cur.rowcount > 0
    conn.close()
    return get_expense_trip_detail(trip_id, db_path=db_path) if updated else None


def delete_expense_trip(
    trip_id: int,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> bool:
    """Detach child audited invoices (preserving audit history) and delete trip row."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            """
            UPDATE audited_invoices
            SET trip_id = NULL,
                approval_status = CASE
                    WHEN UPPER(COALESCE(approval_status, 'SUBMITTED')) = 'DRAFT' THEN 'SUBMITTED'
                    ELSE approval_status
                END
            WHERE trip_id = ?
            """,
            (trip_id,),
        )
        cur = conn.execute("DELETE FROM expense_trips WHERE id = ?", (trip_id,))
        deleted = cur.rowcount > 0
    conn.close()
    return deleted


def assign_records_to_trip(
    record_ids: List[int],
    trip_id: Optional[int],
    user_id: Optional[int],
    is_privileged: bool,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> int:
    """Assign existing audit records into a trip bundle or detach them (trip_id=None)."""
    if not record_ids:
        return 0

    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ",".join("?" for _ in record_ids)

    with conn:
        if trip_id is not None:
            trip_row = conn.execute(
                "SELECT status FROM expense_trips WHERE id = ? LIMIT 1",
                (trip_id,),
            ).fetchone()
            trip_status = (trip_row[0] or "DRAFT").upper() if trip_row else "DRAFT"
            status_expr = (
                "CASE WHEN UPPER(COALESCE(approval_status, 'SUBMITTED')) IN ('DRAFT', 'SUBMITTED') "
                "THEN 'DRAFT' ELSE approval_status END"
                if trip_status == "DRAFT"
                else "CASE WHEN UPPER(COALESCE(approval_status, 'SUBMITTED')) = 'DRAFT' "
                     "THEN 'SUBMITTED' ELSE approval_status END"
            )
            params: List[Any] = [trip_id, *record_ids]
        else:
            status_expr = (
                "CASE WHEN UPPER(COALESCE(approval_status, 'SUBMITTED')) = 'DRAFT' "
                "THEN 'SUBMITTED' ELSE approval_status END"
            )
            params = [None, *record_ids]

        owner_clause = ""
        if not is_privileged and user_id is not None:
            owner_clause = " AND user_id = ?"
            params.append(user_id)

        cur = conn.execute(
            f"""
            UPDATE audited_invoices
            SET trip_id = ?,
                approval_status = {status_expr}
            WHERE id IN ({placeholders}){owner_clause}
            """,
            params,
        )
        updated_count = cur.rowcount

        if trip_id is not None and updated_count > 0:
            conn.execute(
                "UPDATE expense_trips SET updated_at = ? WHERE id = ?",
                (now_str, trip_id),
            )

    conn.close()
    return updated_count


def submit_expense_trip(
    trip_id: int,
    applicant_name: str,
    applicant_email: str,
    notes: Optional[str] = None,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Submit a trip bundle for finance review and move DRAFT/RETURNED receipts to SUBMITTED."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        count_row = conn.execute(
            "SELECT COUNT(*) FROM audited_invoices WHERE trip_id = ?",
            (trip_id,),
        ).fetchone()
        if not count_row or count_row[0] == 0:
            raise ValueError("此出差專案尚無任何單據，請先上傳發票後再送審")

        conn.execute(
            """
            UPDATE expense_trips
            SET status = 'SUBMITTED',
                applicant_name = COALESCE(NULLIF(?, ''), applicant_name),
                applicant_email = COALESCE(NULLIF(?, ''), applicant_email),
                review_notes = COALESCE(?, review_notes),
                submitted_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (applicant_name, applicant_email, notes, now_str, now_str, trip_id),
        )
        conn.execute(
            """
            UPDATE audited_invoices
            SET approval_status = 'SUBMITTED',
                applicant_name = COALESCE(NULLIF(?, ''), applicant_name),
                applicant_email = COALESCE(NULLIF(?, ''), applicant_email)
            WHERE trip_id = ?
              AND UPPER(COALESCE(approval_status, 'SUBMITTED')) IN ('DRAFT', 'RETURNED')
            """,
            (applicant_name, applicant_email, trip_id),
        )
    conn.close()
    detail = get_expense_trip_detail(trip_id, db_path=db_path)
    if not detail:
        raise ValueError(f"找不到出差專案 #{trip_id}")
    return detail


def review_expense_trip(
    trip_id: int,
    action: str,
    reviewer_name: str,
    reviewer_email: str,
    review_notes: Optional[str] = None,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Batch review an entire trip bundle (approve, return, or reject)."""
    status_map = {"approve": "APPROVED", "return": "RETURNED", "reject": "REJECTED"}
    new_status = status_map.get(action.lower())
    if not new_status:
        return None

    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with conn:
        cur = conn.execute(
            """
            UPDATE expense_trips
            SET status = ?,
                reviewer_name = ?,
                reviewer_email = ?,
                review_notes = ?,
                reviewed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (new_status, reviewer_name, reviewer_email, review_notes, now_str, now_str, trip_id),
        )
        if cur.rowcount == 0:
            conn.close()
            return None

        if new_status == "APPROVED":
            conn.execute(
                """
                UPDATE audited_invoices
                SET approval_status = 'APPROVED',
                    reviewer_name = ?,
                    reviewer_email = ?,
                    reviewed_at = ?,
                    review_notes = COALESCE(?, review_notes)
                WHERE trip_id = ?
                  AND UPPER(COALESCE(approval_status, 'SUBMITTED')) IN ('SUBMITTED', 'RETURNED', 'DRAFT')
                """,
                (reviewer_name, reviewer_email, now_str, review_notes, trip_id),
            )
        elif new_status == "RETURNED":
            conn.execute(
                """
                UPDATE audited_invoices
                SET approval_status = 'RETURNED',
                    reviewer_name = ?,
                    reviewer_email = ?,
                    reviewed_at = ?,
                    review_notes = ?
                WHERE trip_id = ?
                  AND UPPER(COALESCE(approval_status, 'SUBMITTED')) = 'SUBMITTED'
                """,
                (reviewer_name, reviewer_email, now_str, review_notes, trip_id),
            )
        elif new_status == "REJECTED":
            conn.execute(
                """
                UPDATE audited_invoices
                SET approval_status = 'REJECTED',
                    reviewer_name = ?,
                    reviewer_email = ?,
                    reviewed_at = ?,
                    review_notes = ?
                WHERE trip_id = ?
                  AND UPPER(COALESCE(approval_status, 'SUBMITTED')) != 'PAID'
                """,
                (reviewer_name, reviewer_email, now_str, review_notes, trip_id),
            )

    conn.close()
    return get_expense_trip_detail(trip_id, db_path=db_path)


def mark_trip_as_paid(
    trip_id: int,
    paid_by: str,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Mark a trip bundle and all non-rejected claims within it as PAID."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        cur = conn.execute(
            """
            UPDATE expense_trips
            SET status = 'PAID',
                paid_at = ?,
                paid_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now_str, paid_by, now_str, trip_id),
        )
        if cur.rowcount == 0:
            conn.close()
            return None

        conn.execute(
            """
            UPDATE audited_invoices
            SET approval_status = 'PAID',
                paid_at = ?,
                paid_by = ?
            WHERE trip_id = ?
              AND UPPER(COALESCE(approval_status, 'SUBMITTED')) IN ('APPROVED', 'SUBMITTED')
            """,
            (now_str, paid_by, trip_id),
        )
    conn.close()
    return get_expense_trip_detail(trip_id, db_path=db_path)


def sync_trip_status_from_records(
    trip_id: int,
    db_path: Union[str, Path] = DEFAULT_DB_PATH,
) -> Optional[str]:
    """Derive and synchronize parent trip status when individual child receipts change."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    trip_row = conn.execute(
        "SELECT status FROM expense_trips WHERE id = ? LIMIT 1",
        (trip_id,),
    ).fetchone()
    if not trip_row:
        conn.close()
        return None

    current_status = (trip_row[0] or "DRAFT").upper()
    rows = conn.execute(
        "SELECT approval_status FROM audited_invoices WHERE trip_id = ?",
        (trip_id,),
    ).fetchall()
    if not rows:
        conn.close()
        return current_status

    statuses = [(r[0] or "SUBMITTED").upper() for r in rows]
    non_rejected = [s for s in statuses if s != "REJECTED"]

    if "RETURNED" in statuses:
        new_status = "RETURNED"
    elif all(s == "REJECTED" for s in statuses):
        new_status = "REJECTED"
    elif non_rejected and all(s == "PAID" for s in non_rejected):
        new_status = "PAID"
    elif non_rejected and all(s in ("APPROVED", "PAID") for s in non_rejected):
        new_status = "APPROVED"
    elif current_status == "RETURNED":
        # Employee resubmitted all returned receipts inside this trip
        new_status = "SUBMITTED"
    else:
        new_status = current_status

    if new_status != current_status:
        with conn:
            if new_status == "PAID":
                conn.execute(
                    "UPDATE expense_trips SET status = ?, paid_at = COALESCE(paid_at, ?), updated_at = ? WHERE id = ?",
                    (new_status, now_str, now_str, trip_id),
                )
            else:
                conn.execute(
                    "UPDATE expense_trips SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now_str, trip_id),
                )

    conn.close()
    return new_status
