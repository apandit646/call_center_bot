import uuid

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, not_found
from app.models import Call, CallAnswer, CallTranscript
from app.schemas.outbound import AnswerOut, CallDetail, CallOut, TranscriptOut
from app.services import storage

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(call_id: uuid.UUID, db: DB, _: CurrentUser):
    call = await db.get(Call, call_id)
    if call is None:
        raise not_found("Call")
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
async def get_recording(call_id: uuid.UUID, db: DB, _: CurrentUser):
    call = await db.get(Call, call_id)
    if call is None or not call.recording_path:
        raise not_found("Recording")
    return FileResponse(storage.resolve(call.recording_path), media_type="audio/mpeg", filename=f"{call.id}.mp3")
