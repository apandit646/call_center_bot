# AI Customer Platform

Two independent workflows on shared infrastructure (FastAPI · PostgreSQL + pgvector · Redis · Gemini · React):

| Workflow | Flow |
|---|---|
| **1. WhatsApp Support** | WhatsApp → webhook → domain detection → **domain-specific RAG** → Gemini → answer / clarification → *(not resolved)* → **ticket** → auto-assigned agent |
| **2. Outbound AI Q&A** | Excel → validate / dedupe / normalise → preview → confirm → campaign + questions → schedule → scheduler → Redis queue → call worker → AI voice asks Q1…Qn → answers extracted → PostgreSQL → analytics / Excel export |

They share customers, auth, Gemini, PostgreSQL, Redis, storage, and the admin portal. They do not share any business logic.

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env      # works as-is in offline mode
docker compose up --build
```

- Admin portal: http://localhost:5173 (sign in with `admin@example.com` / `admin12345`, then change the password)
- API docs (Swagger): http://localhost:8000/docs

> Postgres is published on host port **5433** so it doesn't clash with a local install. Override with `POSTGRES_HOST_PORT=5434 docker compose up`.

**Offline mode:** with no `GEMINI_API_KEY`, no WhatsApp token, and `TELEPHONY_PROVIDER=mock`:
- Gemini is replaced by deterministic fallbacks (keyword domain detection, hashed embeddings, canned replies).
- WhatsApp replies are logged instead of sent.
- The mock telephony provider does not place or simulate calls: every call is marked **Failed** with a "no real telephony provider configured" error. Use `TELEPHONY_PROVIDER=twilio` for real calls.

## Local development (without Docker for the app)

```bash
docker compose up -d postgres redis

cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # set DATABASE_URL port to 5433
alembic upgrade head
uvicorn app.main:app --reload                       # API
python -m app.workers.support_worker                # WhatsApp + RAG ingestion + ticket assignment
python -m app.workers.call_worker                   # places calls, applies call events
python -m app.workers.scheduler                     # moves due calls onto the queue (run one)

cd ../frontend
cp .env.example .env
npm install && npm run dev
```

## Going live

| Integration | Settings | Notes |
|---|---|---|
| Gemini | `GEMINI_API_KEY`, `GEMINI_CHAT_MODEL`, `GEMINI_EMBEDDING_MODEL` | Embeddings are stored at 768 dims (`EMBEDDING_DIM`). If you change the dimension, write a migration and re-index documents. |
| WhatsApp Cloud API | `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | Webhook URL: `{PUBLIC_BASE_URL}/api/v1/webhooks/whatsapp`. Set the app secret so `X-Hub-Signature-256` is enforced. |
| Twilio Voice | `TELEPHONY_PROVIDER=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `PUBLIC_BASE_URL` | Twilio calls back to `{PUBLIC_BASE_URL}/api/v1/telephony/calls/{id}/…` and fetches audio from `/api/v1/telephony/audio/…`. It must be publicly reachable (use ngrok in dev). Requests are verified with `X-Twilio-Signature`. |
| Sarvam AI (voice) | `SPEECH_PROVIDER=sarvam`, `SARVAM_API_KEY`, `SARVAM_TTS_SPEAKER` | Bulbul speaks the script and Saaras transcribes answers. See **Voice & languages** below. |
| Auth | `JWT_SECRET_KEY` | **Must** be changed in production. |

## Architecture

```
React admin (JWT) ──► FastAPI ──► PostgreSQL + pgvector   (source of truth)
                         │
                         └──► Redis queues ──► support_worker  (whatsapp.incoming, knowledge.ingest, ticket.assignment)
                                          ──► call_worker     (outbound.calls, call.events)
                              scheduler ────► (every 10 s: due scheduled_calls → outbound.calls)
