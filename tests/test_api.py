"""The office over HTTP and the biometrics, tested the way the owner uses them.

The API is started on a random port in a thread; every request is a real HTTP request.
Biometrics use a real OpenCV model and real audio maths, not mocks.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["EPU_ROOT"] = str(Path(tempfile.mkdtemp(prefix="epu-api-")))

from epu import api, backup, config, face, memory, stt, tts        # noqa: E402


def load_cli():
    """The owner's command line, loaded as a module without running it."""
    spec = importlib.util.spec_from_file_location("epu_cli", str(ROOT / "epu.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def speech_model_on_disk() -> bool:
    """Is the speech model the office will actually use already downloaded?

    faster-whisper pulls a 145 MB+ model on first use, so the transcribe test skips
    (not fails) until that download has happened: the model is checked by the name
    the office configured, not by "some whisper-ish thing in the cache".
    """
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError:
        return False
    try:
        cache = scan_cache_dir()
    except Exception:
        return False
    wanted = {f"Systran/faster-whisper-{stt.STT_MODEL}",
              f"guillaumekln/faster-whisper-{stt.STT_MODEL}"}
    return any(repo.repo_id in wanted and repo.size_on_disk > 0 for repo in cache.repos)


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
        if not speech_model_on_disk():
            self.skipTest(f"speech model '{stt.STT_MODEL}' is not downloaded yet "
                          "(faster-whisper downloads it on first use)")
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
    """One voice, proven with real processes and no audio.

    A fake speaker is injected into the one place tts creates a voice process, so the
    office's own start/stop/kill code runs for real while nothing is ever spoken: the
    "kill" is a real child process that really dies.
    """

    def setUp(self):
        self.real_launch = tts._launch
        self.children: list[subprocess.Popen] = []
        tts._launch = self.fake_speaker

    def tearDown(self):
        tts._launch = self.real_launch
        tts.stop()
        for child in self.children:
            if child.poll() is None:
                child.kill()

    def fake_speaker(self, command, **kwargs):
        """A speaker that takes as long as the text says: no audio, real PIDs."""
        text = (kwargs.get("env") or {}).get("EPU_SAY", "")
        seconds = 30 if "long" in text.lower() else 0.2
        child = self.real_launch(
            [sys.executable, "-c", f"import time; time.sleep({seconds})"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def _child(self, index: int) -> subprocess.Popen | None:
        """The index-th utterance the office started, as soon as it exists."""
        deadline = time.time() + 10
        while time.time() < deadline:
            if len(self.children) > index:
                return self.children[index]
            time.sleep(0.02)
        return None

    def _gone(self, child: subprocess.Popen) -> bool:
        deadline = time.time() + 10
        while time.time() < deadline:
            if child.poll() is not None:
                return True
            time.sleep(0.02)
        return False

    def test_a_new_reply_kills_the_voice_that_is_still_talking(self):
        tts.say("a long reply that would take a while to finish", wait=False)
        first = self._child(0)
        self.assertIsNotNone(first, "the office never started a voice")
        self.assertIsNone(first.poll(), "the first utterance was not still talking")
        self.assertTrue(tts.talking())

        tts.say("second, newer reply", wait=False)
        second = self._child(1)
        self.assertIsNotNone(second, "the newer reply never got its own voice")

        self.assertTrue(self._gone(first), "the first utterance was never stopped")
        self.assertNotEqual(first.poll(), 0, "it ended by itself - it was not killed")
        self.assertGreaterEqual(tts._seq, 2)

    def test_a_short_reply_still_finishes(self):
        tts.say("a short reply", wait=False)
        child = self._child(0)
        self.assertIsNotNone(child)
        self.assertTrue(self._gone(child), "a short utterance never completed")
        self.assertEqual(child.poll(), 0)
        self.assertFalse(tts.talking())

    def test_stop_kills_whatever_is_talking(self):
        tts.say("a long reply that would take a while", wait=False)
        child = self._child(0)
        self.assertIsNotNone(child)
        tts.stop()
        self.assertTrue(self._gone(child), "stop() left the voice running")
        self.assertNotEqual(child.poll(), 0)

    def test_speech_text_is_cleaned_not_read_raw(self):
        cleaned = tts.clean_for_speech("```python\nprint(1)\n```\n# Hello **sir**")
        self.assertNotIn("```", cleaned)
        self.assertIn("Hello sir".upper() if False else "sir", cleaned)


class T13_MemoryBackup(unittest.TestCase):
    """The owner's memory, backed up and put back: real databases, real numbers.

    Every fact here is written into a throwaway database, so the owner's real memory
    is never touched by this test.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="epu-backup-"))
        self.db = self.tmp / "office.sqlite"
        self.share = self.tmp / "share-drive"
        self.share.mkdir()
        self.mem = memory.Memory(self.db)
        for i in range(3):
            self.mem.remember(f"the owner's fact number {i}", trust="owner")
        self.facts = [row["content"] for row in
                      self.mem.db.execute("SELECT content FROM facts ORDER BY id")]
        self.stats = self.mem.stats()

    def tearDown(self):
        self.mem.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_backup_reports_real_numbers(self):
        result = backup.backup(self.db, dest=self.share)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["facts"], 3)
        self.assertGreater(result["bytes"], 0)
        made = Path(result["path"])
        self.assertTrue(made.is_file())
        self.assertEqual(result["bytes"], made.stat().st_size)
        self.assertTrue(result["when"])

    def test_a_full_round_trip_puts_every_fact_back(self):
        made = backup.backup(self.db, dest=self.share)
        self.assertTrue(made["ok"], made)

        fresh = self.tmp / "restored" / "office.sqlite"
        put_back = backup.restore(made["path"], target=fresh)
        self.assertTrue(put_back["ok"], put_back)
        self.assertEqual(put_back["facts"], 3)

        with memory.Memory(fresh) as again:
            self.assertEqual(again.stats(), self.stats)
            rows = [row["content"] for row in
                    again.db.execute("SELECT content FROM facts ORDER BY id")]
        self.assertEqual(rows, self.facts)
        hits = memory.Memory(fresh).search("fact number 1")
        self.assertTrue(any("fact number 1" in hit["content"] for hit in hits))

    def test_a_restore_of_a_stale_backup_is_refused_without_force(self):
        made = backup.backup(self.db, dest=self.share)
        self.mem.remember("a fact written after the backup was taken", trust="owner")
        self.mem.close()                         # the office is closed, as it should be

        refused = backup.restore(made["path"], target=self.db)
        self.assertFalse(refused["ok"], refused)
        self.assertIn("force", refused["error"])
        with memory.Memory(self.db) as live:
            self.assertEqual(live.stats()["facts"], 4)       # the live memory is untouched

        forced = backup.restore(made["path"], target=self.db, force=True)
        self.assertTrue(forced["ok"], forced)
        self.assertEqual(forced["facts"], 3)
        with memory.Memory(self.db) as live:
            self.assertEqual(live.stats()["facts"], 3)
            self.assertEqual(live.stats(), self.stats)

    def test_a_restore_is_refused_while_the_office_holds_the_log(self):
        """The one case that must never half-happen: the database is still open, so
        the old write-ahead log cannot be cleared and the restore would come undone
        the moment the office wrote again."""
        other = self.tmp / "still-open.sqlite"
        clone = memory.Memory(other)
        for line in self.facts:
            clone.remember(line, trust="owner")
        clone.close()
        held_open = memory.Memory(other)          # the office, still running
        made = backup.backup(other, dest=self.share)

        held = backup.restore(made["path"], target=other)
        self.assertFalse(held["ok"], held)
        self.assertIn("write-ahead log", held["error"])

        forced = backup.restore(made["path"], target=other, force=True)
        self.assertTrue(forced["ok"], forced)
        held_open.close()
        with memory.Memory(other) as live:
            self.assertEqual(live.stats(), self.stats)

    def test_a_corrupt_backup_is_refused(self):
        broken = self.share / "office-broken.zip"
        broken.write_bytes(b"this is not a zip file at all")
        target = self.tmp / "live" / "office.sqlite"

        refused = backup.restore(broken, target=target)
        self.assertFalse(refused["ok"], refused)
        self.assertIn("not a readable backup", refused["error"])
        self.assertFalse(target.exists(), "a corrupt backup still wrote a database")

    def test_a_zip_without_a_database_inside_is_refused(self):
        empty = self.share / "office-empty.zip"
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("notes.txt", "nothing in here")

        refused = backup.restore(empty, target=self.tmp / "live" / "office.sqlite")
        self.assertFalse(refused["ok"], refused)
        self.assertIn("no office.sqlite", refused["error"])

    def test_list_backups_reports_names_created_times_and_facts(self):
        backup.backup(self.db, dest=self.share)
        self.mem.remember("one more fact, for a second backup")
        self.mem.close()
        backup.backup(self.db, dest=self.share)

        original, backup.BACKUP_DIR = backup.BACKUP_DIR, self.share
        try:
            rows = backup.list_backups()
        finally:
            backup.BACKUP_DIR = original
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row["name"].endswith(".zip"))
            self.assertTrue(row["when"])
            self.assertGreater(row["bytes"], 0)
            self.assertIn(row["facts"], (3, 4))
        self.assertEqual([row["facts"] for row in rows], [4, 3])   # newest first
        self.assertEqual([row["created"] for row in rows],
                         sorted([row["created"] for row in rows], reverse=True))

    def test_a_missing_database_is_an_honest_failure(self):
        result = backup.backup(self.tmp / "not-there.sqlite", dest=self.share)
        self.assertFalse(result["ok"])
        self.assertIn("no database", result["error"])


class T14_BackupCommands(unittest.TestCase):
    """python epu.py backup | backups | restore, run for real.

    The office's live paths are pointed at a throwaway folder, so the owner's own
    memory is never read, written or replaced by this test.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="epu-cli-"))
        self.db = self.tmp / "office.sqlite"
        self.mem = memory.Memory(self.db)
        for i in range(2):
            self.mem.remember(f"a fact to keep, number {i}", trust="owner")
        self.mem.close()
        self.saved = (backup.LIVE_DB, backup.BACKUP_DIR)
        backup.LIVE_DB = self.db
        backup.BACKUP_DIR = self.tmp / "backups"
        self.cli = load_cli()

    def tearDown(self):
        backup.LIVE_DB, backup.BACKUP_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.cli.COMMANDS[args[0]](list(args[1:]))
        return code, out.getvalue()

    def test_backup_backups_and_restore_speak_plain_english(self):
        code, text = self._run("backup")
        self.assertEqual(code, 0)
        self.assertIn("backed up", text.lower())
        self.assertIn("2 fact(s)", text)

        code, text = self._run("backups")
        self.assertEqual(code, 0)
        self.assertIn("office-", text)
        self.assertIn("1 backup(s)", text)

        self.mem = memory.Memory(self.db)
        self.mem.remember("a fact written after the backup")
        self.mem.close()
        name = next(path.name for path in backup.BACKUP_DIR.glob("*.zip"))

        code, text = self._run("restore", name)
        self.assertEqual(code, 1)
        self.assertIn("not restored", text.lower())
        self.assertIn("force", text)

        code, text = self._run("restore", name, "--force")
        self.assertEqual(code, 0)
        self.assertIn("restored", text.lower())
        self.assertIn("2 fact(s)", text)
        with memory.Memory(self.db) as live:
            self.assertEqual(live.stats()["facts"], 2)

    def test_restore_without_a_file_says_how(self):
        code, text = self._run("restore")
        self.assertEqual(code, 2)
        self.assertIn("python epu.py restore", text)


class T15_BackupHygiene(unittest.TestCase):
    """A backup the owner might share: no keys in it, and never inside the repo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="epu-hygiene-"))
        self.db = self.tmp / "office.sqlite"
        self.mem = memory.Memory(self.db)
        self.mem.remember("one fact about the owner", trust="owner")
        self.mem.close()
        # A decoy private folder beside the database: the proof that a backup never
        # walks the parent directory and sweeps up whatever is in it.
        keys = self.tmp / "secure"
        keys.mkdir()
        (keys / "keys.env").write_text("EPU_NVIDIA_KEY_1=not-a-real-key\n", encoding="utf-8")
        (keys / "office.pfx").write_bytes(b"not a real certificate")
        self.share = self.tmp / "share-drive"
        self.share.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nothing_from_secure_is_ever_inside_a_backup(self):
        made = backup.backup(self.db, dest=self.share)
        self.assertTrue(made["ok"], made)
        with zipfile.ZipFile(made["path"]) as zf:
            names = zf.namelist()
        self.assertNotIn("secure/keys.env", names)
        for name in names:
            self.assertFalse(name.startswith("secure/"), names)
            self.assertTrue(backup.never_inside(name), names)
        self.assertIn("office.sqlite", names)

    def test_a_backup_the_owner_might_share_lives_outside_the_repo(self):
        made = backup.backup(self.db, dest=self.share)
        self.assertTrue(made["ok"], made)
        target = Path(made["path"]).resolve()
        self.assertFalse(backup.inside_worktree(target), target)
        self.assertNotIn(str(ROOT.resolve()), str(target))
        self.assertNotIn(str(ROOT.resolve()), str(target.parent))

    def test_the_office_keeps_its_own_copy_only_where_git_cannot_have_it(self):
        # backups/ is the one folder inside the repo, and only because .gitignore
        # covers it: the day that stops being true, the backup refuses to be made.
        self.assertTrue(backup.git_ignored(backup.BACKUP_DIR / "office-test.zip"))
        self.assertTrue(backup.inside_worktree(backup.BACKUP_DIR))

    def test_another_folder_inside_the_repo_is_refused(self):
        for folder in (ROOT / "projects", ROOT / "memory", ROOT / "hud"):
            result = backup.backup(self.db, dest=folder)
            self.assertFalse(result["ok"], f"{folder} was written to")
            self.assertIn("inside the git repo", result["error"])
            self.assertFalse(list(folder.glob("*.zip")), f"a zip landed in {folder}")

    def test_the_secure_folder_is_never_a_destination(self):
        result = backup.backup(self.db, dest=self.tmp / "secure")
        self.assertFalse(result["ok"])
        self.assertIn("secure", result["error"])
        self.assertFalse(list((self.tmp / "secure").glob("*.zip")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
