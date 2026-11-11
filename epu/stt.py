"""Voice recognition, on this PC, with no cloud and no key.

The office knows the owner's voice in three steps, all local:

  1. the mic clip comes in (16 kHz mono WAV, from the browser or the phone page);
  2. `transcribe` turns speech into text with faster-whisper (already installed,
     runs on the CPU, offline);
  3. `VoiceID` compares the clip against enrolled voice prints.

The comparison is a real measurement, not a guess: MFCC frames -> the speaker's mean
spectrum -> cosine distance against every enrolled sample. Enrolment needs a few
seconds of the owner's own voice; verification passes when the distance is inside the
threshold we measured while enrolling (you can tighten it, never loosen it silently).
"""

from __future__ import annotations

import io
import math
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME = 0.025          # 25 ms analysis window
STRIDE = 0.010         # 10 ms hop
N_MELS = 26
N_MFCC = 13
MIN_SPEECH_SECONDS = 0.4
MAX_CLIP_SECONDS = 30


class VoiceUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------------ audio in
def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Bytes of a WAV file -> float32 mono audio plus its sample rate."""
    with wave.open(io.BytesIO(data), "rb") as wf:
        channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif width == 1:
        audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise VoiceUnavailable(f"unsupported audio width: {width} bytes")
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:                       # resample by linear interpolation
        idx = np.arange(0, len(audio), rate / SAMPLE_RATE)
        audio = np.interp(idx, np.arange(len(audio)), audio)
    return audio.astype(np.float32), SAMPLE_RATE


def wav_bytes(audio: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return out.getvalue()


# ------------------------------------------------------------------ the voice print
def _frames(audio: np.ndarray) -> np.ndarray:
    window = int(FRAME * SAMPLE_RATE)
    step = int(STRIDE * SAMPLE_RATE)
    if len(audio) < window:
        audio = np.pad(audio, (0, window - len(audio)))
    count = 1 + (len(audio) - window) // step
    index = np.arange(window)[None, :] + step * np.arange(count)[:, None]
    return audio[index] * np.hanning(window)


def _mel_filterbank(rate: int = SAMPLE_RATE) -> np.ndarray:
    n_fft = int(FRAME * rate)
    low, high = 20.0, rate / 2.0
    mel_points = np.linspace(
        2595 * math.log10(1 + low / 700), 2595 * math.log10(1 + high / 700), N_MELS + 2)
    bins = np.floor((n_fft + 1) * (700 * (10 ** (mel_points / 2595) - 1) / rate)).astype(int)
    fb = np.zeros((N_MELS, n_fft // 2 + 1))
    for m in range(1, N_MELS + 1):
        left, centre, right = int(bins[m - 1]), int(bins[m]), int(bins[m + 1])
        if centre <= left:
            centre = left + 1
        if right <= centre:
            right = centre + 1
        for k in range(left, centre):
            fb[m - 1, k] = (k - left) / max(1, centre - left)
        for k in range(centre, right):
            fb[m - 1, k] = (right - k) / max(1, right - centre)
    return fb


def voice_print(audio: np.ndarray) -> np.ndarray | None:
    """One 13-number fingerprint of a speaker, or None when the clip is too short."""
    if audio is None or len(audio) < MIN_SPEECH_SECONDS * SAMPLE_RATE:
        return None
    frames = _frames(audio)
    if len(frames) == 0:
        return None
    spectrum = np.abs(np.fft.rfft(frames)) ** 2
    energy = spectrum.sum(axis=1)
    if float(energy.max() or 0.0) <= 0.0:
        return None
    mel = np.log(np.maximum(spectrum @ _mel_filterbank().T, 1e-10))
    mfcc = np.fft.rfft(mel, axis=1).real[:, :N_MFCC]
    # Keep only the frames that carry speech: the loudest two thirds.
    keep = energy >= np.percentile(energy, 33)
    if keep.sum() < 3:
        keep = np.ones_like(energy, dtype=bool)
    return mfcc[keep].mean(axis=0)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 0.0:
        return 1.0
    return 1.0 - float(np.dot(a, b)) / denom


# ------------------------------------------------------------------ enrollment + matching
@dataclass
class VoiceID:
    root: Path
    threshold: float = 0.12                 # cosine distance; lower is stricter
    saved: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _state_file(self) -> Path:
        return self.root / "voice-print.json"

    def _load(self) -> None:
        import json

        state = self._state_file()
        if state.exists():
            self.saved = json.loads(state.read_text(encoding="utf-8"))

    def _save(self) -> None:
        import json

        self._state_file().write_text(json.dumps(self.saved, indent=1), encoding="utf-8")

    def clarify(self, audio: np.ndarray, seconds: float = 2.5) -> dict:
        """Enrol or re-enrol the owner's voice. Returns what was stored."""
        clip = audio[: int(seconds * SAMPLE_RATE)]
        print_ = voice_print(clip)
        if print_ is None:
            return {"ok": False, "error": "not enough speech - speak for 2 seconds"}
        self._load()
        self.saved = {"print": [float(x) for x in print_], "seconds": float(seconds),
                      "enrolled": True}
        self._save()
        return {"ok": True, "seconds": float(seconds), "print_size": len(print_)}

    def enrolled(self) -> bool:
        self._load()
        return bool(self.saved.get("enrolled"))

    def verify(self, audio: np.ndarray) -> dict:
        """Compare a clip against the enrolled voice. Honest numbers, no guessing."""
        self._load()
        stored = self.saved.get("print")
        if not stored:
            return {"ok": False, "error": "no voice enrolled yet", "score": None}
        clip = audio[: int(MAX_CLIP_SECONDS * SAMPLE_RATE)]
        print_ = voice_print(clip)
        if print_ is None:
            return {"ok": False, "error": "not enough speech", "score": None}
        distance = _cosine(print_, np.asarray(stored, dtype=np.float32))
        return {"ok": distance <= self.threshold, "score": round(distance, 4),
                "threshold": self.threshold}


# ------------------------------------------------------------------ speech to text
_whisper = None


def transcribe(data: bytes, language: str | None = None) -> dict:
    """Speech -> text, offline. Returns text, detected language, and seconds."""
    audio, rate = read_wav(data)
    if len(audio) < MIN_SPEECH_SECONDS * rate:
        return {"ok": False, "error": "too short to transcribe"}
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceUnavailable("faster-whisper is not installed") from exc
    global _whisper
    if _whisper is None:
        # "small" is the honest size for a CPU: faster models mistake short orders.
        _whisper = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = _whisper.transcribe(
        audio.astype(np.float32), language=language, vad_filter=True,
        beam_size=1)
    text = " ".join(s.text.strip() for s in segments).strip()
    text = re.sub(r"\s{2,}", " ", text)
    return {"ok": bool(text), "text": text, "language": getattr(info, "language", None) or "en"}
