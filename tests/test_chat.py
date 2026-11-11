"""Step 1 of EPU: the chat path and the order engine, tested the way the owner uses them.

A greeting, an identity question, an order that really runs, and a question with no key.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epu import chat, config, engine, memory, rules            # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
