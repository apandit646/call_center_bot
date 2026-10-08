"""Problem reports about the platform itself (not customer tickets).

    an employee (agent / supervisor)  -> reports to their company's admin      level "company"
    a company admin                   -> reports to the platform admin         level "platform"

A company admin who cannot solve an employee's report can escalate it: that creates a platform-level
report linked to the original, so the platform admin sees it and the employee's report shows where it went.
"""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Identity, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPk
from app.models.identity import User


_last_moment = datetime.min.replace(tzinfo=UTC)


def _next_moment() -> datetime:
    """Now, but never the same as (or before) the moment handed out last: the clock can return the
    same value twice in a row, and two notes saved together must keep their order."""
    global _last_moment
    _last_moment = max(datetime.now(UTC), _last_moment + timedelta(microseconds=1))
    return _last_moment


class IssueReport(UUIDPk, Timestamps, Base):
    __tablename__ = "issue_reports"

    number: Mapped[int] = mapped_column(Integer, Identity(start=101), unique=True)
    # The reporter's company. Every report comes from inside a company.
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)  # company | platform: who it is addressed to
    category: Mapped[str] = mapped_column(String(32), default="other")
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low | normal | high
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(String(300))  # where in the portal the reporter was
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | in_progress | resolved | closed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An employee's report that the company admin passed on, and the platform report made for it.
    escalated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issue_reports.id", ondelete="SET NULL"), index=True
    )

    reporter: Mapped[User | None] = relationship(lazy="joined")
    comments: Mapped[list["IssueComment"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="IssueComment.created_at"
    )


class IssueComment(UUIDPk, Base):
    __tablename__ = "issue_comments"

    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("issue_reports.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(Text)
    # "status" notes are written by the system when someone changes the status or escalates.
    kind: Mapped[str] = mapped_column(String(16), default="reply")  # reply | status
    # Set here, not by the database: two notes saved together (a status change and its note) must
    # keep the order they were written in, and the database's clock is the same for both.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_next_moment, server_default=func.now()
    )

    author: Mapped[User | None] = relationship(lazy="joined")
