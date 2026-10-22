"""PRIMARY MODULE (part 1): RAM statistics, memory pressure, top processes,
repeated sampling for sustained growth, and measurement-based recommendations.

Data sources (all read-only):
  /proc/meminfo          kernel memory counters (kB)
  /proc/pressure/memory  Pressure Stall Information (PSI), Linux >= 4.20
  /proc/vmstat           cumulative pswpin / pswpout page counters
  psutil                 per-process RSS / VMS
Nothing in this file changes system state.
"""

from __future__ import annotations

import os
import time
from typing import Literal

import psutil
from pydantic import BaseModel, Field

MEMINFO_PATH = "/proc/meminfo"
PSI_PATH = "/proc/pressure/memory"
VMSTAT_PATH = "/proc/vmstat"
MiB = 1024 * 1024


def _mb(n: int | float) -> float:
    return round(n / MiB, 1)


# ---------------------------------------------------------------------------
# Parsing helpers (pure functions, unit-tested with fixture text)
# ---------------------------------------------------------------------------

def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo into {field: bytes}."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.split()
        if not parts or not parts[0].isdigit():
            continue
        n = int(parts[0])
        values[key.strip()] = n * 1024 if len(parts) > 1 and parts[1] == "kB" else n
    return values


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    """Parse PSI lines like 'some avg10=0.00 avg60=0.00 avg300=0.00 total=0'."""
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in ("some", "full"):
            continue
        out[parts[0]] = {k: float(v) for k, v in (p.split("=", 1) for p in parts[1:] if "=" in p)}
    return out


def parse_vmstat(text: str, keys: tuple[str, ...] = ("pswpin", "pswpout", "pgmajfault")) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in keys and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def read_swap_counters() -> dict[str, int] | None:
    text = _read(VMSTAT_PATH)
    return parse_vmstat(text) if text is not None else None


# ---------------------------------------------------------------------------
# Memory statistics
# ---------------------------------------------------------------------------

class PressureInfo(BaseModel):
    psi_available: bool
    some_avg10: float | None = None
    some_avg60: float | None = None
    full_avg10: float | None = None
    full_avg60: float | None = None


class MemoryStats(BaseModel):
    source: str = Field(description="'/proc/meminfo' on Linux, 'psutil' elsewhere")
    total_mb: float
    available_mb: float = Field(description="MemAvailable: estimate of memory usable by new programs without swapping")
    free_mb: float = Field(description="MemFree: completely unused pages (normally small on a healthy Linux system)")
    used_mb: float = Field(description="total - free - buffers - page cache - reclaimable slab (as free(1) computes it)")
    buffers_mb: float
    page_cache_mb: float = Field(description="Cached: file data kept in RAM; mostly reclaimable")
    reclaimable_slab_mb: float
    shmem_mb: float = Field(description="tmpfs/shared memory: counted in Cached but NOT freely reclaimable")
    dirty_mb: float
    available_percent: float
    swap_total_mb: float
    swap_used_mb: float
    swap_free_mb: float
    swap_cached_mb: float
    swap_used_percent: float
    pressure: PressureInfo
    pressure_level: Literal["low", "moderate", "high", "unknown"]
    pressure_reasons: list[str]
    explanation: str


def classify_pressure(available_pct: float, psi: PressureInfo,
                      swap_pages_per_s: float | None = None) -> tuple[str, list[str]]:
    """Heuristic pressure level from measured values.  Thresholds are documented
    in the report and are guidance, not kernel-defined limits."""
    reasons: list[str] = []
    level = "low"

    def bump(new: str, why: str) -> None:
        nonlocal level
        order = {"low": 0, "moderate": 1, "high": 2}
        if order[new] > order[level]:
            level = new
        reasons.append(why)

    if available_pct < 10:
        bump("high", f"only {available_pct:.1f}% of RAM is available (MemAvailable < 10%)")
    elif available_pct < 20:
        bump("moderate", f"{available_pct:.1f}% of RAM is available (between 10% and 20%)")
    if psi.psi_available:
        if (psi.full_avg10 or 0) >= 5:
            bump("high", f"PSI full avg10={psi.full_avg10}: all tasks stalled on memory for >=5% of the last 10 s")
        elif (psi.some_avg10 or 0) >= 10:
            bump("moderate", f"PSI some avg10={psi.some_avg10}: some tasks stalled on memory >=10% of the last 10 s")
    if swap_pages_per_s is not None:
        if swap_pages_per_s >= 1000:
            bump("high", f"heavy swap traffic: {swap_pages_per_s:.0f} pages/s swapped in+out")
        elif swap_pages_per_s >= 50:
            bump("moderate", f"active swapping: {swap_pages_per_s:.0f} pages/s swapped in+out")
    if not reasons:
        reasons.append("available memory is at least 20% and no stall/swap-activity signal was measured")
    return level, reasons


