"""How the voice agent sounds: what it says between questions and how it handles the customer
going off-script. Everything here is instant (no network), and every line is fixed text, so it is
pre-generated as audio before the call and never adds delay.

Customer turns that aren't answers are recognised by keyword first (fast, covers the common
cases in English, Hindi and Hinglish); anything else ambiguous goes to Gemini together with answer
extraction (see answer_extraction.extract_answer), so no extra round trip is added.
"""
import random
import re

from app.models import CampaignQuestion
from app.models.enums import AnswerType

# Each key maps to variants; one is picked at random so the agent doesn't sound like a loop.
# Hindi lines are gender-neutral (they don't depend on the voice) and written as spoken Hinglish.
PHRASES: dict[str, dict[str, list[str]]] = {
    "en": {
        "not_heard": ["Sorry, I didn't catch that."],
        "repeat": ["Sure, here is the question again."],
        "clarify": ["Sorry, I didn't quite get that. Let me ask again."],
        "ack": ["Got it, thank you.", "Okay, noted.", "Thanks for that.", "Alright, thank you."],
        "ack_good": ["That's great to hear!", "Wonderful, thank you!"],
        "ack_bad": ["I'm sorry to hear that. Thank you for being honest.", "Thanks for telling us. We'll work on that."],
        "last_question": ["And one last question."],
        "move_on": ["No problem, let's move on."],
        "wait": ["Sure, take your time."],
        "continue": ["Coming back to my question."],
        "purpose": ["We're calling to get your quick feedback about our service. It will only take a minute or two."],
        "robot": ["Yes, this is an automated assistant calling on behalf of our team. Your answers go straight to them."],
        "offtopic": ["I'm sorry, I can't help with that on this call, but our team will be happy to."],
        "busy": ["No problem at all. We'll call you back at a better time. Have a nice day!"],
        "stop": ["I'm sorry for the trouble. We won't call you again. Have a good day."],
    },
    "hi": {
        "not_heard": ["Sorry, आपकी आवाज़ clear नहीं आई।"],
        "repeat": ["ज़रूर, question फिर से सुनिए।"],
        "clarify": ["Sorry, answer समझ नहीं आया। Question फिर से सुनिए।"],
        "ack": ["ठीक है, धन्यवाद।", "जी, noted।", "अच्छा, thank you।", "जी, बिल्कुल।"],
        "ack_good": ["यह सुनकर बहुत अच्छा लगा!", "बहुत बढ़िया, thank you!"],
        "ack_bad": ["यह सुनकर अफ़सोस हुआ। बताने के लिए धन्यवाद।", "Feedback के लिए धन्यवाद, हम इसे ज़रूर improve करेंगे।"],
        "last_question": ["बस एक आख़िरी सवाल।"],
        "move_on": ["कोई बात नहीं, आगे बढ़ते हैं।"],
        "wait": ["जी, कोई बात नहीं, आराम से।"],
        "continue": ["तो, मेरा सवाल था।"],
        "purpose": ["हम आपका quick feedback लेने के लिए call कर रहे हैं। बस एक-दो मिनट लगेंगे।"],
        "robot": ["जी, यह हमारी team की तरफ़ से एक automated call है। आपके जवाब सीधे team तक पहुँचते हैं।"],
        "offtopic": ["माफ़ कीजिए, इस call पर इसमें मदद नहीं हो पाएगी, लेकिन हमारी team ज़रूर help करेगी।"],
        "busy": ["कोई बात नहीं। हम आपको किसी और समय call करेंगे। आपका दिन शुभ हो!"],
        "stop": ["परेशानी के लिए माफ़ कीजिए। हम आपको दोबारा call नहीं करेंगे। धन्यवाद।"],
    },
}

