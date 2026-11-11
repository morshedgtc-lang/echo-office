"""The chat path: identity and standing orders from disk, orders executed in code,
and a model only for what the code cannot answer.

Budget, in milliseconds, from config/office.json:
    greeting   200    answered with no call at all
    identity   100    answered from facts on disk
    order      ...    planned and executed here, no model, verified on disk
                       — with no tool for it, the cloud brain takes the order instead
    question  3000   one cloud call, or an honest line when no key is set
    voice      ...    speech work, only when the owner switched the local model on
"""

from __future__ import annotations

import os
import re
import time

from . import config, engine, memory, rules
from .config import ROOT, policy

GREETINGS = {
    "hi", "hey", "hello", "yo", "hiya", "good morning", "good afternoon", "good evening",
    "salam", "assalamu alaikum", "oi", "hola", "namaskar",
}

ORDER_VERBS = (
    "make", "create", "build", "write", "save", "add", "remove", "delete", "rename",
    "copy", "move", "open", "close", "run", "execute", "install", "start", "stop",
    "restart", "fix", "repair", "scan", "search", "find", "look", "read", "list",
    "check", "verify", "clean", "tidy", "organize", "update", "change", "set", "test",
    "index", "backup", "restore", "sync", "monitor", "summarise", "summarize",
    "remember", "recall", "work", "show", "play", "say", "speak",
)

# The owner asking for speech: "record a voice note", "transcribe my clip",
# "what did i say". Matching words are kept specific so that an order like
# "record the price in memory" still runs as the order it is.
VOICE_ORDER = re.compile(
    r"\b(?:transcrib\w+|dictat\w+|speech\s+to\s+text|"
    r"(?:voice|audio|speech)\s+(?:note|message|memo|clip)|"
    r"record\s+(?:a\s+)?(?:voice|audio|speech|clip|memo)|"
    r"listen\s+to\s+(?:me|my\s+voice)|what\s+(?:did\s+)?i\s+(?:said|say)|hear\s+me)\b",
    re.I)


class Answer:
    """One reply, with the fields the HUD and the log both want."""

    def __init__(self, reply: str, *, kind: str, provider: str = "office",
                 model: str = "rule", ms: int = 0, language: str = "en",
                 tools: list | None = None, ok: bool = True, **extra):
        self.reply = reply
        self.kind = kind
        self.provider = provider
        self.model = model
        self.ms = ms
        self.language = language
        self.tools = tools or []
        self.ok = ok
        self.extra = extra

    def as_dict(self) -> dict:
        out = {"reply": self.reply, "kind": self.kind, "provider": self.provider,
               "model": self.model, "ms": self.ms, "language": self.language,
               "tools": self.tools, "ok": self.ok}
        out.update(self.extra)
        return out


# ------------------------------------------------------------------ language
_BANGLA = re.compile(r"[\u0980-\u09FF]")
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def detect_language(text: str) -> str:
    body = text or ""
    if _BANGLA.search(body):
        return "bn"
    if _DEVANAGARI.search(body):
        return "hi"
    return "en"


# ------------------------------------------------------------------ intent
def _strip_politeness(low: str) -> str:
    for prefix in ("please ", "sir ", "boss ", "hey ", "ok ", "okay ", "can you ",
                   "could you "):
        while low.startswith(prefix):
            low = low[len(prefix):].strip()
    return low


