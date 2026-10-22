# Memory and Swap Optimizer Agent

Operating System Lab project (BCSE303P), Slot L53+L54, Faculty: NAVEENKUMAR J

| S.No. | Name | Registration Number |
|---|---|---|
| 1 | Kumar Hrishikesh Ranjan | 24BDS0221 |
| 2 | Ashish Kumar | 24BKT059 |

A command-line agent for Linux. You type a request in plain English, and a language model chooses
from a fixed set of typed **MCP tools**. The tools run in a separate MCP server process.
**Memory and swap optimisation is the main module.** File integrity, kernel modules and systemd
services are supporting modules.

The model never gets a shell. Any action that changes system state, including creating a hash
baseline, runs only after a human approves that exact operation. The approval is checked
**inside the server, just before execution**, using an HMAC token that the model never sees.

> Full report: `report/Memory_and_Swap_Optimizer_Agent_Report.pdf` (editable `.docx` alongside).

---

## 1. What it does

| Module | Read-only tools | Approval-gated tools |
|---|---|---|
| **Memory and swap (primary)** | `get_memory_stats`, `get_top_memory_processes`, `sample_process_memory`, `get_swap_info`, `recommend_memory_actions` | `request_swappiness_change` (10–100, temporary), `request_drop_caches` (1/2/3) |
| File integrity (SHA-256) | `verify_file_baseline`, `list_file_baselines` | `request_baseline_creation` (create or replace) |
| Kernel modules | `list_kernel_modules`, `get_kernel_module_info` | `request_module_action` (load/unload/reload, allowlisted modules only) |
| systemd services | `get_service_status` | `request_service_action` (restart/enable, allowlisted units only) |
| Host-only (never offered to the model) | – | `apply_approved_change`, `cancel_pending_change` |

The agent deliberately does **not** kill processes, run `swapoff`, edit `/etc/sysctl.conf` or any
other persistent memory configuration, or claim that performance improved without before/after
measurements.

## 2. Requirements

* Ubuntu 22.04/24.04 or another Linux system with `/proc`. The demo and tests also run on macOS,
  but the memory figures there come from `psutil` instead of `/proc`.
