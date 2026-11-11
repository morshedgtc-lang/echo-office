"""Configuration for EPU: one file (config/office.json) plus environment overrides.

The owner edits the JSON, not the code. Environment variables override single values,
so a key or a port never needs a code change:

    EPU_HUD_PORT=8790          ports.hud
    EPU_BIND=127.0.0.1         bind
    EPU_LOCAL=1                providers.local_enabled
    EPU_VOICE=1                voice.enabled
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

# F:\ECHO — the folder this file lives in (epu/config.py -> epu -> F:\ECHO)
ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "office.json"
SECURE_DIR = ROOT / "secure"
MEMORY_DIR = ROOT / "memory"
PROJECTS_DIR = ROOT / "projects"

# Owner-private files. Keys stay here and nowhere else; .gitignore keeps it out of git.
ENV_FILE = SECURE_DIR / "keys.env"
NVIDIA_ENV_VARS = ("EPU_NVIDIA_KEY_1", "EPU_NVIDIA_KEY_2")


def _load_env_file() -> None:
    """Load secure/keys.env into os.environ if it is present (KEY=value lines)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


@lru_cache(maxsize=1)
def settings() -> dict:
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    env = os.environ

    ports = dict(data.get("ports") or {})
    ports["hud"] = int(env.get("EPU_HUD_PORT") or ports.get("hud") or 8790)
    ports["stream"] = int(env.get("EPU_STREAM_PORT") or ports.get("stream") or 8792)
    ports["api"] = int(env.get("EPU_API_PORT") or ports.get("api") or 8791)
    data["ports"] = ports

    data["bind"] = (env.get("EPU_BIND") or data.get("bind") or "127.0.0.1").strip()

    prov = dict(data.get("providers") or {})
    if env.get("EPU_LOCAL", "") in ("1", "true", "yes", "on"):
        prov["local_enabled"] = True
    if env.get("EPU_LOCAL_ONLY", "") in ("1", "true", "yes", "on"):
        prov["default"] = "local"
    data["providers"] = prov

    voice = dict(data.get("voice") or {})
    if env.get("EPU_VOICE", "") in ("1", "true", "yes", "on"):
        voice["enabled"] = True
    data["voice"] = voice
    return data


def policy() -> dict:
    """The safety and budget rules (the owner's constitution, in numbers)."""
    return dict(settings()["policy"])


def port(kind: str = "hud") -> int:
    return int(settings()["ports"][kind])


def bind() -> str:
    return str(settings()["bind"])


def assistant_name() -> str:
    return str(settings().get("assistant_name") or "EPU")


def owner_address() -> tuple[str, ...]:
    return tuple(settings().get("owner_address") or ("sir", "boss"))


def projects() -> dict:
    """Known client/project folders: slug -> config."""
    return dict(settings().get("projects") or {})


def project_path(slug: str) -> Path:
    return ROOT / str(projects().get(slug, {}).get("path") or f"projects/{slug}")


def local_model_enabled() -> bool:
    return bool(settings()["providers"].get("local_enabled"))


def cloud_providers() -> list[dict]:
    return [p for p in (settings()["providers"].get("cloud") or [])]


def provider_key(p: dict) -> str:
    """First configured key for a cloud provider, or "" when none is set."""
    for name in p.get("key_env") or []:
        value = os.environ.get(str(name), "")
        if value:
            return value
    return ""


def cloud_ready() -> list[dict]:
    """Providers that actually have a key (what the office may use right now)."""
    return [p for p in cloud_providers() if provider_key(p)]


def secure_write(name: str, content: str) -> Path:
    """Write a private file under secure/ with owner-only permissions."""
    SECURE_DIR.mkdir(parents=True, exist_ok=True)
    path = SECURE_DIR / name
    path.write_text(content, encoding="utf-8")
    if os.name == "nt":
        try:
            import subprocess

            user = os.environ.get("USERNAME") or ""
            if user:
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                               capture_output=True)
        except Exception:
            pass
    else:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


def reset_cache() -> None:
    """Tests use this: drop the cached settings so a temp config is picked up."""
    settings.cache_clear()
