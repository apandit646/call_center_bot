"""The support brain shared by every customer-support channel.

    customer turn -> intent + domain -> domain-specific RAG -> Gemini -> answer / clarification
                                                                    +-> not resolved -> ticket

WhatsApp (services/conversation_manager.py) and inbound phone calls (services/voice_support.py)
both call `respond` and only differ in how they deliver the reply. So a company's knowledge base,
domains, ticket rules and company profile behave the same on a chat and on a call.

Everything runs inside one company: its customers, its domains, its knowledge base and its
company profile (so the assistant can say who it works for).

On a phone call the brain also gets the caller's existing tickets (`CallerContext`), so it can tell
their status, add what the caller says to one of them, and avoid raising duplicates.

A call is also handled in ONE model request per turn (`_call_turn`: understand the caller and write
the reply together) instead of two. That halves the wait before the agent speaks and the quota a
call uses, and lets the reply be written by something that knows the company, the caller's requests
and the knowledge base all at once - which is what makes it sound like a person.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Company, Conversation, Domain, Message, Ticket
from app.models.enums import (
    OPEN_TICKET_STATUSES,
    Channel,
    ConversationStatus,
    MessageRole,
    TicketPriority,
    TicketSource,
)
from app.services import ticket_service, voice_persona
from app.services.company_service import profile_text
from app.services.gemini import get_gemini
from app.services.rag import format_context, retrieve

log = logging.getLogger(__name__)

INTENTS = [
    "question", "provide_info", "company_info", "satisfied", "not_resolved", "human_agent", "greeting", "other",
]
# Only a caller can refer to "my earlier request" out of the blue or end the conversation by saying bye.
PHONE_INTENTS = [*INTENTS, "ticket_status", "goodbye"]

ANALYZE_SYSTEM = """You are the routing layer of a customer-support assistant {medium}.
Classify the customer's LATEST message using the conversation so far.
intent:
- question: asks something or describes a problem
- provide_info: supplies details the assistant asked for (ids, dates, amounts...)
- company_info: asks about the company itself - its name, what it does or sells, address, contact details
- satisfied: the issue is resolved / they are thankful and done
- not_resolved: says the answer did not help or the problem persists
- human_agent: explicitly asks for a human / agent / complaint to be raised
- greeting: only a greeting with no request
- other: anything else
domain_code: the single best matching support domain code from the list, or null if none fits.
Keep the current domain unless the customer clearly switched topic.
language: the language of the latest message - "en" (English), "hi" (Hindi in Devanagari script) or
"hinglish" (Hindi/English mix written in Latin letters, e.g. "mera payment cut gaya but plan active nahi hua")."""

ANALYZE_PHONE_EXTRA = """
Two more intents exist on a phone call:
- ticket_status: asks about, or says they are calling about, one of their existing support requests listed below
- goodbye: has nothing more to ask / says bye
provide_info also covers adding more detail to one of their existing support requests.
ticket_number: the number of the existing support request the latest message is about, or null if it is about
something new. Never guess a number that is not in the list."""

ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "domain_code": {"type": ["string", "null"]},
        "domain_confidence": {"type": "number"},
        "language": {"type": "string", "enum": ["en", "hi", "hinglish"]},
    },
    "required": ["intent", "domain_code", "domain_confidence", "language"],
}
ANALYZE_PHONE_SCHEMA = {
    "type": "object",
    "properties": {
        **ANALYZE_SCHEMA["properties"],
        "intent": {"type": "string", "enum": PHONE_INTENTS},
        "ticket_number": {"type": ["integer", "null"]},
    },
    "required": [*ANALYZE_SCHEMA["required"], "ticket_number"],
}

ANSWER_SYSTEM = """You are a friendly, concise customer-support assistant {medium} for the company in
the "Company profile". Answer ONLY from the knowledge base context and the company profile provided.
Never invent policies, amounts, dates or links.
Decide a status:
- answered: the context answers the question. End by asking if this resolved their issue.
- clarify: you need specific details from the customer (e.g. transaction id, order number, email) to help. Ask for them.
- escalate: the context cannot resolve it, it needs account-specific action by staff, or the customer already gave
  the details and it still cannot be resolved. Tell them you will raise a support request.
{style}
Reply in the language given in the prompt; the knowledge base may be in English, so translate as needed.
Keep product names, amounts, ids and technical terms as they are."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "status": {"type": "string", "enum": ["answered", "clarify", "escalate"]},
    },
    "required": ["reply", "status"],
}

COMPANY_SYSTEM = """You are a friendly, concise customer-support assistant {medium} for the company in
the "Company profile". The customer is asking about the company itself. Answer ONLY from the company profile.
If the profile does not say, tell them you don't have that detail and offer to help with their issue.
{style}
Reply in the language given in the prompt; keep names, addresses, numbers and links exactly as written."""

TICKET_STATUS_SYSTEM = """You are a friendly customer-support assistant speaking on a phone call.
The caller is asking about one of their existing support requests. Tell them, from the request details given,
its number, what it is about in a few words, and its current status. Do not promise dates or outcomes that are
not in the details. End by asking whether they would like to add anything to it.
{style}
Reply in the language given in the prompt."""

