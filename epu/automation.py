"""The connector to an automation engine (ActivePieces or anything with a webhook).

EPU is the light office: 24 MB, one process, no Docker. ActivePieces is the optional
hands: 4 services and a browser builder. So this module does the two cheap things and
nothing else:

    trigger(flow, payload)  -> POST to the flow's webhook and report the REAL answer
    status()                -> is the automation engine actually listening right now?

Nothing here pretends. When the engine is not running, the owner hears exactly that in
one line, and the office keeps working exactly as before. Turning it on is one config
line and a start of the engine, never a code change.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from . import config
from .config import ROOT


def _cfg() -> dict:
    return dict(config.settings().get("automation") or {})


def base_url() -> str:
    return str(_cfg().get("base") or "http://127.0.0.1:8080").rstrip("/")


def timeout() -> int:
    return int(_cfg().get("timeout_s") or 30)


def _reachable(host: str, port: int, seconds: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=seconds):
            return True
    except OSError:
        return False


def status() -> dict:
    """Is the automation engine listening? Real TCP answer, not a hope."""
    url = base_url()
    host, port = _host_port(url)
    up = _reachable(host, port)
    return {"ok": up, "base": url, "up": up,
            "note": "listening" if up else "not running right now"}


def _host_port(url: str) -> tuple[str, int]:
    import urllib.parse as parse

    parts = parse.urlparse(url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port


def trigger(flow: str, payload: dict | None = None,
            method: str = "POST") -> dict:
    """Run one flow and report what actually came back."""
    name = str(flow or "").strip().strip("/")
    if not name:
        return {"ok": False, "error": "no flow named"}
    state = status()
    if not state["ok"]:
        return {"ok": False, "error": f"the automation engine is not running "
                                     f"({state['base']}). Start it and I will run the flow.",
                "base": state["base"]}
    url = f"{state['base']}/api/v1/webhooks/{name}"
    body = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout()) as response:
            text = response.read().decode("utf-8", errors="replace")[:2000]
            return {"ok": True, "flow": name, "status": response.status,
                    "seconds": round(time.time() - started, 2), "body": text,
                    "base": state["base"]}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "flow": name, "error": f"the flow answered HTTP {exc.code}",
                "status": exc.code, "base": state["base"]}
    except Exception as exc:
        return {"ok": False, "flow": name, "error": f"the flow did not answer: {exc}",
                "base": state["base"]}


def remember_hook(name: str, payload: dict, mem) -> dict:
    """An inbound webhook becomes a real, visible piece of work."""
    text = json.dumps(payload, ensure_ascii=False)[:600]
    task_id = mem.add_task(f"[webhook:{name}] {text}", scope="owner")
    return {"ok": task_id is not None, "task": task_id, "hook": name,
            "text": text}
