"""Telephony provider callbacks for the outbound Q&A call flow (no JWT - verified by provider signature)."""
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.api.deps import DB
from app.models import Call, Campaign
from app.models.enums import CallStatus
from app.services import speech, storage, voice_qa
from app.services.queue import Queues, enqueue
from app.services.telephony import get_provider

router = APIRouter(prefix="/telephony/calls/{call_id}", tags=["telephony callbacks"])
audio_router = APIRouter(prefix="/telephony/audio", tags=["telephony callbacks"])


async def _form(request: Request) -> dict[str, str]:
    provider = get_provider()
    if not await provider.verify_request(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid provider signature")
    return {k: str(v) for k, v in (await request.form()).items()}


async def _call(db: DB, call_id: uuid.UUID) -> Call:
    call = await db.get(Call, call_id, with_for_update={"of": Call})
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown call")
    return call


@router.post("/start")
async def call_answered(call_id: uuid.UUID, request: Request, db: DB) -> Response:
    """Customer picked up: greet and ask the first question."""
    await _form(request)
    call = await _call(db, call_id)
    action = await voice_qa.start(db, call)
    return await get_provider().render(call.id, action)


@router.post("/answer")
async def customer_answered(call_id: uuid.UUID, request: Request, db: DB, q: int = Query(ge=0)) -> Response:
    """Customer spoke (or stayed silent) after question q."""
    form = await _form(request)
    call = await _call(db, call_id)
    campaign = await db.get(Campaign, call.campaign_id)
    speech_text = await get_provider().transcribe(form, campaign.language)
    action = await voice_qa.handle_answer(db, call, q, speech_text)
    return await get_provider().render(call.id, action)


@router.post("/status", status_code=204)
async def call_status(call_id: uuid.UUID, request: Request) -> None:
    update = get_provider().parse_status(await _form(request))
    await enqueue(Queues.CALL_EVENTS, {
        "call_id": str(call_id),
        "status": update.status.value if isinstance(update.status, CallStatus) else None,
        "duration_seconds": update.duration_seconds,
        "provider_call_id": update.provider_call_id,
    })


@router.post("/recording", status_code=204)
async def recording_ready(call_id: uuid.UUID, request: Request) -> None:
    update = get_provider().parse_status(await _form(request))
    if update.recording_url:
        await enqueue(Queues.CALL_EVENTS, {"call_id": str(call_id), "recording_url": update.recording_url})


@audio_router.get("/{name}")
async def tts_audio(name: str) -> FileResponse:
    """Generated speech that the telephony provider fetches for <Play>. Names are unguessable HMACs."""
    if not speech.AUDIO_NAME_RE.match(name) or not storage.exists(f"tts/{name}"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audio not found")
    return FileResponse(
        storage.resolve(f"tts/{name}"), media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"}
    )
