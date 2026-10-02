# AI Customer Platform

Two independent workflows on shared infrastructure (FastAPI · PostgreSQL + pgvector · Redis · Gemini · React):

| Workflow | Flow |
|---|---|
| **1. WhatsApp Support** | WhatsApp → webhook → domain detection → **domain-specific RAG** → Gemini → answer / clarification → *(not resolved)* → **ticket** → auto-assigned agent |
| **2. Outbound AI Q&A** | Excel → validate / dedupe / normalise → preview → confirm → campaign + questions → schedule → scheduler → Redis queue → call worker → AI voice asks Q1…Qn → answers extracted → PostgreSQL → analytics / Excel export |

They share customers, auth, Gemini, PostgreSQL, Redis, storage, and the admin portal. They do not share any business logic.

**Multi-company.** The platform serves many companies from one install. A platform admin registers each company, sets its limits and hands over a generated login; each company then works in its own private portal with its own customers, knowledge base, campaigns, phone number and WhatsApp number. See **Companies** below.

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env      # works as-is in offline mode
docker compose up --build
```

- Portal: http://localhost:5173. Sign in with `admin@example.com` / `admin12345` (the **platform admin**), change the password, then **Register company**. The page shows the new company's login once; sign in with it to use campaigns, WhatsApp chats and the knowledge base.
- API docs (Swagger): http://localhost:8000/docs

> **Upgrading an existing install:** run `alembic upgrade head` (the Docker image does this on start). Existing data moves into a company called "Default Company" and the first admin becomes the platform admin. Open that company in the portal, set its email, and use **Generate new login** to get a company login for the old data.

> Postgres is published on host port **5433** so it doesn't clash with a local install. Override with `POSTGRES_HOST_PORT=5434 docker compose up`.

**Without keys and accounts:**
- With no `GEMINI_API_KEY`, Gemini is replaced by deterministic stand-ins (keyword domain detection, hashed embeddings, canned replies).
- A company that has not connected WhatsApp neither receives nor sends WhatsApp messages.
- A company that has not connected Twilio cannot schedule campaigns; the portal says what is missing.

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

Gemini and Sarvam are platform-wide (`.env`). Twilio and WhatsApp accounts are **per company**, set in the portal under Company Settings. There is no shared account and no fallback: a channel a company has not connected is simply off for that company.

| Integration | Settings | Notes |
|---|---|---|
| Gemini | `GEMINI_API_KEY`, `GEMINI_CHAT_MODEL`, `GEMINI_EMBEDDING_MODEL` | Embeddings are stored at 768 dims (`EMBEDDING_DIM`). If you change the dimension, write a migration and re-index documents. |
| WhatsApp Cloud API | Per company (portal): Phone number ID, access token and app secret, all required. Platform (`.env`): `WHATSAPP_VERIFY_TOKEN` only | One webhook URL for every company: `{PUBLIC_BASE_URL}/api/v1/webhooks/whatsapp`. Each event is routed by the number that received it; a number no company has connected is ignored. `X-Hub-Signature-256` is always checked with that company's own app secret. |
| Twilio Voice | Per company (portal): Account SID, auth token, calling number. Platform (`.env`): `PUBLIC_BASE_URL` | Twilio calls back to `{PUBLIC_BASE_URL}/api/v1/telephony/calls/{id}/…` and fetches audio from `/api/v1/telephony/audio/…`. It must be publicly reachable (use ngrok in dev). Each callback's `X-Twilio-Signature` is verified with the auth token of the company that owns the call. |
| Sarvam AI (voice) | `SPEECH_PROVIDER=sarvam`, `SARVAM_API_KEY`, `SARVAM_TTS_SPEAKER` | Bulbul speaks the script and Saaras transcribes answers. See **Voice & languages** below. |
| Auth | `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY` | **Must** be changed in production. `SECRETS_ENCRYPTION_KEY` encrypts each company's Twilio and WhatsApp tokens in the database; if you change it later, companies must re-enter their tokens. |

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

### Companies

- **Isolation.** Users, customers, domains, knowledge documents and chunks, conversations, tickets and campaigns all carry a `company_id`, and every API query is filtered by the caller's company. Another company's record id returns 404. The same phone number can be a customer of two companies; each has its own record.
- **Knowledge base.** Each company's knowledge is its own collection: `knowledge_chunks.company_id` is part of every retrieval query (`services/rag.py`), so the WhatsApp bot for one company can never read another company's documents.
- **Registration.** The platform admin enters the company's name, email, address, an "about" text and its products and services, plus limits. The system creates the company, four default support domains and an admin user whose login is the company email, with a generated password shown once.
- **Limits** (blank = unlimited): *max campaigns* is checked when a campaign is created; *calls per day* and *calls at the same time* are enforced by the scheduler. When the daily limit is reached the remaining calls stay pending and go out after midnight in the company's timezone.
- **Suspending** a company blocks its logins, stops its campaigns dialling and ignores its inbound WhatsApp messages. Nothing is deleted.
- **Own Twilio and WhatsApp.** Each company saves its Twilio Account SID, auth token and calling number, and its WhatsApp Phone number ID and access token. Tokens are encrypted at rest (`services/crypto.py`) and never returned by the API. Calls go out on the company's account and number; WhatsApp replies go out from the company's number.
- **The AI knows the company.** The company profile (name, about, products, address, contacts) is given to the voice agent and the WhatsApp bot, so "which company is this?" or "where is your office?" is answered from it. Each campaign also says whether it is about a product or a service, with its name and details, so the voice agent can answer "which product?". Greetings can use `{company}`.

### Voice & languages (Sarvam AI)

| | `SPEECH_PROVIDER=sarvam` (default) | `SPEECH_PROVIDER=twilio` |
|---|---|---|
| TTS (AI voice) | Sarvam **Bulbul v3**, played with TwiML `<Play>` | Twilio `<Say>` (Amazon Polly) |
| STT (customer) | Answer captured with `<Record>` (≤25 s, stops after 3 s of silence), transcribed by Sarvam **Saaras v3** in `codemix` mode, then the clip is deleted from Twilio | Twilio `<Gather input="speech">` |

- **The call follows the customer's language.** Campaigns start in English (`language=en-IN`) with Hindi as a switch language (`alt_languages=["hi-IN"]`). Saaras reports the language of every answer. As soon as the customer replies in Hindi, every later question, prompt, and the closing are spoken in Hindi. A full English sentence switches back, but a lone "yes" or "ok" doesn't, because Hindi speakers use those too.
- **Hindi script.** Admins write questions once in English. The Hindi versions are generated with Sarvam Translate (`mayura:v1`, code-mixed mode, i.e. natural spoken Hinglish, with Gemini as a fallback) when the campaign is scheduled, or on demand with **Auto-translate** in the Questions tab, where they can be edited. `{name}`-style placeholders are preserved. Any line without a translation is spoken in English.
- **Understanding answers.** Gemini normalises Hindi, English, and Hinglish answers to the same structured values, and free-text answers are summarised in English for analytics. Other Bulbul languages (Marathi, Tamil, Bengali, …) can be the starting or switch language.
- **Twilio trial accounts** play a "press any key to execute your code" message. Nothing runs until the callee presses a key, so upgrade the account for real campaigns.

### Natural conversation mode (default)

With **Natural conversation** on (`campaigns.conversational`, default `true`), the call is a real conversation run by an LLM (`services/voice_agent.py`). It uses Sarvam's voice-agent chat model `sarvam-105b-conversations` by default (set `CONVERSATION_LLM=gemini` to use Gemini). The greeting and first question are still pre-recorded; after that, every customer turn goes through two steps:

1. **Extract:** a focused request pulls out any survey answers the customer gave, in any order or several at once, in English, Hindi, or Hinglish. Free text is stored as an English summary.
2. **Reply:** the agent reacts to what the customer said, briefly answers side questions using **About this call** (the campaign description), and asks the next question in its own words.

What the model controls, and what the code controls:

| The model decides | The code decides |
|---|---|
| How to phrase each reply, follow-ups, small talk, transitions | **Which** question is next (the first unanswered, in order; its yes/no or rating format is kept) |
| Answers to side questions, using only the campaign description | The reply language (from the script the customer spoke in) and Devanagari output (transliterated if needed) |
| When the conversation has naturally finished | "Don't call me" → Do Not Call; busy → callback; the call can't end while required questions are unanswered; goodbyes never contain a question; a turn limit |

Each campaign has an editable **AI personality & conversation rules** prompt. The default is a natural CRM representative: short replies, reacting to what the customer said, natural transitions, no robotic phrases. If the model is slow (more than `AGENT_LLM_TIMEOUT_SECONDS`) or fails, that turn falls back to the scripted engine, so a call never breaks. In testing, the model took **about 0.55 s per turn**; together with speech-to-text and speaking the reply, a turn takes about 2.5–3 s. Turn the mode off per campaign for the fastest, fully scripted calls.

### Conversational behaviour & response time

The agent talks like a person rather than a form (`services/voice_persona.py`):
- **Between questions:** it acknowledges each answer ("Got it, thank you." / "ठीक है, धन्यवाद।"), reacts to ratings ("I'm sorry to hear that…" for 1–2, "That's great to hear!" for 4–5), and says "And one last question." before the final question.
- **Recognised off-script turns** get an instant, pre-recorded reply:
  - **Who is this / why are you calling:** explains the call, then asks the question again.
  - **Are you a robot?** Honestly says it's automated, then continues.
  - **Hold on / ek minute:** "Sure, take your time."
  - **Busy / call me later:** says goodbye and **schedules a callback** (at least 60 min later).
  - **Don't call me / not interested:** apologises, hangs up, and marks the contact **Do Not Call**, with no retries.
- **Any other question or tangent:** Gemini writes a short, safe reply in the call's language in the same request that reads the answer, and never invents company facts. The call then returns to the question. After two detours on one question, the agent moves on.

**Keeping the dead air short.** Every step between the customer finishing and the AI speaking is time the caller spends in silence:
- **Recording stops after 2 s of silence** (`ANSWER_SILENCE_SECONDS`).
- **Recognition is limited to Hindi code-mix**, which also returns English words in English. Open auto-detection misread short answers as Bengali or Tamil and caused repeat questions.
- **Gemini stays out of the live path** for clear answers. A yes/no word, a number, or a listed option is taken instantly (about 80 ms of processing). Free-text answers are saved raw and summarised by Gemini **after** the call.
- **All fixed lines are pre-generated as audio** before dialling.
- **Each turn's processing time is logged** as `Call … Q2 turn: 1.4s total (speech-to-text 1.3s, …)`.

The remaining gap, about 1–1.5 s for recording upload plus Sarvam speech-to-text, can only be removed by moving to streaming audio (Twilio Media Streams with Sarvam streaming STT/TTS).
- **No waiting on TTS mid-call.** Before dialling, the call worker pre-generates everything the call can say: the personalised greeting, all questions, the closing, and the retry prompts. Audio is cached by content, so each question is synthesised once per campaign, not once per call. Cached file names are HMACs, so the public audio URLs can't be guessed.
- **Failure-safe.** If Sarvam TTS fails for an utterance, that line falls back to Twilio `<Say>`. If STT fails, the answer is treated as not heard and the question is repeated once.

The **WhatsApp bot** also replies in the customer's language: English, Hindi (Devanagari), or Hinglish (Latin script). Gemini detects the language per message, and the fixed messages (greeting, ticket raised, etc.) exist in all three. Ticket summaries for agents are always written in English.

### Roles

`super_admin` is the platform admin: registers companies, sets limits, suspends, generates logins. It sees no company data. The other three roles work inside one company:

| | admin | supervisor | agent |
|---|---|---|---|
| Tickets & conversations | all | all | own + unassigned |
| Knowledge base / domains | manage | manage | read |
| Campaigns | manage | manage | — |
| Users | manage | read | — |
| Company profile, Twilio & WhatsApp accounts | manage | read profile | read profile |

## Project layout

```
backend/app/
  core/          config, JWT/bcrypt security, async DB, Redis
  models/        company · identity · customers · support (domains, conversations, messages, tickets…) · rag · outbound · system
  schemas/       Pydantic request/response models
  api/v1/        auth, companies (platform admin + a company's own settings), users, customers, domains, knowledge, conversations, tickets,
                 campaigns, calls, dashboard, webhooks (WhatsApp), telephony (call callbacks)
  services/      conversation_manager (workflow 1) · rag · document_processing · ticket_service
                 excel_import · campaign_service · call_service · voice_qa · answer_extraction (workflow 2)
                 company_service (registration, limits, per-company credentials) · crypto
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
