"""Telephony provider callbacks (no JWT - every request is verified by the provider's signature):

  /telephony/calls/{id}/...   outbound campaign calls
  /telephony/inbound...       inbound support calls to a company's number
"""
import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import DB
from app.models import Call, Campaign, Company, SupportCall
from app.models.enums import CallStatus
from app.services import speech, storage, voice_agent, voice_qa, voice_support
from app.services.queue import Queues, enqueue
from app.services.telephony import get_provider
from app.services.telephony.base import TelephonyProvider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/telephony/calls/{call_id}", tags=["telephony callbacks"])
audio_router = APIRouter(prefix="/telephony/audio", tags=["telephony callbacks"])
inbound_router = APIRouter(prefix="/telephony/inbound", tags=["telephony callbacks"])
_background: set[asyncio.Task] = set()


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


# ---------------------------------------------------------------------- inbound support calls
async def _form_of(request: Request) -> dict[str, str]:
    return {k: str(v) for k, v in (await request.form()).items()}


@inbound_router.post("")
async def inbound_call(request: Request, db: DB) -> Response:
    """A customer dialled a company's number. The dialled number tells us which company it is, and
    so which Twilio auth token must have signed this request."""
    form = await _form_of(request)
    company = await voice_support.company_for_number(db, form.get("To"))
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown number")
    provider = get_provider(company)
    if not await provider.verify_request(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid provider signature")
    if not voice_support.inbound_active(company):
        log.info("Inbound call to %s rejected: inbound calls are off for %s", form.get("To"), company.name)
        return provider.reject()
    call, action = await voice_support.start(db, company, form, provider.name)
    return await provider.render(call.id, action, listen_url=voice_support.turn_url(call.id, action.gather_question_index or 0))


async def _inbound(db: DB, call_id: uuid.UUID, request: Request, lock: bool = True) -> tuple[SupportCall, TelephonyProvider, dict[str, str]]:
    call = await db.get(SupportCall, call_id, with_for_update=lock)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown call")
    provider = get_provider(await db.get(Company, call.company_id))
    if not await provider.verify_request(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid provider signature")
    return call, provider, await _form_of(request)


@inbound_router.post("/status", status_code=204)
async def inbound_status(request: Request, db: DB) -> None:
    """The company's number reports a call's final status (set as the number's status callback)."""
    form = await _form_of(request)
    call = await db.scalar(select(SupportCall).where(SupportCall.provider_call_id == form.get("CallSid", "")))
    if call is None:  # e.g. an outbound call's status sent to the number-level callback: not ours
        return
    provider = get_provider(await db.get(Company, call.company_id))
    if not await provider.verify_request(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid provider signature")
    update = provider.parse_status(form)
    await enqueue(Queues.CALL_EVENTS, {
        "support_call_id": str(call.id),
        "status": update.status.value if isinstance(update.status, CallStatus) else None,
        "duration_seconds": update.duration_seconds,
    })


@inbound_router.post("/{call_id}/turn")
async def inbound_turn(call_id: uuid.UUID, request: Request, db: DB, t: int = Query(ge=0)) -> Response:
    """The caller spoke (or stayed silent) after turn t."""
    call, provider, form = await _inbound(db, call_id, request)
    if t == 0 and call.provider_call_id and call.recording_url is None:
        # The call is live now, so the whole-call recording can start (not possible while it rings).
        task = asyncio.create_task(_start_recording(provider, call.provider_call_id, call.id))
        _background.add(task)
        task.add_done_callback(_background.discard)
    started = time.perf_counter()
    heard = await provider.transcribe(form, "hi-IN")  # Hindi code-mix also returns English words in English
    heard_at = time.perf_counter()
    action = await voice_support.handle_turn(db, call, t, heard.text, heard.language)
    listen_url = None if action.gather_question_index is None else voice_support.turn_url(call.id, action.gather_question_index)
    response = await provider.render(call.id, action, listen_url=listen_url)
    done = time.perf_counter()
    log.info("Inbound call %s turn %d: %.2fs total (speech-to-text %.2fs, understand+reply %.2fs)",
             call.id, t + 1, done - started, heard_at - started, done - heard_at)
    return response


async def _start_recording(provider: TelephonyProvider, provider_call_id: str, call_id: uuid.UUID) -> None:
    try:
        await provider.start_recording(provider_call_id, voice_support.recording_url(call_id))
    except Exception as exc:  # a call without a recording is still a good call
        log.warning("Inbound call %s: recording not started (%r)", call_id, exc)


@inbound_router.post("/{call_id}/recording", status_code=204)
async def inbound_recording(call_id: uuid.UUID, request: Request, db: DB) -> None:
    _, provider, form = await _inbound(db, call_id, request, lock=False)
    update = provider.parse_status(form)
    if update.recording_url:
        await enqueue(Queues.CALL_EVENTS, {"support_call_id": str(call_id), "recording_url": update.recording_url})


@audio_router.get("/{name}")
async def tts_audio(name: str) -> FileResponse:
    """Generated speech that the telephony provider fetches for <Play>. Names are unguessable HMACs."""
    if not speech.AUDIO_NAME_RE.match(name) or not storage.exists(f"tts/{name}"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audio not found")
    return FileResponse(
        storage.resolve(f"tts/{name}"), media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"}
    )
