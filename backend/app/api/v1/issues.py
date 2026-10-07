"""Report a problem with the platform.

Who a report goes to depends on who files it:
    agent / supervisor -> their company's admin      ("company" level)
    company admin      -> the platform admin         ("platform" level)

Who can see a report: the person who filed it, and whoever it is addressed to (the company's admins
for a company-level report, the platform admin for a platform-level one). Nobody else - one
company never sees another's reports.
"""
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, PageParams, not_found
from app.models import Company, IssueComment, IssueReport, User
from app.models.enums import UserRole
from app.schemas.common import ORMModel, Page
from app.schemas.support import UserBrief
from app.services.audit import audit

router = APIRouter(prefix="/issues", tags=["issue reports"])

Category = Literal["calls", "whatsapp", "email", "campaigns", "knowledge", "billing", "account", "bug", "other"]
Priority = Literal["low", "normal", "high"]
Status = Literal["open", "in_progress", "resolved", "closed"]
OPEN = ("open", "in_progress")


class IssueCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=5000)
    category: Category = "other"
    priority: Priority = "normal"
    page: str | None = Field(default=None, max_length=300)


class IssueUpdate(BaseModel):
    status: Status
    note: str | None = Field(default=None, max_length=2000)


class CommentCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class EscalateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class CommentOut(ORMModel):
    id: uuid.UUID
    author: UserBrief | None
    text: str
    kind: str
    created_at: datetime


class IssueOut(ORMModel):
    id: uuid.UUID
    number: int
    level: str  # company | platform
    category: str
    priority: str
    subject: str
    description: str
    page: str | None
    status: str
    reporter: UserBrief | None
    reporter_role: str | None = None
    company_id: uuid.UUID
    company_name: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    escalated_from_id: uuid.UUID | None
    escalated_to: dict | None = None  # {id, number, status}: the platform report made for this one
    comment_count: int = 0
    can_manage: bool = False  # the viewer is who this report is addressed to
    can_escalate: bool = False


class IssueDetail(IssueOut):
    comments: list[CommentOut] = []


def _level_for(user: User) -> str:
    """Who a new report from this user goes to."""
    return "platform" if user.role == UserRole.ADMIN else "company"


def _handles(user: User, report: IssueReport) -> bool:
    """Is this user the one the report is addressed to?"""
    if report.level == "platform":
        return user.role == UserRole.SUPER_ADMIN
    return user.role == UserRole.ADMIN and user.company_id == report.company_id


def _may_see(user: User, report: IssueReport) -> bool:
    return report.reporter_id == user.id or _handles(user, report)


def _inbox(user: User):
    """The reports addressed to this user (None = they handle none)."""
    if user.role == UserRole.SUPER_ADMIN:
        return IssueReport.level == "platform"
    if user.role == UserRole.ADMIN:
        return (IssueReport.level == "company") & (IssueReport.company_id == user.company_id)
    return None


async def _load(db: DB, report_id: uuid.UUID, user: User) -> IssueReport:
    report = await db.get(IssueReport, report_id)
    if report is None or not _may_see(user, report):
        raise not_found("Report")  # also for another company's report: do not reveal that it exists
    return report


async def _out(db: DB, report: IssueReport, user: User, detail: bool = False) -> IssueOut:
    cls = IssueDetail if detail else IssueOut
    out = cls.model_validate(report)
    out.reporter_role = report.reporter.role if report.reporter else None
    out.company_name = await db.scalar(select(Company.name).where(Company.id == report.company_id))
    out.comment_count = sum(1 for c in report.comments if c.kind == "reply")
    out.can_manage = _handles(user, report)
    child = await db.scalar(select(IssueReport).where(IssueReport.escalated_from_id == report.id))
    if child is not None:
        out.escalated_to = {"id": str(child.id), "number": child.number, "status": child.status}
    out.can_escalate = out.can_manage and report.level == "company" and child is None and report.status in OPEN
    return out


def _note(db: DB, report: IssueReport, user: User, text: str) -> None:
    db.add(IssueComment(report_id=report.id, author_id=user.id, text=text, kind="status"))


@router.get("/summary")
async def summary(db: DB, user: CurrentUser) -> dict[str, Any]:
    """Counts for the menu badge, and who a new report from this user goes to."""
    inbox = _inbox(user)
    waiting = await db.scalar(select(func.count(IssueReport.id)).where(inbox, IssueReport.status.in_(OPEN))) if inbox is not None else 0
    mine = await db.scalar(
        select(func.count(IssueReport.id)).where(IssueReport.reporter_id == user.id, IssueReport.status.in_(OPEN))
    )
    return {
        "inbox_open": waiting or 0, "mine_open": mine or 0, "has_inbox": inbox is not None,
        "can_report": user.role != UserRole.SUPER_ADMIN,
        "reports_go_to": "the platform administrator" if user.role == UserRole.ADMIN else "your company admin",
    }


