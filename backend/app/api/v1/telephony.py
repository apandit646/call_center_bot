"""Telephony provider callbacks for the outbound Q&A call flow (no JWT - verified by provider signature)."""
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.api.deps import DB
from app.models import Call, Campaign
from app.models.enums import CallStatus
from app.services import speech, storage, voice_agent, voice_qa
from app.services.queue import Queues, enqueue
from app.services.telephony import get_provider
from app.services.telephony.base import TelephonyProvider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/telephony/calls/{call_id}", tags=["telephony callbacks"])
audio_router = APIRouter(prefix="/telephony/audio", tags=["telephony callbacks"])


async def _verified(
    db: DB, call_id: uuid.UUID, request: Request, lock: bool = True
) -> tuple[Call, Campaign, TelephonyProvider, dict[str, str]]:
    """Load the call and check the callback was signed by the telephony account of the call's company.

    The call id in the URL is what tells us which company (and so which Twilio auth token) applies.
    """
    call = await db.get(Call, call_id, with_for_update={"of": Call} if lock else None)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown call")
    campaign = await db.get(Campaign, call.campaign_id)
    provider = get_provider(campaign.company)
    if not await provider.verify_request(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid provider signature")
    return call, campaign, provider, {k: str(v) for k, v in (await request.form()).items()}


@router.post("/start")
async def call_answered(call_id: uuid.UUID, request: Request, db: DB) -> Response:
    """Customer picked up: greet and ask the first question."""
    call, campaign, provider, _ = await _verified(db, call_id, request)
    engine = voice_agent if campaign.conversational else voice_qa
    action = await engine.start(db, call)
    return await provider.render(call.id, action)


@router.post("/answer")
async def customer_answered(call_id: uuid.UUID, request: Request, db: DB, q: int = Query(ge=0)) -> Response:
    """Customer spoke (or stayed silent) after question q."""
    call, campaign, provider, form = await _verified(db, call_id, request)
    started = time.perf_counter()
    lang = voice_qa.recognition_language(campaign, call.language or campaign.language)
    heard = await provider.transcribe(form, lang)
    heard_at = time.perf_counter()
    if campaign.conversational:
        action = await voice_agent.handle_turn(db, call, q, heard.text, heard.language)
    else:
        action = await voice_qa.handle_answer(db, call, q, heard.text, heard.language)
    response = await provider.render(call.id, action)
    done = time.perf_counter()
    # Everything here is dead air for the caller - keep an eye on it.
    log.info("Call %s turn %d: %.2fs total (speech-to-text %.2fs, understand+reply %.2fs)",
             call.id, q + 1, done - started, heard_at - started, done - heard_at)
    return response


@router.post("/status", status_code=204)
async def call_status(call_id: uuid.UUID, request: Request, db: DB) -> None:
    _, _, provider, form = await _verified(db, call_id, request, lock=False)
    update = provider.parse_status(form)
    await enqueue(Queues.CALL_EVENTS, {
        "call_id": str(call_id),
        "status": update.status.value if isinstance(update.status, CallStatus) else None,
        "duration_seconds": update.duration_seconds,
        "provider_call_id": update.provider_call_id,
    })


@router.post("/recording", status_code=204)
async def recording_ready(call_id: uuid.UUID, request: Request, db: DB) -> None:
    _, _, provider, form = await _verified(db, call_id, request, lock=False)
    update = provider.parse_status(form)
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
