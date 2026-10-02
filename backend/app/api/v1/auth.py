import uuid
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import COMPANY_SUSPENDED, DB, CurrentUser
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.models import User
from app.schemas.identity import ChangePasswordRequest, LoginRequest, RefreshRequest, TokenPair, UserOut
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(str(user.id), "access", {"role": user.role}),
        refresh_token=create_token(str(user.id), "refresh"),
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, db: DB, request: Request) -> TokenPair:
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    if user.company is not None and not user.company.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, COMPANY_SUSPENDED)
    user.last_login_at = datetime.now(UTC)
    audit(db, user.id, "login", "user", user.id, ip_address=request.client.host if request.client else None)
    await db.commit()
    return _tokens(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: DB) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token, "refresh")
        user = await db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from None
    if user is None or not user.is_active or (user.company is not None and not user.company.is_active):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    return _tokens(user)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


@router.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordRequest, user: CurrentUser, db: DB) -> None:
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    audit(db, user.id, "change_password", "user", user.id)
    await db.commit()
