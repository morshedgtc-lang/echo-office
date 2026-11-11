"""The deterministic part of the brain: everyday orders, planned and verified in code.

A model is a fallback, never a gatekeeper. "make a folder called reports in
projects/easyphoneunlocker" must really create that folder even when a model would
rather write a plan or say it cannot. Everything here is checked on disk afterwards —
the office never claims work it did not do.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import config, memory
from .config import ROOT, policy

# ------------------------------------------------------------------ what the owner said
_FOLDER = re.compile(
    r"\b(?:make|create|add|new)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\s+"
    r"(?:called\s+|named\s+)?[\"']?(?P<name>[^\s\"']+)[\"']?"
    r"(?:\s+(?:in|inside|under|at)\s+[\"']?(?P<parent>[^\s\"']+)[\"']?)?", re.I)
_FILE = re.compile(
    r"\b(?:make|create|write|save|add)\s+(?:a\s+)?(?:new\s+)?(?:file|document)\s+"
    r"(?:called\s+|named\s+)?[\"']?(?P<path>[^\s\"']+)[\"']?"
    r"(?:\s+(?:with|containing|that\s+says)\s+(?P<text>.+))?", re.I)
_LIST = re.compile(
    r"^(?:please\s+)?(?:list|show|what\s+is\s+in|whats\s+in)\s+(?:me\s+)?"
    r"(?:the\s+)?(?:folders?|files?|contents?|directory)?\s*(?:in|of|inside|at)?\s*"
    r"(?P<path>[^\s?]*)\s*\??$", re.I)
_READ = re.compile(
    r"^(?:please\s+)?(?:read|open|show)\s+(?:me\s+)?(?:the\s+)?(?:file\s+)?"
    r"(?P<path>[\w./\\-]+\.[a-z0-9]{1,6})\s*\??$", re.I)
_SYSTEM = re.compile(
    r"\b(?:system\s*(?:info|status|health|report)|how\s+is\s+(?:the\s+)?(?:pc|computer|system)|"
    r"check\s+(?:the\s+)?(?:pc|computer|system|health))", re.I)
_FIND = re.compile(
    r"^(?:please\s+)?(?:find|search|look\s+for)\s+(?:the\s+)?(?:files?\s+)?"
    r"(?:called\s+|named\s+)?[\"']?(?P<pattern>[\w.*?\[\]-]+)[\"']?"
    r"(?:\s+(?:in|inside|under)\s+(?P<root>\S+))?", re.I)
_RENAME = re.compile(
    r"^(?:please\s+)?rename\s+[\"']?(?P<src>[\w./\\-]+)[\"']?\s+to\s+[\"']?(?P<dst>[\w./\\-]+)[\"']?"
    r"(?:\s+(?:in|inside)\s+(?P<parent>\S+))?", re.I)
_REMEMBER = re.compile(
    r"^(?:please\s+)?(?:remember(?:\s+this|\s+that)?|always\s+remember)\s*[:,-]?\s*(?P<text>.+)$",
    re.I | re.S)
_FIX = re.compile(
    r"^(?:please\s+)?(?:fix|repair|rebuild|heal|check\s+and\s+fix)\s+(?:your\s+|the\s+|my\s+)?"
    r"(?P<target>[a-z][\w\s./-]{0,40}?)\s*[.!]?$"
    r"|^(?:please\s+)?(?:your\s+|the\s+)?(?P<bare>memory|brain)\s+(?:is\s+)?"
    r"(?:broken|not\s+working)\s*[.!]?$", re.I)
_SITE = re.compile(
    r"^(?:please\s+)?(?:check|test|scan|is|how\s+is)\s+(?:my\s+)?"
    r"(?:site|website|domain)\s+(?P<host>[a-z0-9.-]+\.[a-z]{2,})", re.I)
_SITES = re.compile(r"^(?:please\s+)?(?:list|show)\s+(?:my\s+)?(?:sites?|websites?|clients?)\s*$",
                    re.I)
_MEMORY_TARGETS = {"memory", "your memory", "my memory", "the memory", "brain", "your brain",
                   "memory index", "the memory index", "the search index", "search index",
                   "the database", "database", "short term memory", "chat memory"}

KNOWN_ROOTS = ("projects", "docs", "memory", "config", "hud", "scripts")

# Roots the assistant may read but must never write into, whatever the owner's wording.
PROTECTED_ROOTS = ("secure", "logs", "run", "epu", "tests", ".git", "node_modules")


# ------------------------------------------------------------------ paths
def clean_path(raw: str, default: str = "projects") -> str:
    """A path the owner typed, made safe for the office root."""
    p = (raw or "").strip().strip("\"'").rstrip(".")
    p = p.replace("\\", "/").strip("/")
    low = p.lower()
    if not p or low in (".", "here", "current", "the", "my", "root", "home"):
        return default
    if re.match(r"^[a-z]:/", low):
        p = p[3:] if p[2:3] == "/" else p
        for prefix in ("ai-office/", "echo/"):
            if p.lower().startswith(prefix):
                p = p[len(prefix):]
                break
        return p or default
    if "/" not in p and low not in KNOWN_ROOTS:
        return f"{default}/{p}"
    return p


def _inside_office(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def _location_free(text: str) -> str:
    """"write a file with hello in projects" — "in projects" is where, not what."""
    cleaned = (text or "").strip()
    match = re.search(r"\s+in\s+[\"'`]?([a-z0-9._/-]+)[\"'`]?\s*$", cleaned, flags=re.I)
    if match:
        tail = match.group(1).replace("\\", "/").strip("/").split("/")[0].lower()
        if tail in KNOWN_ROOTS or tail in PROTECTED_ROOTS:
            cleaned = cleaned[: match.start()].rstrip(" .,-")
    return cleaned


# ------------------------------------------------------------------ the plan
def plan(text: str, scope: str = "owner") -> list[dict]:
    """A tool plan for a plain order, or [] when the crew model should plan it."""
    t = re.sub(r"^\[[a-z0-9.\s]{1,20}\]\s*", "", (text or "").strip(), flags=re.I)
    if not t:
        return []
    m = _FOLDER.search(t)
    if m:
        parent_raw = (m.group("parent") or "").strip().strip("\"'")
        if parent_raw:
            root = parent_raw.replace("\\", "/").lstrip("/").split("/")[0].lower()
            if root in PROTECTED_ROOTS:
                return [{"tool": "_refuse", "args": {
                    "why": f"{root}/ is a protected folder - only you may change what is "
                           f"inside it. Tell me the job and I will prepare it in projects/ "
                           f"instead."}}]
        parent = clean_path(parent_raw)
        name = m.group("name").strip().strip("\"'")
        return [{"tool": "make_folder", "args": {"path": f"{parent}/{name}", "scope": scope}}]
    m = _FILE.search(t)
    if m:
        return [{"tool": "write_file",
                 "args": {"path": clean_path(m.group("path")),
                          "text": _location_free(m.group("text") or ""), "scope": scope}}]
    m = _REMEMBER.match(t)
    if m:
        return [{"tool": "remember", "args": {"text": m.group("text").strip(), "scope": scope}}]
    m = _RENAME.match(t)
    if m:
        parent = clean_path(m.group("parent") or "")
        src, dst = m.group("src"), m.group("dst")
        if "/" not in src:
            src = f"{parent}/{src}"
        if "/" not in dst:
            dst = f"{parent}/{dst}"
        return [{"tool": "rename_file", "args": {"src": src, "dst": dst, "scope": scope}}]
    m = _READ.match(t)
    if m:
        return [{"tool": "read_file", "args": {"path": m.group("path"), "scope": scope}}]
    m = _LIST.match(t)
    if m:
        return [{"tool": "list_dir", "args": {"path": clean_path(m.group("path"), "projects"),
                                             "scope": scope}}]
    if _SYSTEM.search(t):
        return [{"tool": "system_info", "args": {}}]
    m = _FIND.match(t)
    if m:
        return [{"tool": "search_files", "args": {"pattern": m.group("pattern"),
                                                 "root": clean_path(m.group("root") or ""),
                                                 "scope": scope}}]
    m = _FIX.match(t)
    if m:
        target = (m.group("target") or m.group("bare") or "").strip().lower()
        if target in _MEMORY_TARGETS or "memory" in target or "brain" in target:
            return [{"tool": "memory_repair", "args": {}}]
        return []
    m = _SITE.match(t)
    if m:
        return [{"tool": "check_site", "args": {"host": m.group("host")}}]
    if _SITES.match(t):
        return [{"tool": "list_sites", "args": {}}]
    return []


# ------------------------------------------------------------------ the tools
def _tool_make_folder(mem: memory.Memory, args: dict) -> dict:
    rel = clean_path(args.get("path", ""))
    target = ROOT / rel
    if not _inside_office(target) or rel.split("/")[0] not in policy()["write_roots"]:
        return {"ok": False, "error": f"{rel} is outside the folders I may write"}
    target.mkdir(parents=True, exist_ok=True)
    mem.log_job("make_folder", True, rel)
    return {"ok": True, "path": rel, "abs": str(target)}


def _tool_write_file(mem: memory.Memory, args: dict) -> dict:
    rel = clean_path(args.get("path", ""))
    target = ROOT / rel
    if not _inside_office(target) or rel.split("/")[0] not in policy()["write_roots"]:
        return {"ok": False, "error": f"{rel} is outside the folders I may write"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(args.get("text") or ""), encoding="utf-8")
    mem.log_job("write_file", True, rel)
    return {"ok": True, "path": rel, "bytes": target.stat().st_size}


def _tool_list_dir(mem: memory.Memory, args: dict) -> dict:
    rel = clean_path(args.get("path", "projects"))
    target = ROOT / rel
    if not target.is_dir():
        return {"ok": False, "error": f"no such folder: {rel}"}
    items = sorted(p.name for p in target.iterdir())[:200]
    mem.log_job("list_dir", True, rel)
    return {"ok": True, "path": rel, "items": items}


def _tool_read_file(mem: memory.Memory, args: dict) -> dict:
    rel = clean_path(args.get("path", ""))
    target = ROOT / rel
    if not target.is_file() or not _inside_office(target):
        return {"ok": False, "error": f"no such file: {rel}"}
    if target.stat().st_size > 2_000_000:
        return {"ok": False, "error": f"{rel} is too large (limit 2 MB)"}
    mem.log_job("read_file", True, rel)
    return {"ok": True, "path": rel, "text": target.read_text(encoding="utf-8", errors="replace")}


def _tool_rename(mem: memory.Memory, args: dict) -> dict:
    src = ROOT / clean_path(args.get("src", ""))
    dst = ROOT / clean_path(args.get("dst", ""))
    roots = policy()["write_roots"]
    inside = lambda p: _inside_office(p) and p.relative_to(ROOT).as_posix().split("/")[0] in roots
    if not (inside(src) and inside(dst)):
        return {"ok": False, "error": "rename only works inside the project folders"}
    if not src.exists():
        return {"ok": False, "error": f"no such file: {src.name}"}
    if dst.exists():
        return {"ok": False, "error": f"{dst.name} already exists"}
    src.rename(dst)
    mem.log_job("rename_file", True, f"{src.name} -> {dst.name}")
    return {"ok": True, "path": str(dst.relative_to(ROOT)).replace("\\", "/")}


def _tool_search_files(mem: memory.Memory, args: dict) -> dict:
    import fnmatch

    base = ROOT / clean_path(args.get("root", "projects"))
    if not base.is_dir() or not _inside_office(base):
        return {"ok": False, "error": f"no such folder: {args.get('root')}"}
    out = [str(p.relative_to(ROOT)).replace("\\", "/")
           for p in sorted(base.rglob("*"))
           if fnmatch.fnmatch(p.name, str(args.get("pattern") or "*"))][:100]
    mem.log_job("search_files", True, str(args.get("pattern")))
    return {"ok": True, "files": out}


def _tool_remember(mem: memory.Memory, args: dict) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "nothing to remember"}
    fact_id = mem.remember(text, kind="fact", scope=args.get("scope") or "owner",
                           trust="owner")
    if fact_id is None:
        return {"ok": False, "error": "memory was busy, try again"}
    return {"ok": True, "id": fact_id}


def _tool_system_info(mem: memory.Memory, _args: dict) -> dict:
    mem.log_job("system_info", True, "")
    stats = mem.stats()
    return {"ok": True, "memory": stats}


def _tool_check_site(mem: memory.Memory, args: dict) -> dict:
    """Real site check over the network: DNS, HTTP status, response time, expiry."""
    import ssl
    import socket
    import urllib.request

    host = str(args.get("host") or "").strip().lower()
    if not host or not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host):
        return {"ok": False, "error": f"not a domain I can check: {host}"}
    report: dict = {"ok": True, "host": host, "dns": [], "ip": [], "status": None,
                    "seconds": None, "tls_expires": None, "problems": []}
    try:
        report["ip"] = sorted({info[4][0] for info in socket.getaddrinfo(
            host, 443, proto=socket.IPPROTO_TCP) if info[4]})
    except socket.gaierror as exc:
        report["problems"].append(f"DNS: {exc}")
        report["ok"] = False
    started = time.time()
    try:
        request = urllib.request.Request(f"https://{host}",
                                         headers={"User-Agent": "EPU office check"})
        with urllib.request.urlopen(request, timeout=15) as response:
            report["status"] = response.status
            report["seconds"] = round(time.time() - started, 2)
    except Exception as exc:
        report["seconds"] = round(time.time() - started, 2)
        report["problems"].append(f"https: {exc}")
        report["ok"] = False
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                report["tls_expires"] = tls.getpeercert().get("notAfter")
    except Exception:
        pass
    mem.log_job("check_site", report["ok"], host)
    return report


def _tool_list_sites(mem: memory.Memory, _args: dict) -> dict:
    rows = []
    for slug, project in config.projects().items():
        rows.append({"slug": slug, "title": project.get("title") or slug,
                     "site": project.get("site", "-"),
                     "folder": project.get("path", "-")})
    return {"ok": True, "sites": rows}


def _tool_memory_repair(mem: memory.Memory, _args: dict) -> dict:
    return {"ok": True, **mem.repair()}


def _tool_pc_health(mem: memory.Memory, _args: dict) -> dict:
    """Real machine numbers from Windows itself."""
    from . import sysmon

    info = sysmon.report()
    mem.log_job("pc_health", True, "")
    return {"ok": True, **info}


def _describe_pc(result: dict) -> str:
    from . import sysmon

    return sysmon.describe(result)


TOOLS = {
    "make_folder": _tool_make_folder,
    "write_file": _tool_write_file,
    "list_dir": _tool_list_dir,
    "read_file": _tool_read_file,
    "rename_file": _tool_rename,
    "search_files": _tool_search_files,
    "remember": _tool_remember,
    "system_info": _tool_system_info,
    "memory_repair": _tool_memory_repair,
    "check_site": _tool_check_site,
    "list_sites": _tool_list_sites,
    "pc_health": _tool_pc_health,
    # A protected-folder order is refused in code, before any tool runs: the owner
    # gets the reason, and nothing is claimed or touched.
    "_refuse": lambda mem, args: {"ok": False, "error": args.get("why", "not allowed")},
}


# ------------------------------------------------------------------ run + verify
def _verify(tool: str, result: dict) -> list[str]:
    """Read the end state on disk; never trust the tool's own word."""
    problems = []
    if tool in ("make_folder", "write_file"):
        target = ROOT / str(result.get("path", ""))
        if not target.exists():
            problems.append(f"{tool} said ok but {result.get('path')} does not exist")
        elif tool == "write_file" and target.is_file() and target.stat().st_size == 0:
            problems.append(f"{tool} wrote an empty file at {result.get('path')}")
    elif tool == "rename_file" and not (ROOT / str(result.get("path", ""))).exists():
        problems.append(f"rename said ok but {result.get('path')} does not exist")
    return problems


