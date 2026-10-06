import uuid
from typing import Annotated, Any, TypeVar

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models import User
from app.models.enums import UserRole

DB = Annotated[AsyncSession, Depends(get_db)]
_bearer = HTTPBearer(auto_error=False)
T = TypeVar("T")

COMPANY_SUSPENDED = "This company account is suspended. Please contact the platform administrator."


async def get_current_user(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}
    )
    if creds is None:
        raise unauthorized
    try:
        payload = decode_token(creds.credentials, "access")
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized from None
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    if user.company is not None and not user.company.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, COMPANY_SUSPENDED)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_company_user(user: CurrentUser) -> User:
    """Tenant endpoints: the caller must belong to a company; its id scopes every query."""
    if user.company_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Platform admins manage companies. Sign in with a company account for this."
        )
    return user


def require_roles(*roles: UserRole):
    async def checker(user: CurrentUser) -> User:
        # Only the platform super admin may be without a company.
        if user.role not in roles or (user.role != UserRole.SUPER_ADMIN and user.company_id is None):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return checker


TenantUser = Annotated[User, Depends(require_company_user)]  # any role inside a company
AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]  # company admin
ManagerUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.SUPERVISOR))]
SuperAdmin = Annotated[User, Depends(require_roles(UserRole.SUPER_ADMIN))]  # platform owner


class Pagination:
    def __init__(self, page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=200)) -> None:
        self.page = page
        self.size = size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


PageParams = Annotated[Pagination, Depends()]


def not_found(what: str = "Resource") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


async def owned(db: AsyncSession, model: type[T], obj_id: Any, user: User, what: str = "Resource") -> T:
    """Load a tenant-owned row for the caller. Another company's row is reported as not found, so
    ids can't be probed across companies."""
    obj = await db.get(model, obj_id)
    if obj is None or obj.company_id != user.company_id:
        raise not_found(what)
    return obj
