"""On a campaign call: "I can't talk right now" -> agree a time to call back.

    customer: "I'm busy"            agent: "No problem. What time should I call you back?"
    customer: "5 pm"                agent: "Sure, I'll call you today at 5:00 PM."   (and the call ends)
    customer: "10 pm"               agent: "We call between 9 AM and 7 PM. Would tomorrow at 9:00 AM work?"
    customer: "tomorrow"            agent: "Sure. What time tomorrow suits you?"
    no clear time after two tries   agent: "No problem, we'll call you back at a better time."
                                    (the old behaviour: tried again after the campaign's retry delay)

Both call engines (the scripted one and the conversational one) use this, so they behave the same.
It only decides what to say and what time was agreed; the engines speak it, and call_service
schedules the next call at that time when this call ends.
"""
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import Campaign
from app.services import voice_persona
from app.services.answer_extraction import _NO, _YES, _tokens
from app.services.support_brain import parse_callback_time, spoken_when

MAX_ASKS = 2  # how many times the agent asks for a time before falling back
MAX_DAYS_AHEAD = 7
MAX_PER_CONTACT = 3  # after this many customer-requested call-backs they count as ordinary attempts again

LINES = {
    "en": {
        "ask": "No problem. What time should I call you back?",
        "ask_again": "Sorry, I didn't get the time. When should I call you?",
        "ask_time_on_day": "Sure. What time {day} suits you?",
        "window": "We call between {start} and {end}. Would {when} work?",
        "confirm": "Sure, I'll call you {when}. Have a nice day!",
    },
    "hi": {
        "ask": "कोई बात नहीं। आपको किस समय call किया जाए?",
        "ask_again": "Sorry, समय समझ नहीं आया। आपको किस समय call किया जाए?",
        "ask_time_on_day": "ठीक है। {day} किस समय call किया जाए?",
        "window": "हम {start} से {end} के बीच call करते हैं। क्या {when} ठीक रहेगा?",
        "confirm": "ठीक है, आपको {when} call किया जाएगा। आपका दिन शुभ हो!",
    },
}
_DAY_ONLY = re.compile(r"\b(tomorrow|kal|not today|aaj nahi|aaj nahin)\b|कल|आज नहीं", re.I)
_HAS_TIME = re.compile(
    r"\d|\b(morning|afternoon|evening|night|tonight|subah|savere|dopahar|shaam|sham|raat|half|hour|minute|ghant)\w*\b"
    r"|सुबह|सवेरे|दोपहर|शाम|रात|घंट|मिनट|बजे", re.I)


# Ways of saying yes to "would tomorrow at 9 work?"
_AGREE = _YES | {"ok", "okay", "sure", "fine", "alright", "theek", "thik", "chalega", "done", "ठीक", "चलेगा", "जी"}


@dataclass
class Step:
    say: list[str]
    done: bool  # True = say goodbye and end the call
    callback_at: datetime | None = None  # the agreed time, when one was agreed


def _key(lang: str) -> str:
    return "hi" if lang.lower().startswith("hi") else "en"


def _line(lang: str, name: str, **kwargs: str) -> str:
    return LINES[_key(lang)][name].format(**kwargs)


def _now(campaign: Campaign) -> datetime:
    try:
        return datetime.now(ZoneInfo(campaign.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo("UTC"))


def _clock(t: time) -> str:
    return f"{t.hour % 12 or 12}{f':{t.minute:02d}' if t.minute else ''} {'AM' if t.hour < 12 else 'PM'}"


def in_window(campaign: Campaign, when: datetime) -> bool:
    local, start, end = when.time(), campaign.call_window_start, campaign.call_window_end
    return start <= local <= end if start <= end else (local >= start or local <= end)


def nearest_allowed(campaign: Campaign, when: datetime) -> datetime:
    """The next moment at or after `when` that the campaign may call: the window's opening time,
    later the same day if `when` is before it, otherwise the next day."""
    if in_window(campaign, when):
        return when
    start = campaign.call_window_start
    opening = when.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    return opening if opening >= when else opening + timedelta(days=1)


def _fallback(lang: str, state: dict[str, Any]) -> Step:
    """No time agreed: the customer is called again after the campaign's retry delay."""
    state.pop("cb", None)
    state["callback"] = True
    return Step([voice_persona.phrase(lang, "busy")], done=True)


def _book(campaign: Campaign, lang: str, state: dict[str, Any], when: datetime, now: datetime) -> Step:
    state.pop("cb", None)
    state["callback"] = True
    state["callback_at"] = when.isoformat()
    return Step([_line(lang, "confirm", when=spoken_when(when, now, _key(lang)))], done=True, callback_at=when)


def _ask(lang: str, state: dict[str, Any], name: str, **kwargs: str) -> Step:
    cb = state.setdefault("cb", {"asks": 0})
    if cb["asks"] >= MAX_ASKS and name != "window":
        return _fallback(lang, state)
    cb["asks"] += 1
    return Step([_line(lang, name, **kwargs)], done=False)


def active(state: dict[str, Any]) -> bool:
    """The agent has asked "what time should I call you back?" and is waiting for the answer."""
    return bool(state.get("cb"))


def step(campaign: Campaign, lang: str, state: dict[str, Any], speech: str, now: datetime | None = None) -> Step:
    """The customer said they are busy (first call), or answered our "what time?" (later calls).
    Changes `state`; the caller saves it."""
    now = now or _now(campaign)
    first = not active(state)
    cb = state.setdefault("cb", {"asks": 0})
    speech = (speech or "").strip()

    # "Would tomorrow at 9 work?" -> "yes" / "haan theek hai"
    proposed = cb.pop("proposed", None)
    tokens = _tokens(speech)
    answer = next((t for t in tokens if t in _AGREE or t in _NO), None)
    when = parse_callback_time(speech, now) if speech and _HAS_TIME.search(speech) or _DAY_ONLY.search(speech) else None
    if proposed and when is None and answer in _AGREE:
        return _book(campaign, lang, state, datetime.fromisoformat(proposed), now)

    if when is None:
        return _ask(lang, state, "ask" if first else "ask_again")
    if when > now + timedelta(days=MAX_DAYS_AHEAD) or when <= now:
        return _ask(lang, state, "ask_again")
    if _DAY_ONLY.search(speech) and not _HAS_TIME.search(speech) and cb["asks"] < MAX_ASKS:
        # "not today" / "tomorrow": the day is clear, the time is not
        day = "tomorrow" if _key(lang) == "en" else "कल"
        return _ask(lang, state, "ask_time_on_day", day=day)
    if in_window(campaign, when):
        return _book(campaign, lang, state, when, now)
    if cb.get("offered"):  # we already explained our hours once: don't go round in circles
        return _fallback(lang, state)
    offer = nearest_allowed(campaign, when)
    cb["proposed"], cb["offered"] = offer.isoformat(), True
    return _ask(lang, state, "window", start=_clock(campaign.call_window_start), end=_clock(campaign.call_window_end),
                when=spoken_when(offer, now, _key(lang)))
