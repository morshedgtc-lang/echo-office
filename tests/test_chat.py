"""Step 1 of EPU: the chat path and the order engine, tested the way the owner uses them.

A greeting, an identity question, an order that really runs, and a question with no key.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epu import brains, chat, config, engine, memory, rules      # noqa: E402

try:                       # psutil is an optional dependency: absent -> skip, never fail
    import psutil
except ImportError:        # pragma: no cover - the office laptop has psutil
    psutil = None


@contextlib.contextmanager
def no_cloud():
    """No key, no local engine, no network: the test must cost nothing and wait never."""
    keys = ["EPU_NVIDIA_KEY_1", "EPU_NVIDIA_KEY_2"]
    keys += [name for p in config.cloud_providers() for name in (p.get("key_env") or [])]
    saved = {k: os.environ.pop(k, None) for k in keys}
    local = os.environ.pop("EPU_LOCAL", None)
    config.reset_cache()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
        if local is not None:
            os.environ["EPU_LOCAL"] = local
        config.reset_cache()


def office_child(texts: tuple[str, ...]) -> dict:
    """Run the real office in a fresh process and report what it did and loaded.

    A child is the only honest measurement: this test process carries pytest's own
    imports (and, in a full run, the other test modules'), so its RSS and module list
    say nothing about the chat path. Every text below is answered on disk, so the
    child costs no network - keys.env is loaded by config on import, and no text here
    ever reaches a cloud provider.
    """
    code = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "from epu import chat\n"
        "replies = [chat.answer(t) for t in %r]\n"
        "heavy = sorted(m for m in sys.modules if m.split('.')[0] in\n"
        "               ('numpy', 'cv2', 'faster_whisper', 'whisper', 'huggingface_hub')\n"
        "               or m.startswith('epu.stt'))\n"
        "try:\n"
        "    import psutil\n"
        "    rss = round(psutil.Process().memory_info().rss / 1048576, 1)\n"
        "except Exception:\n"
        "    rss = None\n"
        "print(json.dumps({'replies': replies, 'heavy': heavy, 'rss_mb': rss}))\n"
    ) % (str(ROOT), list(texts))
    env = {k: v for k, v in os.environ.items()
           if k not in ("EPU_LOCAL", "EPU_LOCAL_ONLY", "EPU_NVIDIA_KEY_1", "EPU_NVIDIA_KEY_2")}
    done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0 or not done.stdout.strip():
        raise AssertionError(f"the office process failed: {done.stderr[-2000:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


class T05_GreetingAndIdentity(unittest.TestCase):
    def test_greeting_is_instant_and_names_the_assistant(self):
        mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-")) / "office.sqlite")
        rules.seed(mem)
        started = time.time()
        answer = chat.answer("hi", mem=mem)
        elapsed = time.time() - started
        self.assertLess(elapsed, 0.2, elapsed)
        self.assertIn("EPU", answer["reply"])
        self.assertEqual(answer["kind"], "greeting")
        mem.close()

    def test_bangla_greeting_is_answered_in_bangla(self):
        self.assertTrue(chat.detect_language("হ্যালো") == "bn")
        reply = rules.quick_reply(language="bn")
        self.assertIn("EPU", reply)

    def test_identity_comes_from_disk_not_from_a_model(self):
        mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-")) / "office.sqlite")
        rules.seed(mem)
        answer = chat.answer("who am i", mem=mem)
        self.assertEqual(answer["provider"], "office")       # no cloud call
        self.assertIn("Mohammad Morshed", answer["reply"])
        self.assertLess(answer["ms"], 100)
        mem.close()

    def test_standing_orders_come_from_the_owners_own_facts(self):
        mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-")) / "office.sqlite")
        rules.seed(mem)
        answer = chat.answer("what are my standing orders", mem=mem)
        self.assertIn("never refuse", answer["reply"])
        mem.close()


class T06_OrdersReallyRun(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="epu-order-"))
        self.mem = memory.Memory(self.root / "office.sqlite")
        self.scope = "owner"

    def _run(self, text: str) -> dict:
        return engine.run(text, self.mem, scope=self.scope)

    def test_make_folder_creates_the_folder_it_named(self):
        result = self._run("make a folder called reports in projects/easyphoneunlocker")
        self.assertTrue(result["ok"], result)
        target = config.ROOT / "projects/easyphoneunlocker/reports"
        self.assertTrue(target.is_dir())
        self.assertIn("projects/easyphoneunlocker/reports", result["reply"])
        self.assertEqual(result["verification_problems"] if "verification_problems" in result
                         else result.get("problems"), [])

    def test_write_file_writes_the_text_it_named(self):
        result = self._run("write a file called notes.txt with hello from EPU in projects")
        self.assertTrue(result["ok"], result)
        target = config.ROOT / "projects/notes.txt"
        self.assertEqual(target.read_text(encoding="utf-8"), "hello from EPU")
        target.unlink()

    def test_list_dir_shows_what_is_really_there(self):
        (config.ROOT / "projects/test-list").mkdir(parents=True, exist_ok=True)
        result = self._run("list the folders in projects/test-list")
        self.assertTrue(result["ok"], result)
        self.assertIn("test-list", result["reply"])
        (config.ROOT / "projects/test-list").rmdir()

    def test_rename_moves_the_file_and_reports_the_new_name(self):
        src = config.ROOT / "projects/before.txt"
        src.write_text("x", encoding="utf-8")
        result = self._run("rename before.txt to after.txt in projects")
        self.assertTrue(result["ok"], result)
        self.assertTrue((config.ROOT / "projects/after.txt").exists())
        self.assertFalse(src.exists())
        (config.ROOT / "projects/after.txt").unlink()

    def test_remember_stores_it_and_search_finds_it(self):
        result = self._run("remember that the wifi code is stored in the office only")
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.mem.search("wifi code"))

    def test_fix_memory_reports_honest_numbers(self):
        result = self._run("fix your memory")
        self.assertTrue(result["ok"], result)
        self.assertIn("memory checked", result["reply"])

    def test_an_order_outside_the_write_folders_is_refused_by_code(self):
        result = self._run("make a folder called bad in secure")
        self.assertFalse(result["ok"])
        self.assertIn("protected", result["error"].lower())
        self.assertFalse((config.ROOT / "secure" / "bad").exists())

    def test_the_protected_root_refusal_is_one_line_and_names_the_way_forward(self):
        """The safety gate stays, but the owner is told what he can do instead."""
        result = self._run("make a folder called bad in secure")
        reply = result["reply"]
        self.assertFalse(result["ok"])
        self.assertEqual(reply.count("\n"), 0, reply)
        self.assertNotIn("cannot", reply.lower())
        self.assertTrue(reply.rstrip().endswith("instead."), reply)
        self.assertFalse((config.ROOT / "secure" / "bad").exists())

    def test_unknown_order_is_not_faked(self):
        self.assertEqual(engine.plan("teleport to the moon"), [])


class T07_NoKeyHonesty(unittest.TestCase):
    def test_a_question_without_a_key_says_so_plainly(self):
        for name in ("EPU_NVIDIA_KEY_1", "EPU_NVIDIA_KEY_2"):
            os.environ.pop(name, None)
        mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-")) / "office.sqlite")
        answer = chat.answer("what is the capital of France", mem=mem)
        self.assertIn("no cloud key", answer["reply"])
        self.assertFalse(answer["ok"])
        mem.close()


class T08_Budget(unittest.TestCase):
    def test_every_non_model_path_answers_under_a_second(self):
        mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-")) / "office.sqlite")
        rules.seed(mem)
        for text in ("hi", "who am i", "my orders"):
            started = time.time()
            chat.answer(text, mem=mem)
            self.assertLess(time.time() - started, 1.0, text)
        mem.close()


class T13_OrdersAlwaysGetAnAttempt(unittest.TestCase):
    """The standing order is "never refuse": no plan is never an answer of its own."""

    def setUp(self):
        self.mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-fall-")) / "office.sqlite")
        rules.seed(self.mem)
        brains._fused.clear()   # the router keeps a sick model aside for 15 s; tests must not

    def _capture_cloud(self, text, **kwargs):
        """Ask through the real chat path, catching the payload the model would get."""
        seen = {}

        def fake_post(url, key, payload, timeout):
            seen["system"] = payload["messages"][0]["content"]
            seen["messages"] = [m["content"] for m in payload["messages"]]
            return {"choices": [{"message": {"content": "Here is a plan, sir."}}]}

        with no_cloud(), \
                mock.patch.object(config, "cloud_ready",
                                  return_value=[{"id": "t", "models": ["t-model"],
                                                 "base": "http://127.0.0.1:1/v1",
                                                 "priority": 1, "timeout_s": 5}]), \
                mock.patch.object(config, "provider_key", return_value="k"), \
                mock.patch.object(brains, "_post", side_effect=fake_post):
            answer = chat.answer(text, mem=self.mem, **kwargs)
        return answer, seen

    def test_an_order_with_no_tool_reaches_the_model(self):
        answer, seen = self._capture_cloud("summarise my week", chat_id="fall")
        self.assertEqual(answer["reply"], "Here is a plan, sir.")
        self.assertEqual(answer["kind"], "question")
        self.assertNotIn("do not have a tool", answer["reply"])
        self.assertIn("summarise my week", " ".join(seen["messages"]))

    def test_the_model_sees_the_tool_manifest_in_its_system_prompt(self):
        _answer, seen = self._capture_cloud("summarise my week", chat_id="fall")
        for tool in ("make_folder", "write_file", "list_dir"):
            self.assertIn(tool, seen["system"])
        self.assertIn("never refuse", seen["system"])

    def test_the_system_prompt_carries_the_manifest_on_its_own(self):
        prompt = brains.system_prompt("owner", self.mem, "anything")
        self.assertIn("Tools the office already runs in code", prompt)
        self.assertIn("make_folder", prompt)
        self.assertIn("say what is missing", prompt)

    def test_an_unplanned_order_without_a_key_still_gets_a_real_attempt(self):
        with no_cloud():
            answer = chat.answer("summarise my week", mem=self.mem, chat_id="fall")
        self.assertFalse(answer["ok"])
        self.assertIn("key", answer["reply"])
        self.assertNotIn("cannot", answer["reply"])
        self.assertNotIn("do not have a tool", answer["reply"])

    def tearDown(self):
        self.mem.close()


class T14_SpeechModelStaysBehindTheSwitch(unittest.TestCase):
    """Whisper is ~1 GB of RAM: the chat path may not touch it unless asked to."""

    def setUp(self):
        self.mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-voice-")) / "office.sqlite")

    @contextlib.contextmanager
    def local_model(self, on: bool):
        saved = os.environ.get("EPU_LOCAL")
        if on:
            os.environ["EPU_LOCAL"] = "1"
        else:
            os.environ.pop("EPU_LOCAL", None)
        config.reset_cache()
        try:
            yield
        finally:
            if saved is None:
                os.environ.pop("EPU_LOCAL", None)
            else:
                os.environ["EPU_LOCAL"] = saved
            config.reset_cache()

    def test_a_record_order_names_what_is_missing_and_imports_nothing(self):
        before = set(sys.modules)
        with self.local_model(False), no_cloud():
            answer = chat.answer("record a voice note", mem=self.mem, chat_id="voice")
        self.assertIn("turn on the local model", answer["reply"])
        self.assertIn("transcribe", answer["reply"])
        self.assertNotIn("cannot", answer["reply"].lower())
        self.assertEqual(answer["reply"].count("\n"), 0)
        # the chat path added no speech model to this process (another test module
        # may have imported one already: only what THIS path loads is our fault).
        self.assertNotIn("epu.stt", set(sys.modules) - before,
                         "the chat path imported the speech model")

    def test_voice_orders_are_recognised(self):
        for text in ("record a voice note", "transcribe my clip", "speech to text please",
                     "listen to me for a second", "what did i say"):
            self.assertTrue(chat.is_voice_order(text), text)
        for text in ("record the price in memory", "make me a coffee", "who am i"):
            self.assertFalse(chat.is_voice_order(text), text)

    def test_the_office_process_loads_no_speech_face_or_math_model(self):
        """The whole chat path, in its own process: numpy, cv2 and stt stay unloaded."""
        result = office_child(("hi", "who am i", "record a voice note",
                               "make a folder called epu-budget-check in projects"))
        self.assertEqual(result["heavy"], [], result["heavy"])
        self.assertIn("EPU here", result["replies"][0]["reply"])
        self.assertIn("turn on the local model", result["replies"][2]["reply"])
        self.assertTrue(result["replies"][3]["ok"], result["replies"][3])
        self.assertIn("epu-budget-check", result["replies"][3]["reply"])
        (config.ROOT / "projects/epu-budget-check").rmdir()

    @unittest.skipIf(psutil is None, "psutil is not installed")
    def test_idle_memory_stays_under_the_design_budget(self):
        """The design promises idle RSS < 250 MB; the office must be measured, not hoped."""
        result = office_child(("hi", "who am i", "record a voice note"))
        if result["rss_mb"] is None:
            self.skipTest("psutil is not installed in the child process")
        self.assertLess(result["rss_mb"], 250, result["rss_mb"])

    def test_every_local_path_answers_inside_the_time_budget(self):
        """Greeting, identity and an order: no network, no key, no waiting."""
        folder = config.ROOT / "projects/epu-budget-test"
        with no_cloud():
            try:
                for text in ("hi", "who am i",
                             "make a folder called epu-budget-test in projects",
                             "summarise my week"):
                    started = time.time()
                    answer = chat.answer(text, mem=self.mem, chat_id="budget")
                    elapsed = time.time() - started
                    self.assertLess(elapsed, 1.0, f"{text} took {elapsed:.2f}s")
                    self.assertLess(answer["ms"], 1000, f"{text} ran {answer['ms']}ms")
            finally:
                if folder.exists():
                    folder.rmdir()

    def test_suite_has_no_hard_dependency_on_downloaded_models(self):
        """A missing speech model, face model or numpy is a skip, never an ImportError."""
        banned = re.compile(r"^\s*(?:import|from)\s+(faster_whisper|whisper|cv2|numpy|"
                            r"huggingface_hub)\b")
        for name in ("test_epu.py", "test_chat.py"):
            source = (Path(__file__).parent / name).read_text(encoding="utf-8")
            for number, line in enumerate(source.splitlines(), 1):
                self.assertIsNone(banned.match(line),
                                  f"{name}:{number} imports a downloaded model: {line.strip()}")
        # and the chat path itself proves it: nothing heavy loads when it answers.
        self.assertEqual(office_child(("hi", "record a voice note"))["heavy"], [])

    def tearDown(self):
        self.mem.close()


class T15_ScopeWalls(unittest.TestCase):
    """Two clients, one chat_id: a scope is a wall, not a filter."""

    def setUp(self):
        self.mem = memory.Memory(Path(tempfile.mkdtemp(prefix="epu-wall-")) / "office.sqlite")
        brains._fused.clear()   # a sick-model pause must not leak between tests

    def _cloud_answer(self, text: str, scope: str, seen: dict) -> dict:
        def fake_post(url, key, payload, timeout):
            seen["system"] = payload["messages"][0]["content"]
            seen["all"] = " || ".join(m["content"] for m in payload["messages"])
            raise RuntimeError("no network in this test")

        # The router parks a sick model for 15 s (FUSE_SECONDS). Two answers in one
        # test are two separate calls, so the cooldown is cleared between them.
        brains._fused.clear()
        with no_cloud(), \
                mock.patch.object(config, "cloud_ready",
                                  return_value=[{"id": "t", "models": ["t-model"],
                                                 "base": "http://127.0.0.1:1/v1",
                                                 "priority": 1, "timeout_s": 5}]), \
                mock.patch.object(config, "provider_key", return_value="k"), \
                mock.patch.object(brains, "_post", side_effect=fake_post):
            return chat.answer(text, chat_id="hud", scope=scope, mem=self.mem)

    def test_one_clients_words_never_reach_another_client(self):
        mem = self.mem
        mem.remember("client alpha price list: the gold plan costs 500 taka",
                     scope="client:alpha", trust="assistant")
        mem.remember("client beta price list: the silver plan costs 900 taka",
                     scope="client:beta", trust="assistant")

        seen = {}
        self._cloud_answer("what is my price list", "client:alpha", seen)
        self.assertIn("gold plan", seen["system"])
        self.assertNotIn("silver plan", seen["system"])

        seen = {}
        self._cloud_answer("what is my price list", "client:beta", seen)
        self.assertIn("silver plan", seen["system"])
        self.assertNotIn("gold plan", seen["system"])
        self.assertNotIn("gold plan", seen["all"])

        # the conversation itself is walled the same way
        beta_rows = [r["content"] for r in mem.recent("hud", 50, "client:beta")]
        self.assertTrue(beta_rows)
        self.assertFalse(any("gold plan" in c for c in beta_rows))
        alpha_rows = [r["content"] for r in mem.recent("hud", 50, "client:alpha")]
        self.assertFalse(any("silver plan" in c for c in alpha_rows))

    def test_history_handed_to_the_brain_is_scope_filtered(self):
        mem = self.mem
        mem.add_chat("hud", "owner", "the gold plan costs 500 taka", "client:alpha")
        mem.add_chat("hud", "assistant", "noted for alpha", "client:alpha")
        mem.add_chat("hud", "owner", "the silver plan costs 900 taka", "client:beta")
        mem.remember("client alpha price list: the gold plan costs 500 taka",
                     scope="client:alpha", trust="assistant")

        history = mem.recent("hud", 4, "client:beta")
        body = " ".join(r["content"] for r in history)
        self.assertIn("silver plan", body)
        self.assertNotIn("gold plan", body)

        prompt_for_beta = brains.system_prompt("client:beta", mem, "my price list")
        self.assertNotIn("gold plan", prompt_for_beta)

    def tearDown(self):
        self.mem.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
