"""The memory's insurance policy: real backups, verified restores, honest numbers.

WHY this file exists: `memory/*.sqlite` is git-ignored on purpose (it is the owner's
private data), so the office's identity facts and every "remember that" he ever said
live on exactly one disk. One dead disk and he is a stranger to his own office again —
the complaint that killed the previous build. So a backup is not called a backup
until it has been opened and counted, and a restore never quietly overwrites a newer
database.

Two rules that never bend:
  * only the database is ever zipped. Never `secure/`, never the projects, never the
    repo — a backup the owner might hand to somebody must not carry his keys;
  * a backup never lands where git would commit it. The default folder sits inside
    the repo but `.gitignore` covers `*.zip`; any other destination inside the
    worktree is refused, and a shareable backup goes on another drive.

stdlib only: sqlite3 + zipfile. No new dependency, ever.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

from . import memory
from .config import ROOT, SECURE_DIR

LIVE_DB = memory.DB_PATH                 # F:\ECHO\memory\office.sqlite
BACKUP_DIR = ROOT / "backups"            # inside the repo, covered by *.zip
DB_NAME = "office.sqlite"                # the one member every backup carries
META_NAME = "backup.json"                # real numbers, so a restore can be honest
KEEP = 20                                # how many backups stay on disk
SIDECARS = ("-wal", "-shm")              # WAL sidecars: newest writes live here


class BackupError(RuntimeError):
    """A backup that cannot be verified. Reported, never raised at the owner."""


# ------------------------------------------------------------------ where a backup may live
def _worktree_root() -> Path | None:
    """The git worktree this office lives in, found by walking up for .git."""
    for folder in (ROOT, *ROOT.parents):
        if (folder / ".git").exists():
            return folder
    return None


def inside_worktree(path: Path | str) -> bool:
    """True when the path is inside the git worktree (so git could commit it)."""
    root = _worktree_root()
    if root is None:
        return False
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _ignored_patterns(root: Path) -> list[str]:
    file = root / ".gitignore"
    if not file.is_file():
        return []
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def _pattern_matches(pattern: str, rel: str) -> bool:
    body = pattern.rstrip("/")
    if "/" in body:                          # anchored to the repo root
        return fnmatch.fnmatch(rel, body) or fnmatch.fnmatch(rel, body + "/*")
    return any(fnmatch.fnmatch(part, body) for part in rel.split("/"))


def git_ignored(path: Path | str) -> bool:
    """True when the repo's own .gitignore already covers this path.

    Only the root .gitignore is read: EPU's rules live there, and a second ignore
    file would be one more thing for the owner to keep in sync.
    """
    root = _worktree_root()
    if root is None:
        return False
    target = Path(path).resolve()
    try:
        rel = target.relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    return any(_pattern_matches(pattern, rel) for pattern in _ignored_patterns(root))


def _folder(dest: Path | str | None) -> Path:
    folder = Path(dest).expanduser() if dest else BACKUP_DIR
    if not folder.is_absolute():
        folder = Path.cwd() / folder
    return folder


def _writable(folder: Path) -> str:
    """Why this folder may not hold a backup, or '' when it may.

    The office keeps its own copy in backups/ because the owner wants a backup in one
    command, and .gitignore covers *.zip there. "Ignored today" is one .gitignore
    edit away from "committed", though — so backups/ is the ONLY folder inside the
    worktree allowed, and even it is refused the day git stops ignoring it. A backup
    the owner might share goes on another drive, outside the repo entirely.
    """
    if not never_inside(folder):
        return (f"refusing to write a backup inside {SECURE_DIR} - that is where the "
                "owner's keys live")
    home = _is_home(folder)
    if not inside_worktree(folder):
        return ""                              # another drive: git can never have it
    if home:
        if git_ignored(folder / f"office-{timestamp()}.zip"):
            return ""
        return (f"{folder} is no longer ignored by .gitignore - fix that first, or the "
                "backup will land in git")
    return (f"{folder} is inside the git repo, and a backup must never be written "
            "where git could commit it - use another drive, or the backups/ folder")


def _is_home(folder: Path) -> bool:
    """Is this the office's own backups/ folder, wherever that lives today?"""
    try:
        return folder.resolve() == BACKUP_DIR.resolve()
    except OSError:
        return False


def timestamp() -> str:
    """A filename-safe stamp: ':' is not legal in a Windows filename."""
    return time.strftime("%Y-%m-%dT%H-%M-%S")


def _target(folder: Path) -> Path:
    """A filename nobody has used yet: two backups in the same second must not
    overwrite each other (the owner clicking backup twice, or a chat that saves)."""
    base = folder / f"office-{timestamp()}"
    path = base.with_name(base.name + ".zip")
    count = 2
    while path.exists():
        path = base.with_name(f"{base.name}-{count}.zip")
        count += 1
    return path