CALL_TURN_SYSTEM = """You are a customer-support executive of the company in the "Company profile", talking with a
caller on a live phone call. You are a warm, attentive person on the phone - not a script and not a search engine.

KNOW YOUR COMPANY. The profile says who you work for, what the company does and what it sells. Use it to understand
what the caller is talking about, and answer anything about the company itself (its name, what it does, its products
and services, address, contact details) from it, in your own words.

Each turn, read the caller's LATEST words together with the conversation so far, and return:

intent:
- question: asks something or describes a problem
- provide_info: gives details you asked for, or adds detail to one of their existing support requests
- company_info: asks about the company itself - who you are, what the company does or sells, where it is, how to reach it
- ticket_status: asks about, or says they are calling about, one of their existing support requests listed in the prompt
- satisfied: their issue is sorted / they are thankful and done
- goodbye: has nothing more to ask / says bye
- not_resolved: they TRIED what you said and it did not work, or the problem is still there. (Not understanding you,
  or asking you to repeat or to say it in another language, is NOT this: that is "question" - explain it again.)
- human_agent: clearly asks for a person, a call back, or for a complaint to be raised
- greeting: only a greeting, no request yet
- other: anything else
domain_code: the best matching support domain code from the list, or null. Keep the current one unless they changed topic.
ticket_number: the number of the existing request the latest words are about, or null. Never guess a number not in the list.
abusive: true ONLY if the caller swears at, insults or threatens you or the company. Being angry, upset, impatient or
complaining strongly is NOT abusive - that caller needs extra patience and a sincere apology.
reply: what you say next, out loud.
status (for question / provide_info / other):
- answered: you answered from the knowledge base or the company profile
- clarify: you need one specific thing from them to help; ask for it
- escalate: the knowledge cannot solve it, or it needs someone to act on their account. Tell them, kindly, that you
  are raising a request for the team.

HOW YOU SPEAK:
- Like a real person on the phone: short, warm, everyday words. 1-2 short sentences, at most 40 words.
- React to what they JUST said. A small natural acknowledgement is fine ("Sure", "I understand", "जी", "अच्छा") but
  never the same opening twice in a row.
- Never read document text aloud. No headings, bullet points, codes or half sentences from the knowledge base:
  understand it and explain the answer simply, the way you would tell a friend.
- Never repeat a sentence you already said on this call. If they did not understand, or ask you to say it again or
  in another language, explain it again differently and more simply.
- Do not finish every answer with "did that help?". Ask it only now and then, when it fits.
- If they sound upset, say sorry once, sincerely, and help. Do not argue.
- Facts come ONLY from the knowledge base context and the company profile. Never invent policies, prices, dates,
  discounts or links. If you do not know, say so honestly and offer to have the team call them back.
- company_info: answer from the company profile. greeting: greet them back and ask how you can help.
- ticket_status: tell them the request's number (digit by digit), what it is about in a few words and where it
  stands; promise nothing that is not written there; ask if they want to add anything.
- No lists, links, symbols, markdown or emojis: every word is spoken by a voice.
- Your voice is {gender}. In Hindi, use {gender} verb forms for yourself ({verbs}) - never the other gender's.
{manners}
Reply in the language given in the prompt; the knowledge base may be in English, so translate as needed, but keep
product names, amounts, ids and technical terms as they are."""

CALL_TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": PHONE_INTENTS},
        "domain_code": {"type": ["string", "null"]},
        "ticket_number": {"type": ["integer", "null"]},
        "abusive": {"type": "boolean"},
        "reply": {"type": "string"},
        "status": {"type": "string", "enum": ["answered", "clarify", "escalate"]},
    },
    "required": ["intent", "domain_code", "ticket_number", "abusive", "reply", "status"],
}

TICKET_SYSTEM = """Summarise a customer-support conversation for a human agent.
Always write in English, translating anything the customer said in Hindi or Hinglish.
issue: one line describing the problem.
summary: 2-5 short lines with all facts the customer gave (ids, amounts, dates) and what was already tried.
priority: low | medium | high | urgent (money lost, service down or security -> high/urgent)."""

TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "issue": {"type": "string"},
        "summary": {"type": "string"},
        "priority": {"type": "string", "enum": [p.value for p in TicketPriority]},
    },
    "required": ["issue", "summary", "priority"],
}

# How the agent talks on a call (inbound support uses the same manners as the outbound sales agent).
CALL_MANNERS = (
    "On this call: sound like a helpful person, never robotic or scripted. Always answer what the caller is saying "
    "NOW; if they change the topic, follow the new topic first and do not go back to finish an earlier sentence "
    "(never say \"as I was saying\"). Ask only one question at a time. If they disagree or are unhappy, acknowledge "
    "it and do not argue. Never make up information, prices or discounts; if you do not know, say you will check or "
    "that the team will help. If they clearly want to end the call, close politely and do not keep asking questions."
)
_WAIT = re.compile(r"^\s*(wait|stop|hold on|one (minute|second|sec)|just a (minute|second|sec)|ek minute|ruko|rukiye|ruk)\b"
                   r"|^\s*(रुको|रुकिए|एक मिनट|एक सेकंड)", re.I)

# Swearing at the agent, in English and Hindi (Roman or Devanagari). The model also flags abuse it
# recognises; this list makes the rule work even when the model cannot be reached.
_ABUSE = re.compile(
    r"\b(fuck\w*|motherfuck\w*|bitch\w*|bastard\w*|asshole\w*|dickhead\w*|son of a bitch|madarchod\w*|maderchod\w*"
    r"|behenchod\w*|bhenchod\w*|bahenchod\w*|chutiy\w*|chutia\w*|gaand\w*|gandu\w*|bhosdi\w*|bhosad\w*|randi\w*"
    r"|harami\w*|haramzad\w*|kamin[ae]\w*|kutt[ei] k[ae]\w*|lund\w*|lawd[ae]\w*|teri maa k\w*)\b"
    r"|मादरचोद|बहनचोद|भेनचोद|चूतिय|चुतिय|गांडू|गाँडू|भोसड़ी|भोसडी|रंडी|हरामी|हरामज़ाद|हरामजाद|कमीन|कुत्ते क|साले कुत्ते|लौड़",
    re.I)

# How each channel is described to the model, and how its replies must look.
_MEDIUM = {Channel.WHATSAPP: "replying on WhatsApp", Channel.PHONE: "speaking with a caller on a live phone call"}
_STYLE = {
    Channel.WHATSAPP: {
        "answer": "Keep replies under 120 words, plain text, no markdown headings.",
        "company": "Keep replies under 80 words, plain text, no markdown headings.",
    },
    Channel.PHONE: {
        "answer": "Your reply is SPOKEN aloud: 1-2 short sentences, at most 40 words. No lists, links, markdown or emojis.\n"
                  + CALL_MANNERS,
        "company": "Your reply is SPOKEN aloud: 1-2 short sentences, at most 35 words. No lists, links, markdown or emojis.\n"
                   + CALL_MANNERS,
    },
}