* Python 3.10 or newer (tested with Python 3.12)
* Optional: [Ollama](https://ollama.com) for a local LLM, or an OpenAI API key
* Optional: Docker, which is used for the reproducible Ubuntu test environment

## 3. Installation (Ubuntu)

```bash
sudo apt install -y python3 python3-venv kmod procps
```

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

```bash
cp .env.example .env
```

## 4. Starting the agent

**Demo mode** needs no model and no API key. It uses **deterministic keyword routing, not LLM
inference**, and labels every answer that way.

```bash
mso-agent --provider demo
```

**Local LLM with Ollama** (the default provider):

```bash
ollama pull qwen2.5:3b
```

```bash
mso-agent --provider ollama
```

**OpenAI** (optional). Set `OPENAI_API_KEY` in `.env` first.

```bash
mso-agent --provider openai
```

Other useful forms:

```bash
mso-agent --provider demo --once "My computer is lagging. Free up some RAM cache and check for memory leaks."
```

```bash
mso-agent --provider demo --dry-run
```

`--dry-run` shows approved privileged commands without running them.

Example requests:

```
My computer is lagging. Free up some RAM cache and check for memory leaks.
show the top 5 processes by memory
check pid 1234 for memory leaks
show swap info and swappiness
set swappiness to 30
create a baseline for /home/<you>/project
verify /home/<you>/project
list kernel modules matching snd
status of cron.service
```

REPL commands: `/tools`, `/call <tool> <json>` (calls one tool through the same validation and
approval path), `/audit [n]`, `/help`, `/quit`.

### Privileged actions and sudo

The agent runs as a normal user and refuses to start as root. Approved privileged commands are
run as `sudo -n <fixed command>`. The `-n` flag means sudo never prompts, so the agent never reads
or stores a password. Without a sudo rule, an approved change fails safely with
`sudo: a password is required`.

To allow only the memory-module actions, you can install the narrow rule in
`docker/sudoers-mso-example`. Replace the user name first. **Do not grant broad passwordless sudo.**

```bash
sudo visudo -f /etc/sudoers.d/mso-agent
```

## 5. Demonstration

Leak-detection demo using a process that deliberately grows (1 MiB every 0.25 s, capped at 120 MiB):

```bash
python scripts/leaky_demo.py &
```

```bash
mso-agent --provider demo --once "check pid $! for memory leaks"
```

All scripted demo sessions (run on Linux):

```bash
scripts/run_demos.sh evidence/transcripts
```

## 6. Tests

```bash
pytest -v
```

Reproducible Ubuntu 24.04 run plus transcripts. This needs Docker. Adding `--live` also runs the
privileged swappiness/drop_caches check inside a disposable privileged container and restores the
original value.

```bash
scripts/collect_evidence.sh --live
```

## 7. Project layout

```
src/mso_agent/
  interface/repl.py          Interface layer: REPL and approval prompt
  orchestration/agent.py     Agent loop and approval round-trip (host side)
  orchestration/providers.py Ollama / OpenAI adapters
  orchestration/demo_router.py  DEMO ROUTING (deterministic, not an LLM)
  orchestration/mcp_client.py   MCP stdio client (official SDK)
  server.py                  MCP server: 17 typed tools, validation hook
  security/validation.py     strict argument validation, allowlists, path checks
  security/approval.py       HMAC approval gate (single use, expiry, exact scope)
  security/blacklist.py      supplementary regex screen
  security/policy.py         all bounds and limits in one place
  execution/runner.py        fixed executables, shell=False, timeout, output limit
  tools/memory.py swap.py recommend.py      primary module
  tools/integrity.py kernel_modules.py services.py   supporting modules
  audit.py                   JSON-lines audit log with secret redaction
tests/                       pytest suite (validation, approval bypass, injection,
                             timeouts, output limits, hashing, sampling, mocked privileged ops,
                             real MCP stdio integration)
scripts/                     demos, evidence collection, leaky demo process
docker/                      Ubuntu 24.04 Dockerfile, example narrow sudoers rule
docs/diagrams/               editable diagram sources (.dot, .py) and rendered PNG/SVG
evidence/                    real test results, transcripts, audit log, live check output
report/                      report (DOCX + PDF) and the script that builds it
reference/                   the Review 1 / Review 2 PDFs and Review 3 template supplied
```

## 8. Changes from the Review 2 design

* **No LangChain/LlamaIndex.** The agent uses a small hand-written loop with the official MCP SDK
  (`mcp` 2.x, `MCPServer`/`Client`). This makes it easier to follow in a viva.
* **Approval is enforced in the server**, not only in the REPL prompt. Two-phase tools are used
  (`request_*`, then the host-only `apply_approved_change`) with an HMAC token bound to the exact
  operation.
* **No raw `sudo` allowlist for the model.** Fixed command keys are mapped to absolute executables
  and run with `sudo -n`. An optional, narrow sudoers rule is provided.
* **`drop_caches` is kept but de-emphasised.** The recommender advises against it when the page
  cache is simply doing its job.
* **The file-integrity baseline is stored in the agent's data directory**, not as a hidden file
  inside the monitored directory. Creating a baseline also requires approval.
* `smem` is not used. RSS comes from `psutil`, and leak detection uses repeated RSS sampling.
* A **deterministic demo router** was added so the system can be shown without a model. It is
  clearly labelled as not being an LLM.

## 9. Verification status (summary)

See the report's verification matrix and `evidence/` for details.

* **Tested on Linux (Ubuntu 24.04 container, kernel 7.0 LinuxKit):** all read-only memory and swap
  tools, PSI, sampling, recommendations, SHA-256 baselines, `/proc/modules` listing, the approval
  workflow, all rejection paths, and the full test suite.
* **Tested live in a disposable privileged container with the narrow sudoers rule:**
  `vm.swappiness` change and restore, and `drop_caches`.
* **Tested with mocks only:** `modprobe` load/unload/reload and `systemctl restart/enable`. These
  are implemented, but not verified live: the container has no systemd and no loadable modules.
* **LLM providers:** the Ollama and OpenAI adapters are tested with mocked HTTP only. Live LLM inference
  was **not** verified for this submission, because the Ollama image could not be downloaded in the
  build environment in time and no OpenAI key was used. All demos use the clearly labelled demo router.
# memory-and-swap-optimizer-agent
