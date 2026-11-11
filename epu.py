"""EPU's command line — what the owner actually touches.

    python epu.py init          create the folders + seed the owner's identity
    python epu.py whoami        answer "who am I" from disk, instantly
    python epu.py order "..."   run one order the way the HUD would
    python epu.py memory        memory numbers (honest)
    python epu.py repair        "fix your memory": check and repair, real report
    python epu.py doctor        is the office healthy? one screen

No cloud call happens in any of these except `order`, and `whoami`/`memory`/`repair`
never touch the network at all.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from epu import config, memory, rules            # noqa: E402


def _open() -> memory.Memory:
    return memory.Memory()


def cmd_init(_args: list[str]) -> int:
    for folder in (config.PROJECTS_DIR, config.MEMORY_DIR, ROOT / "hud", ROOT / "run",
                   ROOT / "logs", config.SECURE_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    for slug, project in config.projects().items():
        path = config.project_path(slug)
        path.mkdir(parents=True, exist_ok=True)
        note = path / "README.md"
        if not note.exists():
            note.write_text(f"# {project.get('title', slug)}\n\nProject folder for {slug}. "
                            "EPU works here; client data stays inside this folder.\n",
                            encoding="utf-8")
    mem = _open()
    seeded = rules.seed(mem)
    stats = mem.stats()
    mem.close()
    print(f"EPU ready, sir. Folders created, {seeded} identity fact(s) seeded, "
          f"{stats['facts']} memories in total.")
    return 0


def cmd_whoami(_args: list[str]) -> int:
    mem = _open()
    started = time.time()
    print(rules.identity_reply("who am i", "en", mem))
    print(f"  ({int((time.time() - started) * 1000)} ms, from disk, no model)")
    mem.close()
    return 0


def cmd_memory(_args: list[str]) -> int:
    mem = _open()
    stats = mem.stats()
    mem.close()
    print("Memory, sir:")
    for key, value in stats.items():
        print(f"  {key:<12} {value}")
    return 0


def cmd_repair(_args: list[str]) -> int:
    mem = _open()
    report = mem.repair()
    mem.close()
    print(f"Memory check, sir: {'healthy' if report['ok'] else 'problems found'}")
    print("  checked: " + ", ".join(report["checked"]))
    if report["fixed"]:
        print("  fixed:")
        for line in report["fixed"]:
            print(f"    - {line}")
    if report["problems"]:
        print("  left open:")
        for line in report["problems"]:
            print(f"    - {line}")
    return 0 if report["ok"] else 1


def cmd_doctor(_args: list[str]) -> int:
    mem = _open()
    stats = mem.stats()
    mem.close()
    print("EPU doctor, sir:")
    print(f"  memory         {stats['facts']} facts, {stats['chat']} chat rows, "
          f"{stats['indexed']} indexed")
    print(f"  tasks          {stats['open_tasks']} open, {stats['done_tasks']} done")
    cloud = config.cloud_ready()
    print(f"  cloud brain    {len(cloud)} provider(s) with keys" if cloud
          else "  cloud brain    NO KEY — add secure/keys.env (owner-only)")
    print(f"  local model    {'on' if config.local_model_enabled() else 'off'}")
    print(f"  names          owner: sir/boss · assistant: {config.assistant_name()}")
    return 0


def cmd_order(args: list[str]) -> int:
    text = " ".join(args).strip()
    if not text:
        print('Say it as: python epu.py order "make a folder called reports in '
              'projects/easyphoneunlocker"')
        return 2
    from epu import chat  # imported here so the subcommands above stay instant

    result = chat.answer(text, chat_id="cli")
    print(result["reply"])
    return 0


def cmd_serve(args: list[str]) -> int:
    """Start the office. --lan opens it to the owner's devices on the same WiFi,
    --tls serves https so those devices may use the camera and the microphone."""
    from epu import api, tlsutil

    lan = "--lan" in args or os.environ.get("EPU_BIND") == "0.0.0.0" or "--bind" in args
    if lan and config.bind() != "0.0.0.0":
        os.environ["EPU_BIND"] = "0.0.0.0"
        config.reset_cache()
    elif "--local" in args or not lan:
        os.environ["EPU_BIND"] = "127.0.0.1"
        config.reset_cache()
    use_tls = "--tls" in args or os.environ.get("EPU_TLS") == "1"
    host = os.environ.get("EPU_BIND") or config.bind()
    port = config.port("hud")
    if api.already_running("127.0.0.1", port):
        print(f"EPU is already running, sir: http{'s' if use_tls else ''}://127.0.0.1:{port}/")
        return 0
    try:
        server = api.build_server(host, port)
    except OSError as exc:
        print(f"cannot start on {host}:{port} - {exc}")
        return 1
    scheme = "http"
    if use_tls:
        if tlsutil.wrap(server) is None:
            print("TLS certificate could not be made - starting plain http instead, sir")
            print("(the laptop browser will then refuse the camera/mic; run from the PC)")
        else:
            scheme = "https"
    url = f"{scheme}://{host}:{port}/"
    print(f"EPU online at {url}  (Ctrl+C to stop)")
    if host == "0.0.0.0":
        lan_ip = "this PC's IP"
        print(f"LAN mode, sir: your laptop opens http{'s' if scheme == 'https' else ''}"
              f"://{lan_ip}:{port}/ and must accept the certificate once.")
    if not api.passphrase_set():
        print("No passphrase yet - set one with: python epu.py passphrase")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEPU stopped, sir.")
    return 0


def cmd_passphrase(args: list[str]) -> int:
    from epu import api

    phrase = args[0] if args else ""
    if len(phrase) < 8:
        print("use 8+ characters, sir: python epu.py passphrase \"your office pass\"")
        return 2
    api.set_passphrase(phrase)
    print("passphrase set, sir. Other devices on the WiFi now need it to talk to EPU.")
    return 0


COMMANDS = {
    "init": cmd_init, "whoami": cmd_whoami, "memory": cmd_memory,
    "repair": cmd_repair, "doctor": cmd_doctor, "order": cmd_order,
    "serve": cmd_serve, "passphrase": cmd_passphrase,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python epu.py {" + "|".join(COMMANDS) + "}")
        raise SystemExit(2)
    raise SystemExit(COMMANDS[sys.argv[1]](sys.argv[2:]))