_SATISFIED = re.compile(
    r"\b(thanks|thank you|thx|resolved|solved|got it|that helps|perfect|great|dhanyavaad|dhanyawad|shukriya"
    r"|ho gaya|ho gya)\b|धन्यवाद|शुक्रिया|हो गया", re.I)
_NOT_RESOLVED = re.compile(
    r"\b(not (resolved|solved|working|helpful)|didn'?t help|still|same issue|useless|nahi hua|abhi bhi|ab bhi"
    r"|kaam nahi)\b|नहीं हुआ|अभी भी|काम नहीं", re.I)
_HUMAN = re.compile(
    r"\b(human|agent|representative|real person|complaint|raise (a )?(ticket|issue)|insaan)\b|शिकायत|एजेंट", re.I)
_COMPANY = re.compile(
    r"\b(company name|your company|which company|what company|about (your|the) company|who are you"
    r"|your (office )?address|where are you located|company ka naam|kaun si company|konsi company|aap kaun)\b"
    r"|कंपनी का नाम|कौन सी कंपनी|आप कौन", re.I)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|hii+|good (morning|afternoon|evening)|namaste|namaskar|नमस्ते|नमस्कार)[\s!.,?]*$", re.I)
_TICKET_REF = re.compile(
    r"\b(my (earlier |previous |last |old )?(ticket|request|complaint)|(ticket|request|complaint) (status|number)"
    r"|status of|(earlier|previous|last|old) (ticket|request|complaint)|pichli (request|complaint)"
    r"|purani (request|complaint)|request ka status)\b|पिछली (request|शिकायत)|पुरानी (request|शिकायत)|request का status",
    re.I)
# "Also, ..." right after a request was discussed: more detail for it, not a new topic.
_ADDING = re.compile(r"^\s*(also|and also|and another thing|one more thing|plus|aur|ek aur baat)\b|^\s*(और|एक और)", re.I)
_GOODBYE = re.compile(
    r"\b(bye|goodbye|that'?s all|that is all|nothing else|no thanks|no thank you|bas itna hi)\b"
    r"|बस इतना|और कुछ नहीं|अलविदा", re.I)


