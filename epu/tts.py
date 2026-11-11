"""The office voice, with one speaker that never talks over itself.

Two engines, in order:
    windows   built into Windows (System.Speech), offline, no key — the default;
    edge      the free neural voice (edge-tts) when it is installed.

Only one utterance plays at a time: a new reply kills the old one, so the owner never
hears two voices at once (that was the old build's worst habit, and the reason this
file looks the way it does).

WHY the utterance is a tracked child process: Windows SAPI gives the caller no handle
to stop a Speak() call that is already running, so the speech runs as its own
PowerShell process and the office keeps its PID in `_child`. `_stop_windows()` kills
that PID, which is the only honest stop there is. `_child` is only ever read or
written under `_lock`, and the lock is held while an utterance is STARTED or STOPPED,
never while it is speaking — so a new reply never waits for the old one to finish.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from .config import ROOT, policy

MAX_CHARS = 900

# One mutex for "who is speaking right now": the sequence number and the child PID.
_lock = threading.Lock()
_seq = 0
_thread: threading.Thread | None = None
_child: subprocess.Popen | None = None

# The one place a voice process is created. A test swaps it for a fake speaker, so
# the kill is proven with real processes and no audio.
_launch = subprocess.Popen


def clean_for_speech(text: str) -> str:
    """Strip the things not worth hearing: code, tables, markdown, long lines."""
    import re

    body = str(text or "")
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
    """Kill the Windows utterance that is speaking right now, if there is one.

    The PID is the whole trick: SAPI cannot be stopped from outside, so the utterance
    is a PowerShell child and killing that child is the stop. A child that already
    finished leaves nothing to do. This must never raise — a voice that refuses to
    die is still better than an office that crashes.
    """
    global _child

    proc, _child = _child, None
    if proc is None:
        return                                   # nothing was talking
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)                     # reap it, but never wait on a hung one
    except Exception:
        pass


def _stop_edge() -> None:
    try:
        from . import edge_voice

        edge_voice.stop()
    except Exception:
        pass


def _start_windows(text: str) -> subprocess.Popen | None:
    """Start one Windows utterance and register its PID. Caller holds `_lock`."""
    global _child

    command = ["powershell", "-NoProfile", "-Command",
               "Add-Type -AssemblyName System.Speech; "
               "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
               ".Speak($env:EPU_SAY)"]
    try:
        proc = _launch(command, env={**os.environ, "EPU_SAY": text},
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except OSError:
        return None                              # no PowerShell here: silence, not a crash
    _child = proc
    return proc


def _reap(proc: subprocess.Popen) -> None:
    """Wait for one utterance to finish, then forget its PID."""
    global _child

    try:
        proc.wait()
    except Exception:
        pass
    finally:
        with _lock:
            if _child is proc:                   # a newer reply may own the voice now
                _child = None


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
    """Speak one reply, unless a newer one has already taken the voice."""
    global _seq

    with _lock:
        if mine != _seq:                         # a newer reply came in: let it speak
            return
        engine = os.environ.get("EPU_TTS_ENGINE", "windows").strip().lower()
        if engine in ("edge", "auto"):
            if _speak_edge(text, agent):
                return
        proc = _start_windows(text)
    if proc is not None:
        _reap(proc)                              # outside the lock: never block a reply


def say(text: str, agent: str = "you", wait: bool = False) -> str:
    """Speak one reply. Returns the language it was spoken in; never raises."""
    global _seq, _thread

    body = clean_for_speech(text)
    if not body:
        return "en"
    with _lock:
        _seq += 1
        mine = _seq
        # A new reply stops the old one instead of playing on top of it: the old
        # child is dead before this one is allowed to start, so the owner hears one
        # voice even when two replies land in the same second.
        _stop_windows()
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

    with _lock:
        _seq += 1
        _stop_windows()
    _stop_edge()


def talking() -> bool:
    """Is a voice speaking right now? A best-effort read for the HUD's VOICE row."""
    proc = _child
    return proc is not None and proc.poll() is None


def voices() -> dict:
    """What the office can speak with, for the HUD's VOICE row."""
    return {"languages": [{"code": code, "name": name}
                          for code, name in (("en", "English"), ("bn", "Bangla"),
                                             ("hi", "Hindi"))],
            "engine": os.environ.get("EPU_TTS_ENGINE", "windows"),
            "policy": policy().get("voice", {})}
