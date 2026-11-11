"""The laws of the office, in code.

  * who the owner is and who the assistant is — answered from disk, never invented;
  * how the owner is addressed, and how the assistant is named;
  * the standing orders the owner gave with his own words.

Everything here is data-driven so the owner can change it in memory and it changes
everywhere. `seed()` writes the facts once; a later `remember()` edit wins.
"""

from __future__ import annotations

import re

from . import memory
from .config import assistant_name, owner_address

# Identity questions the assistant must answer instantly, in every language it speaks.
_IDENTITY_MARKERS = (
    "who am i", "who am i", "who i am", "what is my name", "my name", "do you know me",
    "you know me", "about me", "who is the owner", "who is owner",
    "আমি কে", "আমাকে চেনো", "আমার নাম",
    "मैं कौन", "मुझे जानते हो", "मेरा नाम",
)

# Questions about the owner's standing orders / rules.
_RULES_MARKERS = (
    "standing order", "standing orders", "my orders", "my order", "your orders",
    "your order", "your rules", "my rules", "the rules", "the law", "your law",
    "your instructions", "my instructions",
)

# The facts that make the assistant never forget the owner. Seeded once, then owned
# by the owner: if he says "my name is X", that becomes the fact.
SEED_FACTS = [
    ("Owner name: Mohammad Morshed. He is the creator and owner of this office.",
     {"kind": "fact", "trust": "owner", "source": "owner:identity"}),
    ("The owner calls the assistant {assistant}. The assistant calls the owner sir or boss.",
     {"kind": "fact", "trust": "owner", "source": "owner:identity"}),
    ("The owner builds mobile-phone repair and unlock software tools for technicians, "
     "and sells those services at easyphoneunlocker.com.",
     {"kind": "fact", "trust": "owner", "source": "owner:business"}),
    ("Standing order: never refuse an owner order. Try the work, report the real result, "
     "and say in one line what is missing.",
     {"kind": "fact", "trust": "owner", "source": "owner:standing-order"}),
    ("Standing order: never repeat the same opening line. For a greeting, answer once; "
     "for a question, answer directly; for an order, 'Yes sir,' once and then the work.",
     {"kind": "fact", "trust": "owner", "source": "owner:standing-order"}),
    ("Standing order: the PC must stay fast. No local model unless the owner turns it on; "
     "nothing heavy runs in the chat path; memory writes never block a reply.",
     {"kind": "fact", "trust": "owner", "source": "owner:standing-order"}),
    ("Standing order: EPU may keep working day and night in projects/, but never changes "
     "its own code in epu/ and never pushes to git without the word 'ship'.",
     {"kind": "fact", "trust": "owner", "source": "owner:standing-order"}),
]


def name_facts(mem: memory.Memory) -> dict:
    """Read the identity facts: name, home, and the owner's own words about ourselves."""
    rows = mem.db.execute(
        "SELECT content FROM facts WHERE trust='owner' AND kind='fact' ORDER BY id").fetchall()
    out: dict = {}
    for row in rows:
        body = (row["content"] or "").strip()
        low = body.lower()
        if "name" not in out and low.startswith("owner name:") and ":" in body:
            out["name"] = body.split(":", 1)[1].split(".")[0].strip()
        elif "home" not in out and "bangladesh" in low:
            out["home"] = "Bangladesh, Asia/Dhaka (UTC+6)"
        elif "calls the assistant" in low:
            out["address"] = body
    return out


def identity_question(text: str) -> bool:
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    return any(m in low for m in _IDENTITY_MARKERS)


def rules_question(text: str) -> bool:
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    return any(m in low for m in _RULES_MARKERS)


def identity_reply(text: str, language: str = "en", mem: memory.Memory | None = None) -> str:
    """The owner's identity, answered from disk. No model call, no invention."""
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    facts = {}
    if mem is not None:
        facts = name_facts(mem)
    name = facts.get("name") or "Mohammad Morshed"
    home = facts.get("home") or "Bangladesh, Asia/Dhaka (UTC+6)"
    assistant = assistant_name()
    if language == "bn":
        return (f"হ্যাঁ স্যার, আপনি {name} — আমার স্রষ্টা ও মালিক। "
                f"আপনি আমাকে {assistant} ডাকেন। আমি আপনাকে ভুলব না, স্যার।")
    if language == "hi":
        return (f"जी सर, आप {name} हैं — मेरे रचयिता और मालिक। "
                f"आप मुझे {assistant} बुलाते हैं। मैं आपको कभी नहीं भूलूँगा, सर।")
    return (f"Yes sir, you are {name} from {home} — my creator and owner. "
            f"You call me {assistant}. I will not forget you, boss.")


def rules_reply(mem: memory.Memory | None = None) -> str:
    """The owner's standing orders, from his own facts."""
    if mem is None:
        return ("Yes sir, I have no standing order saved yet. Say 'remember that ...' "
                "and it becomes my law.")
    rows = mem.search("standing order never refuse", k=6)
    lines = [r["content"].strip() for r in rows if "standing order" in (r["content"] or "").lower()]
    if not lines:
        return ("Yes sir, I have no standing order saved yet. Say 'remember that ...' "
                "and it becomes my law.")
    body = lines[0].rstrip(".")
    for extra in lines[1:3]:
        body += ". Also: " + extra.rstrip(".").lower()[:1] + extra.rstrip(".")[1:]
    return f"Yes sir, your standing orders: {body}."


def quick_reply(mem: memory.Memory | None = None, language: str = "en") -> str:
    """A greeting costs nothing: no model, no network, no delay."""
    assistant = assistant_name()
    sir = owner_address()[0]
    if language == "bn":
        return f"হ্যাঁ স্যার, {assistant} আছি। বলুন, কী করব?"
    if language == "hi":
        return f"जी सर, {assistant} हूँ। बताइए, क्या करूँ?"
    return f"Yes {sir}, {assistant} here. What is the order, boss?"


def seed(mem: memory.Memory, force: bool = False) -> int:
    """Write the seed facts once (or again with force). Returns how many were stored."""
    assistant = assistant_name()
    existing = {r["content"] for r in mem.db.execute(
        "SELECT content FROM facts WHERE trust='owner'")} if not force else set()
    saved = 0
    for template, meta in SEED_FACTS:
        body = template.format(assistant=assistant)
        if body in existing:
            continue
        if mem.remember(body, **meta) is not None:
            saved += 1
    return saved