# Fixed bot messages in each supported language. Gemini-written answers follow the same language.
LANGUAGES = ("en", "hi", "hinglish")
LANGUAGE_INSTRUCTION = {
    "en": "English",
    "hi": "Hindi in Devanagari script (keep common English words like payment, refund, app, OTP in English)",
    "hinglish": "Hinglish - Hindi mixed with English, written in Latin letters, the way the customer writes",
}
# A voice needs Devanagari to pronounce Hindi well, so there is no romanised option on a call.
VOICE_LANGUAGE_INSTRUCTION = {
    "en": "English. Simple, friendly Indian English.",
    "hi": "Hindi written in DEVANAGARI script, the way people really talk: keep common English words like "
          "payment, refund, app, request, team in English letters. Never write Hindi in Roman letters.",
}
REPLIES = {
    "en": {
        "greeting": "Hello! I'm the virtual support assistant for {company}. How can I help you today?",
        "satisfied": "Glad I could help! If you need anything else, just send a message here.",
        "which_topic": "Sure, I can help. Could you tell me a bit more? Is this about {topics}?",
        "sorry": "I'm sorry I couldn't resolve this for you here.",
        "ticket_raised": "I've raised support request #{number}. Our team will review it and get back to you.",
        "ticket_update": "Thanks, I've added this to your support request #{number}. Our team will get back to you soon.",
        "found": "Here's what I found:",
        "resolved_q": "Did this resolve your issue?",
        "need_details": "I couldn't find an answer to that. Could you share a few more details "
                        "(for example an order or transaction ID)?",
    },
    "hi": {
        "greeting": "नमस्ते! मैं {company} का virtual support assistant हूँ। बताइए, मैं आपकी क्या मदद कर सकता हूँ?",
        "satisfied": "खुशी हुई कि मदद हो पाई! आगे कुछ भी चाहिए तो यहीं message कीजिए।",
        "which_topic": "ज़रूर, मैं मदद करता हूँ। थोड़ा और बताइए - क्या यह {topics} से जुड़ा है?",
        "sorry": "माफ़ कीजिए, मैं यहाँ यह issue solve नहीं कर पाया।",
        "ticket_raised": "मैंने आपकी support request #{number} raise कर दी है। हमारी team इसे देखकर आपसे संपर्क करेगी।",
        "ticket_update": "धन्यवाद, मैंने यह आपकी support request #{number} में जोड़ दिया है। हमारी team जल्द संपर्क करेगी।",
        "found": "मुझे यह जानकारी मिली:",
        "resolved_q": "क्या इससे आपका issue solve हुआ?",
        "need_details": "मुझे इसका जवाब नहीं मिला। क्या आप थोड़ी और जानकारी दे सकते हैं (जैसे order या transaction ID)?",
    },
    "hinglish": {
        "greeting": "Namaste! Main {company} ka virtual support assistant hoon. Batayiye, main aapki kya madad kar sakta hoon?",
        "satisfied": "Khushi hui ki madad ho payi! Aage kuch bhi chahiye toh yahin message kijiye.",
        "which_topic": "Zaroor, main madad karta hoon. Thoda aur batayiye - kya yeh {topics} se related hai?",
        "sorry": "Sorry, main yahan yeh issue solve nahi kar paya.",
        "ticket_raised": "Maine aapki support request #{number} raise kar di hai. Hamari team ise dekh kar aapse contact karegi.",
        "ticket_update": "Thanks, maine yeh aapki support request #{number} mein add kar diya hai. Team jaldi contact karegi.",
        "found": "Mujhe yeh jaankari mili:",
        "resolved_q": "Kya isse aapka issue solve hua?",
        "need_details": "Mujhe iska jawab nahi mila. Kya aap thodi aur details de sakte hain (jaise order ya transaction ID)?",
    },
}
# Spoken lines for phone calls: short, no symbols, and gender-neutral in Hindi so they fit any voice.
# {number} is given digit by digit ("1 0 0 2 3") so the voice reads a ticket number, not a quantity.
VOICE_REPLIES = {
    "en": {
        "greeting": "How can I help you today?",
        "satisfied": "Thank you for calling {company}. Have a nice day!",
        "which_topic": "Sure, I can help. Is this about {topics}?",
        "sorry": "I'm sorry, I couldn't resolve this on the call.",
        "ticket_raised": "I've raised support request number {number}. Our team will call you back.",
        "ticket_update": "Thank you. I've added that to your request number {number}. Our team will get back to you soon.",
        "found": "Here is what I found.",
        "resolved_q": "Did that help?",
        "need_details": "I couldn't find an answer to that. Could you tell me a little more?",
        "no_tickets": "I don't see an earlier request from this number. Please tell me what the problem is.",
        "ask_phone": "I can raise a request for our team. Please tell me your ten digit mobile number.",
        "ticket_status": "Your request number {number}, about {issue}, is {status}. Would you like to add anything to it?",
        "go_ahead": "Sure, go ahead.",
        "say_again": "Sorry, I missed that. Could you say it once more?",
        "abuse_warning": "I really do want to help you, but please speak respectfully. If this language continues, "
                         "I will have to end the call. Now, please tell me, how can I help?",
        "abuse_bye": "I am ending this call now because of the language being used. You are welcome to call again. Thank you.",
    },
    "hi": {
        "greeting": "बताइए, आपकी क्या मदद की जाए?",
        "satisfied": "{company} को call करने के लिए धन्यवाद। आपका दिन शुभ हो!",
        "which_topic": "ज़रूर। क्या यह {topics} से जुड़ा है?",
        "sorry": "माफ़ कीजिए, यह issue call पर solve नहीं हो पाया।",
        "ticket_raised": "आपकी support request number {number} raise हो गई है। हमारी team आपको call करेगी।",
        "ticket_update": "धन्यवाद। यह जानकारी आपकी request number {number} में जोड़ दी गई है। हमारी team जल्द संपर्क करेगी।",
        "found": "मुझे यह जानकारी मिली।",
        "resolved_q": "क्या इससे मदद मिली?",
        "need_details": "इसका जवाब मुझे नहीं मिला। क्या आप थोड़ा और बता सकते हैं?",
        "no_tickets": "इस number से कोई पुरानी request नहीं दिख रही। बताइए, problem क्या है?",
        "ask_phone": "Request raise करने के लिए कृपया अपना दस अंकों का mobile number बताइए।",
        "ticket_status": "आपकी request number {number}, {issue} के बारे में, अभी {status} है। क्या आप इसमें कुछ जोड़ना चाहेंगे?",
        "go_ahead": "जी, बताइए।",
        "say_again": "Sorry, ठीक से समझ नहीं आया। क्या आप एक बार फिर बताएँगे?",
        "abuse_warning": "आपकी मदद ज़रूर की जाएगी, लेकिन कृपया ठीक भाषा में बात कीजिए। ऐसी भाषा जारी रही तो यह call "
                         "काटनी पड़ेगी। अब बताइए, आपकी क्या मदद की जाए?",
        "abuse_bye": "ऐसी भाषा की वजह से यह call अभी समाप्त की जा रही है। आप दोबारा call कर सकते हैं। धन्यवाद।",
    },
}
TICKET_STATUS_WORDS = {
    "en": {"open": "open and with our team", "in_progress": "being worked on",
           "pending_customer": "waiting for some information from you", "resolved": "marked as resolved",
           "closed": "closed"},
    "hi": {"open": "open", "in_progress": "in progress", "pending_customer": "आपकी जानकारी के इंतज़ार में",
           "resolved": "resolved", "closed": "closed"},
}
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HINGLISH_MARKERS = {
    "hai", "hain", "nahi", "nahin", "kya", "mera", "meri", "mere", "mujhe", "aap", "aapka", "raha", "rahi", "hua",
    "hui", "gaya", "gayi", "abhi", "bhi", "kab", "kyun", "kaise", "paisa", "paise", "kijiye", "karo", "tha", "thi",
    "haan", "yaar", "bhai", "karna", "karni", "chahiye", "gya", "kar", "se", "baat", "ka", "ke", "ki", "mein",
    "hoon", "hu", "toh", "kuch", "liye", "milega", "mila", "namaste", "namaskar", "ji",
}


def detect_language(text: str) -> str:
    """Offline guess used when Gemini is unavailable (and for quick follow-ups)."""
    letters = [ch for ch in text if ch.isalpha()]
    if letters and sum(1 for ch in letters if _DEVANAGARI.match(ch)) / len(letters) > 0.3:
        return "hi"
    tokens = re.findall(r"[a-z]+", text.lower())
    markers = sum(1 for t in tokens if t in _HINGLISH_MARKERS)
    return "hinglish" if markers >= 2 or (markers == 1 and len(tokens) <= 4) else "en"


def voice_lang(lang: str) -> str:
    """The spoken-line table for a brain language: anything Hindi is spoken as Devanagari Hindi."""
    return "en" if lang == "en" else "hi"


def spoken_number(number: int) -> str:
    return " ".join(str(number))


def _t(lang: str, key: str, channel: str = Channel.WHATSAPP, **kwargs: Any) -> str:
    if channel == Channel.PHONE:
        if "number" in kwargs:
            kwargs["number"] = spoken_number(kwargs["number"])
        return VOICE_REPLIES[voice_lang(lang)][key].format(**kwargs)
    return REPLIES.get(lang, REPLIES["en"])[key].format(**kwargs)


