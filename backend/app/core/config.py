from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    APP_NAME: str = "AI Customer Platform"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"
    # Public base URL of this API; telephony providers call back to it (use an ngrok URL in dev).
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    # Before dialling, check that PUBLIC_BASE_URL really answers from the internet (a tunnel that died
    # would otherwise make every customer hear the provider's "application error" message).
    PUBLIC_URL_CHECK: bool = True
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    LOG_LEVEL: str = "INFO"

    # --- Auth / JWT ---
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # The platform super admin (registers companies); created on first start.
    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_PASSWORD: str = "admin12345"
    # Encrypts each company's Twilio / WhatsApp secrets in the database. Empty = derived from
    # JWT_SECRET_KEY. Changing it makes stored secrets unreadable (companies must re-enter them).
    SECRETS_ENCRYPTION_KEY: str = ""

    # --- Infrastructure ---
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_platform"
    REDIS_URL: str = "redis://localhost:6379/0"
    STORAGE_DIR: str = "./storage"

    # --- Gemini ---
    # Leave GEMINI_API_KEY empty to run with the offline fake client (dev/testing only).
    GEMINI_API_KEY: str = ""
    GEMINI_CHAT_MODEL: str = "gemini-2.5-flash"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIM: int = 768

    # --- RAG / conversation ---
    RAG_TOP_K: int = 5
    RAG_MIN_SIMILARITY: float = 0.55
    CHUNK_SIZE_CHARS: int = 1200
    CHUNK_OVERLAP_CHARS: int = 200
    MAX_UNRESOLVED_TURNS: int = 3
    CONVERSATION_IDLE_HOURS: int = 24
    CONVERSATION_HISTORY_TURNS: int = 12

    # --- WhatsApp Cloud API ---
    # Numbers, access tokens and app secrets are per company (Company settings in the portal), never
    # here. The verify token is the one thing all companies share: Meta sends it when the single
    # webhook URL is registered, before any company can be identified.
    WHATSAPP_API_BASE: str = "https://graph.facebook.com/v21.0"

    # Email support through SendGrid. Each company connects its own SendGrid account in Company Settings.
    # EMAIL_ENABLED is the master switch: false = no email is sent for any company (incoming email is
    # still recorded, and replies are saved as "not sent").
    EMAIL_ENABLED: bool = True
    SENDGRID_API_BASE: str = "https://api.sendgrid.com"
    WHATSAPP_VERIFY_TOKEN: str = "verify-me"

    # --- Telephony ---
    # Twilio accounts and calling numbers are per company (Company settings in the portal), never here.
    TWILIO_API_BASE: str = "https://api.twilio.com"
    TWILIO_VALIDATE_SIGNATURE: bool = True
    VOICE_NAME: str = "Polly.Aditi"  # Twilio <Say> voice; also the fallback if Sarvam TTS fails
    VOICE_MAX_REPROMPTS: int = 1

    # --- Speech (STT/TTS) for real calls: sarvam | twilio ---
    # sarvam: Bulbul TTS audio via <Play>, answers captured with <Record> and transcribed by Saaras.
    # twilio: Twilio <Say> (Polly) + Twilio's built-in <Gather input="speech"> recognition.
    SPEECH_PROVIDER: str = "sarvam"
    SARVAM_API_KEY: str = ""
    SARVAM_API_BASE: str = "https://api.sarvam.ai"
    SARVAM_STT_MODEL: str = "saaras:v3"
    # saaras:v3 only. "codemix" = Hinglish-friendly: Hindi words in Devanagari, English words stay in English.
    SARVAM_STT_MODE: str = "codemix"
    # Empty (recommended) = recognise in the call's languages: Hindi code-mix for Hindi+English
    # campaigns, which handles English words too. "unknown" = auto-detect across all Indian
    # languages, which misfires on short answers.
    SARVAM_STT_LANGUAGE: str = ""
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_TTS_SPEAKER: str = "priya"
    SARVAM_TTS_PACE: float = 1.0
    SARVAM_TTS_SPEAKER_GENDER: str = "Female"  # match the speaker; Hindi verb forms depend on it
    SARVAM_TRANSLATE_MODE: str = "code-mixed"  # formal | modern-colloquial | classic-colloquial | code-mixed
    # --- Conversational voice agent (campaigns with conversational=true) ---
    # sarvam: Sarvam chat model built for voice agents; gemini: Gemini (needs a paid key for volume).
    CONVERSATION_LLM: str = "sarvam"
    SARVAM_CHAT_MODEL: str = "sarvam-105b-conversations"
    AGENT_NAME: str = "Priya"  # how the agent introduces itself if asked; match the TTS voice
    AGENT_LLM_TIMEOUT_SECONDS: float = 8.0  # slower than this -> scripted fallback for that turn
    ANSWER_MAX_SECONDS: int = 25  # Saaras' synchronous API accepts up to 30 s of audio
    ANSWER_SILENCE_SECONDS: int = 2  # stop recording after this much silence (dead air before we reply)

    # --- Streaming calls (Exotel Voicebot: we decide when the caller has finished speaking) ---
    STREAM_SPEECH_RMS: int = 500  # loudness (16-bit scale) above which a 20 ms frame counts as speech
    STREAM_END_SILENCE_MS: int = 900  # this much quiet after speech = the caller has finished
    STREAM_NO_SPEECH_SECONDS: int = 7  # nothing said for this long = treat the turn as silence
    # Barge-in: if the caller starts talking while the agent is speaking, the agent stops at once and listens.
    STREAM_BARGE_IN: bool = True
    STREAM_BARGE_IN_MS: int = 240  # this much continuous speech counts as an interruption (not a cough or "hmm")

    # --- Inbound support calls ---
    INBOUND_MAX_TURNS: int = 30  # a call that long is going in circles: close it politely
    INBOUND_TICKET_HISTORY: int = 5  # how many of the caller's recent tickets the agent is told about

    # --- Outbound calls ---
    DEFAULT_PHONE_REGION: str = "IN"
    SCHEDULER_INTERVAL_SECONDS: int = 10
    GLOBAL_MAX_CONCURRENT_CALLS: int = 20
    # A call stuck in a non-terminal state longer than this is marked failed.
    CALL_STALE_MINUTES: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