def describe(tool: str, result: dict, args: dict) -> str:
    """One honest line about what really happened."""
    if tool == "make_folder":
        return f"folder ready at {result.get('path')}"
    if tool == "write_file":
        return f"file written: {result.get('path')} ({result.get('bytes', 0)} bytes)"
    if tool == "list_dir":
        items = result.get("items") or []
        where = result.get("path") or "the folder"
        return f"{where} has {len(items)} item(s): " + ", ".join(items[:40]) if items \
            else f"{where} is empty"
    if tool == "read_file":
        body = (result.get("text") or "").strip()
        if len(body) > 1000:
            body = body[:1000].rstrip() + " ...(the rest is on screen)"
        return f"here is {result.get('path')}:\n{body or '(empty file)'}"
    if tool == "rename_file":
        return f"renamed to {result.get('path')}"
    if tool == "search_files":
        files = result.get("files") or []
        return f"found {len(files)} file(s): " + ", ".join(files[:25]) if files \
            else "no matching file"
    if tool == "remember":
        return f"remembered (memory #{result.get('id')})"
    if tool == "system_info":
        return f"memory holds {result['memory']['facts']} facts"
    if tool == "pc_health":
        return _describe_pc(result)
    if tool == "memory_repair":
        fixed, problems = result.get("fixed") or [], result.get("problems") or []
        return ("memory checked, sir: " + (f"{result.get('stats', {}).get('facts')} facts. "
                + ("I fixed: " + "; ".join(fixed) + "."
                   if fixed else "Nothing needed fixing.")
                + ((" Left open: " + "; ".join(problems) + ".") if problems else "")))
    if tool == "check_site":
        host = result.get("host")
        if result.get("ok"):
            return (f"{host} is up: HTTP {result['status']} in {result['seconds']}s, "
                    f"IPs {', '.join(result['ip'] or []) or 'n/a'}, certificate valid to "
                    f"{result.get('tls_expires') or 'unknown'}")
        return (f"{host} is DOWN: " + ("; ".join(result.get("problems") or ["unknown"]))
                + ". I am not guessing - want me to check DNS or the hosting?")
    if tool == "list_sites":
        sites = result.get("sites") or []
        return ", ".join(f"{s['title']} ({s['site']}) in {s['folder']}" for s in sites) \
            if sites else "no sites recorded yet, sir"
    return f"{tool} done"