def _language_instruction(lang: str, channel: str) -> str:
    if channel == Channel.PHONE:
        return VOICE_LANGUAGE_INSTRUCTION[voice_lang(lang)]
    return LANGUAGE_INSTRUCTION.get(lang, "English")


def _system(template: str, channel: str, style: str = "answer") -> str:
    return template.replace("{medium}", _MEDIUM[Channel(channel)]).replace("{style}", _STYLE[Channel(channel)][style])


@dataclass
class BrainReply:
    """What to tell the customer next. The channel delivers `text` (sends it or speaks it)."""

    text: str
    # greeting | company_info | satisfied | answered | clarify | escalated | ticket_status | ticket_updated | need_phone
    # | abuse_warning | abuse_end
    kind: str
    meta: dict[str, Any] = field(default_factory=dict)
    ticket: Ticket | None = None  # the ticket this turn raised, updated or talked about
    end: bool = False  # the customer is done; a phone call says goodbye and hangs up


@dataclass
class _Draft:
    """What the one model request of a call turn wrote. The steps below use it instead of asking the model again."""

    reply: str = ""
    status: str = "answered"
    chunks: list[Any] = field(default_factory=list)  # the knowledge the reply was written from
    failed: bool = False  # the model could not be reached (e.g. quota used up): nothing was written


@dataclass
class CallerContext:
    """What a phone call knows about the caller before they say anything."""

    tickets: list[Ticket] = field(default_factory=list)  # their recent tickets, newest first
    focus_ticket: Ticket | None = None  # the one being discussed right now, if any
    can_raise_ticket: bool = True  # False while we don't know who is calling (caller id withheld)


# --------------------------------------------------------------------------- entry point
async def respond(
    db: AsyncSession,
    conversation: Conversation,
    company: Company,
    text: str,
    channel: str = Channel.WHATSAPP,
    lang: str | None = None,
    caller: CallerContext | None = None,
) -> BrainReply:
    """Decide the reply to the customer's latest message (already saved on the conversation).

    lang: fixed by the channel when it knows better (a call follows the caller's spoken language);
    otherwise detected from the message.
    """
    caller = caller or CallerContext()
    if channel == Channel.PHONE and len(text.split()) <= 8 and (
        _WAIT.search(text) or voice_persona.detect_intent(text) == "wait"
    ):
        # "Wait, I have a question" / "hold on": stop and listen; whatever they say next is what matters.
        return BrainReply(_t(lang or detect_language(text), "go_ahead", channel), "greeting")
    domains = await domains_of(db, company.id)
    history = await history_of(db, conversation)
    draft: _Draft | None = None
    if channel == Channel.PHONE and get_gemini().enabled:
        analysis, draft = await _call_turn(
            db, conversation, company, domains, text, history, lang or detect_language(text), caller
        )
    else:
        analysis = await _analyze(text, history, domains, conversation.domain, channel, caller.tickets)
    intent = analysis["intent"]
    if lang is None:
        lang = analysis.get("language") if analysis.get("language") in LANGUAGES else detect_language(text)
    if channel == Channel.PHONE and (analysis.get("abusive") or _ABUSE.search(text)):
        return await _abuse(db, conversation, lang)
    referenced = _referenced_ticket(analysis, caller)
    # "Payment nahi hua" / "still not working" on the first message describes the problem; it can
    # only mean "your answer didn't help" once the bot has actually answered something - or when a
    # caller is talking about a request they already have.
    if intent == "not_resolved" and not (referenced or caller.focus_ticket) and not await _bot_has_answered(db, conversation):
        intent = "question"

    # Feedback turns ("that didn't help", "thanks") describe the current issue, so they never re-route it.
    can_switch = intent not in ("company_info", "ticket_status", "goodbye") and (
        conversation.domain is None or intent in ("question", "provide_info", "other")
    )
    domain = (_domain_by_code(domains, analysis.get("domain_code")) if can_switch else None) or conversation.domain
    if domain and domain.id != conversation.domain_id:
        conversation.domain_id = domain.id
        conversation.domain = domain

    if channel == Channel.PHONE:
        if intent in ("goodbye", "satisfied"):
            if conversation.status != ConversationStatus.ESCALATED:
                conversation.status = ConversationStatus.RESOLVED
            conversation.unresolved_turns = 0
            return BrainReply(_t(lang, "satisfied", channel, company=company.name), "satisfied", end=True)
        if intent == "ticket_status":
            return await _ticket_status(caller, referenced, text, history, lang, draft)
        # More about a request they already have: add it there instead of raising a duplicate.
        target = referenced or caller.focus_ticket
        if target and target.status in OPEN_TICKET_STATUSES and intent in ("provide_info", "not_resolved", "human_agent", "other"):
            return await add_to_ticket(db, target, text, lang, channel)

    if intent == "greeting" and not conversation.domain_id:
        greeting = draft.reply if draft and draft.reply else _t(lang, "greeting", channel, company=company.name)
        return BrainReply(greeting, "greeting")
    if intent == "company_info":
        return await _answer_company_question(company, text, history, lang, channel, draft)
    if intent == "satisfied":
        conversation.status = ConversationStatus.RESOLVED
        conversation.unresolved_turns = 0
        return BrainReply(_t(lang, "satisfied", channel, company=company.name), "satisfied")
    if intent in ("not_resolved", "human_agent"):
        return await _raise(db, conversation, intent, lang, channel, caller)
    if draft is not None:
        return await _answer_from_draft(db, conversation, draft, lang, caller)
    return await answer_with_rag(db, conversation, domains, text, history, lang, channel, caller)