def get_memory_stats(swap_pages_per_s: float | None = None) -> MemoryStats:
    text = _read(MEMINFO_PATH)
    if text:
        m = parse_meminfo(text)
        total = m.get("MemTotal", 0)
        free = m.get("MemFree", 0)
        available = m.get("MemAvailable", free)
        buffers = m.get("Buffers", 0)
        cached = m.get("Cached", 0)
        sreclaim = m.get("SReclaimable", 0)
        shmem = m.get("Shmem", 0)
        dirty = m.get("Dirty", 0)
        swap_total = m.get("SwapTotal", 0)
        swap_free = m.get("SwapFree", 0)
        swap_cached = m.get("SwapCached", 0)
        source = MEMINFO_PATH
    else:  # non-Linux fallback so the demo still runs on a developer laptop
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        total, free, available = vm.total, vm.free, vm.available
        buffers = getattr(vm, "buffers", 0)
        cached = getattr(vm, "cached", 0)
        sreclaim = shmem = dirty = swap_cached = 0
        swap_total, swap_free = sw.total, sw.free
        source = "psutil"
    used = max(0, total - free - buffers - cached - sreclaim)
    swap_used = max(0, swap_total - swap_free)

    psi_text = _read(PSI_PATH)
    if psi_text:
        p = parse_psi(psi_text)
        psi = PressureInfo(
            psi_available=True,
            some_avg10=p.get("some", {}).get("avg10"),
            some_avg60=p.get("some", {}).get("avg60"),
            full_avg10=p.get("full", {}).get("avg10"),
            full_avg60=p.get("full", {}).get("avg60"),
        )
    else:
        psi = PressureInfo(psi_available=False)

    avail_pct = round(100.0 * available / total, 1) if total else 0.0
    level, reasons = classify_pressure(avail_pct, psi, swap_pages_per_s) if total else ("unknown", ["no data"])
    explanation = (
        f"'Free' ({_mb(free)} MB) counts only unused pages. 'Available' ({_mb(available)} MB) also "
        f"includes page cache and reclaimable slab that the kernel can drop on demand, so it is the "
        f"better measure of headroom. {_mb(cached)} MB of page cache is not 'wasted' memory."
    )
    return MemoryStats(
        source=source,
        total_mb=_mb(total), available_mb=_mb(available), free_mb=_mb(free), used_mb=_mb(used),
        buffers_mb=_mb(buffers), page_cache_mb=_mb(cached), reclaimable_slab_mb=_mb(sreclaim),
        shmem_mb=_mb(shmem), dirty_mb=_mb(dirty), available_percent=avail_pct,
        swap_total_mb=_mb(swap_total), swap_used_mb=_mb(swap_used), swap_free_mb=_mb(swap_free),
        swap_cached_mb=_mb(swap_cached),
        swap_used_percent=round(100.0 * swap_used / swap_total, 1) if swap_total else 0.0,
        pressure=psi, pressure_level=level, pressure_reasons=reasons, explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Top processes
# ---------------------------------------------------------------------------

def _clean_name(name: str | None) -> str:
    # Process names are attacker-controllable text; strip control characters and cap length.
    return "".join(ch for ch in (name or "?") if ch.isprintable())[:64]


class ProcessMemory(BaseModel):
    pid: int
    name: str
    user: str | None
    rss_mb: float = Field(description="Resident Set Size: RAM pages currently mapped (shared pages counted in full)")
    vms_mb: float = Field(description="Virtual size: address space reserved, not RAM used")
    percent_of_ram: float


class TopProcesses(BaseModel):
    count: int
    total_ram_mb: float
    processes: list[ProcessMemory]
    note: str = ("RSS double-counts shared libraries between processes, so the sum of RSS can exceed real usage. "
                 "Command lines are deliberately not collected because they may contain secrets.")


def get_top_processes(limit: int = 10) -> TopProcesses:
    rows: list[ProcessMemory] = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "memory_percent"]):
        info = proc.info
        mi = info.get("memory_info")
        if mi is None:
            continue
        rows.append(ProcessMemory(
            pid=info["pid"], name=_clean_name(info.get("name")), user=info.get("username"),
            rss_mb=_mb(mi.rss), vms_mb=_mb(mi.vms), percent_of_ram=round(info.get("memory_percent") or 0.0, 2),
        ))
    rows.sort(key=lambda r: r.rss_mb, reverse=True)
    return TopProcesses(count=min(limit, len(rows)), total_ram_mb=_mb(psutil.virtual_memory().total),
                        processes=rows[:limit])


