"""Memory for EPU: scoped, durable, and never allowed to block an answer.

Lessons from the previous build (the one the owner deleted):
  * a legacy `chat`/`expires` column shape in the short-term table silently rejected
    every insert, so the assistant never remembered the conversation — the schema is
    created in ONE place here and `repair()` migrates anything older;
  * a 30 s SQLite lock in the chat path froze the assistant for half a minute — every
    write here uses timeout=2 and degrades to "not saved" instead of stalling;
  * owner facts must outrank a thousand knowledge chunks, so search ranks by trust
    tier first and only then by text match.

Schema (one place, one version):
    facts    durable knowledge: kind (fact|note|task|doc), scope ('owner'|'shared'|'client:<slug>'),
             trust ('owner'|'assistant'|'external'), content, created
    chat     short conversation: chat_id, scope, role, content, created
    tasks    the worker queue the owner and the assistant open work in
    jobs     finished job records (what was done, what the result was)
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

from .config import MEMORY_DIR, ROOT, policy

DB_PATH = MEMORY_DIR / "office.sqlite"
MAX_CONTENT = 1200

# Trust tiers, best first. A search hit in a higher tier beats any number of hits below.
TIERS = {"owner": 3, "assistant": 2, "external": 1}

# Words that carry no meaning in a search; identity questions are made of them.
STOPWORDS = {
    "the", "and", "for", "you", "your", "are", "was", "were", "what", "when", "where",
    "which", "who", "whom", "whose", "why", "how", "this", "that", "these", "those",
    "with", "from", "have", "has", "had", "his", "her", "its", "our", "their", "them",
    "can", "could", "shall", "should", "will", "would", "may", "might", "must", "does",
    "did", "done", "not", "all", "any", "some", "one", "out", "into", "about", "over",
    "am", "is", "be", "been", "being", "i", "me", "my", "mine", "of", "to", "in", "on",
    "at", "by", "up", "down", "it", "as", "no", "so", "if", "then", "than", "too",
    "very", "just", "also", "how", "sir", "boss", "please", "hey", "ok", "okay",
    "do", "know", "tell", "give", "need", "want", "say", "said",
}

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, scope TEXT NOT NULL, trust TEXT NOT NULL,
    content TEXT NOT NULL, created REAL NOT NULL, uses INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts(scope, kind, id);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    content, content='facts', content_rowid='id', tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL, scope TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat ON chat(chat_id, id);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open',
    text TEXT NOT NULL, result TEXT, created REAL, closed REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL, kind TEXT NOT NULL, ok INTEGER NOT NULL,
    detail TEXT, created REAL
);
"""

_write_lock = threading.Lock()


def terms(query: str) -> list[str]:
    """Search words: lowercase, punctuation dropped, stopwords removed."""
    words = re.findall(r"[a-z0-9ঀ-৿ऀ-ॿ]{2,}", (query or "").lower())
    out, seen = [], set()
    for w in words:
        if w not in STOPWORDS and w not in seen:
            seen.add(w)
            out.append(w)
    return out