async def _call_turn(
    db: AsyncSession, conversation: Conversation, company: Company, domains: list[Domain], text: str, history: str,
    lang: str, caller: CallerContext,
) -> tuple[dict[str, Any], _Draft]:
    """One model request for a turn of a phone call: what the caller wants AND what to say back.

    The knowledge search covers the company's whole knowledge base (not one domain), so the caller is
    never asked "is this about billing or technical?" before getting an answer."""
    channel = Channel.PHONE
    fallback = {
        **_heuristic_analysis(text, domains, channel, caller.tickets),
        "abusive": bool(_ABUSE.search(text)), "reply": "", "status": "answered",
    }
    chunks = await retrieve(db, f"{history[-600:]}\n{text}", company.id, None)
    domain_list = "\n".join(f"- {d.code}: {d.name}. {d.description or ''}" for d in domains) or "(none)"
    prompt = (
        f"Company profile:\n{profile_text(company) or '(not provided)'}\n\n"
        f"Support domains:\n{domain_list}\n"
        f"Current domain: {conversation.domain.code if conversation.domain else 'none'}\n\n"
        f"The caller's existing support requests:\n{tickets_text(caller.tickets)}\n\n"
        f"Knowledge base context:\n{format_context(chunks)}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"Caller's latest words:\n{text}\n\n"
        f"Reply language: {_language_instruction(lang, channel)}"
    )
    # The reply is spoken by the configured voice, and Hindi verbs show the speaker's gender.
    female = settings.SARVAM_TTS_SPEAKER_GENDER.lower() == "female"
    system = (
        CALL_TURN_SYSTEM.replace("{manners}", CALL_MANNERS)
        .replace("{gender}", "female" if female else "male")
        .replace("{verbs}", '"कर सकती हूँ", "बताती हूँ"' if female else '"कर सकता हूँ", "बताता हूँ"')
    )
    result = await get_gemini().generate_json(system, prompt, fallback, CALL_TURN_SCHEMA, temperature=0.4)
    failed = result is fallback
    if result.get("intent") not in PHONE_INTENTS:
        result["intent"] = "question"
    status = result.get("status") if result.get("status") in ("answered", "clarify", "escalate") else "answered"
    return result, _Draft((result.get("reply") or "").strip(), status, chunks, failed)


async def _answer_from_draft(
    db: AsyncSession, conversation: Conversation, draft: _Draft, lang: str, caller: CallerContext
) -> BrainReply:
    """The caller asked something and the call's model request already wrote the answer."""
    channel = Channel.PHONE
    if draft.failed or not draft.reply:
        # The model could not be reached. Reading raw document text to a caller helps nobody: ask them
        # to repeat (the model is usually back within seconds), and after a few of these hand it to the team.
        conversation.unresolved_turns += 1
        if conversation.unresolved_turns > settings.MAX_UNRESOLVED_TURNS:
            return await _raise(db, conversation, "assistant_unavailable", lang, channel, caller)
        return BrainReply(_t(lang, "say_again", channel), "clarify", meta={"assistant_unavailable": True})
    return await _answered(db, conversation, draft.status, draft.reply, draft.chunks, lang, channel, caller)


async def _abuse(db: AsyncSession, conversation: Conversation, lang: str) -> BrainReply:
    """The caller is swearing at the agent: one polite warning, and the call ends if it happens again."""
    channel = Channel.PHONE
    metas = await db.scalars(
        select(Message.meta).where(Message.conversation_id == conversation.id, Message.role == MessageRole.ASSISTANT)
    )
    if any((meta or {}).get("abuse_warning") for meta in metas):
        log.info("Conversation %s: abusive language again after a warning; ending the call", conversation.id)
        return BrainReply(_t(lang, "abuse_bye", channel), "abuse_end", meta={"abuse_end": True}, end=True)
    return BrainReply(_t(lang, "abuse_warning", channel), "abuse_warning", meta={"abuse_warning": True})


async def domains_of(db: AsyncSession, company_id: Any) -> list[Domain]:
    return list(
        (await db.scalars(select(Domain).where(Domain.company_id == company_id, Domain.is_active.is_(True)))).all()
    )


# --------------------------------------------------------------------------- steps
async def _analyze(
    text: str, history: str, domains: list[Domain], current: Domain | None, channel: str, tickets: list[Ticket]
) -> dict[str, Any]:
    phone = channel == Channel.PHONE
    fallback = _heuristic_analysis(text, domains, channel, tickets)
    domain_list = "\n".join(f"- {d.code}: {d.name}. {d.description or ''}" for d in domains) or "(none)"
    prompt = (
        f"Support domains:\n{domain_list}\n\n"
        f"Current domain: {current.code if current else 'none'}\n\n"
        + (f"The caller's existing support requests:\n{tickets_text(tickets)}\n\n" if phone else "")
        + f"Conversation so far:\n{history}\n\n"
        f"Latest customer message:\n{text}"
    )
    system = _system(ANALYZE_SYSTEM, channel) + (ANALYZE_PHONE_EXTRA if phone else "")
    result = await get_gemini().generate_json(
        system, prompt, fallback, ANALYZE_PHONE_SCHEMA if phone else ANALYZE_SCHEMA
    )
    if result.get("intent") not in (PHONE_INTENTS if phone else INTENTS):
        result["intent"] = "question"
    return result


def tickets_text(tickets: list[Ticket]) -> str:
    """The caller's tickets as the model sees them."""
    if not tickets:
        return "(none)"
    return "\n".join(
        f"- #{t.number} [{t.status}{', assigned to ' + t.assigned_to.full_name if t.assigned_to else ''}] "
        f"raised {t.created_at:%Y-%m-%d}: {t.issue[:200]}"
        for t in tickets
    )


def _referenced_ticket(analysis: dict[str, Any], caller: CallerContext) -> Ticket | None:
    number = analysis.get("ticket_number")
    return next((t for t in caller.tickets if t.number == number), None) if number else None


