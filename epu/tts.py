"""The office voice, with one speaker that never talks over itself.

Two engines, in order:
    windows   built into Windows (System.Speech), offline, no key — the default;
    edge      the free neural voice (edge-tts) when it is installed.

Only one utterance plays at a time: a new reply interrupts the old one, so the owner
never hears two voices at once (that was the old build's worst habit).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import ROOT, policy

MAX_CHARS = 900

_lock = threading.Lock()
_seq = 0
_thread: threading.Thread | None = None


def clean_for_speech(text: str) -> str:
    """Strip the things not worth hearing: code, tables, markdown, long lines."""
    import re

    body = text or ""
    body = re.sub(r"```.*?```", " (the code is on screen) ", body, flags=re.S)
    body = "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("|"))
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    body = re.sub(r"[`*_#>]+", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > MAX_CHARS:
        cut = body.rfind(". ", 0, MAX_CHARS)
        body = body[: cut + 1 if cut > 200 else MAX_CHARS] + " The rest is on screen."
    return body


def _stop_windows() -> None:
    """Windows SAPI has no handle to stop from outside, so the helper is short by design."""
    return None


def _stop_edge() -> None:
    try:
        from . import edge_voice

        edge_voice.stop()
    except Exception:
        pass


def _speak_windows(text: str) -> None:
    script = ("Add-Type -AssemblyName System.Speech; "
              "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
              ".Speak($env:EPU_SAY)")
    subprocess.run(["powershell", "-NoProfile", "-Command", script],
                   env={**os.environ, "EPU_SAY": text}, check=False)


def _speak_edge(text: str, agent: str) -> bool:
    try:
        from . import edge_voice
    except ImportError:
        return False
    try:
        return edge_voice.speak(text, agent or "you")
    except Exception:
        return False


def _serial(text: str, agent: str, mine: int) -> None:
    global _seq

    with _lock:
        if mine != _seq:                      # a newer reply came in: let it speak
            return
        engine = os.environ.get("EPU_TTS_ENGINE", "windows").strip().lower()
        if engine in ("edge", "auto"):
            if _speak_edge(text, agent):
                return
        _speak_windows(text)


def say(text: str, agent: str = "you", wait: bool = False) -> str:
    """Speak one reply. Returns the language it was spoken in; never raises."""
    global _seq, _thread

    body = clean_for_speech(text)
    if not body:
        return "en"
    _seq += 1
    mine = _seq
    # A new reply stops the old one instead of playing on top of it.
    _stop_edge()
    if wait:
        _serial(body, agent, mine)
        return "en"
    _thread = threading.Thread(target=_serial, args=(body, agent, mine),
                               daemon=True, name="epu-voice")
    _thread.start()
    return "en"


def stop() -> None:
    """Emergency stop for the voice box."""
    global _seq

    _seq += 1
    _stop_edge()


def voices() -> dict:
    """What the office can speak with, for the HUD's VOICE row."""
    return {"languages": [{"code": code, "name": name}
                          for code, name in (("en", "English"), ("bn", "Bangla"),
                                             ("hi", "Hindi"))],
            "engine": os.environ.get("EPU_TTS_ENGINE", "windows"),
            "policy": policy().get("voice", {})}