def is_greeting(text: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", (text or "").strip().lower())
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean in GREETINGS or clean.lstrip("hey ").strip() in GREETINGS


def is_order(text: str) -> bool:
    low = _strip_politeness((text or "").strip().lower())
    if not low or low.endswith("?") or len(low.split()) < 2:
        return False
    word = re.split(r"[\s,]+", low, maxsplit=1)[0]
    return word in ORDER_VERBS


def is_voice_order(text: str) -> bool:
    """True when the owner wants speech turned into text on this PC.

    That work needs the speech model, which is a downloaded asset and about 1 GB of
    RAM once loaded, so the chat path only reaches for it behind the local-model
    switch the owner controls (config/office.json -> providers.local_enabled).
    """
    return VOICE_ORDER.search(_strip_politeness((text or "").strip().lower())) is not None


# ------------------------------------------------------------------ voice orders
# The speech model is a downloaded asset that holds ~1 GB of RAM once loaded, and
# the standing order is "no local model unless the owner turns it on". So this path
# never imports epu.stt while that switch is off: a RECORD order must cost no RAM,
# no download and no time, and must still say in one line what is missing.
_VOICE_OFF = {
    "en": ("Yes sir, the local model is off, so there is no speech model on this PC yet. "
           "Say 'turn on the local model' and I will transcribe here, no cloud."),
    "bn": ("হ্যাঁ স্যার, লোকাল মডেল বন্ধ, তাই এই পিসিতে এখন স্পিচ মডেল নেই। "
           "'turn on the local model' বলুন — তাহলে ক্লাউড ছাড়া এখানেই ট্রান্সক্রাইব করব।"),
    "hi": ("जी सर, लोकल मॉडल बंद है, इसलिए इस पीसी पर अभी स्पीच मॉडल नहीं है। "
           "'turn on the local model' कहें — फिर बिना क्लाउड यहीं ट्रांसक्राइब करूँगा।"),
}


def _voice_answer(language: str) -> Answer:
    """A RECORD/VOICE order, answered honestly in one line.

    Refusal is reserved for genuinely destructive work; a missing speech model is
    only a missing piece, so the line ends with what the owner can do about it.
    """
    if not config.local_model_enabled():
        reply = _VOICE_OFF.get(language) or _VOICE_OFF["en"]
        return Answer(reply, kind="voice", language=language)
    from . import stt          # lazy: nothing is imported while the switch is off
    reply = (f"Yes sir, speech is on: the {stt.STT_MODEL} model runs on this PC. "
             f"Send me a clip on the voice button and I will transcribe it here, "
             f"no cloud.")
    return Answer(reply, kind="voice", provider="local", model=stt.STT_MODEL,
                  language=language)


def _brain(body: str, language: str, started: float, chat_id: str, scope: str,
           mem: memory.Memory) -> Answer:
    """The model's turn: one question, one answer, with the tool manifest in hand.

    This is the one path that can cost money and seconds, so it is also the one path
    allowed to be honest about being unable to answer (no key, nothing answered) —
    and even then the reply names what is missing instead of refusing the order.
    History is scope-filtered: one client's words never travel inside another's.
    """
    from . import brains

    result = brains.ask(body, scope=scope, mem=mem,
                        history=mem.recent(chat_id, 4, scope))
    reply = result.get("reply") or ""
    mem.add_chat(chat_id, "assistant", reply, scope)
    return Answer(reply, kind="question",
                  provider=result.get("provider", "office"),
                  model=result.get("model", "rule"), language=language,
                  ok=bool(result.get("ok")),
                  ms=int((time.time() - started) * 1000))


# ------------------------------------------------------------------ the answer
def answer(text: str, *, chat_id: str = "hud", agent: str = "you",
           scope: str = "owner", mem: memory.Memory | None = None) -> dict:
    started = time.time()
    body = (text or "").strip()
    language = detect_language(body)
    low = _strip_politeness(body.lower())
    close_mem = mem is None
    if mem is None:
        mem = memory.Memory()

    def finish(result: Answer) -> dict:
        if close_mem:
            mem.close()
        return result.as_dict()

    try:
        if not body:
            return finish(Answer(f"Yes {policy() and 'sir'}?", kind="rule", ms=0,
                                language=language))

        # 1) who am I / do you know me — from disk, no model
        if rules.identity_question(body):
            reply = rules.identity_reply(body, language, mem)
            mem.add_chat(chat_id, "owner", body, scope)
            mem.add_chat(chat_id, "assistant", reply, scope)
            return finish(Answer(reply, kind="identity", language=language,
                                 ms=int((time.time() - started) * 1000)))

        # 2) what are my standing orders — the owner's own facts
        if rules.rules_question(body):
            reply = rules.rules_reply(mem)
            mem.add_chat(chat_id, "owner", body, scope)
            mem.add_chat(chat_id, "assistant", reply, scope)
            return finish(Answer(reply, kind="rules", language=language,
                                 ms=int((time.time() - started) * 1000)))

        # 3) a greeting — zero cost
        if is_greeting(body):
            reply = rules.quick_reply(mem=mem, language=language)
            mem.add_chat(chat_id, "owner", body, scope)
            mem.add_chat(chat_id, "assistant", reply, scope)
            return finish(Answer(reply, kind="greeting", language=language,
                                 ms=int((time.time() - started) * 1000)))

        # 4) a RECORD/VOICE order — the speech model lives behind the local switch
        if is_voice_order(body):
            spoken = _voice_answer(language)
            mem.add_chat(chat_id, "owner", body, scope)
            mem.add_chat(chat_id, "assistant", spoken.reply, scope)
            spoken.ms = int((time.time() - started) * 1000)
            return finish(spoken)

        # 5) an order — planned and executed here, verified on disk
        actions = engine.plan(body, scope=scope)
        if is_order(body) or actions:
            mem.add_chat(chat_id, "owner", body, scope)
            if not actions:
                # No tool in the office does this yet, and the owner's order still
                # gets a real attempt: the cloud brain answers with the tool manifest
                # in its system prompt, so it can plan around what the office has.
                return finish(_brain(body, language, started, chat_id, scope, mem))
            result = engine.run(body, mem, scope=scope)
            reply = result.get("reply") or (
                "Yes sir, I could not run that one here yet. Name the tool you want "
                "and I will build it.")
            mem.add_chat(chat_id, "assistant", reply, scope)
            return finish(Answer(reply, kind="order", provider="tools", model="engine",
                                 language=language, tools=result.get("tools") or [],
                                 ok=bool(result.get("ok")),
                                 ms=int((time.time() - started) * 1000)))

        # 6) a real question — the cloud brain, or an honest line without no key
        mem.add_chat(chat_id, "owner", body, scope)
        return finish(_brain(body, language, started, chat_id, scope, mem))
    except Exception as exc:  # a broken brain must never leave the owner with nothing
        return finish(Answer(f"Yes sir, something in the office broke: {exc}. "
                             f"Tell me to repair it and I will.", kind="error",
                             ok=False, ms=int((time.time() - started) * 1000),
                             language=language))
