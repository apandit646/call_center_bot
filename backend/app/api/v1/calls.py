import uuid

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import DB, TenantUser, not_found
from app.models import Call, CallAnswer, CallTranscript, Campaign, User
from app.schemas.outbound import AnswerOut, CallDetail, CallOut, TranscriptOut
from app.services import storage

router = APIRouter(prefix="/calls", tags=["calls"])


async def _call(db: DB, call_id: uuid.UUID, user: User) -> Call:
    """A call belongs to the company that owns its campaign."""
    call = await db.get(Call, call_id)
    campaign = await db.get(Campaign, call.campaign_id) if call else None
    if campaign is None or campaign.company_id != user.company_id:
        raise not_found("Call")
    return call


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(call_id: uuid.UUID, db: DB, user: TenantUser):
    call = await _call(db, call_id, user)
    transcript = (
        await db.scalars(select(CallTranscript).where(CallTranscript.call_id == call.id).order_by(CallTranscript.turn_index))
    ).all()
    answers = (await db.scalars(select(CallAnswer).where(CallAnswer.call_id == call.id))).all()
    base = CallOut.model_validate(call)
    base.has_recording = bool(call.recording_path)
    return CallDetail(
        **base.model_dump(),
        transcript=[TranscriptOut.model_validate(t) for t in transcript],
        answers=[AnswerOut.model_validate(a) for a in answers],
    )


@router.get("/{call_id}/recording")
async def get_recording(call_id: uuid.UUID, db: DB, user: TenantUser):
    call = await _call(db, call_id, user)
    if not call.recording_path:
        raise not_found("Recording")
    return FileResponse(storage.resolve(call.recording_path), media_type="audio/mpeg", filename=f"{call.id}.mp3")
