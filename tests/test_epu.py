"""Test suite for EPU step 1: config, memory, and the owner's laws.

    python -m pytest tests -q          (pytest, if installed)
    python -m unittest discover -s tests -v   (stdlib, always available)

The tests run in a throwaway folder and never touch the real office.
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

TMP = Path(tempfile.mkdtemp(prefix="epu-test-"))
os.environ["EPU_ROOT"] = str(TMP)          # reserved for future subprocess runs


def _installed(name: str) -> bool:
    """Optional dependencies are detected, never assumed: a missing one is a skip."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):      # pragma: no cover - a broken install
        return False


HAVE_NUMPY = _installed("numpy")

import epu.config as config          # noqa: E402
from epu import brains, memory, rules         # noqa: E402


def fresh(tmp: Path | None = None) -> memory.Memory:
    """A Memory pointed at a throwaway database."""
    base = Path(tmp or tempfile.mkdtemp(prefix="epu-test-"))
    return memory.Memory(base / "office.sqlite")


class T01_Config(unittest.TestCase):
    def test_settings_load_and_ports_have_defaults(self):
        s = config.settings()
        self.assertEqual(s["name"], "EPU")
        self.assertGreaterEqual(s["ports"]["hud"], 1024)
        self.assertIn("hud", s["ports"])

    def test_env_overrides_a_port(self):
        os.environ["EPU_HUD_PORT"] = "9911"
        config.reset_cache()
        try:
            self.assertEqual(config.port("hud"), 9911)
        finally:
            del os.environ["EPU_HUD_PORT"]
            config.reset_cache()

    def test_cloud_is_off_without_keys(self):
        self.assertEqual(config.cloud_ready(), [])
        self.assertFalse(config.provider_key(config.cloud_providers()[0]))


class T02_MemorySchema(unittest.TestCase):
    def test_schema_is_created_once_and_facts_searchable(self):
        mem = fresh()
        mem.remember("the owner is Morshed", kind="fact", scope="owner", trust="owner")
        hits = mem.search("Morshed", scope="client:x")
        self.assertTrue(any("Morshed" in h["content"] for h in hits))
        mem.close()

    def test_stopwords_do_not_break_a_search(self):
        mem = fresh()
        mem.remember("owner name is Morshed", trust="owner")
        self.assertEqual(mem.search("who am i"), [])          # nothing but stopwords
        self.assertEqual(mem.search("  "), [])
        mem.close()

    def test_owner_facts_outrank_external_knowledge(self):
        mem = fresh()
        mem.remember("Morshed is the owner", kind="fact", scope="owner", trust="owner")
        for i in range(5):
            mem.remember(f"python note {i} mentions morshed by accident",
                         kind="note", scope="shared", trust="external")
        hits = mem.search("morshed")
        self.assertEqual(hits[0]["trust"], "owner", hits[0])
        mem.close()

    def test_client_scope_does_not_leak_into_another_client(self):
        mem = fresh()
        mem.remember("client A secret price list", scope="client:a", trust="assistant")
        hits = mem.search("price list", scope="client:b")
        self.assertEqual(hits, [])
        mem.close()

    def test_chat_memory_never_raises_when_the_db_is_busy(self):
        mem = fresh()
        self.assertTrue(mem.add_chat("hud", "owner", "hello"))
        self.assertEqual(len(mem.recent("hud")), 1)
        mem.close()


class T03_Repair(unittest.TestCase):
    def test_repair_reports_honest_numbers(self):
        base = Path(tempfile.mkdtemp(prefix="epu-repair-"))
        mem = memory.Memory(base / "office.sqlite")
        mem.remember("a fact worth keeping", trust="owner")
        out = mem.repair()
        self.assertTrue(out["ok"], out)
        self.assertIn("sqlite integrity", out["checked"])
        self.assertEqual(out["stats"]["facts"], 1)
        mem.close()

    def test_repair_rebuilds_an_index_with_ghost_rows(self):
        base = Path(tempfile.mkdtemp(prefix="epu-ghost-"))
        mem = memory.Memory(base / "office.sqlite")
        mem.remember("a memory worth finding", trust="owner")
        row = mem.db.execute("SELECT id, content FROM facts LIMIT 1").fetchone()
        # simulate a row the search index lost (a memory that cannot be recalled)
        mem.db.execute(
            "INSERT INTO facts_fts(facts_fts, rowid, content) VALUES ('delete', ?, ?)",
            (row["id"], row["content"]))
        self.assertEqual(mem.search("memory worth finding"), [])
        out = mem.repair()
        self.assertTrue(out["ok"], out)
        self.assertTrue(any("search index rebuilt" in f for f in out["fixed"]), out)
        hits = mem.search("memory worth finding")
        self.assertTrue(hits)
        mem.close()

    def test_repair_migrates_a_legacy_chat_table(self):
        base = Path(tempfile.mkdtemp(prefix="epu-legacy-"))
        path = base / "office.sqlite"
        import sqlite3

        db = sqlite3.connect(path)
        db.executescript(
            "CREATE TABLE chat (id INTEGER PRIMARY KEY, chat TEXT NOT NULL, role TEXT NOT NULL,"
            " content TEXT NOT NULL, trust TEXT NOT NULL, created REAL NOT NULL,"
            " expires REAL NOT NULL, chat_id TEXT);"
            "INSERT INTO chat (chat, role, content, trust, created, expires, chat_id)"
            " VALUES ('main','owner','legacy line kept','owner',strftime('%s','now'),0,'');")
        db.commit()
        db.close()
        mem = memory.Memory(path)
        out = mem.repair()
        self.assertTrue(any("conversation table rebuilt" in f for f in out["fixed"]), out)
        cols = {r[1] for r in mem.db.execute("PRAGMA table_info(chat)")}
        self.assertIn("scope", cols)
        self.assertNotIn("expires", cols)
        kept = mem.db.execute("SELECT content FROM chat").fetchall()
        self.assertEqual([r["content"] for r in kept], ["legacy line kept"])
        mem.close()


