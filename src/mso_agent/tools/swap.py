"""PRIMARY MODULE (part 2): swap devices, swap activity, vm.swappiness, and the
two approved memory actions (temporary swappiness change, drop_caches).

Read paths:  /proc/swaps, /proc/sys/vm/swappiness, /proc/vmstat
Write paths: only through execution.runner ("sudo -n sysctl -w ...") and only
             after the ApprovalGate verified a human approval.

Deliberately NOT implemented: swapoff, editing /etc/sysctl.conf or any other
persistent configuration, killing processes.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

from ..execution import runner
from ..security import policy
from . import memory

SWAPS_PATH = "/proc/swaps"
SWAPPINESS_PATH = "/proc/sys/vm/swappiness"
CACHE_PRESSURE_PATH = "/proc/sys/vm/vfs_cache_pressure"


class SwapDevice(BaseModel):
    name: str
    type: str
    size_mb: float
    used_mb: float
    priority: int


class SwapActivity(BaseModel):
    window_seconds: float
    pages_swapped_in: int
    pages_swapped_out: int
    pages_per_second: float = Field(description="(swap-in + swap-out pages) / window")


class SwapInfo(BaseModel):
    devices: list[SwapDevice]
    swap_configured: bool
    swappiness: int | None
    vfs_cache_pressure: int | None
    cumulative_pswpin: int | None
    cumulative_pswpout: int | None
    explanation: str


def parse_proc_swaps(text: str) -> list[SwapDevice]:
    devices = []
    for line in text.splitlines()[1:]:          # first line is the header
        parts = line.split()
        if len(parts) >= 5:
            devices.append(SwapDevice(
                name=parts[0], type=parts[1], size_mb=round(int(parts[2]) / 1024, 1),
                used_mb=round(int(parts[3]) / 1024, 1), priority=int(parts[4]),
            ))
    return devices


def read_int(path: str) -> int | None:
    text = memory._read(path)
    try:
        return int(text.strip()) if text else None
    except ValueError:
        return None


def get_swap_info() -> SwapInfo:
    text = memory._read(SWAPS_PATH)
    devices = parse_proc_swaps(text) if text else []
    counters = memory.read_swap_counters() or {}
    swappiness = read_int(SWAPPINESS_PATH)
    return SwapInfo(
        devices=devices,
        swap_configured=bool(devices),
        swappiness=swappiness,
        vfs_cache_pressure=read_int(CACHE_PRESSURE_PATH),
        cumulative_pswpin=counters.get("pswpin"),
        cumulative_pswpout=counters.get("pswpout"),
        explanation=(
            "vm.swappiness (0-200 on Linux >= 5.8, default 60) is the relative cost the kernel assigns to "
            "swapping anonymous pages versus dropping file-backed page cache when it must reclaim memory. "
            "Lower values prefer dropping cache; higher values swap anonymous pages more readily. It does not "
            "make the kernel swap when there is no memory pressure. Swap *used* alone is not a problem; "
            "sustained swap-in/out traffic is."
        ),
    )


def measure_swap_activity(window_s: float, sleep=time.sleep) -> SwapActivity | None:
    before = memory.read_swap_counters()
    if before is None:
        return None
    sleep(window_s)
    after = memory.read_swap_counters() or before
    sin = max(0, after.get("pswpin", 0) - before.get("pswpin", 0))
    sout = max(0, after.get("pswpout", 0) - before.get("pswpout", 0))
    return SwapActivity(window_seconds=window_s, pages_swapped_in=sin, pages_swapped_out=sout,
                        pages_per_second=round((sin + sout) / window_s, 1))


# ---------------------------------------------------------------------------
# Approved actions (called by the server ONLY after ApprovalGate.consume())
# ---------------------------------------------------------------------------

def describe_swappiness_change(value: int) -> tuple[str, dict]:
    current = read_int(SWAPPINESS_PATH)
    summary = (f"Temporarily set vm.swappiness from {current} to {value} "
               f"(runs: sudo -n sysctl -w vm.swappiness={value}).")
    details = {
        "current_value": current,
        "requested_value": value,
        "allowed_range": [policy.SWAPPINESS_MIN, policy.SWAPPINESS_MAX],
        "persistence": "temporary - lost at reboot; no file under /etc is modified",
        "restore_instructions": (f"sudo sysctl -w vm.swappiness={current}" if current is not None
                                 else "re-read the original value before changing it"),
    }
    return summary, details


def execute_swappiness_change(value: int, dry_run: bool) -> dict:
    if not (policy.SWAPPINESS_MIN <= value <= policy.SWAPPINESS_MAX):  # defence in depth
        raise ValueError("swappiness out of range")
    before = read_int(SWAPPINESS_PATH)
    result = runner.run_command("sysctl_set", [f"vm.swappiness={value}"], dry_run=dry_run)
    after = read_int(SWAPPINESS_PATH)
    return {
        "executed": not result.dry_run,
        "success": result.ok,
        "swappiness_before": before,
        "swappiness_after": after,
        "verified": (after == value) if not result.dry_run else None,
        "command": result.as_dict(),
        "restore_instructions": f"sudo sysctl -w vm.swappiness={before}" if before is not None else None,
        "note": "Temporary runtime change only. Measure memory/swap again before claiming any effect.",
    }


def describe_drop_caches(level: int) -> tuple[str, dict]:
    meaning = {1: "page cache", 2: "reclaimable slab objects (dentries and inodes)", 3: "page cache and slab objects"}
    summary = f"Run sync, then write {level} to vm.drop_caches to drop clean {meaning[level]}."
    details = {
        "level": level,
        "warning": ("The kernel documentation describes drop_caches as a testing/debugging aid. Dropped cache "
                    "must be re-read from disk, so file access may be slower afterwards. It does not fix a "
                    "memory leak and only clean (already written) pages are dropped."),
    }
    return summary, details


def execute_drop_caches(level: int, dry_run: bool) -> dict:
    if level not in policy.DROP_CACHES_LEVELS:
        raise ValueError("invalid drop_caches level")
    before = memory.get_memory_stats()
    sync_result = runner.run_command("sync") if not dry_run else None
    result = runner.run_command("sysctl_set", [f"vm.drop_caches={level}"], dry_run=dry_run)
    after = memory.get_memory_stats()
    return {
        "executed": not result.dry_run,
        "success": result.ok,
        "sync": sync_result.as_dict() if sync_result else None,
        "command": result.as_dict(),
        "measured_before": {"free_mb": before.free_mb, "available_mb": before.available_mb,
                            "page_cache_mb": before.page_cache_mb},
        "measured_after": {"free_mb": after.free_mb, "available_mb": after.available_mb,
                           "page_cache_mb": after.page_cache_mb},
        "note": ("These are the measured counters only. A higher 'free' figure is expected after dropping "
                 "cache and is NOT evidence of better performance; the cache will refill as files are used."),
    }
