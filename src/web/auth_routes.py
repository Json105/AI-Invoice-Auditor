"""FastAPI endpoints for authentication, current user context, and pluggable OAuth status."""

import os
from typing import Optional, Dict, List
from fastapi import APIRouter, Depends, HTTPException, status, Header, Response, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db, User, SessionLocal
from ..services.auth_service import (
    authenticate_user,
    create_access_token,
    decode_access_token,
    get_user_by_email,
    hash_password,
    verify_password,
)

auth_router = APIRouter(prefix="/api/auth", tags=["Authentication"])
PRIVILEGED_ROLES = {"admin", "accountant", "manager"}


def is_privileged_user(user: User) -> bool:
    """Return True if user has finance/admin cross-employee visibility."""
    return (user.role or "").lower() in PRIVILEGED_ROLES


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    department: str
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Extract current user from Bearer token if present."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None
    user = get_user_by_email(db, payload["sub"])
    if not user or not user.is_active:
        return None
    return user


def get_current_user_required(
    user: Optional[User] = Depends(get_current_user_optional)
) -> User:
    """Dependency that requires an authenticated user."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="請先登入系統 (Authentication required)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@auth_router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate user with email and password, returning JWT access token."""
    user = authenticate_user(db, req.email, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="帳號或密碼錯誤，或帳號已被停用 (Invalid credentials or inactive account)",
        )

    token = create_access_token(data={
        "sub": user.email,
        "role": user.role,
        "name": user.full_name,
        "dept": user.department
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "department": user.department,
            "is_active": user.is_active,
        }
    }


@auth_router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user_required)):
    """Get current authenticated user profile."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "department": current_user.department,
        "is_active": current_user.is_active,
    }


@auth_router.post("/logout")
async def logout():
    """Logout endpoint."""
    return {"status": "success", "message": "已成功登出"}


@auth_router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """Allow logged-in user to change their own password."""
    db_user = db.query(User).filter(User.id == current_user.id).first()
    if not db_user or not verify_password(req.current_password, db_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="目前密碼不正確",
        )

    db_user.hashed_password = hash_password(req.new_password)
    db.commit()
    return {"status": "success", "message": "密碼已更新，下次請使用新密碼登入"}


@auth_router.get("/users", response_model=List[UserResponse])
async def list_users(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """List company users for privileged roles (admin/accountant/manager)."""
    if not is_privileged_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="僅管理員或財務主管可查詢員工名單",
        )
    users = db.query(User).order_by(User.id.asc()).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "department": u.department,
            "is_active": u.is_active,
        }
        for u in users
    ]


@auth_router.get("/oauth-status")
async def get_oauth_status():
    """Pluggable OAuth2 feature flag check.

    Returns whether Google Workspace or Azure AD is enabled via environment variables.
    """
    providers = []
    if os.getenv("GOOGLE_CLIENT_ID"):
        providers.append("google")
    if os.getenv("AZURE_CLIENT_ID"):
        providers.append("azure")

    return {
        "oauth_enabled": len(providers) > 0,
        "providers": providers,
        "hint": "若公司 IT 未開通 SSO，可直接使用內建帳密登入"
    }
