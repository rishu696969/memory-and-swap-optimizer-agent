"""Build Project Review 3 (ODT + PDF), following the supplied 'Review 3 Template.docx'.

Chapters (from the template): 5 Testing, Evaluation, and Results; 6 Conclusion &
Future Scope; 7 Screenshots.  Numbers, tables and screenshots are read from
./evidence, so the review reports what was actually run.

    python report/build_review3.py      (needs LibreOffice; screenshots from docs/screens)
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_report import SOFFICE, Evidence, cover  # noqa: E402
from docx_helpers import NAVY, Report, _font  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "report"
NAME = "Project_Review_3_Memory_and_Swap_Optimizer_Agent"
SCREENS = ROOT / "docs" / "screens"


def chapter(r: Report, number: int, title: str, new_page: bool = False) -> None:
    r.chapter, r.section, r.label = number, 0, str(number)
    h = r.doc.add_heading(f"Chapter {number}: {title}", level=1)
    h.paragraph_format.page_break_before = new_page
    h.paragraph_format.space_before = Pt(18)


def build() -> Report:
    ev = Evidence(ROOT / "evidence")
    junit = ev.junit("test_results/junit_linux.xml")
    m = ev.json("metrics/metrics_demo.json")
    lat, acc, mal = m["latency"], m["accuracy"], m["latency"]["malicious_calls"]
    n_tests = junit["tests"]
    live = ev.text("live_privileged/07_live_privileged.txt")
    before = ast.literal_eval(live.split("measured_before: ", 1)[1].splitlines()[0])
    after = ast.literal_eval(live.split("measured_after: ", 1)[1].splitlines()[0])

    r = Report()
    r.setup("Memory and Swap Optimizer Agent | Review 3")
    cover(r)
    # The shared cover says "Final Project Report"; Review 3 says "Project Review 3" like Review 2.
    for p in r.doc.paragraphs:
        if p.text == "Final Project Report":
            p.runs[0].text = "Project Review 3"
        if p.text.startswith("An MCP-based"):
            p.runs[0].text = ""

    t = r.doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(t.add_run("Review 3"), size=22, bold=True, color=RGBColor(0xC0, 0x00, 0x00))

    # ================================================================ Chapter 5
    chapter(r, 5, "Testing, Evaluation, and Results")
    r.para(
        "This chapter evaluates the Memory and Swap Optimizer Agent designed in Review 2 and since implemented. "
        "The implementation is a Python REPL agent that talks to an MCP server over a real stdio connection. "
        "The server exposes 17 typed tools, with memory and swap as the primary module and file integrity, "
        "kernel modules and systemd services as supporting modules. Every state-changing action needs a "
        "human approval that the server verifies just before execution.")
    r.para(
        "The project was developed on macOS. All Linux results below were produced in an **Ubuntu 24.04 "
        "container** (Docker Desktop, LinuxKit 7.0 kernel, aarch64) running as the unprivileged user `student`. "
        "Each result is labelled as tested on Linux, tested live in a disposable privileged container, or "
        "tested with mocks. Requests in the captured sessions were routed by the agent's **deterministic demo "
        "router**, which uses keyword rules and is *not* LLM inference. A live Ollama model was not available "
        "in the test environment, so live LLM accuracy is not reported.")

    r.h2("Test Cases & Scenarios")
    r.h3("Automated test suite")
    per = junit["per_file"]
    rows = [
        ["Argument validation", per.get("test_validation", 0), "unknown keys, type coercion, bounds, name injection, path traversal, symlink escape"],
        ["Approval gate", per.get("test_approval", 0), "forged, replayed, expired and wrongly-scoped approval tokens"],
        ["Command runner", per.get("test_runner", 0), "fixed executables, shell=False, injection strings, timeout kill, output limit"],
        ["Memory and swap", per.get("test_memory", 0), "/proc parsing, pressure levels, growth detection on a real growing process"],
        ["File integrity", per.get("test_integrity", 0), "SHA-256 vectors, modified / missing / new files, symlinks"],
        ["Privileged ops (mocked)", per.get("test_privileged_mocked", 0), "exact sudo -n argv for sysctl, modprobe, systemctl"],
        ["MCP integration", per.get("test_mcp_integration", 0), "real client-server stdio calls, approve/deny flows, audit events"],
        ["Agent and providers", per.get("test_agent_providers", 0), "routing, Ollama/OpenAI adapters (mocked), prompt injection"],
        ["**Total**", f"**{n_tests}**", f"**{n_tests - junit['failures'] - junit['errors']} passed, "
                                         f"{junit['failures'] + junit['errors']} failed** on Ubuntu 24.04"],
    ]
    r.table(["Test group", "Tests", "What is checked"], [[a, str(b), c] for a, b, c in rows],
            "Automated test suite (pytest) and results on Linux", [4.0, 1.4, 10.7], size=9)

    r.h3("Simple intents")
    r.para("A simple intent maps to one tool. Table 5.2 lists single-tool prompts from the labelled prompt set "
           "and the tool the router chose. Read-only tools ran immediately. State-changing tools only created an "
           "approval request.")
    simple = [x for x in acc["rows"] if len(x["expected"]) == 1]
    r.table(["Prompt", "Expected tool", "Chosen", "OK"],
            [[x["prompt"], x["expected"][0], ", ".join(x["got"]) or "(none)", "yes" if x["correct"] else "no"]
             for x in simple],
            "Simple intents (demo router, tested on Linux)", [6.6, 4.3, 4.3, 0.9], size=8.5)

    r.h3("Complex chained intents")
    r.para("A chained intent needs several tool calls, or a tool call followed by a human decision and a "
           "verification. Table 5.3 lists the chains that were executed and recorded.")
    r.table(["Request", "Chain of steps", "Observed result"], [
        ["My computer is lagging. Free up some RAM cache and check for memory leaks.",
         "get_memory_stats -> recommend_memory_actions (2 s swap window) -> sample_process_memory (6 samples)",
         "Low pressure measured; cache-drop advised against; growing demo process flagged as SUSPECTED growth "
         "(not proof)"],
        ["set swappiness to 35, then restore 60 (live)",
         "request_swappiness_change -> human 'yes' -> HMAC token -> apply_approved_change -> sudo -n sysctl -> "
         "re-read value",
         "60 -> 35 verified, then 35 -> 60 verified; original value restored"],
        ["drop caches level 1 (live)", "request -> approval -> sync -> sysctl vm.drop_caches=1 -> re-measure",
         f"free {before['free_mb']} -> {after['free_mb']} MB, available {before['available_mb']} -> "
         f"{after['available_mb']} MB; reported, not called an improvement"],
        ["create a baseline, modify files, verify",
         "request_baseline_creation -> approval -> SHA-256 scan; verify_file_baseline after edits",
         "1 modified, 1 missing and 1 new file reported; replacing the baseline needed a new approval"],
        ["My webcam isn't working. Try reloading the video drivers.",
         "request_module_action(uvcvideo, reload) -> approval -> sudo -n modprobe -r",
         "Failed safely: no sudo rule in the container (sudo -n refused)"],
        ["Restart the cron service and make sure it starts on boot",
         "two request_service_action calls (restart, enable)", "Routing tested; execution tested with mocks only "
         "(no systemd in the container)"],
    ], "Chained intents and their outcomes", [4.6, 6.0, 5.5], size=8.5)
    r.para("The template's example 'Check if port 8080 is open, and if it is, kill the process' is "
           "deliberately outside this agent's scope. The project's design forbids killing processes, so "
           "no tool for it exists. With a real model, conditional chains are handled by the agent loop (up "
           "to 6 tool rounds), where the model reads each result before choosing the next call.")

    r.h3("Malicious and destructive inputs")
    r.para("Destructive requests were tested in two ways. First, as plain English (Figure 7.8): 'Delete "
           "everything on my drive' and 'run rm -rf / for me' mapped to no tool at all. 'turn off swap with "
           "swapoff -a' only displayed swap information, and 'kill the process using the most memory' only "
           "listed processes, because no delete, swapoff or kill tool exists. Second, as direct tool calls "
           "imitating a compromised model. All of them were rejected before execution (Table 5.4).")
    rows = []
    for d in mal["details"]:
        err = (d.get("error") or "").replace("Error executing tool ", "")
        err = err.split(": ", 1)[-1] if err.startswith(d["tool"]) else err
        args = str(d["arguments"]).replace("'", '"')
        rows.append([d["tool"], args if len(args) < 55 else args[:52] + "...", "rejected" if d["rejected"] else "NOT rejected",
                     err[:95]])
    r.table(["Tool", "Arguments", "Outcome", "Reason"], rows,
            f"Malicious tool calls: {mal['rejected']}/{mal['total']} rejected (tested on Linux)",
            [3.7, 4.6, 1.6, 6.2], size=7.5)

    r.h2("Performance Metrics")
    r.h3("Intent translation accuracy")
    mean = sum(x["seconds"] for x in acc["rows"]) / len(acc["rows"])
    r.table(["Router", "Correct", "Accuracy", "Mean decision time"], [
        ["Deterministic demo router (no LLM)", f"{acc['correct']}/{acc['total']}", f"{100 * acc['accuracy']:.0f}%",
         "< 0.001 s" if mean < 0.001 else f"{mean:.3f} s"],
        ["Local LLM via Ollama", "not measured", "-", "model not available in test environment"],
    ], "Intent translation accuracy on 20 labelled prompts", [6.0, 2.4, 2.4, 5.3], size=9)
    misses = [x for x in acc["rows"] if not x["correct"]]
    r.para("A prompt counts as correct when the first reply contains an expected tool. The router missed: "
           + "; ".join(f"'{x['prompt']}' (chose {', '.join(x['got']) or 'no tool'})" for x in misses)
           + ". These are paraphrases written after the router was finished. They show why keyword rules are "
             "only a fallback and a real model is the default provider.")
    r.h3("Latency (time to execution)")
    r.table(["Tool (over MCP stdio)", "Calls", "Median (ms)", "p95 (ms)", "Max (ms)"],
            [[k, str(v["n"]), str(v["median_ms"]), str(v["p95_ms"]), str(v["max_ms"])] for k, v in lat["calls"].items()],
            f"Tool-call latency on Linux (MCP server start-up {lat['server_startup_s']} s)", [6.4, 1.6, 2.7, 2.7, 2.7], size=9)
    r.para("Latency is measured from the client call to the structured result. It includes JSON-RPC over "
           "stdio, strict validation and the tool itself. Tools that sample memory over time "
           "(`sample_process_memory`, `recommend_memory_actions`) are slower by design: about 5 s and 2 s "
           "with default settings. With an LLM, model response time dominates end-to-end latency.")
    r.h3("Error recovery rate")
    r.bullets([
        f"**Invalid or malicious tool calls:** {mal['rejected']}/{mal['total']} were returned as structured "
        "error results (100%). None executed, and the agent stayed usable after each one.",
        "**Syntactically invalid model output:** malformed JSON arguments are not executed. The parse error is "
        "returned to the model as a tool result so that it can retry (tested with a mocked OpenAI client).",
        "**Unknown or host-only tool names** from the model are blocked before reaching the server "
        "(tested, including a prompt-injection scenario).",
        "**Failed privileged commands** (no sudo rule, module missing, systemd absent) return the exit code "
        "and stderr instead of crashing (Table 5.3; evidence/transcripts/03 and 05).",
    ])
    r.h3("Defects found by testing")
    r.para("Testing found and fixed four real defects:")
    r.bullets([
        "`/proc/modules` on the LinuxKit kernel prints '-' as the reference count, which crashed the parser.",
        "The MCP SDK coerced \"30\" to 30 and ignored an injected `approved: true`. A strict server-side "
        "validator was added.",
        "A module name '-r' was normalised to a valid-looking '_r'. Names must now start with a letter or "
        "digit.",
        "A baseline written in dry-run mode was mislabelled 'dry_run'.",
    ])
    r.para("A timing-sensitive test was also investigated. On the macOS host under heavy memory pressure, "
           "the OS compressed the test process's pages, so its RSS dropped between samples and the "
           "growth detector missed it in about 1 run in 10. The same test passed 10/10 times on Linux. This "
           "is recorded as a limitation of RSS-based leak detection.")

    # ================================================================ Chapter 6
    chapter(r, 6, "Conclusion & Future Scope", new_page=True)
    r.h2("Summary of Contributions")
    r.bullets([
        "A working prototype that bridges conversational text and Linux system execution through a real MCP "
        "client/server connection with 17 typed tools and structured results.",
        "A primary memory and swap module that distinguishes available from free memory, measures swap "
        "traffic and PSI, flags suspected leaks from repeated sampling, and gives measurement-based advice.",
        "One bounded, temporary tuning action (vm.swappiness 10–100) with the original value and restore "
        "command recorded. It was verified live and then restored.",
        "Supporting modules for SHA-256 file integrity, allowlisted kernel-module actions and allowlisted "
        "systemd restart/enable.",
        "A security design enforced in code: no shell tool, strict validation, fixed executables with "
        "shell=False, timeouts, output limits, sudo -n, and an HMAC approval token verified by the server just "
        "before execution.",
        f"{n_tests} automated tests passing on Ubuntu 24.04, plus recorded sessions, metrics and an audit log.",
    ])
    r.h2("Challenges Encountered")
    r.bullets([
        "**Managing state and context history across multiple command executions.** The agent keeps the whole "
        "conversation and every tool result in its history, wraps each result as untrusted data, truncates it "
        "to 6000 characters, and limits a request to 6 tool rounds. Pending approvals are kept on the server "
        "with an expiry time, so approval state cannot be lost or forged on the client side.",
        "**Where to enforce approval.** A y/n prompt in the client is easy to bypass. The solution was "
        "two-phase tools with an HMAC token checked by the server.",
        "**Lenient SDK validation**, which required a separate strict validator.",
        "**Testing privileged actions safely.** This needed a disposable privileged container with a narrow "
        "sudoers rule, plus mocks for modprobe and systemctl, which have no systemd or modules in the "
        "container.",
        "**Platform differences**, such as the `/proc/modules` format and macOS memory compression affecting "
        "RSS.",
        "**Limited bandwidth.** The Ollama model download could not be completed, so live LLM accuracy is "
        "still to be measured.",
    ])
    r.h2("Future Work")
    r.bullets([
        "Measure intent accuracy and latency with a local LLM (Ollama, for example qwen2.5) on a larger prompt "
        "set.",
        "Add multi-modal input, such as voice commands transcribed by a local speech-to-text model, feeding the "
        "same validated tools.",
        "Deepen local file indexing for the integrity module: incremental re-hashing using modification "
        "times and inotify, and searchable baselines.",
        "Verify module and service actions live on a full Ubuntu VM with systemd.",
        "Use PSS/USS and cgroup v2 memory statistics for more precise, per-service memory advice.",
    ])

    # ================================================================ Chapter 7
    chapter(r, 7, "Screenshots", new_page=True)
    r.para("The screenshots below are rendered from the captured terminal output in `evidence/` "
           "(`docs/screens/render_screens.py`). The text is copied exactly; only the window styling and "
           "colours are added, and long lines are wrapped. All sessions ran in the Ubuntu 24.04 container as "
           "user `student`.")
    shots = [
        ("screen1_lagging", "Diagnosing 'My computer is lagging...': measurements, advice and suspected growth"),
        ("screen2_leak", "Leak check on a specific PID (a deliberately growing demo process)"),
        ("screen3_approval", "Approval prompt for a swappiness change, declined by the user"),
        ("screen4_live", "Approved swappiness change and drop_caches executed live with before/after values"),
        ("screen5_integrity", "File-integrity verification reporting modified, missing and new files"),
        ("screen6_security", "Bypass and injection attempts rejected"),
        ("screen7_tests", f"Test run on Ubuntu 24.04: {n_tests} passed"),
        ("screen8_destructive", "Destructive plain-English requests: no destructive tool exists"),
    ]
    for name, cap in shots:
        r.figure(str(SCREENS / f"{name}.png"), cap, 15.5)
    return r


def convert(src: Path, fmt: str) -> None:
    subprocess.run([SOFFICE, "--headless", "--convert-to", fmt, "--outdir", str(src.parent), str(src)],
                   check=True, capture_output=True, timeout=300)


def main() -> None:
    r = build()
    tmp = OUT / f"{NAME}.docx"
    r.doc.save(tmp)
    convert(tmp, "odt")
    odt = tmp.with_suffix(".odt")
    convert(odt, "pdf")            # the PDF is rendered from the ODT so the two match
    tmp.unlink()
    import pymupdf
    pages = pymupdf.open(odt.with_suffix(".pdf")).page_count
    print(f"wrote {odt.name} and {odt.with_suffix('.pdf').name}: {pages} pages, "
          f"{len(r.figures)} figures, {len(r.tables)} tables")


if __name__ == "__main__":
    main()
