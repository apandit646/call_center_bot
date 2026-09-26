import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk
from app.models.enums import UserRole


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.AGENT)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent_profile: Mapped["AgentProfile | None"] = relationship(back_populates="user", uselist=False, lazy="selectin")


class AgentProfile(UUIDPk, Timestamps, Base):
    __tablename__ = "agent_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    # Domain codes this agent handles, e.g. ["billing", "refund"]. Empty = all domains.
    domain_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_open_tickets: Mapped[int] = mapped_column(Integer, default=20)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="agent_profile")
