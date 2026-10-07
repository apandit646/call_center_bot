"""Telephony provider callbacks (no JWT - every request is verified by the provider's signature):

  /telephony/calls/{id}/...   outbound campaign calls (Twilio)
  /telephony/inbound...       inbound support calls to a company's number (Twilio)
  /telephony/exotel/...       Exotel: the two-way audio stream of a call, and its status callbacks.
                              Exotel does not sign requests, so these check a per-company secret.
"""
import asyncio
import base64
import binascii
import hmac
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import DB
from app.core.database import SessionLocal
from app.models import Call, Callback, Campaign, Company, SupportCall
from app.models.enums import CallStatus
from app.services import call_languages, callback_service, speech, storage, voice_agent, voice_qa, voice_stream, voice_support
from app.services.company_service import ExotelCredentials, exotel_credentials
from app.services.queue import Queues, enqueue
from app.services.telephony import get_provider
from app.services.telephony.base import TelephonyProvider
from app.services.telephony.exotel import valid_key

log = logging.getLogger(__name__)
router = APIRouter(prefix="/telephony/calls/{call_id}", tags=["telephony callbacks"])
audio_router = APIRouter(prefix="/telephony/audio", tags=["telephony callbacks"])
inbound_router = APIRouter(prefix="/telephony/inbound", tags=["telephony callbacks"])
exotel_router = APIRouter(prefix="/telephony/exotel", tags=["telephony callbacks"])
# Calls we place because a customer asked to be called back ("call me at 5 pm").
callbacks_router = APIRouter(prefix="/telephony/callbacks/{callback_id}", tags=["telephony callbacks"])
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
    provider = get_provider(campaign.company, call.provider)  # the provider that placed this call
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
async def _callback(db: DB, callback_id: uuid.UUID, request: Request) -> tuple[Callback, Company, TelephonyProvider]:
    callback = await db.get(Callback, callback_id)
    if callback is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown callback")
    company = await db.get(Company, callback.company_id)
    provider = get_provider(company)
    if not await provider.verify_request(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid provider signature")
    return callback, company, provider


@callbacks_router.post("/start")
async def callback_answered(callback_id: uuid.UUID, request: Request, db: DB) -> Response:
    """The customer picked up the call they asked for: from here it is a support call like an inbound one."""
    callback, company, provider = await _callback(db, callback_id, request)
    form = await _form_of(request)
    if form.get("CallSid") and callback.provider_call_id != form["CallSid"]:
        callback.provider_call_id = form["CallSid"]
        await db.flush()
    call, action = await voice_support.start(db, company, {**form, "From": callback.phone}, provider.name)
    return await provider.render(call.id, action, listen_url=voice_support.turn_url(call.id, action.gather_question_index or 0))


@callbacks_router.post("/status", status_code=204)
async def callback_status(callback_id: uuid.UUID, request: Request, db: DB) -> None:
    """How the callback's call ended: reached, or to be tried again."""
    callback, _, provider = await _callback(db, callback_id, request)
    update = provider.parse_status(await _form_of(request))
    if callback.support_call_id is not None:  # the conversation happened: close it like any support call
        await enqueue(Queues.CALL_EVENTS, {
            "support_call_id": str(callback.support_call_id),
            "status": update.status.value if isinstance(update.status, CallStatus) else None,
            "duration_seconds": update.duration_seconds,
        })
    await callback_service.apply_status(db, callback.id, update.status)


@exotel_router.post("/callbacks/{callback_id}/status", status_code=204)
async def exotel_callback_status(callback_id: uuid.UUID, request: Request, db: DB, key: str = "") -> None:
    callback = await db.get(Callback, callback_id)
    if callback is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown callback")
    company = await db.get(Company, callback.company_id)
    creds = exotel_credentials(company)
    if creds is None or not valid_key(creds.secret, str(callback_id), key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid key")
    update = get_provider(company, "exotel").parse_status(await _exotel_body(request))
    await callback_service.apply_status(db, callback.id, update.status)


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
    provider = get_provider(await db.get(Company, call.company_id), call.provider)
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
    provider = get_provider(await db.get(Company, call.company_id), call.provider)
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
    # English + Hindi companies use Hindi code-mix recognition; with more languages on, it detects the language.
    heard = await provider.transcribe(form, call_languages.recognition_language(await db.get(Company, call.company_id)))
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


# ---------------------------------------------------------------------- Exotel
def _stream_login_ok(authorization: str | None, creds: ExotelCredentials) -> bool:
    """Exotel sends the login from the Voicebot URL as a Basic Authorization header."""
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    try:
        _, _, password = base64.b64decode(authorization[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return hmac.compare_digest(password, creds.secret)


async def _exotel_stream(websocket: WebSocket, company_id: uuid.UUID, key: str | None) -> None:
    async with SessionLocal() as db:
        company = await db.get(Company, company_id)
    creds = exotel_credentials(company) if company and company.is_active and company.telephony_provider == "exotel" else None
    allowed = creds is not None and (
        valid_key(creds.secret, "stream", key) if key is not None
        else _stream_login_ok(websocket.headers.get("authorization"), creds)
    )
    if not allowed:
        log.warning("Exotel audio stream for company %s refused (wrong or missing key)", company_id)
        await websocket.close(code=1008)  # refused before accepting: the provider sees HTTP 403
        return
    await websocket.accept()
    log.info("Exotel audio stream opened for company %s", company_id)
    await voice_stream.StreamSession(websocket, company.id).run()


@exotel_router.websocket("/stream/{company_id}/{key}")
async def exotel_stream(websocket: WebSocket, company_id: uuid.UUID, key: str) -> None:
    """The Voicebot applet of a company's Exotel flow connects here for the length of one call:
    the caller's audio comes in, the agent's audio goes out (services/voice_stream.py).
    The key at the end of the address is what lets Exotel in."""
    await _exotel_stream(websocket, company_id, key)


@exotel_router.websocket("/stream/{company_id}")
async def exotel_stream_with_login(websocket: WebSocket, company_id: uuid.UUID) -> None:
    """The same stream, for an address written as wss://user:password@host/...: Exotel turns that
    login into a Basic Authorization header."""
    await _exotel_stream(websocket, company_id, None)


async def _exotel_body(request: Request) -> dict[str, str]:
    """Exotel posts JSON or a form depending on how the callback was set up; a Passthru applet uses the query."""
    data: dict[str, object] = dict(request.query_params)
    if "json" in request.headers.get("content-type", ""):
        try:
            body = json.loads(await request.body() or b"{}")
            data.update(body if isinstance(body, dict) else {})
        except ValueError:
            pass
    elif request.method == "POST":
        data.update(dict(await request.form()))
    return {k: str(v) for k, v in data.items() if v is not None and not isinstance(v, (dict, list))}


@exotel_router.post("/calls/{call_id}/status", status_code=204)
async def exotel_call_status(call_id: uuid.UUID, request: Request, db: DB, key: str = "") -> None:
    """The final status of an outbound call we placed (set as its StatusCallback)."""
    call = await db.get(Call, call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown call")
    campaign = await db.get(Campaign, call.campaign_id)
    creds = exotel_credentials(campaign.company)
    if creds is None or not valid_key(creds.secret, str(call_id), key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid key")
    provider = get_provider(campaign.company, "exotel")
    update = provider.parse_status(await _exotel_body(request))
    event_status, duration, error = update.status, update.duration_seconds, None
    if event_status == CallStatus.COMPLETED and call.answered_at is None:
        # Exotel says the call happened, yet its flow never opened the audio stream to us, so the
        # customer heard nothing. Say so on the call instead of showing a silent "completed".
        details = await provider.call_details(update.provider_call_id or call.provider_call_id or "")
        picked_up = (details.get("Details") or {}).get("Leg1Status") == "completed"
        flow_leg = (details.get("Details") or {}).get("Leg2Status")
        duration = duration if duration is not None else details.get("Duration")
        event_status = CallStatus.FAILED
        error = (
            f"{'The customer answered, but ' if picked_up else ''}Exotel did not connect the call to the AI agent: "
            f"flow {creds.app_id} {'failed' if flow_leg == 'failed' else 'ended'} without opening the audio stream. "
            "In Exotel's App Bazaar, make the first step of that flow a Voicebot applet with the address shown in "
            "Company Settings (it changes whenever the public address changes)."
        )
        log.error("Exotel call %s: %s", call_id, error)
    await enqueue(Queues.CALL_EVENTS, {
        "call_id": str(call_id),
        "status": event_status.value if isinstance(event_status, CallStatus) else None,
        "duration_seconds": duration if isinstance(duration, int) else None,
        "provider_call_id": update.provider_call_id,
        "recording_url": update.recording_url,
        "error": error,
    })


@exotel_router.post("/{company_id}/passthru", include_in_schema=False)
@exotel_router.get("/{company_id}/passthru")
async def exotel_passthru(company_id: uuid.UUID, request: Request, db: DB, key: str = "") -> dict[str, str]:
    """The Passthru applet after the Voicebot applet: the conversation is over and, if the flow
    records, the recording is ready. Works for both inbound and outbound calls of the company."""
    company = await db.get(Company, company_id)
    creds = exotel_credentials(company)
    if creds is None or not valid_key(creds.secret, "passthru", key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid key")
    update = get_provider(company, "exotel").parse_status(await _exotel_body(request))
    sid = update.provider_call_id or ""
    inbound = await db.scalar(
        select(SupportCall.id).where(SupportCall.provider_call_id == sid, SupportCall.company_id == company.id)
    )
    outbound = None if inbound else await db.scalar(
        select(Call.id).join(Campaign, Campaign.id == Call.campaign_id)
        .where(Call.provider_call_id == sid, Campaign.company_id == company.id)
    )
    if (inbound or outbound) and update.recording_url:
        await enqueue(Queues.CALL_EVENTS, {
            **({"support_call_id": str(inbound)} if inbound else {"call_id": str(outbound)}),
            "recording_url": update.recording_url,
        })
    return {"status": "ok"}  # 200 tells the flow to carry on to its Hangup applet


@audio_router.get("/{name}")
async def tts_audio(name: str) -> FileResponse:
    """Generated speech that the telephony provider fetches for <Play>. Names are unguessable HMACs."""
    if not speech.AUDIO_NAME_RE.match(name) or not storage.exists(f"tts/{name}"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audio not found")
    return FileResponse(
        storage.resolve(f"tts/{name}"), media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"}
    )