# Checked in this order; "stop" first so "not interested, don't call" never becomes "busy".
_INTENT_PHRASES: dict[str, list[str]] = {
    "stop": [
        "don't call", "do not call", "stop calling", "not interested", "remove my number", "never call",
        "call mat", "mat karo call", "interested nahi", "dobara call mat",
        "कॉल मत", "call मत", "मत करो call", "interested नहीं", "दोबारा call मत", "दोबारा कॉल मत",
    ],
    "busy": [
        "i'm busy", "i am busy", "busy right now", "call later", "call me later", "call back later",
        "call me back later", "not a good time", "i'm driving", "i am driving", "in a meeting",
        "busy hoon", "busy hu", "baad mein call", "baad me call", "abhi time nahi", "abhi baat nahi",
        "बिज़ी", "व्यस्त", "बाद में call", "बाद में कॉल", "बाद में बात", "अभी time नहीं", "अभी टाइम नहीं", "अभी बात नहीं",
    ],
    "wait": [
        "hold on", "one minute", "one second", "just a minute", "just a second", "give me a minute",
        "ek minute", "ek second", "ruko", "rukiye",
        "एक मिनट", "एक सेकंड", "रुको", "रुकिए",
    ],
    "robot": [
        "are you a robot", "is this a robot", "are you human", "am i talking to a", "is this a recording",
        "are you a machine", "are you ai", "robot ho", "robot hai",
        "रोबोट", "मशीन हो", "machine हो",
    ],
    "purpose": [
        "who is this", "who are you", "who is calling", "who's calling", "what is this about", "what is this call",
        "why are you calling", "why did you call", "where did you get my number", "how did you get my number",
        "kaun bol", "aap kaun", "kyu call", "kyon call", "kis baare", "number kahan",
        "कौन बोल", "आप कौन", "कौन है", "क्यों call", "क्यों कॉल", "किस बारे", "नंबर कहाँ", "number कहाँ",
    ],
}


# Said before the "purpose" line when the customer asks who is calling. Built per company, so it is
# pre-generated as audio with the rest of the call's lines (see voice_qa.all_utterances).
_COMPANY_INTRO = {"en": "This is a call from {company}.", "hi": "यह call {company} की तरफ़ से है।"}


def company_intro(lang: str, company_name: str | None) -> str:
    if not company_name:
        return ""
    return _COMPANY_INTRO.get(lang.split("-")[0], _COMPANY_INTRO["en"]).format(company=company_name)


def _pattern(phrases: list[str]) -> re.Pattern[str]:
    # Latin phrases match on word boundaries; Devanagari ones as plain substrings (\b doesn't work there).
    parts = [rf"\b{re.escape(p)}\b" if p.isascii() else re.escape(p) for p in phrases]
    return re.compile("|".join(parts), re.IGNORECASE)


_INTENTS = {name: _pattern(phrases) for name, phrases in _INTENT_PHRASES.items()}
CALL_ENDING_INTENTS = {"stop", "busy"}


def detect_intent(speech: str) -> str | None:
    """stop | busy | wait | robot | purpose, or None if this looks like an ordinary answer."""
    text = speech.replace("’", "'")
    return next((name for name, pattern in _INTENTS.items() if pattern.search(text)), None)


def _variants(lang: str, key: str) -> list[str]:
    return PHRASES.get(lang.split("-")[0], PHRASES["en"]).get(key) or PHRASES["en"][key]


def phrase(lang: str, key: str) -> str:
    return random.choice(_variants(lang, key))


def all_phrases(lang: str) -> list[str]:
    """Every fixed line in a language - pre-generated as audio before dialling."""
    table = PHRASES.get(lang.split("-")[0], PHRASES["en"])
    return [text for variants in table.values() for text in variants]


def acknowledgement(question: CampaignQuestion, normalized: dict, lang: str) -> str:
    """A short, human reaction to the answer just given."""
    value = normalized.get("value")
    if question.answer_type == AnswerType.RATING and isinstance(value, (int, float)):
        if value <= 2:
            return phrase(lang, "ack_bad")
        if value >= 4:
            return phrase(lang, "ack_good")
    return phrase(lang, "ack")
