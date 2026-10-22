"""PRIMARY MODULE (part 3): rule-based recommendations from measured values.

Each recommendation cites the evidence it is based on, so the LLM (or the demo
router) can explain *why*.  The rules never suggest killing a process
automatically, disabling swap, or editing persistent configuration.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel

from . import memory, swap


class Recommendation(BaseModel):
    id: str
    severity: Literal["info", "warning", "critical"]
    title: str
    evidence: dict
    advice: str


class RecommendationReport(BaseModel):
    pressure_level: str
    measurements: dict
    recommendations: list[Recommendation]
    disclaimer: str = ("Advisory only: no system state was changed. Any change must be requested explicitly and "
                       "approved by a human; its effect should be judged by measuring again, not assumed.")


def build_recommendations(stats: memory.MemoryStats, swap_info: swap.SwapInfo,
                          activity: swap.SwapActivity | None,
                          top: memory.TopProcesses) -> list[Recommendation]:
    """Pure function: measurements in, recommendations out (unit-tested)."""
    recs: list[Recommendation] = []
    rate = activity.pages_per_second if activity else None

    if stats.pressure_level == "high":
        biggest = top.processes[0] if top.processes else None
        recs.append(Recommendation(
            id="high-pressure", severity="critical", title="Memory pressure is high",
            evidence={"available_percent": stats.available_percent, "reasons": stats.pressure_reasons,
                      "largest_process": biggest.model_dump() if biggest else None},
            advice=("Close or restart the largest non-essential applications yourself (the agent never kills "
                    "processes). If this is frequent, the workload needs more RAM or limits (e.g. cgroup memory.max)."),
        ))
    elif stats.pressure_level == "moderate":
        recs.append(Recommendation(
            id="moderate-pressure", severity="warning", title="Memory headroom is getting low",
            evidence={"available_percent": stats.available_percent, "reasons": stats.pressure_reasons},
            advice="Review the top processes below and run sample_process_memory to check for sustained growth.",
        ))
    else:
        recs.append(Recommendation(
            id="healthy", severity="info", title="No memory pressure measured",
            evidence={"available_percent": stats.available_percent, "available_mb": stats.available_mb},
            advice="No action is needed. Lag, if any, is more likely CPU, disk I/O or a single slow application.",
        ))

    # Large page cache with good availability is normal and should be left alone.
    if stats.page_cache_mb > 0.25 * stats.total_mb and stats.available_percent >= 20:
        recs.append(Recommendation(
            id="cache-is-fine", severity="info", title="Large page cache is normal",
            evidence={"page_cache_mb": stats.page_cache_mb, "free_mb": stats.free_mb,
                      "available_mb": stats.available_mb},
            advice=("The cache is reclaimable and speeds up file access. Dropping caches is not recommended; it "
                    "would only make 'free' look larger temporarily."),
        ))

    if not swap_info.swap_configured and stats.swap_total_mb == 0:
        recs.append(Recommendation(
            id="no-swap", severity="warning" if stats.available_percent < 20 else "info",
            title="No swap space is configured",
            evidence={"swap_total_mb": stats.swap_total_mb},
            advice=("Without swap, rarely used anonymous pages cannot be moved out of RAM and the OOM killer acts "
                    "sooner under pressure. Adding a swap file is an administrator decision and is not automated."),
        ))
    elif rate is not None and rate >= 50:
        recs.append(Recommendation(
            id="active-swapping", severity="critical" if rate >= 1000 else "warning",
            title="Active swap-in/swap-out traffic measured",
            evidence={"pages_per_second": rate, "swap_used_mb": stats.swap_used_mb,
                      "swappiness": swap_info.swappiness},
            advice=("Sustained swapping means the working set does not fit in RAM. Reduce the workload or add RAM. "
                    "Changing vm.swappiness only shifts reclaim between cache and anonymous memory; it cannot "
                    "create memory."),
        ))
    elif stats.swap_used_mb > 0:
        recs.append(Recommendation(
            id="idle-swap", severity="info", title="Swap is in use but not actively moving",
            evidence={"swap_used_mb": stats.swap_used_mb,
                      "pages_per_second": rate if rate is not None else "not measured"},
            advice="Idle pages sitting in swap are harmless; no action needed.",
        ))

    if swap_info.swappiness is not None and swap_info.swappiness not in range(10, 101):
        recs.append(Recommendation(
            id="unusual-swappiness", severity="info", title="vm.swappiness is outside 10-100",
            evidence={"swappiness": swap_info.swappiness},
            advice="Confirm the value was set deliberately; the distribution default is usually 60.",
        ))
    return recs


def recommend(window_seconds: int, sleep=time.sleep) -> RecommendationReport:
    activity = swap.measure_swap_activity(window_seconds, sleep=sleep)
    stats = memory.get_memory_stats(activity.pages_per_second if activity else None)
    info = swap.get_swap_info()
    top = memory.get_top_processes(5)
    recs = build_recommendations(stats, info, activity, top)
    return RecommendationReport(
        pressure_level=stats.pressure_level,
        measurements={
            "total_mb": stats.total_mb, "available_mb": stats.available_mb,
            "available_percent": stats.available_percent, "free_mb": stats.free_mb,
            "page_cache_mb": stats.page_cache_mb, "swap_used_mb": stats.swap_used_mb,
            "swap_total_mb": stats.swap_total_mb, "swappiness": info.swappiness,
            "swap_pages_per_second": activity.pages_per_second if activity else None,
            "psi_some_avg10": stats.pressure.some_avg10,
            "top_processes": [{"pid": p.pid, "name": p.name, "rss_mb": p.rss_mb} for p in top.processes],
        },
        recommendations=recs,
    )
