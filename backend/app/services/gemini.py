"""Thin async wrapper over Gemini.

Every structured call takes a `fallback` value. It is returned when no API key is configured
(offline dev mode) or when Gemini fails / returns unparsable JSON, so business workflows degrade
gracefully instead of crashing a WhatsApp reply or a live phone call.
"""
import asyncio
import hashlib
import json
import logging
import math
import re
from typing import Any, Literal

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.core.config import settings

log = logging.getLogger(__name__)

EmbedTask = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
# Every text in a batch counts as one request against the embedding quota (100 per minute on the
# free tier), so a batch must leave room for the questions customers ask in the same minute.
_EMBED_BATCH = 50
_QUOTA_RETRIES = 12  # indexing a document waits out the per-minute quota this many times before giving up
_RETRY_IN = re.compile(r"retry in ([\d.]+)s", re.I)
_NO_AFC = types.AutomaticFunctionCallingConfig(disable=True)  # no tools are passed; skips SDK warning


class GeminiClient:
    def __init__(self) -> None:
        self.enabled = bool(settings.GEMINI_API_KEY)
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY) if self.enabled else None
        if not self.enabled:
            log.warning("GEMINI_API_KEY not set - using offline fake LLM (answers will be canned).")

    # ---------- Embeddings ----------
    async def embed(self, texts: list[str], task: EmbedTask, patient: bool = False) -> list[list[float]]:
        """patient: when the per-minute quota is used up, wait for it to refill and carry on instead
        of failing. Right for indexing a document in the background (a big one simply takes a few
        minutes); wrong for a live question, where the caller must not be kept waiting."""
        if not texts:
            return []
        if not self._client:
            return [_fake_embedding(t) for t in texts]
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            vectors.extend(await self._embed_batch(texts[i : i + _EMBED_BATCH], task, patient))
        return vectors

    async def _embed_batch(self, batch: list[str], task: EmbedTask, patient: bool) -> list[list[float]]:
        for attempt in range(_QUOTA_RETRIES + 1):
            try:
                resp = await self._client.aio.models.embed_content(
                    model=settings.GEMINI_EMBEDDING_MODEL,
                    contents=batch,
                    config=types.EmbedContentConfig(task_type=task, output_dimensionality=settings.EMBEDDING_DIM),
                )
                return [_normalize(e.values or []) for e in resp.embeddings or []]
            except genai_errors.ClientError as exc:
                if exc.code != 429 or not patient or attempt == _QUOTA_RETRIES:
                    raise
                delay = _quota_wait_seconds(exc)
                log.warning("Gemini embedding quota used up; continuing in %.0fs (wait %d of %d)", delay, attempt + 1, _QUOTA_RETRIES)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def embed_one(self, text: str, task: EmbedTask) -> list[float]:
        return (await self.embed([text], task))[0]

    # ---------- Generation ----------
    async def _generate(self, prompt: str, config: types.GenerateContentConfig) -> Any:
        """One request to the chat model. "Model busy" (5xx) is usually over within a moment, and a caller
        is waiting on the line, so that is tried once more straight away; anything else is the caller's to handle."""
        try:
            return await self._client.aio.models.generate_content(model=settings.GEMINI_CHAT_MODEL, contents=prompt, config=config)
        except genai_errors.ServerError as exc:
            log.warning("Gemini is busy (%s); trying once more", exc.code)
            await asyncio.sleep(0.4)
            return await self._client.aio.models.generate_content(model=settings.GEMINI_CHAT_MODEL, contents=prompt, config=config)

    async def generate_text(self, system: str, prompt: str, fallback: str, temperature: float = 0.3) -> str:
        if not self._client:
            return fallback
        try:
            resp = await self._generate(
                prompt,
                types.GenerateContentConfig(
                    system_instruction=system, temperature=temperature, automatic_function_calling=_NO_AFC
                ),
            )
            return (resp.text or "").strip() or fallback
        except Exception as exc:
            _log_failure("generate_text", exc)
            return fallback

    async def generate_json(
        self,
        system: str,
        prompt: str,
        fallback: dict[str, Any],
        schema: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        if not self._client:
            return fallback
        try:
            resp = await self._generate(
                prompt,
                types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    automatic_function_calling=_NO_AFC,
                ),
            )
            data = _parse_json(resp.text or "")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data
        except Exception as exc:
            _log_failure("generate_json", exc)
            return fallback


def _quota_wait_seconds(exc: Exception) -> float:
    """How long Gemini asks us to wait ("Please retry in 42.39s"), plus a little; a minute if it doesn't say."""
    match = _RETRY_IN.search(str(exc))
    return min(float(match.group(1)) + 2, 90) if match else 62.0


def _log_failure(what: str, exc: Exception) -> None:
    if isinstance(exc, genai_errors.ClientError) and exc.code == 429:
        # Quota exhausted is an expected operating condition (free tier): one line, not a traceback.
        log.warning("Gemini %s: quota exceeded (429); using offline fallback", what)
    else:
        log.exception("Gemini %s failed; using offline fallback", what)


def _parse_json(text: str) -> Any:
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _fake_embedding(text: str) -> list[float]:
    """Deterministic hashed bag-of-words vector so offline retrieval still roughly works."""
    vec = [0.0] * settings.EMBEDDING_DIM
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        vec[h % settings.EMBEDDING_DIM] += 1.0
    if not any(vec):
        vec[0] = 1.0
    return _normalize(vec)


_gemini: GeminiClient | None = None


def get_gemini() -> GeminiClient:
    global _gemini
    if _gemini is None:
        _gemini = GeminiClient()
    return _gemini
