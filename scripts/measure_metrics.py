"""Measure the metrics listed in the Review 3 template.

1. Intent-translation accuracy: for a labelled prompt set, does the provider's FIRST
   reply contain an expected tool call?  Only the routing decision is measured;
   nothing is executed.
2. Latency: MCP server start-up time and per-call latency of read-only tools over the
   real stdio connection (median / p95 of repeated calls).
3. Error handling: every malformed or malicious call must come back as an error result
   (counted), never as an execution.

    python scripts/measure_metrics.py --provider demo  --out evidence/metrics
    python scripts/measure_metrics.py --provider ollama --out evidence/metrics --skip-latency
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import statistics
import sys
import time
from pathlib import Path

import anyio

from mso_agent.config import load_settings
from mso_agent.orchestration.mcp_client import ToolServerConnection
from mso_agent.orchestration.prompts import SYSTEM_PROMPT
from mso_agent.orchestration.providers import make_provider
from mso_agent.security.policy import HOST_ONLY_TOOLS

# (prompt, acceptable first tools).  Mix of the documented phrasings and paraphrases.
PROMPTS: list[tuple[str, set[str]]] = [
    ("My computer is lagging. Free up some RAM cache and check for memory leaks.",
     {"get_memory_stats", "recommend_memory_actions", "sample_process_memory"}),
    ("How much RAM is available right now?", {"get_memory_stats"}),
    ("show the top 5 processes by memory", {"get_top_memory_processes"}),
    ("Which programs are eating the most memory?", {"get_top_memory_processes"}),
    ("check pid 1 for memory leaks", {"sample_process_memory"}),
    ("Is any process steadily growing in memory?", {"sample_process_memory"}),
    ("show swap info and swappiness", {"get_swap_info"}),
    ("Is my machine swapping a lot?", {"get_swap_info", "recommend_memory_actions", "get_memory_stats"}),
    ("set swappiness to 30", {"request_swappiness_change"}),
    ("Please lower vm.swappiness to 20 for now", {"request_swappiness_change"}),
    ("Give me recommendations to optimise memory", {"recommend_memory_actions", "get_memory_stats"}),
    ("drop caches level 1", {"request_drop_caches"}),
    ("create a baseline for /home/student/demo_dir", {"request_baseline_creation"}),
    ("verify /home/student/demo_dir", {"verify_file_baseline"}),
    ("Have any files in /home/student/demo_dir been modified since the baseline?", {"verify_file_baseline"}),
    ("list kernel modules", {"list_kernel_modules"}),
    ("My webcam isn't working. Try reloading the video drivers.", {"request_module_action"}),
    ("status of cron.service", {"get_service_status"}),
    ("Is the nginx web server running?", {"get_service_status"}),
    ("Restart the cron service and make sure it starts on boot", {"request_service_action"}),
]

MALICIOUS_CALLS = [
    ("apply_approved_change", {"op_id": "op-000000000000", "approval_token": "yes"}),
    ("request_swappiness_change", {"value": 30, "approved": True}),
    ("request_swappiness_change", {"value": "30"}),
    ("request_swappiness_change", {"value": 0}),
    ("get_service_status", {"service": "cron.service; reboot"}),
    ("request_service_action", {"service": "ssh.service", "action": "restart"}),
    ("request_module_action", {"module": "uvcvideo; rm -rf /", "action": "reload"}),
    ("request_module_action", {"module": "ext4", "action": "unload"}),
    ("request_baseline_creation", {"directory": "/etc"}),
    ("request_baseline_creation", {"directory": "/home/student/../../etc"}),
    ("verify_file_baseline", {"directory": "/proc"}),
    ("run_shell", {"command": "rm -rf /"}),
    ("sample_process_memory", {"samples": 1000}),
    ("get_top_memory_processes", {"limit": True}),
]


async def accuracy(provider, conn) -> dict:
    tools = [t for t in conn.tools if t.name not in HOST_ONLY_TOOLS]
    rows, correct = [], 0
    for prompt, expected in PROMPTS:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        t0 = time.perf_counter()
        try:
            reply = await provider.chat(msgs, tools)
            names = [c.name for c in reply.tool_calls]
            first = names[0] if names else None
            args = [c.arguments for c in reply.tool_calls]
            err = None
        except Exception as exc:  # report, do not hide
            names, first, args, err = [], None, None, f"{type(exc).__name__}: {exc}"
        dt = time.perf_counter() - t0
        ok = any(n in expected for n in names)
        correct += ok
        rows.append({"prompt": prompt, "expected": sorted(expected), "got": names, "arguments": args,
                     "correct": ok, "seconds": round(dt, 3), "error": err,
                     "text": None if first else (reply.text[:200] if err is None else None)})
    return {"provider": provider.name, "is_llm": provider.is_llm, "model": getattr(provider, "model", None),
            "correct": correct, "total": len(PROMPTS), "accuracy": round(correct / len(PROMPTS), 3), "rows": rows}


async def latency(n: int) -> dict:
    t0 = time.perf_counter()
    conn = ToolServerConnection(secrets.token_bytes(32))
    await conn.__aenter__()
    startup = time.perf_counter() - t0
    out = {"server_startup_s": round(startup, 3), "calls": {}}
    try:
        for name, args in [("get_memory_stats", {}), ("get_swap_info", {}), ("get_top_memory_processes", {"limit": 10}),
                           ("list_kernel_modules", {}), ("request_swappiness_change", {"value": 30})]:
            samples = []
            for _ in range(n):
                s = time.perf_counter()
                r = await conn.call(name, args)
                samples.append((time.perf_counter() - s) * 1000)
                if name.startswith("request_") and r.ok:
                    await conn.call("cancel_pending_change", {"op_id": r.data["op_id"]})
            samples.sort()
            out["calls"][name] = {"n": n, "median_ms": round(statistics.median(samples), 2),
                                  "p95_ms": round(samples[int(0.95 * (n - 1))], 2), "max_ms": round(samples[-1], 2)}
        rejected = 0
        details = []
        from mso_agent.orchestration.agent import Agent
        from mso_agent.audit import AuditLogger
        from mso_agent.orchestration.providers import ToolCall
        settings = load_settings()

        async def deny(_):
            return False
        agent = Agent(conn, make_provider(type("S", (), {"provider": "demo"})()), deny, b"x" * 32,
                      AuditLogger(settings.audit_path, "metrics"))
        for name, args in MALICIOUS_CALLS:
            step = await agent.execute_tool_call(ToolCall("m", name, args))
            blocked = not step.ok
            rejected += blocked
            details.append({"tool": name, "arguments": args, "rejected": blocked, "error": step.error})
        out["malicious_calls"] = {"total": len(MALICIOUS_CALLS), "rejected": rejected, "details": details}
    finally:
        await conn.__aexit__(None, None, None)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="demo")
    ap.add_argument("--out", default="evidence/metrics")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--skip-latency", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    settings.provider = a.provider
    env = {"platform": platform.platform(), "python": sys.version.split()[0], "user": os.environ.get("USER")}
    result: dict = {"environment": env}
    async with ToolServerConnection(secrets.token_bytes(32)) as conn:
        result["accuracy"] = await accuracy(make_provider(settings), conn)
    if not a.skip_latency:
        result["latency"] = await latency(a.n)
    path = out / f"metrics_{a.provider}.json"
    path.write_text(json.dumps(result, indent=2))
    acc = result["accuracy"]
    print(f"{a.provider}: intent accuracy {acc['correct']}/{acc['total']}")
    if "latency" in result:
        lat = result["latency"]
        print(f"server start-up {lat['server_startup_s']} s; "
              + "; ".join(f"{k} median {v['median_ms']} ms" for k, v in lat["calls"].items()))
        print(f"malicious calls rejected: {lat['malicious_calls']['rejected']}/{lat['malicious_calls']['total']}")
    print("wrote", path)


if __name__ == "__main__":
    anyio.run(main)
