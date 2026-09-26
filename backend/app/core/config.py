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
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]
    LOG_LEVEL: str = "INFO"

    # --- Auth / JWT ---
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_PASSWORD: str = "admin12345"

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
    WHATSAPP_API_BASE: str = "https://graph.facebook.com/v21.0"
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""  # empty = log outgoing messages instead of sending
    WHATSAPP_VERIFY_TOKEN: str = "verify-me"
    WHATSAPP_APP_SECRET: str = ""  # validates X-Hub-Signature-256; empty disables the check (dev only)

    # --- Telephony ---
    TELEPHONY_PROVIDER: str = "mock"  # mock | twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
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
    # "unknown" = auto-detect per answer, so customers can reply in Hindi, English or a mix.
    # Empty = always use the campaign's language.
    SARVAM_STT_LANGUAGE: str = "unknown"
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_TTS_SPEAKER: str = "priya"
    SARVAM_TTS_PACE: float = 1.0
    SARVAM_TTS_SPEAKER_GENDER: str = "Female"  # match the speaker; Hindi verb forms depend on it
    SARVAM_TRANSLATE_MODE: str = "code-mixed"  # formal | modern-colloquial | classic-colloquial | code-mixed
    ANSWER_MAX_SECONDS: int = 25  # Saaras' synchronous API accepts up to 30 s of audio
    ANSWER_SILENCE_SECONDS: int = 3  # stop recording after this much silence

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