class Memory:
    """One connection, one place where every rule about memory lives."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # timeout=2: a busy database must cost milliseconds, never half a minute.
        self.db = sqlite3.connect(str(self.path), timeout=2, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.pol = policy()
        self._migrate()

    # ---------------------------------------------------------------- schema
    def _migrate(self) -> None:
        try:
            self.db.executescript(_SCHEMA)
            self._fix_fts_drift()
        except sqlite3.OperationalError:
            # Another connection is migrating or writing. The schema is already there;
            # failing the chat here is what the old build did wrong.
            try:
                self.db.rollback()
            except sqlite3.Error:
                pass

    def _fix_fts_drift(self) -> None:
        stored = {r["id"] for r in self.db.execute("SELECT id FROM facts")}
        indexed = {r[0] for r in self.db.execute("SELECT rowid FROM facts_fts")}
        if indexed == stored:
            return
        try:
            self.db.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
            self.db.execute(
                "DELETE FROM facts_fts WHERE rowid NOT IN (SELECT id FROM facts)")
        except sqlite3.Error:
            pass

    def close(self) -> None:
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------------------------------------------------------- durable facts
    def remember(self, content: str, *, kind: str = "fact", scope: str = "owner",
                 trust: str = "owner", source: str = "owner") -> int | None:
        """Store one durable fact. Returns its id, or None when the database was busy —
        the caller keeps talking either way."""
        content = (content or "").strip()[:MAX_CONTENT]
        if not content:
            return None
        with _write_lock:
            try:
                cur = self.db.execute(
                    "INSERT INTO facts (kind, scope, trust, content, created) VALUES (?,?,?,?,?)",
                    (kind, scope, trust, content, time.time()))
                self.db.execute(
                    "INSERT INTO facts_fts(rowid, content) VALUES (?,?)", (cur.lastrowid, content))
                return int(cur.lastrowid)
            except sqlite3.Error:
                return None

    def remember_many(self, chunks: list[tuple[str, dict]], scope: str = "owner") -> int:
        saved = 0
        for content, meta in chunks:
            if self.remember(content, kind=meta.get("kind", "note"), scope=scope,
                             trust=meta.get("trust", "owner"),
                             source=meta.get("source", "owner")) is not None:
                saved += 1
        return saved

    def forget(self, fact_id: int) -> bool:
        with _write_lock:
            try:
                row = self.db.execute("SELECT content FROM facts WHERE id=?", (fact_id,)).fetchone()
                if row is None:
                    return False
                self.db.execute("INSERT INTO facts_fts(facts_fts, rowid, content) "
                                "VALUES ('delete', ?, ?)", (fact_id, row["content"]))
                self.db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
                return True
            except sqlite3.Error:
                return False

    # ---------------------------------------------------------------- conversation
    def add_chat(self, chat_id: str, role: str, content: str, scope: str = "owner") -> bool:
        """Best-effort conversation memory. Never raises, never blocks."""
        content = (content or "").strip()[:MAX_CONTENT]
        if not content:
            return False
        with _write_lock:
            try:
                self.db.execute(
                    "INSERT INTO chat (chat_id, scope, role, content, created) VALUES (?,?,?,?,?)",
                    (chat_id or "main", scope, role, content, time.time()))
                return True
            except sqlite3.Error:
                return False

    def recent(self, chat_id: str, n: int = 6, scope: str | None = None) -> list[sqlite3.Row]:
        """Recent conversation for one chat. `scope` keeps client A out of client B:
        a scope is a wall, not a filter."""
        try:
            if scope:
                return self.db.execute(
                    "SELECT role, content, created FROM chat "
                    "WHERE chat_id=? AND scope=? ORDER BY id DESC LIMIT ?",
                    (chat_id or "main", scope, max(1, n))).fetchall()[::-1]
            return self.db.execute(
                "SELECT role, content, created FROM chat WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                (chat_id or "main", max(1, n))).fetchall()[::-1]
        except sqlite3.Error:
            return []

    # ---------------------------------------------------------------- search
    def search(self, query: str, scope: str | None = None, k: int | None = None) -> list[dict]:
        """Ranked recall: trust tier first, then text match. Owner facts always win."""
        k = k or int(self.pol["chat"]["recall_k"])
        words = terms(query)
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        sql = """
            SELECT f.id, f.kind, f.scope, f.trust, f.content, f.created,
                   bm25(facts_fts) AS rank
            FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid
            WHERE facts_fts MATCH ?
        """
        params: list = [match]
        if scope:
            sql += " AND (f.scope = ? OR f.scope = 'shared' OR f.trust = 'owner')"
            params.append(scope)
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(k * 6, 30))
        try:
            rows = self.db.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
        scored = []
        for r in rows:
            hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", (r["content"] or "").lower()))
            scored.append({
                "id": r["id"], "kind": r["kind"], "scope": r["scope"], "trust": r["trust"],
                "content": r["content"], "hits": hits,
                "score": TIERS.get(r["trust"], 1) * 100 + hits * 10 - abs(r["rank"] or 0),
            })
        scored.sort(key=lambda d: d["score"], reverse=True)
        return scored[:k]

    def context(self, query: str, scope: str | None = None, max_chars: int = 900) -> str:
        """A short block of recalled memory, for a system prompt."""
        hits = self.search(query, scope=scope)
        out, used = [], 0
        for h in hits:
            line = h["content"].strip().replace("\n", " ")
            if len(line) > 220:
                line = line[:220] + "..."
            if used + len(line) > max_chars:
                break
            out.append(line)
            used += len(line)
        return "\n".join(out)

    # ---------------------------------------------------------------- tasks ("what is next")
    def add_task(self, text: str, scope: str = "owner") -> int | None:
        with _write_lock:
            try:
                cur = self.db.execute(
                    "INSERT INTO tasks (scope, text, created) VALUES (?,?,?)",
                    (scope, text, time.time()))
                return int(cur.lastrowid)
            except sqlite3.Error:
                return None

    def tasks(self, scope: str | None = None, state: str = "open") -> list[sqlite3.Row]:
        try:
            if scope:
                return self.db.execute(
                    "SELECT * FROM tasks WHERE scope=? AND state=? ORDER BY id",
                    (scope, state)).fetchall()
            return self.db.execute("SELECT * FROM tasks WHERE state=? ORDER BY id",
                                   (state,)).fetchall()
        except sqlite3.Error:
            return []

    def close_task(self, task_id: int, result: str = "", ok: bool = True) -> bool:
        with _write_lock:
            try:
                self.db.execute(
                    "UPDATE tasks SET state=?, result=?, closed=? WHERE id=?",
                    ("done" if ok else "failed", result[:MAX_CONTENT], time.time(), task_id))
                return True
            except sqlite3.Error:
                return False

    def log_job(self, kind: str, ok: bool, detail: str = "", scope: str = "owner") -> None:
        with _write_lock:
            try:
                self.db.execute(
                    "INSERT INTO jobs (scope, kind, ok, detail, created) VALUES (?,?,?,?,?)",
                    (scope, kind, 1 if ok else 0, detail[:MAX_CONTENT], time.time()))
            except sqlite3.Error:
                pass

    # ---------------------------------------------------------------- numbers
    def stats(self) -> dict:
        try:
            return {
                "facts": self.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
                "indexed": self.db.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0],
                "chat": self.db.execute("SELECT COUNT(*) FROM chat").fetchone()[0],
                "open_tasks": self.db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE state='open'").fetchone()[0],
                "done_tasks": self.db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE state='done'").fetchone()[0],
                "jobs": self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            }
        except sqlite3.Error:
            return {"facts": 0, "indexed": 0, "chat": 0, "open_tasks": 0,
                    "done_tasks": 0, "jobs": 0, "error": True}

    # ---------------------------------------------------------------- repair ("fix your memory")
    def repair(self) -> dict:
        """Check and fix the office's own memory, reporting real numbers only."""
        report: dict = {"ok": True, "checked": [], "fixed": [], "problems": []}

        def check(name: str) -> None:
            report["checked"].append(name)

        check("sqlite integrity")
        try:
            state = self.db.execute("PRAGMA integrity_check").fetchone()[0]
            if state != "ok":
                report["problems"].append(f"integrity_check: {state}")
                report["ok"] = False
        except sqlite3.Error as exc:
            report["problems"].append(f"integrity_check failed: {exc}")
            report["ok"] = False

        check("search index")
        try:
            # The property that matters: every memory can be found again. Row counts on
            # an external-content FTS5 table lie (they report the content table), so the
            # check is a real search: the longest word of each recent memory, one query.
            recent = self.db.execute(
                "SELECT id, content FROM facts ORDER BY id DESC LIMIT 25").fetchall()
            lost = []
            for row in recent:
                words = sorted(terms(row["content"] or ""), key=len, reverse=True)
                if not words:
                    continue
                hits = self.db.execute(
                    "SELECT COUNT(*) FROM facts_fts WHERE facts_fts MATCH ?",
                    (f'"{words[0]}"',)).fetchone()[0]
                if not hits:
                    lost.append(row["id"])
            if lost:
                self.db.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
                self.db.execute(
                    "DELETE FROM facts_fts WHERE rowid NOT IN (SELECT id FROM facts)")
                still_lost = []
                for row in recent:
                    words = sorted(terms(row["content"] or ""), key=len, reverse=True)
                    if words and not self.db.execute(
                            "SELECT COUNT(*) FROM facts_fts WHERE facts_fts MATCH ?",
                            (f'"{words[0]}"',)).fetchone()[0]:
                        still_lost.append(row["id"])
                report["fixed"].append(
                    f"search index rebuilt: {len(lost)} memories were unfindable, "
                    f"{len(still_lost)} still are")
                if still_lost:
                    report["problems"].append(
                        f"memories still unfindable after rebuild: ids {still_lost[:10]}")
                    report["ok"] = False
            else:
                self.db.execute("INSERT INTO facts_fts(facts_fts) VALUES('integrity-check')")
        except sqlite3.Error as exc:
            report["problems"].append(f"index check failed: {exc}")
            report["ok"] = False

        check("conversation shape")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(chat)")}
        bad = {"chat", "expires"} & cols
        if bad:
            self._rebuild_chat(cols)
            report["fixed"].append("conversation table rebuilt to the current shape")

        check("expired conversation rows")
        ttl_hours = float(self.pol["chat"]["short_term_ttl_hours"])
        cap = int(self.pol["chat"]["short_term_items"])
        try:
            with _write_lock:
                expired = self.db.execute(
                    "DELETE FROM chat WHERE created < ?", (time.time() - ttl_hours * 3600,
                                                           )).rowcount or 0
                rows = self.db.execute(
                    "SELECT chat_id, COUNT(*) c FROM chat GROUP BY chat_id").fetchall()
                over = 0
                for row in rows:
                    keep = self.db.execute(
                        "SELECT id FROM chat WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                        (row["chat_id"], cap)).fetchall()
                    ids = [r["id"] for r in keep]
                    self.db.execute(
                        "DELETE FROM chat WHERE chat_id=? AND id NOT IN (%s)"
                        % ",".join("?" * len(ids)), [row["chat_id"], *ids]) if ids else None
                    over += max(0, (row["c"] or 0) - len(ids))
                self.db.commit()
            if expired:
                report["fixed"].append(f"purged {expired} expired conversation rows")
            if over:
                report["fixed"].append(f"trimmed {over} conversation rows over the "
                                       f"{cap}-row cap")
        except sqlite3.Error as exc:
            report["problems"].append(f"conversation cleanup failed: {exc}")
            report["ok"] = False

        check("write-ahead log")
        try:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            report["fixed"].append("write-ahead log checkpointed")
        except sqlite3.Error:
            pass  # a live writer holds it; the checkpoint happens on its own later

        report["stats"] = self.stats()
        return report

    def _rebuild_chat(self, cols: set) -> None:
        """Old short-term shape -> current shape, keeping whatever rows exist."""
        with _write_lock:
            try:
                chat_id_expr = ("COALESCE(NULLIF(chat_id, ''), chat)" if "chat_id" in cols
                                and "chat" in cols else
                                ("COALESCE(chat_id, '')" if "chat_id" in cols else "'main'"))
                scope_expr = "COALESCE(scope, 'owner')" if "scope" in cols else "'owner'"
                self.db.executescript(
                    f"""
                    BEGIN;
                    CREATE TABLE IF NOT EXISTS chat_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id TEXT NOT NULL, scope TEXT NOT NULL, role TEXT NOT NULL,
                        content TEXT NOT NULL, created REAL NOT NULL);
                    INSERT OR IGNORE INTO chat_new (id, chat_id, scope, role, content, created)
                        SELECT id, {chat_id_expr}, {scope_expr}, role, content, created FROM chat;
                    DROP TABLE chat;
                    ALTER TABLE chat_new RENAME TO chat;
                    CREATE INDEX IF NOT EXISTS idx_chat ON chat(chat_id, id);
                    COMMIT;
                    """
                )
            except sqlite3.Error:
                try:
                    self.db.rollback()
                except sqlite3.Error:
                    pass