async def answer_with_rag(
    db: AsyncSession, conversation: Conversation, domains: list[Domain], text: str, history: str,
    lang: str = "en", channel: str = Channel.WHATSAPP, caller: CallerContext | None = None,
) -> BrainReply:
    caller = caller or CallerContext()
    if conversation.domain is None:
        conversation.unresolved_turns += 1
        topics = ", ".join(d.name for d in domains) or "billing, technical or account issues"
        if conversation.unresolved_turns >= settings.MAX_UNRESOLVED_TURNS:
            return await _raise(db, conversation, "domain_unknown", lang, channel, caller)
        return BrainReply(_t(lang, "which_topic", channel, topics=topics), "clarify")

    # Include the previous customer turn so short follow-ups ("TXN12345") still retrieve well.
    chunks = await retrieve(db, f"{history[-600:]}\n{text}", conversation.company_id, conversation.domain.id)
    fallback = _fallback_answer(chunks, lang, channel)
    company = await db.get(Company, conversation.company_id)
    prompt = (
        f"Company profile:\n{profile_text(company) or '(not provided)'}\n\n"
        f"Domain: {conversation.domain.name}\n\n"
        f"Knowledge base context:\n{format_context(chunks)}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"Customer's latest message:\n{text}\n\n"
        f"Reply language: {_language_instruction(lang, channel)}"
    )
    result = await get_gemini().generate_json(
        _system(ANSWER_SYSTEM, channel), prompt, fallback, ANSWER_SCHEMA, temperature=0.3
    )
    status = result.get("status", "answered")
    reply = (result.get("reply") or fallback["reply"]).strip()
    return await _answered(db, conversation, status, reply, chunks, lang, channel, caller)


async def _answered(
    db: AsyncSession, conversation: Conversation, status: str, reply: str, chunks: list[Any], lang: str, channel: str,
    caller: CallerContext,
) -> BrainReply:
    """Act on an answer the model wrote: say it, keep count of unanswered turns, or raise a ticket."""
    if status == "escalate":
        return await _raise(db, conversation, "rag_cannot_resolve", lang, channel, caller, preface=reply)
    if status == "clarify":
        conversation.unresolved_turns += 1
        if conversation.unresolved_turns > settings.MAX_UNRESOLVED_TURNS:
            return await _raise(db, conversation, "too_many_clarifications", lang, channel, caller)
    else:
        conversation.unresolved_turns = 0
    return BrainReply(
        reply, "clarify" if status == "clarify" else "answered",
        meta={"rag_status": status, "chunks": [str(c.chunk_id) for c in chunks]},
    )


async def _answer_company_question(
    company: Company, text: str, history: str, lang: str, channel: str, draft: _Draft | None = None
) -> BrainReply:
    """ "What is your company's name?" / "Where is your office?" - answered from the company profile,
    with no domain or knowledge-base lookup needed."""
    if draft is not None:  # a call: already answered by the turn's model request (or it could not be reached)
        return BrainReply(draft.reply or _spoken_profile(company), "company_info", meta={"company_info": True})
    profile = profile_text(company)
    prompt = (
        f"Company profile:\n{profile or '(not provided)'}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"Customer's latest message:\n{text}\n\n"
        f"Reply language: {_language_instruction(lang, channel)}"
    )
    # Offline, a chat can show the whole profile; a call just says who this is.
    fallback = (profile[:900] or company.name) if channel != Channel.PHONE else _spoken_profile(company)
    reply = await get_gemini().generate_text(_system(COMPANY_SYSTEM, channel, "company"), prompt, fallback=fallback)
    return BrainReply(reply, "company_info", meta={"company_info": True})


def _spoken_profile(company: Company) -> str:
    about = (company.description or "").strip()
    first_sentence = re.split(r"(?<=[.!?।])\s", about)[0][:220] if about else ""
    return f"This is {company.name}. {first_sentence}".strip()


async def _ticket_status(
    caller: CallerContext, referenced: Ticket | None, text: str, history: str, lang: str, draft: _Draft | None = None
) -> BrainReply:
    """Tell a caller where their existing request stands."""
    channel = Channel.PHONE
    if not caller.tickets:
        return BrainReply(_t(lang, "no_tickets", channel), "clarify")
    ticket = (
        referenced or caller.focus_ticket
        or next((t for t in caller.tickets if t.status in OPEN_TICKET_STATUSES), caller.tickets[0])
    )
    words = TICKET_STATUS_WORDS[voice_lang(lang)]
    fallback = _t(
        lang, "ticket_status", channel, number=ticket.number, issue=_short(ticket.issue),
        status=words.get(ticket.status, ticket.status),
    )
    if draft is not None:  # already written by the turn's model request (or it could not be reached)
        return BrainReply(draft.reply or fallback, "ticket_status", meta={"ticket_id": str(ticket.id)}, ticket=ticket)
    prompt = (
        f"The request the caller means:\n{tickets_text([ticket])}\n"
        f"(Say its number digit by digit: {spoken_number(ticket.number)}.)\n\n"
        f"All their requests:\n{tickets_text(caller.tickets)}\n\n"
        f"Conversation so far:\n{history}\n\n"
        f"Caller's latest message:\n{text}\n\n"
        f"Reply language: {_language_instruction(lang, channel)}"
    )
    reply = await get_gemini().generate_text(_system(TICKET_STATUS_SYSTEM, channel), prompt, fallback=fallback)
    return BrainReply(reply, "ticket_status", meta={"ticket_id": str(ticket.id)}, ticket=ticket)


async def add_to_ticket(db: AsyncSession, ticket: Ticket, text: str, lang: str, channel: str) -> BrainReply:
    """The customer said more about a request that is already open: record it for the agent."""
    ticket_service.add_event(db, ticket, "customer_message", None, text=text, channel=str(channel))
    await db.commit()
    return BrainReply(
        _t(lang, "ticket_update", channel, number=ticket.number), "ticket_updated",
        meta={"ticket_id": str(ticket.id)}, ticket=ticket,
    )


