"""The HTTP office: chat, status, voice, and biometrics, from any device on the same network.

    python epu.py serve [--lan] [--port 8790]

Binds 127.0.0.1 by default (this PC only). `--lan` (or EPU_BIND=0.0.0.0) lets the
owner's devices on the same WiFi connect — e.g. the laptop at 192.168.0.104 — and
then every request from another machine needs the office passphrase. That is the
trade: convenience on the home network, never an open door.

Endpoints
    POST /api/login          {passphrase} -> a session cookie, checked against secure/
    GET  /api/status         office numbers (no secrets, ever)
    POST /api/chat           {message, chat_id, scope} -> one reply
    POST /api/transcribe     {audio (base64 wav), language?} -> {text, language}
    POST /api/say            {text, agent} -> speaks on this PC
    POST /api/face/enrol     {image (base64 jpg)} -> enrol the owner's face
    POST /api/face/verify    {image} -> {ok, distance, threshold}
    GET  /api/face/status    enrolled? samples?
    POST /api/voice/enrol    {audio} -> enrol the owner's voice
    POST /api/voice/verify   {audio} -> {ok, score, threshold}
    GET  /api/voice/status
    GET  /                    the HUD (hud/hud.html + hud.js + hud.css)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import chat, config, engine, face, memory, rules, stt, tts
from .config import ROOT, SECURE_DIR

PASS_FILE = SECURE_DIR / "owner.pass"
SESSIONS: dict[str, dict] = {}
COOKIE = "EPU_SESSION"
PUBLIC_PATHS = {"/", "/hud/hud.html", "/hud/hud.js", "/hud/hud.css", "/api/status",
                "/api/login", "/favicon.ico"}

STATE = {
    "started": 0.0,
    "requests": 0,
    "last_chat_ms": 0,
    "face": None,          # epu.face.FaceID
    "voice": None,         # epu.stt.VoiceID
}


# ------------------------------------------------------------------ sessions
def _hash_pass(phrase: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", phrase.encode("utf-8"),
                               bytes.fromhex(salt), 200_000, dklen=32).hex()


def set_passphrase(phrase: str) -> None:
    if len(phrase) < 8:
        raise ValueError("use at least 8 characters, sir")
    salt = secrets.token_hex(16)
    config.secure_write("owner.pass",
                        json.dumps({"salt": salt, "hash": _hash_pass(phrase, salt),
                                    "created": time.time()}, indent=1))
    for token in list(SESSIONS):
        SESSIONS.pop(token, None)


def passphrase_set() -> bool:
    return PASS_FILE.exists()


def check_passphrase(phrase: str) -> bool:
    try:
        data = json.loads(PASS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return hmac.compare_digest(_hash_pass(phrase, data["salt"]), data["hash"])


def _session_token(handler: BaseHTTPRequestHandler) -> str:
    cookie = handler.headers.get("Cookie") or ""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith(COOKIE + "="):
            return part.split("=", 1)[1]
    return ""


def _session_ok(handler: BaseHTTPRequestHandler) -> bool:
    token = _session_token(handler)
    entry = SESSIONS.get(token)
    if not entry:
        return False
    if _is_loopback(handler) or entry["ip"] == handler.client_address[0]:
        return True
    return False


def _is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    return handler.client_address[0] in ("127.0.0.1", "::1", "localhost")


# ------------------------------------------------------------------ helpers
def _b64(data: str) -> bytes:
    if data and "," in data:
        data = data.split(",", 1)[1]
    return base64.b64decode(data or "")


def _short_ip(ip: str) -> str:
    return ip if len(ip) < 46 else ip[:20] + "..."


# ------------------------------------------------------------------ the handler
class Handler(BaseHTTPRequestHandler):
    server_version = "EPU/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:                    # quiet: no console noise
        pass

    # ---------------------------------------------------------------- plumbing
    def _json(self, payload: dict, code: int = 200, extra_headers: dict | None = None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 25_000_000:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _static(self, path: str) -> bool:
        file_map = {"/": "hud/hud.html", "/hud/hud.html": "hud/hud.html",
                    "/hud/hud.js": "hud/hud.js", "/hud/hud.css": "hud/hud.css"}
        relative = file_map.get(path)
        if not relative:
            return False
        target = ROOT / relative
        if not target.is_file():
            return False
        body = target.read_bytes()
        content = {"hud/hud.html": "text/html; charset=utf-8",
                   "hud/hud.js": "text/javascript; charset=utf-8",
                   "hud/hud.css": "text/css; charset=utf-8"}[relative]
        self.send_response(200)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return True

    def _gate(self, path: str) -> bool:
        """True when the request may proceed."""
        if path in PUBLIC_PATHS:
            return True
        if _is_loopback(self):
            return True                      # the owner at this PC
        if passphrase_set() and _session_ok(self):
            return True
        self._json({"error": "locked - enter the office passphrase"}, 401)
        return False

    # ---------------------------------------------------------------- methods
    def do_GET(self):
        started = time.time()
        path = urllib.parse.urlparse(self.path).path
        STATE["requests"] += 1
        try:
            if self._static(path):
                return
            if not self._gate(path):
                return
            if path == "/api/status":
                return self._json(self._status())
            if path == "/api/face/status":
                return self._json({"enrolled": bool(STATE["face"] and STATE["face"].enrolled()),
                                   "samples": (STATE["face"].state or {}).get("samples", 0)
                                   if STATE["face"] else 0})
            if path == "/api/voice/status":
                return self._json({"enrolled": bool(STATE["voice"] and STATE["voice"].enrolled()),
                                   "threshold": (STATE["voice"].threshold
                                                 if STATE["voice"] else None)})
            if path == "/api/voices":
                return self._json(tts.voices())
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)[:300]}, 500)
        finally:
            STATE["last_chat_ms"] = int((time.time() - started) * 1000)

    def do_POST(self):
        started = time.time()
        path = urllib.parse.urlparse(self.path).path
        STATE["requests"] += 1
        try:
            if not self._gate(path):
                return
            data = self._body()
            if path == "/api/login":
                return self._login(data)
            if path == "/api/logout":
                token = _session_token(self)
                SESSIONS.pop(token, None)
                return self._json({"ok": True})
            if path == "/api/chat":
                return self._chat(data)
            if path == "/api/transcribe":
                return self._transcribe(data)
            if path == "/api/say":
                text = str(data.get("text") or "").strip()
                if not text:
                    return self._json({"error": "empty text"}, 400)
                language = tts.say(text, str(data.get("agent") or "you"))
                return self._json({"ok": True, "language": language})
            if path == "/api/face/enrol":
                return self._face_enrol(data)
            if path == "/api/face/verify":
                return self._face_verify(data)
            if path == "/api/voice/enrol":
                return self._voice_enrol(data)
            if path == "/api/voice/verify":
                return self._voice_verify(data)
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)[:300]}, 500)
        finally:
            STATE["last_chat_ms"] = int((time.time() - started) * 1000)

    # ---------------------------------------------------------------- endpoints
    def _status(self) -> dict:
        mem = memory.Memory()
        try:
            stats = mem.stats()
        finally:
            mem.close()
        return {
            "name": config.assistant_name(),
            "uptime_s": int(time.time() - STATE["started"]),
            "requests": STATE["requests"],
            "memory": stats,
            "cloud": tts_health(),
            "local_model": config.local_model_enabled(),
            "voice": {"engine": tts.voices().get("engine"),
                      "enrolled": bool(STATE["voice"] and STATE["voice"].enrolled())},
            "face": {"enrolled": bool(STATE["face"] and STATE["face"].enrolled())},
            "tasks": len(memory.Memory().tasks()),
        }

    def _login(self, data: dict) -> None:
        if not passphrase_set():
            return self._json({"error": "no passphrase set on this office yet"}, 400)
        phrase = str(data.get("passphrase") or "")
        if not check_passphrase(phrase):
            return self._json({"error": "wrong passphrase"}, 401)
        token = secrets.token_hex(24)
        SESSIONS[token] = {"ip": self.client_address[0], "expires": time.time() + 8 * 3600}
        self._json({"ok": True, "owner": config.settings()["name"]},
                   extra_headers={"Set-Cookie":
                                  f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"})

    def _chat(self, data: dict) -> None:
        message = str(data.get("message") or "").strip()
        if not message:
            return self._json({"error": "empty message"}, 400)
        scope = str(data.get("scope") or "owner")
        result = chat.answer(message, chat_id=str(data.get("chat_id") or "hud"),
                             agent=str(data.get("agent") or "you"), scope=scope)
        return self._json(result)

    def _transcribe(self, data: dict) -> None:
        audio = _b64(str(data.get("audio") or ""))
        if not audio:
            return self._json({"error": "no audio"}, 400)
        result = stt.transcribe(audio, str(data.get("language") or "") or None)
        if not result.get("text"):
            return self._json({"error": result.get("error") or "could not hear you",
                               "text": ""})
        return self._json({"text": result["text"], "language": result.get("language")})

    def _face_enrol(self, data: dict) -> None:
        image = _b64(str(data.get("image") or ""))
        if not image:
            return self._json({"ok": False, "error": "no photo"}, 400)
        result = STATE["face"].enrol(image) if STATE["face"] else {"ok": False,
                                                                   "error": "no face id"}
        return self._json(result)

    def _face_verify(self, data: dict) -> None:
        image = _b64(str(data.get("image") or ""))
        if not image:
            return self._json({"ok": False, "error": "no photo"}, 400)
        if not STATE["face"]:
            return self._json({"ok": False, "error": "no face enrolled yet"})
        result = STATE["face"].verify(image)
        return self._json(result)

    def _voice_enrol(self, data: dict) -> None:
        audio = _b64(str(data.get("audio") or ""))
        if not audio:
            return self._json({"ok": False, "error": "no audio"}, 400)
        try:
            samples, _rate = stt.read_wav(audio)
        except stt.VoiceUnavailable as exc:
            return self._json({"ok": False, "error": str(exc)})
        if not STATE["voice"]:
            return self._json({"ok": False, "error": "no voice id"})
        return self._json(STATE["voice"].clarify(samples, float(data.get("seconds") or 2.5)))

    def _voice_verify(self, data: dict) -> None:
        audio = _b64(str(data.get("audio") or ""))
        if not audio:
            return self._json({"ok": False, "error": "no audio"}, 400)
        try:
            samples, _rate = stt.read_wav(audio)
        except stt.VoiceUnavailable as exc:
            return self._json({"ok": False, "error": str(exc)})
        if not STATE["voice"]:
            return self._json({"ok": False, "error": "no voice enrolled yet"})
        result = STATE["voice"].verify(samples)
        if result.get("ok"):
            result["text"] = (stt.transcribe(audio) or {}).get("text", "")
        return self._json(result)


def tts_health() -> dict:
    from . import brains

    return {"providers": brains.health(), "talking": False}


def build_server(bind: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    host = bind or os.environ.get("EPU_BIND") or config.bind()
    port = port or int(os.environ.get("EPU_HUD_PORT") or config.port("hud"))
    STATE["started"] = time.time()
    STATE["face"] = face.FaceID(config.MEMORY_DIR / "biometrics" / "face")
    STATE["voice"] = stt.VoiceID(config.MEMORY_DIR / "biometrics" / "voice")
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((host, port), Handler)


def already_running(host: str = "127.0.0.1", port: int | None = None) -> bool:
    """One office only: a second launch must never create a twin server."""
    import socket

    port = port or int(os.environ.get("EPU_HUD_PORT") or config.port("hud"))
    for probe in (port, port + 2):
        try:
            with socket.create_connection((host, probe), timeout=0.4):
                return True
        except OSError:
            continue
    return False
