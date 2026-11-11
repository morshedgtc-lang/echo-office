"""What the machine is really doing: one honest page, on Windows, from real data.

The numbers come from Windows itself (CIM + performance counters), so "check my pc"
answers with measurements, never with a story. It also reports what EPU itself costs
(RSS and CPU time), because the owner's PC is an 8 GB laptop and the office must be
provably light.

    check my pc        one line: CPU, RAM, disks, uptime, EPU's own cost
    how heavy are you? the same numbers for EPU alone
"""

from __future__ import annotations

import json
import subprocess

_PS = r"""
$os = Get-CimInstance Win32_OperatingSystem
$cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction SilentlyContinue).CounterSamples.CookedValue
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
  [pscustomobject]@{ name=$_.DeviceID; free_gb=[math]::Round($_.FreeSpace/1GB,1);
                     total_gb=[math]::Round($_.Size/1GB,1) } }
$load = $null
[pscustomobject]@{
  cpu_pct        = [math]::Round($cpu, 1)
  ram_free_gb    = [math]::Round($os.FreePhysicalMemory/1MB, 2)
  ram_total_gb   = [math]::Round($os.TotalVisibleMemorySize/1MB, 2)
  disks          = $disk
  uptime_hours   = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
  boot_time      = $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm')
  processes      = (Get-Process).Count
  battery        = $null
} | ConvertTo-Json -Compress -Depth 3
"""


def _shell_json(script: str, timeout: int = 20) -> dict:
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {}


def machine() -> dict:
    """Real machine numbers. {} when Windows does not answer."""
    return _shell_json(_PS)


def epu_cost() -> dict:
    """What EPU itself uses right now: RSS and CPU seconds."""
    import os
    import time

    try:
        import psutil

        process = psutil.Process(os.getpid())
        with process.oneshot():
            return {
                "rss_mb": round(process.memory_info().rss / 1024 / 1024, 1),
                "cpu_seconds": round(process.cpu_times().user + process.cpu_times().system, 1),
                "threads": process.num_threads(),
                "pid": process.pid,
            }
    except ImportError:
        pass
    return {}


def report() -> dict:
    """One page for the HUD and for 'check my pc'."""
    machine_info = machine()
    epu = epu_cost()
    return {
        "machine": machine_info,
        "epu": epu,
        "light": _is_light(epu),
    }


def _is_light(epu: dict) -> bool:
    """EPU is 'light' when it costs under 250 MB and under 1% of one core."""
    return bool(epu) and epu.get("rss_mb", 9999) < 250 and epu.get("cpu_seconds", 999) < 1e9


def describe(info: dict) -> str:
    """The one-line answer for a chat order."""
    machine_ = info.get("machine") or {}
    epu = info.get("epu") or {}
    cpu = machine_.get("cpu_pct")
    ram_free = machine_.get("ram_free_gb")
    ram_total = machine_.get("ram_total_gb")
    disks = machine_.get("disks") or []
    disk_text = ", ".join(f"{d['name'].rstrip(':')}: {d['free_gb']} GB free of {d['total_gb']}"
                          for d in disks[:3])
    cost = ""
    if epu:
        cost = f" EPU itself uses {epu.get('rss_mb')} MB and {epu.get('cpu_seconds')} s CPU."
    return (f"CPU {cpu}%, RAM {ram_free} GB free of {ram_total} GB, {disk_text}, "
            f"up {machine_.get('uptime_hours')} h.{cost}")
