"""The cloud brain: one router, many providers, health tracked per model.

Everything local-first means by default: the local model is OFF unless the owner turns
it on (config/office.json -> providers.local_enabled), because a local model on his CPU
is what made his PC slow. Without a key the office says so plainly and never pretends.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config, memory

_fused: dict[str, float] = {}          # model -> "too sick to use" timestamp
_last_winner: str | None = None
FUSE_SECONDS = 15


def _sick(model: str) -> bool:
    until = _fused.get(model, 0.0)
    return until > time.time()


def _mark(model: str, healthy: bool) -> None:
    if healthy:
        _fused.pop(model, None)
    else:
        _fused[model] = time.time() + FUSE_SECONDS


def _post(url: str, key: str, payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _local_reply(body: str, prompt: str, mem: memory.Memory, history: list) -> dict:
    base = config.settings()["providers"]["local"]["base"]
    model = config.settings()["providers"]["local"]["model"]
    try:
        data = _post(f"{base}/v1/chat/completions", "", {
            "model": model, "max_tokens": 512, "temperature": 0.3, "stream": False,
            "messages": [{"role": "system", "content": prompt}] + list(history) +
                        [{"role": "user", "content": body}]}, 30)
        reply = (data["choices"][0]["message"]["content"] or "").strip()
        return {"ok": True, "reply": reply, "provider": "local", "model": model}
    except Exception as exc:
        return {"ok": False, "reply": "", "error": str(exc)[:200],
                "provider": "local", "model": model}


def ask(text: str, *, scope: str = "owner", mem: memory.Memory | None = None,
        history: list | None = None) -> dict:
    """One question, one answer: try the best healthy model, be honest when none is up."""
    global _last_winner

    settings = config.settings()
    prompt = system_prompt(scope, mem, text)
    history = [{"role": h["role"], "content": h["content"]} for h in (history or [])]

    providers = sorted(config.cloud_ready(), key=lambda p: p.get("priority", 5))
    if settings["providers"].get("local_enabled") and settings["providers"]["default"] == "local":
        return _local_reply(text, prompt, mem, history)

    if not providers:
        return {"ok": False,
                "reply": "Yes sir, I have no cloud key yet. Put the key in "
                         "secure/keys.env (owner-only) and I answer in under a second.",
                "provider": "office", "model": "none"}

    tried = []
    for provider in providers:
        key = config.provider_key(provider)
        base = provider.get("base", "https://api.openai.com/v1")
        for model in provider.get("models", []):
            if _sick(model) or (_last_winner and _last_winner != model and _fused):
                tried.append(f"{model} (cooling)")
                continue
            payload = {"model": model,
                       "max_tokens": int(provider.get("max_tokens", 1024)),
                       "temperature": float(provider.get("temperature", 0.3)),
                       "stream": False,
                       "messages": [{"role": "system", "content": prompt}] + history +
                                   [{"role": "user", "content": text}]}
            try:
                data = _post(f"{base}/chat/completions", key, payload,
                             int(provider.get("timeout_s", 20)))
                reply = (data["choices"][0]["message"]["content"] or "").strip()
                _mark(model, True)
                if reply:
                    _last_winner = model
                    return {"ok": True, "reply": reply, "provider": provider["id"],
                            "model": model}
            except urllib.error.HTTPError as exc:
                _mark(model, False)
                tried.append(f"{model} HTTP {exc.code}")
            except Exception as exc:
                _mark(model, False)
                tried.append(f"{model} {type(exc).__name__}")

    if config.local_model_enabled():
        return _local_reply(text, prompt, mem, history)
    return {"ok": False,
            "reply": "Yes sir, no cloud model answered right now (" +
                     "; ".join(tried[:3]) + "). I am not guessing an answer.",
            "provider": "office", "model": "none"}


def system_prompt(scope: str, mem: memory.Memory | None, text: str) -> str:
    from . import rules

    settings = config.settings()
    owner = ", ".join(settings["owner_address"])
    assistant = settings["assistant_name"]
    lines = [
        f"You are {assistant}, the owner's assistant. Call the owner {owner} in every reply.",
        "The owner's order is final: try the work, never refuse, never say you cannot. "
        "If a tool or permission is missing, say in one line exactly what you need.",
        "Never invent facts, file paths, command output or sources. If you do not know, "
        "say so plainly.",
        "Answer in simple short English. The owner is not a native English speaker.",
        "Do not repeat the same opening line. For a question, answer directly.",
    ]
    if mem is not None:
        context = mem.context(text, scope=scope, max_chars=700)
        if context:
            lines.append("What you already know (owner-trusted facts):")
            lines.append(context)
    return "\n".join(lines)


def health() -> list[dict]:
    """What the router may use right now: which providers have credentials."""
    return [{"id": p["id"], "models": len(p.get("models") or []),
             "configured": bool(config.provider_key(p))} for p in config.cloud_providers()]