```

- **Redis carries only ids and temporary state.** Every job is re-loaded from PostgreSQL. Queues are reliable: `BLMOVE` moves each job to a per-worker processing list, and jobs a crashed worker left behind are recovered on restart. `call:{id}` holds per-call reprompt state, and a lock per WhatsApp sender keeps one customer's messages in order.
- **Webhooks are idempotent.** WhatsApp and telephony events land in `webhook_events` (unique on source + external id) before they are queued, so a redelivered event is never processed twice.
- **Concurrency.** 10,000 contacts become 10,000 `scheduled_calls` rows, not threads. The scheduler only queues as many as `max_concurrent_calls` (per campaign) and `GLOBAL_MAX_CONCURRENT_CALLS` allow, and only inside the campaign's call window and timezone.
- **Retries.** A no-answer, busy, or failed call creates a new `scheduled_call` after `retry_delay_minutes`, up to `max_attempts`. Calls stuck without a final status are failed after `CALL_STALE_MINUTES`.
- **Voice turn-taking** is provider-agnostic (`services/voice_qa.py`). Each spoken answer is saved raw, and Gemini also extracts a structured `normalized_answer` (always a `value` key, plus extra facts) with a confidence score.

### Voice & languages (Sarvam AI)

| | `SPEECH_PROVIDER=sarvam` (default) | `SPEECH_PROVIDER=twilio` |
|---|---|---|
| TTS (AI voice) | Sarvam **Bulbul v3**, played with TwiML `<Play>` | Twilio `<Say>` (Amazon Polly) |
| STT (customer) | Answer captured with `<Record>` (≤25 s, stops after 3 s of silence), transcribed by Sarvam **Saaras v3** in `codemix` mode, then the clip is deleted from Twilio | Twilio `<Gather input="speech">` |

- **Hindi + English.** Campaigns default to `hi-IN`. Write scripts in Hinglish, meaning Hindi in Devanagari with everyday English words left in English (e.g. "क्या आप हमारा product use कर रहे हैं?"). STT auto-detects the language of each answer (`SARVAM_STT_LANGUAGE=unknown`), so customers can reply in Hindi, English, or a mix. Gemini normalises every answer to the same structured values, and free-text answers are summarised in English for analytics. Other Bulbul languages (Marathi, Tamil, Bengali, …) are selectable per campaign.
- **No waiting on TTS mid-call.** Before dialling, the call worker pre-generates everything the call can say: the personalised greeting, all questions, the closing, and the retry prompts. Audio is cached by content, so each question is synthesised once per campaign, not once per call. Cached file names are HMACs, so the public audio URLs can't be guessed.
- **Failure-safe.** If Sarvam TTS fails for an utterance, that line falls back to Twilio `<Say>`. If STT fails, the answer is treated as not heard and the question is repeated once.

The **WhatsApp bot** also replies in the customer's language: English, Hindi (Devanagari), or Hinglish (Latin script). Gemini detects the language per message, and the fixed messages (greeting, ticket raised, etc.) exist in all three. Ticket summaries for agents are always written in English.

### Roles

| | admin | supervisor | agent |
|---|---|---|---|
| Tickets & conversations | all | all | own + unassigned |
| Knowledge base / domains | manage | manage | read |
| Campaigns | manage | manage | — |
| Users | manage | read | — |

## Project layout

```
backend/app/
  core/          config, JWT/bcrypt security, async DB, Redis
  models/        identity · customers · support (domains, conversations, messages, tickets…) · rag · outbound · system
  schemas/       Pydantic request/response models
  api/v1/        auth, users, customers, domains, knowledge, conversations, tickets,
                 campaigns, calls, dashboard, webhooks (WhatsApp), telephony (call callbacks)
  services/      conversation_manager (workflow 1) · rag · document_processing · ticket_service
                 excel_import · campaign_service · call_service · voice_qa · answer_extraction (workflow 2)
                 gemini · queue · storage · telephony/{twilio,mock}
  workers/       support_worker · call_worker · scheduler
backend/alembic/ migrations
frontend/src/    api client (auto token refresh) · auth context · pages for every module
```

## Next steps

- **Streaming voice.** The current voice loop is turn-based: play the question, record the answer, transcribe it. For lower latency and barge-in, bridge a Twilio Media Stream websocket to Sarvam's streaming STT/TTS (or Gemini Live). `voice_qa` already holds the question state machine that provider would reuse.
- **Gemini quota.** The free tier allows 5 requests/minute for `gemini-2.5-flash`. Each WhatsApp message uses 2 calls and each call answer uses 1, so production needs a paid tier. On a 429, the bot falls back to its offline replies instead of failing.
- **Object storage.** `services/storage.py` writes to local disk (a Docker volume). Swap in S3/GCS behind the same functions.
- **Tests & CI.** Add pytest coverage around `conversation_manager`, `excel_import`, and `campaign_service.scheduler_tick`.