async def _raise(
    db: AsyncSession, conversation: Conversation, reason: str, lang: str, channel: str, caller: CallerContext,
    preface: str | None = None,
) -> BrainReply:
    """Raise a ticket - unless we don't know who the caller is yet, in which case ask for their number first."""
    if not caller.can_raise_ticket:
        return BrainReply(_t(lang, "ask_phone", channel), "need_phone", meta={"reason": reason})
    return await escalate(db, conversation, reason, preface=preface, lang=lang, channel=channel)


async def escalate(
    db: AsyncSession, conversation: Conversation, reason: str, preface: str | None = None, lang: str = "en",
    channel: str = Channel.WHATSAPP,
) -> BrainReply:
    """Raise a ticket from the conversation; the reply tells the customer its number."""
    history = await history_of(db, conversation, limit=40)
    customer_lines = [line.removeprefix("Customer: ") for line in history.splitlines() if line.startswith("Customer: ")]
    fallback = {
        "issue": (customer_lines[0] if customer_lines else "Customer needs assistance")[:200],
        "summary": "\n".join(customer_lines[-6:]),
        "priority": TicketPriority.MEDIUM.value,
    }
    data = await get_gemini().generate_json(TICKET_SYSTEM, f"Conversation:\n{history}", fallback, TICKET_SCHEMA)
    priority = data.get("priority") if data.get("priority") in set(TicketPriority) else TicketPriority.MEDIUM

    conversation.status = ConversationStatus.ESCALATED
    conversation.summary = data.get("summary") or fallback["summary"]
    ticket = await ticket_service.create_ticket(
        db,
        company_id=conversation.company_id,
        customer_id=conversation.customer_id,
        conversation_id=conversation.id,
        domain_id=conversation.domain_id,
        issue=(data.get("issue") or fallback["issue"])[:1000],
        ai_summary=conversation.summary,
        priority=priority,
        source=TicketSource.PHONE if channel == Channel.PHONE else TicketSource.WHATSAPP,
    )
    log.info("Conversation %s escalated (%s) -> ticket #%s", conversation.id, reason, ticket.number)

    lead = preface or _t(lang, "sorry", channel)
    joiner = " " if channel == Channel.PHONE else "\n\n"
    return BrainReply(
        f"{lead}{joiner}{_t(lang, 'ticket_raised', channel, number=ticket.number)}", "escalated",
        meta={"ticket_id": str(ticket.id), "escalation_reason": reason}, ticket=ticket,
    )


# --------------------------------------------------------------------------- helpers
async def history_of(db: AsyncSession, conversation: Conversation, limit: int | None = None) -> str:
    limit = limit or settings.CONVERSATION_HISTORY_TURNS
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).all()
    label = {MessageRole.CUSTOMER: "Customer", MessageRole.AGENT: "Agent"}
    return "\n".join(f"{label.get(m.role, 'Assistant')}: {m.content}" for m in reversed(rows))


def save_message(
    db: AsyncSession,
    conversation: Conversation,
    direction: str,
    role: str,
    content: str,
    wa_message_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation.id, direction=direction, role=role, content=content,
        wa_message_id=wa_message_id, meta=meta or {},
    )
    db.add(msg)
    return msg


async def _bot_has_answered(db: AsyncSession, conversation: Conversation) -> bool:
    rows = await db.scalars(
        select(Message.meta).where(Message.conversation_id == conversation.id, Message.role == MessageRole.ASSISTANT)
    )
    return any((meta or {}).get("rag_status") for meta in rows)


def _domain_by_code(domains: list[Domain], code: str | None) -> Domain | None:
    return next((d for d in domains if code and d.code == code), None)


def _short(text: str, limit: int = 80) -> str:
    """A ticket issue short enough to say aloud."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0]


def _heuristic_analysis(
    text: str, domains: list[Domain], channel: str = Channel.WHATSAPP, tickets: list[Ticket] | None = None
) -> dict[str, Any]:
    """Offline routing, used when Gemini is unavailable."""
    ticket_number = None
    intent = None
    if channel == Channel.PHONE:
        digits = re.sub(r"[^\d]", "", text)
        ticket_number = next((t.number for t in tickets or [] if str(t.number) in digits), None)
        if ticket_number or _TICKET_REF.search(text):
            intent = "ticket_status"
        elif _GOODBYE.search(text):
            intent = "goodbye"
        elif _ADDING.search(text):
            intent = "provide_info"
    if intent is None:
        if _HUMAN.search(text):
            intent = "human_agent"
        elif _COMPANY.search(text):
            intent = "company_info"
        elif _NOT_RESOLVED.search(text):
            intent = "not_resolved"
        elif _SATISFIED.search(text):
            intent = "satisfied"
        elif _GREETING.match(text):
            intent = "greeting"
        else:
            intent = "question"

    lowered = text.lower()
    best, best_score = None, 0
    for d in domains:
        terms = {d.code.lower(), d.name.lower(), *(k.lower() for k in d.keywords or [])}
        score = sum(1 for t in terms if t and t in lowered)
        if score > best_score:
            best, best_score = d, score
    result = {
        "intent": intent,
        "domain_code": best.code if best else None,
        "domain_confidence": 0.5 if best else 0.0,
        "language": detect_language(text),
    }
    if channel == Channel.PHONE:
        result["ticket_number"] = ticket_number
    return result


def _fallback_answer(chunks: list[Any], lang: str = "en", channel: str = Channel.WHATSAPP) -> dict[str, Any]:
    if chunks:
        if channel == Channel.PHONE:  # a spoken answer: the first couple of sentences, not the whole chunk
            body = " ".join(re.split(r"(?<=[.!?।])\s+", " ".join(chunks[0].content.split()))[:2])[:320]
            return {"reply": f"{_t(lang, 'found', channel)} {body} {_t(lang, 'resolved_q', channel)}", "status": "answered"}
        return {
            "reply": f"{_t(lang, 'found')}\n\n{chunks[0].content[:600]}\n\n{_t(lang, 'resolved_q')}",
            "status": "answered",
        }
    return {"reply": _t(lang, "need_details", channel), "status": "clarify"}
