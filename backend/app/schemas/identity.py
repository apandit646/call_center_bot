import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.schemas.common import ORMModel
from app.schemas.company import CompanyBrief


def _company_role(role: UserRole | None) -> UserRole | None:
    # Company admins manage their own staff; the platform role is never handed out through /users.
    if role == UserRole.SUPER_ADMIN:
        raise ValueError("role must be admin, supervisor or agent")
    return role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class AgentProfileSchema(ORMModel):
    domain_codes: list[str] = []
    max_open_tickets: int = Field(default=20, ge=1, le=500)
    is_available: bool = True


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    company: CompanyBrief | None = None  # None for the platform super admin
    agent_profile: AgentProfileSchema | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8)
    role: UserRole = UserRole.AGENT
    agent_profile: AgentProfileSchema | None = None

    _role = field_validator("role")(_company_role)


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)
    agent_profile: AgentProfileSchema | None = None

    _role = field_validator("role")(_company_role)