class T04_Rules(unittest.TestCase):
    def test_identity_question_is_detected(self):
        self.assertTrue(rules.identity_question("who am i"))
        self.assertTrue(rules.identity_question("do you know me"))
        self.assertTrue(rules.identity_question("আমি কে"))
        self.assertFalse(rules.identity_question("make me a coffee"))

    def test_identity_reply_is_answered_from_disk(self):
        mem = fresh()
        rules.seed(mem)
        reply = rules.identity_reply("who am i", "en", mem)
        self.assertIn("Mohammad Morshed", reply)
        self.assertIn("creator and owner", reply)
        self.assertIn(config.assistant_name(), reply)
        mem.close()

    def test_identity_reply_is_fast_and_in_bangla(self):
        mem = fresh()
        rules.seed(mem)
        started = time.time()
        reply = rules.identity_reply("who am i", "bn", mem)
        self.assertLess(time.time() - started, 0.1)
        self.assertIn("স্যার", reply)
        mem.close()

    def test_seed_is_idempotent(self):
        mem = fresh()
        self.assertGreater(rules.seed(mem), 0)
        self.assertEqual(rules.seed(mem), 0)
        mem.close()

    def test_standing_orders_come_from_the_owner(self):
        mem = fresh()
        rules.seed(mem)
        reply = rules.rules_reply(mem)
        self.assertIn("standing orders", reply.lower())
        self.assertIn("never refuse", reply)
        mem.close()

    def test_greeting_never_repeats_itself_inside_one_reply(self):
        line = rules.quick_reply(language="en")
        self.assertEqual(line.count("Yes"), 1)
        self.assertTrue(line.startswith("Yes sir, EPU here"))


class T05_NoDownloadedModels(unittest.TestCase):
    """The chat path must never need a downloaded model, a key or the network."""

    def test_a_record_order_never_loads_the_speech_model(self):
        """The speech model is ~1 GB of RAM: it may not load for a chat order."""
        import sys

        from epu import chat

        mem = fresh()
        before = set(sys.modules)
        answer = chat.answer("record a voice note", mem=mem)
        self.assertIn("turn on the local model", answer["reply"])
        self.assertNotIn("epu.stt", set(sys.modules) - before)
        self.assertNotIn("faster_whisper", set(sys.modules) - before)
        mem.close()

    @unittest.skipUnless(HAVE_NUMPY, "numpy is not installed: the speech path skips")
    def test_speech_stays_off_until_the_owner_turns_it_on(self):
        local = os.environ.get("EPU_LOCAL")
        os.environ.pop("EPU_LOCAL", None)
        config.reset_cache()
        try:
            self.assertFalse(config.local_model_enabled())
            from epu import chat

            mem = fresh()
            self.assertIn("turn on the local model",
                          chat.answer("record a voice note", mem=mem)["reply"])
            os.environ["EPU_LOCAL"] = "1"
            config.reset_cache()
            self.assertTrue(config.local_model_enabled())
            answer = chat.answer("record a voice note", mem=mem)
            self.assertIn("speech is on", answer["reply"])
            self.assertEqual(answer["model"], "base")
            mem.close()
        finally:
            os.environ.pop("EPU_LOCAL", None)
            if local is not None:
                os.environ["EPU_LOCAL"] = local
            config.reset_cache()

    def test_the_chat_path_imports_no_heavy_model(self):
        """numpy, cv2 and the speech model are all absent after a whole conversation."""
        import subprocess
        import sys

        code = ("import sys; from epu import chat; "
                "chat.answer('hi'); chat.answer('who am i'); "
                "chat.answer('record a voice note'); "
                "print([m for m in sys.modules if m.split('.')[0] in "
                "('numpy', 'cv2', 'faster_whisper', 'whisper') or m.startswith('epu.stt')])")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.strip(), "[]", out.stdout)


class T06_CloudRouter(unittest.TestCase):
    """The brain that answers when code has no tool: honest, scoped, never invented."""

    def test_the_local_brain_does_not_crash_the_process(self):
        """EPU_LOCAL=1 used to die on a missing `os` import: the owner must get a reply."""
        local = os.environ.get("EPU_LOCAL")
        os.environ["EPU_LOCAL"] = "1"
        config.reset_cache()
        for name in ("EPU_NVIDIA_KEY_1", "EPU_NVIDIA_KEY_2"):
            os.environ.pop(name, None)
        mem = fresh()
        try:
            brains._fused.clear()
            result = brains.ask("hello", mem=mem)
            self.assertIn(result["provider"], ("office", "openjarvis"))
            self.assertTrue(result["reply"])
            self.assertNotIn("name 'os' is not defined", result["reply"])
        finally:
            os.environ.pop("EPU_LOCAL", None)
            if local is not None:
                os.environ["EPU_LOCAL"] = local
            config.reset_cache()
            mem.close()

    def test_no_key_is_said_plainly_with_one_action_to_take(self):
        for name in ("EPU_NVIDIA_KEY_1", "EPU_NVIDIA_KEY_2"):
            os.environ.pop(name, None)
        result = brains.ask("what is the wifi code", mem=fresh())
        self.assertIn("no cloud key", result["reply"])
        self.assertIn("secure/keys.env", result["reply"])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
