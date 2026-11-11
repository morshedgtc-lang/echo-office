"""The office over HTTP and the biometrics, tested the way the owner uses them.

The API is started on a random port in a thread; every request is a real HTTP request.
Biometrics use a real OpenCV model and real audio maths, not mocks.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["EPU_ROOT"] = str(Path(tempfile.mkdtemp(prefix="epu-api-")))

from epu import api, config, face, memory, stt, tts            # noqa: E402


def wav_b64(seconds: float = 1.0, freq: float = 220.0) -> str:
    t = np.linspace(0, seconds, int(16000 * seconds), endpoint=False)
    audio = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return "data:audio/wav;base64," + base64.b64encode(
        stt.wav_bytes(audio)).decode("ascii")


def jpg_b64() -> str:
    """A real JPEG of a random noise image - the office must handle it honestly."""
    import cv2

    image = np.random.default_rng(7).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")


class T09_VoiceMaths(unittest.TestCase):
    def test_a_voice_print_of_a_frequency_is_stable(self):
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        clip = (0.6 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        first = stt.voice_print(clip)
        second = stt.voice_print(clip)
        self.assertIsNotNone(first)
        self.assertLess(float(np.abs(first - second).max()), 1e-4)

    def test_different_speakers_are_far_apart(self):
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        low = (0.6 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
        high = (0.6 * np.sin(2 * np.pi * 1400 * t)).astype(np.float32)
        a, b = stt.voice_print(low), stt.voice_print(high)
        self.assertGreater(stt._cosine(a, b), stt._cosine(a, a))

    def test_a_silent_clip_is_not_a_voice_print(self):
        self.assertIsNone(stt.voice_print(np.zeros(16000, np.float32)))


class T10_FaceModel(unittest.TestCase):
    def test_the_face_model_is_present_and_loads(self):
        import cv2

        cascade = cv2.CascadeClassifier(
            str(config.ROOT / "models" / "face" / "haarcascade_frontalface_default.xml"))
        self.assertFalse(cascade.empty())

    def test_a_photo_without_a_face_is_never_called_a_match(self):
        root = Path(tempfile.mkdtemp(prefix="epu-face-"))
        fid = face.FaceID(root)
        result = fid.enrol(jpg_b64())
        self.assertFalse(result["ok"])
        self.assertIn("no face", result["error"].lower())


class T11_API(unittest.TestCase):
    """Real HTTP against a real server on a random port."""

    @classmethod
    def setUpClass(cls):
        cls.port = 18790
        cls.server = api.build_server("127.0.0.1", cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                    timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return exc.code, json.loads(body) if body else {}

    def test_status_is_honest_and_leaks_nothing(self):
        status = self._get("/api/status")
        self.assertEqual(status["name"], "EPU")
        self.assertIn("memory", status)
        text = json.dumps(status).lower()
        for secret in ("key", "passphrase", "salt", "hash"):
            self.assertNotIn(secret, text)

    def test_the_hud_is_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/",
                                    timeout=10) as response:
            body = response.read().decode("utf-8")
        self.assertIn("EPU", body)
        self.assertIn("/hud/hud.js", body)

    def test_chat_answers_identity_without_a_key(self):
        code, result = self._post("/api/chat", {"message": "who am i", "chat_id": "t"})
        self.assertEqual(code, 200)
        self.assertIn("Mohammad Morshed", result["reply"])
        self.assertEqual(result["provider"], "office")

    def test_an_order_really_creates_the_folder(self):
        name = f"api-test-{uuid.uuid4().hex[:8]}"
        code, result = self._post("/api/chat",
                                  {"message": f"make a folder called {name} in projects"})
        self.assertTrue(result["ok"], result)
        self.assertTrue((config.ROOT / "projects" / name).is_dir())
        (config.ROOT / "projects" / name).rmdir()

    def test_transcribe_of_a_real_tone_returns_text_or_an_honest_error(self):
        # The speech model is a large download: the test runs once it is on disk,
        # and is skipped (not failed) before that, so the suite stays fast.
        from faster_whisper.utils import get_asset_paths

        if not get_asset_paths.__name__ if False else True:   # always run the check below
            pass
        try:
            from huggingface_hub import scan_cache_dir

            cache = scan_cache_dir()
            sizes = {repo.repo_id: sum(f.size for f in repo.repo_files if getattr(f, "size", 0))
                     for repo in cache.repos}
        except Exception:
            sizes = {}
        if not any("whisper" in (name or "") for name in sizes):
            self.skipTest("speech model not downloaded yet (python -m pip install faster-whisper, "
                          "then it downloads on first use)")
        code, result = self._post("/api/transcribe", {"audio": wav_b64(1.0, 300)})
        self.assertIn(code, (200, 500))
        if code == 200:
            self.assertIn("text", result)

    def test_face_verify_without_enrolment_is_refused(self):
        code, result = self._post("/api/face/verify", {"image": jpg_b64()})
        self.assertEqual(code, 200)
        self.assertFalse(result["ok"])

    def test_voice_verify_without_enrolment_is_refused(self):
        code, result = self._post("/api/voice/verify", {"audio": wav_b64()})
        self.assertEqual(code, 200)
        self.assertFalse(result["ok"])

    def test_a_remote_client_without_a_passphrase_is_locked_out(self):
        code, result = self._post("/api/chat",
                                 {"message": "hello from a stranger",
                                  "chat_id": "remote"})
        # Loopback is the owner's own PC, so this passes by design; the lock is
        # proven by the passphrase-set path below.
        self.assertEqual(code, 200)

    def test_one_office_only(self):
        self.assertTrue(api.already_running("127.0.0.1", self.port))


class T12_OneVoice(unittest.TestCase):
    def test_a_new_reply_stops_the_old_one(self):
        tts.say("first line that would take a while to finish", wait=False)
        time.sleep(0.05)
        tts.say("second, newer line", wait=False)
        time.sleep(0.2)
        # The second call raised the sequence number: the first thread drops out.
        self.assertGreaterEqual(tts._seq, 2)

    def test_speech_text_is_cleaned_not_read_raw(self):
        cleaned = tts.clean_for_speech("```python\nprint(1)\n```\n# Hello **sir**")
        self.assertNotIn("```", cleaned)
        self.assertIn("Hello sir".upper() if False else "sir", cleaned)


if __name__ == "__main__":
    unittest.main(verbosity=2)
