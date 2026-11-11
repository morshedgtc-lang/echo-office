"""The chat path: identity and standing orders from disk, orders executed in code,
and a model only for what the code cannot answer.

Budget, in milliseconds, from config/office.json:
    greeting   200    answered with no call at all
    identity   100    answered from facts on disk
    order      ...    planned and executed here, no model, verified on disk
    question  3000   one cloud call, or an honest line when no key is set
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

        # 4) an order — planned and executed here, verified on disk
        if is_order(body) or engine.plan(body, scope=scope):
            mem.add_chat(chat_id, "owner", body, scope)
            result = engine.run(body, mem, scope=scope)
            if result.get("reply"):
                reply = result["reply"]
            else:
                reply = (f"Yes sir, I do not have a tool for that yet. Say 'remember that "
                         f"...' or name the tool you want and I will build it.")
                result["ok"] = False
            mem.add_chat(chat_id, "assistant", reply, scope)
            return finish(Answer(reply, kind="order", provider="tools", model="engine",
                                 language=language, tools=result.get("tools") or [],
                                 ok=bool(result.get("ok")),
                                 ms=int((time.time() - started) * 1000)))

        # 5) a real question — the cloud brain, or an honest line without no key
        mem.add_chat(chat_id, "owner", body, scope)
        from . import brains

        result = brains.ask(body, scope=scope, mem=mem, history=mem.recent(chat_id, 4, scope))
        reply = result.get("reply") or ""
        mem.add_chat(chat_id, "assistant", reply, scope)
        return finish(Answer(reply, kind="question", provider=result.get("provider", "office"),
                             model=result.get("model", "rule"), language=language,
                             ok=bool(result.get("ok")),
                             ms=int((time.time() - started) * 1000)))
    except Exception as exc:  # a broken brain must never leave the owner with nothing
        return finish(Answer(f"Yes sir, something in the office broke: {exc}. "
                             f"Tell me to repair it and I will.", kind="error",
                             ok=False, ms=int((time.time() - started) * 1000),
                             language=language))