# ---------------------------------------------------------------------------
# Repeated sampling for sustained growth ("suspected leak" detection)
# ---------------------------------------------------------------------------

class GrowthAnalysis(BaseModel):
    pid: int
    name: str
    samples_mb: list[float]
    first_mb: float
    last_mb: float
    net_growth_mb: float
    slope_mb_per_min: float
    increasing_fraction: float = Field(description="share of consecutive samples that went up")
    suspected_sustained_growth: bool
    verdict: str


def analyze_growth(pid: int, name: str, samples_bytes: list[int], interval_s: float,
                   threshold_bytes: float) -> GrowthAnalysis:
    """Decide whether a series of RSS samples shows sustained growth.

    Flagged when ALL hold: net growth >= threshold, least-squares slope > 0 and
    at least 60% of steps increase.  This flags a *suspected* leak only.
    """
    n = len(samples_bytes)
    if n < 2:
        raise ValueError("need at least two samples")
    xs = [i * interval_s for i in range(n)]
    mean_x, mean_y = sum(xs) / n, sum(samples_bytes) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, samples_bytes)) / sxx if sxx else 0.0
    ups = sum(1 for a, b in zip(samples_bytes, samples_bytes[1:]) if b > a)
    frac = ups / (n - 1)
    net = samples_bytes[-1] - samples_bytes[0]
    flagged = net >= threshold_bytes and slope > 0 and frac >= 0.6
    if flagged:
        verdict = (f"SUSPECTED sustained growth: RSS rose {_mb(net)} MB over {interval_s * (n - 1):.1f} s "
                   f"and increased in {ups}/{n - 1} intervals. This is a signal to investigate, not proof "
                   f"of a leak (caches, buffers and warm-up can grow legitimately).")
    else:
        verdict = (f"No sustained growth above {_mb(threshold_bytes)} MB in this window "
                   f"(net {_mb(net):+} MB, {ups}/{n - 1} intervals increased).")
    return GrowthAnalysis(
        pid=pid, name=name, samples_mb=[_mb(s) for s in samples_bytes],
        first_mb=_mb(samples_bytes[0]), last_mb=_mb(samples_bytes[-1]), net_growth_mb=_mb(net),
        slope_mb_per_min=round(slope * 60 / MiB, 2), increasing_fraction=round(frac, 2),
        suspected_sustained_growth=flagged, verdict=verdict,
    )


class SamplingReport(BaseModel):
    samples: int
    interval_seconds: float
    duration_seconds: float
    growth_threshold_mb: float
    results: list[GrowthAnalysis]
    vanished_pids: list[int]
    disclaimer: str = ("Growth in RSS over a short window only flags a SUSPECTED leak. Confirming a leak needs "
                       "longer observation and application-level tools (e.g. valgrind, heaptrack, tracemalloc).")


def sample_processes(pids: list[int], samples: int, interval_s: float, threshold_mb: float,
                     sleep=time.sleep) -> SamplingReport:
    procs: dict[int, psutil.Process] = {}
    names: dict[int, str] = {}
    series: dict[int, list[int]] = {}
    vanished: list[int] = []
    for pid in pids:
        try:
            p = psutil.Process(pid)
            procs[pid], names[pid], series[pid] = p, _clean_name(p.name()), []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            vanished.append(pid)
    start = time.monotonic()
    for i in range(samples):
        for pid in list(procs):
            try:
                series[pid].append(procs[pid].memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                vanished.append(pid)
                procs.pop(pid)
        if i < samples - 1:
            sleep(interval_s)
    results = [
        analyze_growth(pid, names[pid], series[pid], interval_s, threshold_mb * MiB)
        for pid in procs if len(series[pid]) >= 2
    ]
    results.sort(key=lambda r: r.net_growth_mb, reverse=True)
    return SamplingReport(samples=samples, interval_seconds=interval_s,
                          duration_seconds=round(time.monotonic() - start, 2),
                          growth_threshold_mb=threshold_mb, results=results, vanished_pids=sorted(set(vanished)))


def own_pids() -> set[int]:
    """PIDs of this server and its parent (the agent), excluded from 'top N' sampling."""
    return {os.getpid(), os.getppid()}
