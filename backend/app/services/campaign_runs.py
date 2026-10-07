"""Runs: using one campaign again and again.

A campaign keeps its script (greeting, questions, languages, settings). Each time it is used to
call a list of people is a numbered RUN with its own contacts, calls and results:

    Run 1  ->  completed            (contacts, calls and answers stay with Run 1)
    "run again": a new list | the same contacts | only the people not reached
    Run 2  ->  draft -> scheduled -> running -> completed ...

The campaign's own status is always the status of its CURRENT (latest) run, so scheduling, dialling
and completion keep working exactly as before; they simply only look at the current run's contacts.
A contact belongs to a run (campaign_contacts.run_id); a call belongs to its contact, and an answer
to its call, so everything can be read per run without storing the run on every row.

Rules that hold across runs:
  * someone who asked not to be called is never put into a later run;
  * with a new list, people who already completed an earlier run are left out (unless switched off);
  * a question that has answers is never deleted when the script is edited: it is retired, so old
    runs keep showing exactly what was asked.
"""
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Call, Campaign, CampaignContact, CampaignRun
from app.models.enums import CampaignStatus, ContactStatus

log = logging.getLogger(__name__)

FINISHED = (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED)
SOURCES = ("new_list", "same_contacts", "not_reached")


class RunError(Exception):
    pass


async def ensure(db: AsyncSession, campaign: Campaign) -> CampaignRun:
    """The campaign's current run. Every campaign has one; it is created here the first time it is needed."""
    if campaign.current_run_id is not None:
        run = await db.get(CampaignRun, campaign.current_run_id)
        if run is not None:
            return run
    run = CampaignRun(campaign_id=campaign.id, number=campaign.run_number or 1, source="first")
    db.add(run)
    await db.flush()
    campaign.current_run_id, campaign.run_number = run.id, run.number
    return run


def contacts_of(campaign: Campaign, run: CampaignRun | None) -> ColumnElement[bool]:
    """The condition that picks a run's contacts. run None = every run of the campaign.
    Contacts with no run recorded belong to the current run."""
    in_campaign = CampaignContact.campaign_id == campaign.id
    if run is None:
        return in_campaign
    if run.id == campaign.current_run_id:
        return in_campaign & or_(CampaignContact.run_id == run.id, CampaignContact.run_id.is_(None))
    return in_campaign & (CampaignContact.run_id == run.id)


async def resolve(db: AsyncSession, campaign: Campaign, which: str | None) -> CampaignRun | None:
    """A "run" query value -> the run. Nothing = the current run; "all" = None (every run)."""
    if not which:
        return await ensure(db, campaign)
    if which == "all":
        return None
    try:
        run = await db.get(CampaignRun, uuid.UUID(which))
    except ValueError:
        run = None
    if run is None or run.campaign_id != campaign.id:
        raise RunError("Unknown run")
    return run


def snapshot(campaign: Campaign) -> list[dict[str, Any]]:
    """The questions as they are asked right now, kept with a run when it closes."""
    return [
        {"id": str(q.id), "key": q.key, "text": q.text, "answer_type": q.answer_type, "options": q.options}
        for q in campaign.questions
    ]


async def never_call(db: AsyncSession, campaign: Campaign) -> set[uuid.UUID]:
    """Customers who asked, on any run of this campaign, not to be called again."""
    rows = await db.scalars(
        select(CampaignContact.customer_id).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status == ContactStatus.DO_NOT_CALL
        )
    )
    return set(rows.all())


async def already_completed(db: AsyncSession, campaign: Campaign, before: CampaignRun) -> set[uuid.UUID]:
    """Customers who completed this campaign in a run before `before`."""
    rows = await db.scalars(
        select(CampaignContact.customer_id).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status == ContactStatus.COMPLETED,
            CampaignContact.run_id.isnot(None), CampaignContact.run_id != before.id,
        )
    )
    return set(rows.all())