def _when(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


# ------------------------------------------------------------------ the copy itself
def _snapshot(source: Path, folder: Path) -> tuple[int, Path]:
    """A verified copy of the live database, in a temp file inside `folder`.

    WHY sqlite's own backup API and not a file copy: the newest writes sit in the
    -wal/-shm sidecars, so copying office.sqlite alone silently loses the last facts
    the owner spoke. `Connection.backup()` reads through them. The sidecars are then
    folded in and the copy is checked with sqlite's own integrity check before
    anybody is allowed to call it a backup.
    """
    handle, name = tempfile.mkstemp(prefix="snapshot-", suffix=".sqlite", dir=str(folder))
    os.close(handle)
    copy = Path(name)
    copy.unlink()                              # sqlite must create the file itself
    facts, state = 0, "not checked"
    try:
        src = sqlite3.connect(str(source), timeout=5)
        try:
            try:
                src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass                           # a live writer holds it: backup() still sees it
            dst = sqlite3.connect(str(copy), timeout=5)
            try:
                src.backup(dst)
                try:
                    facts = int(dst.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
                except sqlite3.Error as exc:
                    raise BackupError(f"{source.name} is not an EPU memory "
                                      f"database (no facts table): {exc}")
                state = dst.execute("PRAGMA integrity_check").fetchone()[0]
                dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                dst.close()
        finally:
            src.close()
        _drop_sidecars(copy)
    except BaseException:
        _drop_sidecars(copy)
        copy.unlink(missing_ok=True)
        raise
    if state != "ok":
        _drop_sidecars(copy)
        copy.unlink(missing_ok=True)
        raise BackupError(f"the copy failed sqlite's own integrity check: {state}")
    return facts, copy


def _drop_sidecars(db: Path) -> None:
    """Remove a database's WAL sidecars. A stale -wal beside a fresh database is
    exactly how a restore gets corrupted, so they never travel with the file."""
    for suffix in SIDECARS:
        try:
            Path(str(db) + suffix).unlink(missing_ok=True)
        except OSError:
            pass                           # the office is running: sqlite retires it later


def _discard(copy: Path | None) -> None:
    if copy is None:
        return
    _drop_sidecars(copy)
    try:
        copy.unlink(missing_ok=True)
    except OSError:
        pass


def never_inside(path: Path | str) -> bool:
    """Is this file (or this zip member) clear of the owner's private files?

    One question, asked of the source database, of the destination folder and of
    every member of the finished zip, so "secure/ is never inside a backup" is a
    check and not a promise.
    """
    parts = str(path).replace("\\", "/").strip("/").split("/")
    return SECURE_DIR.name not in parts


def _carries_no_secrets(target: Path, source: Path) -> str:
    """The last look before a zip is called a backup: the database and its own
    record, nothing else — and never anything from secure/."""
    if not never_inside(source):
        return f"refusing to back up a database that lives inside {SECURE_DIR}"
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    for name in names:
        if not never_inside(name):
            return f"refusing to keep a backup that carries {name}"
    extra = [name for name in names if name not in (DB_NAME, META_NAME)]
    if extra:
        return f"refusing to keep a backup with unexpected files: {extra[:3]}"
    return ""


# ------------------------------------------------------------------ backup
def backup(path: Path | str | None = None, dest: Path | str | None = None) -> dict:
    """Copy the office's memory into a timestamped, verified zip.

    `path` is the database to copy (default: the live one), `dest` the folder to
    write it in (default: backups/). Returns real numbers — path, bytes, facts — or
    an honest {"ok": False, "error": ...}. Never raises.
    """
    source = Path(path).expanduser() if path else LIVE_DB
    if not source.is_file():
        return {"ok": False, "error": f"no database at {source}"}
    folder = _folder(dest)
    refused = _writable(folder)
    if refused:
        return {"ok": False, "error": refused}
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"cannot create {folder}: {exc}"}

    copy: Path | None = None
    created = time.time()
    try:
        facts, copy = _snapshot(source, folder)
        target = _target(folder)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(copy, DB_NAME)
            zf.writestr(META_NAME, json.dumps({
                "created": created, "source": str(source), "facts": facts,
                "bytes": copy.stat().st_size,
            }, indent=1))
        problem = _carries_no_secrets(target, source)
        if problem:
            target.unlink(missing_ok=True)
            return {"ok": False, "error": problem}
    except (BackupError, sqlite3.Error, OSError, ValueError) as exc:
        _discard(copy)
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
    finally:
        _discard(copy)
    _prune(folder)
    return {"ok": True, "path": str(target), "name": target.name,
            "bytes": target.stat().st_size, "facts": facts, "created": created,
            "when": _when(created)}


def _prune(folder: Path) -> None:
    """Keep the newest KEEP backups; older ones are dead weight, not history."""
    try:
        items = sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in items[KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ restore
def _find(path: Path | str) -> Path | None:
    """The backup as given, or a name from the backups folder (the CLI's habit)."""
    given = Path(path).expanduser()
    if given.is_file():
        return given
    named = BACKUP_DIR / given.name
    return named if named.is_file() else None


def _verify(payload: bytes, folder: Path) -> dict:
    """Open a candidate database on its own and count it before it goes near the
    live memory. A corrupt file is refused here, where it can still do no harm."""
    handle, name = tempfile.mkstemp(prefix="verify-", suffix=".sqlite", dir=str(folder))
    os.close(handle)
    probe = Path(name)
    try:
        probe.write_bytes(payload)
        db = sqlite3.connect(str(probe), timeout=5)
        try:
            state = db.execute("PRAGMA integrity_check").fetchone()[0]
            if state != "ok":
                return {"ok": False, "error": f"the backup is not a healthy database: {state}"}
            facts = int(db.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
        finally:
            db.close()
    except sqlite3.Error as exc:
        # A readable file that is not one of ours: a random database, a half-written
        # copy, an old build's file. It is refused, not guessed at.
        why = ("the backup is not an EPU memory database" if "no such table" in str(exc)
               else "the backup will not open as a database")
        return {"ok": False, "error": f"{why}: {exc}"}
    finally:
        _drop_sidecars(probe)
        probe.unlink(missing_ok=True)
    return {"ok": True, "facts": facts}


def restore(path: Path | str, target: Path | str | None = None,
            force: bool = False) -> dict:
    """Put one backup back as the live database, after checking it opens.

    Refuses to overwrite a database that changed after the backup was taken, unless
    `force` is True — a restore must never quietly undo the owner's last hour.
    """
    live = Path(target).expanduser() if target else LIVE_DB
    zip_path = _find(path)
    if zip_path is None:
        return {"ok": False, "error": f"no backup at {path}"}
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            if DB_NAME not in names:
                return {"ok": False,
                        "error": f"{zip_path.name} carries no {DB_NAME} inside"}
            payload = zf.read(DB_NAME)
            meta = json.loads(zf.read(META_NAME).decode("utf-8")) if META_NAME in names else {}
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"{zip_path.name} is not a readable backup: {exc}"}

    created = float(meta.get("created") or zip_path.stat().st_mtime)
    live.parent.mkdir(parents=True, exist_ok=True)
    # A restore must never quietly undo the owner's last hour: if the live memory
    # changed after this backup was taken, the owner has to say force.
    if live.is_file() and not force and live.stat().st_mtime > created:
        return {"ok": False, "error": (
            f"the live memory changed at {_when(live.stat().st_mtime)}, after this "
            f"backup was taken ({_when(created)}) - restore with force to overwrite it")}
    checked = _verify(payload, live.parent)
    if not checked["ok"]:
        return checked

    # A write-ahead log beside the live memory means a program still has that
    # database open (usually the office). Windows cannot delete the log then, and a
    # fresh connection would replay the old facts back on top of the restored file —
    # so the honest answer is "close it first", unless the owner forces it.
    held = [str(live) + suffix for suffix in SIDECARS
            if Path(str(live) + suffix).exists()]
    if held and not force:
        return {"ok": False, "error": (
            f"{live.name} still has its write-ahead log beside it, so something (the "
            "office?) still has that database open - close it first, or restore with "
            "force")}

    staging = live.with_name(live.name + ".restore-tmp")
    try:
        staging.write_bytes(payload)
        _put_back(staging, live)
        try:
            # The file remembers when its data was taken, so restoring the same
            # backup twice is not mistaken for overwriting something newer.
            os.utime(live, (int(created), int(created)))
        except OSError:
            pass
    except OSError as exc:
        _discard(staging)
        return {"ok": False, "error": f"could not write {live}: {exc}"}
    return {"ok": True, "path": str(live), "facts": checked["facts"],
            "bytes": len(payload), "created": created, "when": _when(created)}


def _put_back(staging: Path, live: Path) -> None:
    """Move the verified copy into place, however this machine allows it.

    WHY two ways: `os.replace` is the honest atomic swap, but Windows refuses to
    rename over a database sqlite still holds open (the running office). Writing the
    bytes in place still works then. Either way the sidecars go first, so a stale WAL
    is never replayed onto the new file.
    """
    _drop_sidecars(live)
    try:
        os.replace(staging, live)
        return
    except OSError:
        pass
    shutil.copyfile(staging, live)
    _drop_sidecars(live)
    staging.unlink(missing_ok=True)


# ------------------------------------------------------------------ what is on disk
def _meta(path: Path) -> dict:
    """The backup's own record of itself, or {} when it cannot be read."""
    try:
        with zipfile.ZipFile(path) as zf:
            if META_NAME not in zf.namelist():
                return {}
            return json.loads(zf.read(META_NAME).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def list_backups() -> list[dict]:
    """Every backup on disk, newest first, with its real created time."""
    if not BACKUP_DIR.is_dir():
        return []
    items = []
    for item in BACKUP_DIR.glob("*.zip"):
        try:
            size = item.stat().st_size
        except OSError:
            continue
        meta = _meta(item)
        created = float(meta.get("created") or item.stat().st_mtime)
        items.append({"name": item.name, "path": str(item), "bytes": size,
                      "created": created, "when": _when(created),
                      "facts": meta.get("facts")})
    items.sort(key=lambda row: row["created"], reverse=True)
    return items