def run(text: str, mem: memory.Memory, scope: str = "owner") -> dict:
    """Run a planned order end to end, verify it, and report the truth."""
    started = time.time()
    actions = plan(text, scope=scope)
    if not actions:
        return {"ok": False, "error": "no plan", "reply": ""}
    done = []
    for action in actions:
        tool = action["tool"]
        args = action.get("args") or {}
        fn = TOOLS.get(tool)
        if fn is None:
            return {"ok": False, "error": f"tool not wired: {tool}", "tools": [t for t, *_ in done]}
        result = fn(mem, args)
        if not result.get("ok"):
            return {"ok": False, "tool": tool, "error": result.get("error") or "failed",
                    "reply": f"Yes sir, I tried {tool} but it failed: "
                             f"{result.get('error') or 'failed'}. Tell me to fix it and I "
                             f"will build what is missing.",
                    "tools": [t for t, *_ in done],
                    "ms": int((time.time() - started) * 1000)}
        done.append((tool, result, args))
    problems = []
    for tool, result, _args in done:
        problems += _verify(tool, result)
    lines = [describe(tool, result, args) for tool, result, args in done]
    body = "; ".join(lines).rstrip(".")
    reply = "Yes sir, " + body + "."
    if problems:
        reply += "\nWarning: " + "; ".join(problems) + "."
    return {"ok": not problems, "reply": reply,
            "tools": [t for t, *_ in done], "problems": problems,
            "ms": int((time.time() - started) * 1000)}