async def start(db: AsyncSession, campaign: Campaign, source: str, skip_completed: bool = True) -> CampaignRun:
    """Close the finished run and open the next one. The campaign goes back to draft, ready for
    contacts (source "new_list") or straight for scheduling (the other two). Commits."""
    if source not in SOURCES:
        raise RunError(f"Choose who to call: one of {', '.join(SOURCES)}")
    if campaign.status not in FINISHED:
        raise RunError(
            f"This campaign's current run is {campaign.status}. A new run can start once it has completed or been cancelled."
        )
    previous = await ensure(db, campaign)
    # Contacts created before runs existed belong to the run that is closing.
    await db.execute(
        update(CampaignContact)
        .where(CampaignContact.campaign_id == campaign.id, CampaignContact.run_id.is_(None))
        .values(run_id=previous.id)
    )
    previous.status = campaign.status
    previous.started_at = previous.started_at or campaign.start_at
    previous.completed_at = campaign.completed_at or datetime.now(UTC)
    previous.questions = snapshot(campaign)

    run = CampaignRun(campaign_id=campaign.id, number=previous.number + 1, source=source, skip_completed=skip_completed)
    db.add(run)
    await db.flush()

    if source != "new_list":
        blocked = await never_call(db, campaign)
        earlier = (await db.scalars(select(CampaignContact).where(CampaignContact.run_id == previous.id))).all()
        if source == "not_reached":
            earlier = [c for c in earlier if c.status != ContactStatus.COMPLETED]
        chosen = [c for c in earlier if c.customer_id not in blocked]
        run.skipped_dnc = len(earlier) - len(chosen)
        if not chosen:
            await db.rollback()
            raise RunError(
                "There is nobody to call: " + (
                    "everyone was reached in the last run." if source == "not_reached" and not earlier
                    else "the last run had no contacts." if not earlier
                    else "everyone left has asked not to be called."
                )
            )
        db.add_all(
            CampaignContact(campaign_id=campaign.id, run_id=run.id, customer_id=c.customer_id, variables=c.variables)
            for c in chosen
        )

    campaign.current_run_id, campaign.run_number = run.id, run.number
    campaign.status, campaign.start_at, campaign.completed_at = CampaignStatus.DRAFT, None, None
    await db.commit()
    log.info("Campaign %s: run %d started (%s)", campaign.name, run.number, source)
    return run


async def listing(db: AsyncSession, campaign: Campaign) -> list[dict[str, Any]]:
    """Every run, newest first, with how it went."""
    current = await ensure(db, campaign)
    runs = (
        await db.scalars(select(CampaignRun).where(CampaignRun.campaign_id == campaign.id).order_by(CampaignRun.number.desc()))
    ).all()
    out = []
    for run in runs:
        is_current = run.id == current.id
        mine = contacts_of(campaign, run)
        by_status = dict((await db.execute(select(CampaignContact.status, func.count()).where(mine).group_by(CampaignContact.status))).all())
        calls = await db.scalar(
            select(func.count(Call.id)).join(CampaignContact, CampaignContact.id == Call.campaign_contact_id).where(mine)
        ) or 0
        total = sum(by_status.values())
        completed = by_status.get(ContactStatus.COMPLETED, 0)
        out.append({
            "id": str(run.id), "number": run.number, "is_current": is_current, "source": run.source,
            "status": campaign.status if is_current else run.status,
            "started_at": (campaign.start_at if is_current else run.started_at),
            "completed_at": (campaign.completed_at if is_current else run.completed_at),
            "contacts": total, "completed": completed, "failed": by_status.get(ContactStatus.FAILED, 0),
            "do_not_call": by_status.get(ContactStatus.DO_NOT_CALL, 0),
            "waiting": total - completed - by_status.get(ContactStatus.FAILED, 0) - by_status.get(ContactStatus.DO_NOT_CALL, 0),
            "calls": calls, "completion_rate": round(completed / total, 3) if total else 0,
            "skipped_do_not_call": run.skipped_dnc, "skipped_completed": run.skipped_completed,
            "skip_completed": run.skip_completed, "created_at": run.created_at,
        })
    return out
