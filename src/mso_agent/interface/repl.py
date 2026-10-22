"""Interface layer: the command-line REPL (Read-Eval-Print Loop).

It reads requests, prints answers and tool steps, and shows the approval
prompt for state-changing actions.  It holds no system logic itself.

    mso-agent                      # interactive, provider from MSO_PROVIDER (default ollama)
    mso-agent --provider demo      # deterministic demo routing, no model needed
    mso-agent --once "show swap info" --provider demo
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import textwrap

import anyio
import httpx

from .. import __version__
from ..audit import AuditLogger
from ..config import load_dotenv, load_settings
from ..orchestration.agent import Agent, AgentTurn
from ..orchestration.mcp_client import ToolServerConnection
from ..orchestration.providers import ToolCall, make_provider
from ..server import refuse_root

HELP = """Type a request in plain English, for example:
  my computer is lagging, free up some RAM cache and check for memory leaks
  show the top 5 processes by memory        show swap info
  set swappiness to 30                      create a baseline for ~/project/sample_data
  verify ~/project/sample_data              list kernel modules matching snd
  status of cron.service                    restart cron.service
REPL commands:
  /tools                 list MCP tools (host-only tools are marked)
  /call <tool> <json>    call one tool directly (same validation and approval path)
  /audit [n]             show the last n audit-log entries
  /help                  this text          /quit   exit"""

INTERACTIVE = sys.stdin.isatty()


async def read_line(prompt: str) -> str | None:
    def _read() -> str | None:
        try:
            line = input(prompt)
        except EOFError:
            return None
        if not INTERACTIVE:
            print(line)          # echo piped input so saved transcripts are readable
        return line
    return await anyio.to_thread.run_sync(_read)


async def ask_human(request: dict) -> bool:
    """Show the exact pending operation and require the word 'yes'."""
    print("\n" + "=" * 72)
    print("  APPROVAL REQUIRED - a state-changing action was requested")
    print("=" * 72)
    print(f"  operation id : {request['op_id']}")
    print(f"  action       : {request['action']}")
    print(f"  arguments    : {json.dumps(request['arguments'])}")
    print(textwrap.fill(f"  summary      : {request['summary']}", 72, subsequent_indent=" " * 17))
    for k, v in (request.get("details") or {}).items():
        print(textwrap.fill(f"  {k:<13}: {v}", 72, subsequent_indent=" " * 17))
    print(f"  expires in   : {request['expires_in_seconds']} s")
    print("=" * 72)
    answer = await read_line("Type 'yes' to approve this exact operation (anything else denies): ")
    return (answer or "").strip().lower() == "yes"


def print_turn(turn: AgentTurn) -> None:
    for s in turn.steps:
        mark = "ok" if s.ok else "ERROR"
        extra = f" [{s.approval}]" if s.approval else ""
        print(f"  -> tool {s.name}({json.dumps(s.arguments)}) : {mark}{extra}")
        if not s.ok and s.error:
            print(f"     {s.error[:300]}")
    print()
    print(turn.text)
    print()


async def check_provider(settings) -> str | None:
    """Return an error message if the configured provider cannot be used."""
    if settings.provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{settings.ollama_host}/api/tags")
                r.raise_for_status()
                names = [m.get("name", "") for m in r.json().get("models", [])]
        except (httpx.HTTPError, ValueError) as exc:
            return (f"Ollama is not reachable at {settings.ollama_host} ({exc.__class__.__name__}). "
                    "Start it with 'ollama serve', or run with --provider demo.")
        if not any(n == settings.ollama_model or n.split(":")[0] == settings.ollama_model for n in names):
            return (f"Ollama model '{settings.ollama_model}' is not pulled (have: {names}). "
                    f"Run 'ollama pull {settings.ollama_model}' or set MSO_OLLAMA_MODEL.")
    if settings.provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        return "OPENAI_API_KEY is not set. Put it in .env or the environment, or use --provider demo."
    return None


async def run(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.provider:
        settings.provider = args.provider
    if args.dry_run:
        os.environ["MSO_DRY_RUN"] = "1"
        settings.dry_run = True
    problem = await check_provider(settings)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    audit = AuditLogger(settings.audit_path, component="repl")
    provider = make_provider(settings)
    approval_key = secrets.token_bytes(32)       # lives only in this process and the server's env

    async with ToolServerConnection(approval_key) as conn:
        agent = Agent(conn, provider, ask_human, approval_key, audit)
        mode = (f"LLM provider: {provider.name} ({getattr(provider, 'model', '')})" if provider.is_llm
                else "DEMO ROUTING (deterministic keyword rules - no LLM inference)")
        print(f"Memory and Swap Optimizer Agent v{__version__}")
        print(f"{mode} | MCP tools: {len(conn.tools)} | dry-run: {settings.dry_run}")
        print(f"audit log: {settings.audit_path}")
        if args.once:
            print(f"\n> {args.once}")
            print_turn(await agent.handle(args.once))
            return 0
        print("Type /help for examples, /quit to exit.\n")
        while True:
            line = await read_line("mso> ")
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit", "quit", "exit"):
                break
            if line == "/help":
                print(HELP)
            elif line == "/tools":
                for t in conn.tools:
                    tag = "read-only" if t.read_only else ("HOST-ONLY" if t.name in
                                                          {"apply_approved_change", "cancel_pending_change"} else "needs approval")
                    print(f"  {t.name:<28} [{tag}] {t.description.splitlines()[0][:80]}")
            elif line.startswith("/audit"):
                parts = line.split()
                n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
                for rec in audit.tail(n):
                    print("  " + json.dumps(rec)[:200])
            elif line.startswith("/call "):
                _, _, rest = line.partition(" ")
                name, _, raw = rest.strip().partition(" ")
                try:
                    arguments = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError as exc:
                    print(f"  invalid JSON: {exc}")
                    continue
                step = await agent.execute_tool_call(ToolCall(id="manual", name=name, arguments=arguments))
                print(f"  -> {name}: {'ok' if step.ok else 'ERROR'}" + (f" [{step.approval}]" if step.approval else ""))
                print(textwrap.indent(json.dumps(step.data if step.ok else step.error, indent=2, default=str)[:4000], "  "))
            else:
                try:
                    print_turn(await agent.handle(line))
                except httpx.HTTPError as exc:
                    print(f"  LLM request failed: {exc}")
    return 0


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="mso-agent", description="Memory and Swap Optimizer Agent (MCP)")
    parser.add_argument("--provider", choices=["demo", "ollama", "openai"], help="override MSO_PROVIDER")
    parser.add_argument("--once", metavar="TEXT", help="handle one request and exit")
    parser.add_argument("--dry-run", action="store_true", help="approved privileged commands are printed, not run")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    refuse_root()
    raise SystemExit(anyio.run(run, args))


if __name__ == "__main__":
    main()
