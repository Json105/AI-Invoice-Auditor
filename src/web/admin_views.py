"""SQLAdmin Configuration and Views for AI-Invoice Auditor."""

import os
from typing import Any
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from ..database import User, AuditRecordDB, SessionLocal, engine
from ..services.auth_service import authenticate_user, hash_password


class AdminAuth(AuthenticationBackend):
    """Authentication backend protecting /admin access."""

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        if not username or not password:
            return False

        with SessionLocal() as db:
            user = authenticate_user(db, username, password)
            if user and user.role in ("admin", "accountant", "manager"):
                request.session.update({
                    "admin_user": user.email,
                    "admin_role": user.role,
                    "admin_name": user.full_name
                })
                return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        admin_user = request.session.get("admin_user")
        if not admin_user:
            return False
        return True


class UserAdmin(ModelView, model=User):
    """Admin View for User and RBAC Role Management."""
    name = "使用者與權限"
    name_plural = "使用者與權限管理"
    icon = "fa-solid fa-users-gear"

    column_list = [
        User.id,
        User.email,
        User.full_name,
        User.role,
        User.department,
        User.is_active,
        User.created_at,
    ]
    column_searchable_list = [User.email, User.full_name, User.department]
    column_sortable_list = [User.id, User.email, User.role, User.created_at]
    column_filters = [User.role, User.department, User.is_active]

    form_columns = [
        User.email,
        User.full_name,
        User.hashed_password,
        User.role,
        User.department,
        User.is_active,
    ]
    column_labels = {
        User.id: "ID",
        User.email: "公司信箱",
        User.full_name: "同仁姓名",
        User.hashed_password: "登入密碼 (輸入明文將自動加密)",
        User.role: "角色權限",
        User.department: "歸屬部門",
        User.is_active: "帳號啟用",
        User.created_at: "建立時間",
    }

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """Normalize email and auto-hash plaintext passwords before saving."""
        email = data.get("email") or getattr(model, "email", None)
        if email:
            normalized_email = str(email).strip().lower()
            data["email"] = normalized_email
            model.email = normalized_email

        raw_password = data.get("hashed_password")
        if raw_password:
            pw_str = str(raw_password)
            # Only hash if admin entered plaintext rather than keeping existing bcrypt hash
            if not pw_str.startswith(("$2a$", "$2b$", "$2y$")):
                hashed = hash_password(pw_str)
                data["hashed_password"] = hashed
                model.hashed_password = hashed


class AuditRecordAdmin(ModelView, model=AuditRecordDB):
    """Admin View for Audited Invoices, Approval Lifecycle, and Fraud Review."""
    name = "報銷憑證"
    name_plural = "報銷憑證與審批總覽"
    icon = "fa-solid fa-file-invoice-dollar"

    column_list = [
        AuditRecordDB.id,
        AuditRecordDB.user_id,
        AuditRecordDB.invoice_number,
        AuditRecordDB.document_type,
        AuditRecordDB.applicant_name,
        AuditRecordDB.total_amount,
        AuditRecordDB.currency,
        AuditRecordDB.audit_status,
        AuditRecordDB.approval_status,
        AuditRecordDB.reviewer_name,
        AuditRecordDB.stored_file_path,
        AuditRecordDB.created_at,
    ]
    column_searchable_list = [
        AuditRecordDB.invoice_number,
        AuditRecordDB.seller_name,
        AuditRecordDB.buyer_name,
        AuditRecordDB.applicant_name,
        AuditRecordDB.reviewer_name,
    ]
    column_sortable_list = [
        AuditRecordDB.id,
        AuditRecordDB.user_id,
        AuditRecordDB.total_amount,
        AuditRecordDB.created_at,
        AuditRecordDB.approval_status,
    ]
    column_filters = [
        AuditRecordDB.user_id,
        AuditRecordDB.approval_status,
        AuditRecordDB.audit_status,
        AuditRecordDB.currency,
        AuditRecordDB.document_type,
    ]
    form_columns = [
        AuditRecordDB.invoice_number,
        AuditRecordDB.invoice_date,
        AuditRecordDB.document_type,
        AuditRecordDB.currency,
        AuditRecordDB.total_amount,
        AuditRecordDB.seller_name,
        AuditRecordDB.purpose,
        AuditRecordDB.audit_status,
        AuditRecordDB.approval_status,
        AuditRecordDB.applicant_name,
        AuditRecordDB.applicant_email,
        AuditRecordDB.reviewer_name,
        AuditRecordDB.review_notes,
        AuditRecordDB.paid_by,
    ]
    column_labels = {
        AuditRecordDB.id: "序號",
        AuditRecordDB.user_id: "員工ID",
        AuditRecordDB.invoice_number: "發票/票據號碼",
        AuditRecordDB.document_type: "單據類型",
        AuditRecordDB.seller_name: "開立廠商",
        AuditRecordDB.currency: "幣別",
        AuditRecordDB.total_amount: "報銷金額",
        AuditRecordDB.audit_status: "AI 稽核結果",
        AuditRecordDB.approval_status: "財務審批狀態",
        AuditRecordDB.applicant_name: "報銷申請人",
        AuditRecordDB.applicant_email: "申請人信箱",
        AuditRecordDB.reviewer_name: "審核人",
        AuditRecordDB.reviewer_email: "審核人信箱",
        AuditRecordDB.reviewed_at: "審核時間",
        AuditRecordDB.review_notes: "審核意見/退件理由",
        AuditRecordDB.paid_at: "撥款時間",
        AuditRecordDB.paid_by: "撥款經辦人",
        AuditRecordDB.stored_file_path: "原圖路徑",
        AuditRecordDB.created_at: "上傳日期",
    }


def setup_admin(app):
    """Mount SQLAdmin onto the FastAPI application."""
    session_secret = os.getenv("ADMIN_SESSION_SECRET", "ai-invoice-auditor-admin-session-secret-key-2026")
    auth_backend = AdminAuth(secret_key=session_secret)
    admin = Admin(
        app,
        engine,
        authentication_backend=auth_backend,
        title="AI-Invoice Auditor | 企業管理後台",
        logo_url=None,
    )
    admin.add_view(UserAdmin)
    admin.add_view(AuditRecordAdmin)
    return admin