@router.get("", response_model=Page[IssueOut])
async def list_issues(
    db: DB, user: CurrentUser, pg: PageParams,
    box: Literal["mine", "inbox"] = "mine",
    status_: Status | None = Query(None, alias="status"),
    open_only: bool = False,
):
    """box=mine: the reports I filed. box=inbox: the reports addressed to me (admins and the platform admin)."""
    if box == "inbox":
        where = _inbox(user)
        if where is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a company admin or the platform admin has an inbox")
    else:
        where = IssueReport.reporter_id == user.id
    stmt = select(IssueReport).where(where)
    if status_:
        stmt = stmt.where(IssueReport.status == status_)
    if open_only:
        stmt = stmt.where(IssueReport.status.in_(OPEN))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    # Open ones first, the most urgent at the top, then the newest.
    urgent = (IssueReport.priority == "high")
    rows = (
        await db.scalars(
            stmt.order_by(IssueReport.status.in_(OPEN).desc(), urgent.desc(), IssueReport.created_at.desc())
            .offset(pg.offset).limit(pg.size)
        )
    ).unique().all()
    return Page(items=[await _out(db, r, user) for r in rows], total=total, page=pg.page, size=pg.size)


@router.post("", response_model=IssueDetail, status_code=201)
async def report_issue(body: IssueCreate, db: DB, user: CurrentUser):
    """File a report. An employee's goes to their company admin; a company admin's goes to the platform admin."""
    if user.role == UserRole.SUPER_ADMIN or user.company_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The platform admin receives reports; there is nobody above to report to")
    report = IssueReport(
        company_id=user.company_id, reporter_id=user.id, level=_level_for(user), subject=body.subject.strip(),
        description=body.description.strip(), category=body.category, priority=body.priority, page=body.page,
    )
    db.add(report)
    await db.flush()
    audit(db, user.id, "report", "issue_report", report.id, {"level": report.level, "subject": report.subject})
    await db.commit()
    return await _out(db, await db.get(IssueReport, report.id, populate_existing=True), user, detail=True)


@router.get("/{report_id}", response_model=IssueDetail)
async def get_issue(report_id: uuid.UUID, db: DB, user: CurrentUser):
    return await _out(db, await _load(db, report_id, user), user, detail=True)


@router.post("/{report_id}/comments", response_model=IssueDetail)
async def add_comment(report_id: uuid.UUID, body: CommentCreate, db: DB, user: CurrentUser):
    """A reply on the report, from the person who filed it or the one handling it."""
    report = await _load(db, report_id, user)
    if report.status == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This report is closed. File a new one if the problem is back.")
    db.add(IssueComment(report_id=report.id, author_id=user.id, text=body.text.strip()))
    # The reporter writing on a resolved report means it is not resolved for them.
    if report.status == "resolved" and report.reporter_id == user.id and not _handles(user, report):
        report.status, report.resolved_at = "open", None
        _note(db, report, user, "Reopened by the reporter")
    report.updated_at = datetime.now(UTC)
    await db.commit()
    return await _out(db, await db.get(IssueReport, report.id, populate_existing=True), user, detail=True)


@router.patch("/{report_id}", response_model=IssueDetail)
async def set_status(report_id: uuid.UUID, body: IssueUpdate, db: DB, user: CurrentUser):
    """Change the status. Only whoever the report is addressed to may do this."""
    report = await _load(db, report_id, user)
    if not _handles(user, report):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person handling this report can change its status")
    if body.status != report.status:
        _note(db, report, user, f"Status: {report.status.replace('_', ' ')} → {body.status.replace('_', ' ')}")
        report.status = body.status
        report.resolved_at = datetime.now(UTC) if body.status in ("resolved", "closed") else None
        if report.escalated_from_id and body.status in ("resolved", "closed"):
            # Tell the employee's original report that the platform admin has dealt with it.
            original = await db.get(IssueReport, report.escalated_from_id)
            if original is not None:
                _note(db, original, user, f"The platform administrator marked report #{report.number} as {body.status}")
    if body.note and body.note.strip():
        db.add(IssueComment(report_id=report.id, author_id=user.id, text=body.note.strip()))
    audit(db, user.id, "set_status", "issue_report", report.id, {"status": body.status})
    await db.commit()
    return await _out(db, await db.get(IssueReport, report.id, populate_existing=True), user, detail=True)


@router.post("/{report_id}/escalate", response_model=IssueDetail, status_code=201)
async def escalate(report_id: uuid.UUID, body: EscalateRequest, db: DB, user: CurrentUser):
    """A company admin passes an employee's report on to the platform admin. Returns the new platform report."""
    report = await _load(db, report_id, user)
    if not (_handles(user, report) and report.level == "company"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the company admin can pass an employee's report to the platform admin")
    if await db.scalar(select(IssueReport.id).where(IssueReport.escalated_from_id == report.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This report was already sent to the platform admin")
    if report.status not in OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This report is {report.status}; reopen it before passing it on")
    who = report.reporter.full_name if report.reporter else "an employee"
    description = f"Reported by {who}:\n\n{report.description}"
    if body.note and body.note.strip():
        description += f"\n\nNote from the company admin:\n{body.note.strip()}"
    platform = IssueReport(
        company_id=report.company_id, reporter_id=user.id, level="platform", subject=report.subject, description=description,
        category=report.category, priority=report.priority, page=report.page, escalated_from_id=report.id,
    )
    db.add(platform)
    await db.flush()
    report.status = "in_progress"
    _note(db, report, user, f"Sent to the platform administrator as report #{platform.number}")
    audit(db, user.id, "escalate", "issue_report", report.id, {"to": str(platform.id)})
    await db.commit()
    return await _out(db, await db.get(IssueReport, platform.id, populate_existing=True), user, detail=True)

