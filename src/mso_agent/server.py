"""Tooling layer: the MCP server (official MCP Python SDK, stdio transport).

Run directly with:  python -m mso_agent.server   (the REPL starts it for you)

Every tool call passes through SecureMCPServer.call_tool(), which
  1. audits the raw request,
  2. applies strict validation (security/validation.py) -- unknown tools,
     unknown keys such as "approved", and wrong types are rejected here,
  3. runs the tool and audits the outcome.
State-changing tools are split in two: request_* tools only create a pending
operation; apply_approved_change executes it after verifying the human's HMAC
approval token (security/approval.py).  The model is never given a shell.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .audit import AuditLogger
from .config import load_settings
from .execution import runner
from .security import policy
from .security.approval import ApprovalError, ApprovalGate
from .security.validation import (
    ValidationError,
    validate_arguments,
    validate_directory,
    validate_module_name,
    validate_service_name,
)
from .tools import integrity, kernel_modules, memory, recommend, services, swap

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
REQUEST_ONLY = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
HOST_ONLY = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


class ApprovalRequest(BaseModel):
    status: Literal["approval_required"]
    op_id: str
    action: str
    arguments: dict[str, Any]
    nonce: str
    summary: str
    details: dict[str, Any]
    expires_in_seconds: int
    note: str


class ChangeResult(BaseModel):
    op_id: str
    action: str
    arguments: dict[str, Any]
    status: Literal["executed", "dry_run", "failed"]
    result: dict[str, Any]


class CancelResult(BaseModel):
    op_id: str
    cancelled: bool


class SecureMCPServer(MCPServer):
    """MCPServer with a strict validation + audit hook in front of every tool."""

    def __init__(self, name: str, audit: AuditLogger):
        super().__init__(name, version=__version__)
        self.audit = audit

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):  # type: ignore[override]
        self.audit.log("tool_call_received", tool=name, arguments=arguments)
        try:
            clean = validate_arguments(name, arguments)
        except ValidationError as exc:
            self.audit.log("tool_call_rejected", tool=name, reason=str(exc))
            raise ToolError(f"rejected by validator: {exc}") from None
        result = await super().call_tool(name, clean, context)
        self.audit.log("tool_call_finished", tool=name, is_error=bool(getattr(result, "is_error", False)))
        return result


def build_server() -> SecureMCPServer:
    settings = load_settings()
    audit = AuditLogger(settings.audit_path, component="mcp-server")
    key_hex = os.environ.get("MSO_APPROVAL_KEY", "")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        key = b""
    if len(key) < 16:
        # Fail closed: with a random key nobody can produce a valid approval token.
        key = secrets.token_bytes(32)
        audit.log("approval_key_missing", note="random key generated; approvals are impossible")
    gate = ApprovalGate(key, ttl_seconds=settings.approval_ttl_s)
    mcp = SecureMCPServer("memory-swap-optimizer", audit)

    def fail(exc: Exception) -> ToolError:
        return ToolError(str(exc))

    # ------------------------------------------------------------------ memory
    @mcp.tool(annotations=READ_ONLY)
    def get_memory_stats() -> memory.MemoryStats:
        """Report total, available, free and used RAM, buffers, page cache, shared memory, swap usage and a
        memory-pressure level (from MemAvailable and PSI). Use this first for any 'slow/lagging/RAM' question."""
        return memory.get_memory_stats()

    @mcp.tool(annotations=READ_ONLY)
    def get_top_memory_processes(
        limit: Annotated[int, Field(ge=1, le=policy.TOP_PROCESSES_MAX, description="how many processes")] = 10,
    ) -> memory.TopProcesses:
        """List the processes using the most resident memory (RSS)."""
        return memory.get_top_processes(limit)

    @mcp.tool(annotations=READ_ONLY)
    def sample_process_memory(
        pid: Annotated[int | None, Field(ge=1, description="process to sample; omit to sample the top_n largest")] = None,
        top_n: Annotated[int, Field(ge=1, le=5)] = 3,
        samples: Annotated[int, Field(ge=policy.SAMPLES_MIN, le=policy.SAMPLES_MAX)] = 6,
        interval_seconds: Annotated[float, Field(ge=policy.INTERVAL_MIN_S, le=policy.INTERVAL_MAX_S)] = 1.0,
        growth_threshold_mb: Annotated[float, Field(ge=1, le=4096)] = 10.0,
    ) -> memory.SamplingReport:
        """Sample process RSS repeatedly and flag SUSTAINED growth (a suspected leak, never proof of one).
        Use for 'check for memory leaks'."""
        if samples * interval_seconds > policy.SAMPLING_BUDGET_S:
            raise ToolError(f"samples x interval must be <= {policy.SAMPLING_BUDGET_S} s")
        if pid is not None:
            pids = [pid]
        else:
            mine = memory.own_pids()
            pids = [p.pid for p in memory.get_top_processes(top_n + len(mine)).processes if p.pid not in mine][:top_n]
        return memory.sample_processes(pids, samples, interval_seconds, growth_threshold_mb)

    @mcp.tool(annotations=READ_ONLY)
    def get_swap_info() -> swap.SwapInfo:
        """Show swap devices and usage, vm.swappiness, vfs_cache_pressure and cumulative swap-in/out counters."""
        return swap.get_swap_info()

    @mcp.tool(annotations=READ_ONLY)
    def recommend_memory_actions(
        window_seconds: Annotated[int, Field(ge=policy.WINDOW_MIN_S, le=policy.WINDOW_MAX_S)] = 2,
    ) -> recommend.RecommendationReport:
        """Measure memory, swap activity over a short window and top processes, then return evidence-based
        recommendations. Advisory only; changes nothing."""
        return recommend.recommend(window_seconds)

    @mcp.tool(annotations=REQUEST_ONLY)
    def request_swappiness_change(
        value: Annotated[int, Field(ge=policy.SWAPPINESS_MIN, le=policy.SWAPPINESS_MAX)],
    ) -> ApprovalRequest:
        """Ask the human to approve a TEMPORARY vm.swappiness change (10-100). Does not execute anything."""
        summary, details = swap.describe_swappiness_change(value)
        return ApprovalRequest(**gate.create("set_swappiness", {"value": value}, summary, details).public_view())

    @mcp.tool(annotations=REQUEST_ONLY)
    def request_drop_caches(level: Literal[1, 2, 3]) -> ApprovalRequest:
        """Ask the human to approve dropping clean caches (1=page cache, 2=dentries/inodes, 3=both).
        Rarely useful; prefer explaining that cache is reclaimable. Does not execute anything."""
        summary, details = swap.describe_drop_caches(level)
        return ApprovalRequest(**gate.create("drop_caches", {"level": level}, summary, details).public_view())

    # --------------------------------------------------------- file integrity
    @mcp.tool(annotations=READ_ONLY)
    def verify_file_baseline(directory: str) -> integrity.VerifyReport:
        """Re-hash a directory with SHA-256 and report modified, missing and new files against its baseline."""
        try:
            root = validate_directory(directory, settings.allowed_roots)
            return integrity.verify_baseline(root, settings.baseline_dir)
        except (ValidationError, FileNotFoundError, ValueError) as exc:
            raise fail(exc) from None

    @mcp.tool(annotations=READ_ONLY)
    def list_file_baselines() -> list[integrity.BaselineInfo]:
        """List directories that have a stored SHA-256 baseline."""
        return integrity.list_baselines(settings.baseline_dir)

    @mcp.tool(annotations=REQUEST_ONLY)
    def request_baseline_creation(directory: str) -> ApprovalRequest:
        """Ask the human to approve creating (or replacing) the SHA-256 baseline of a directory inside the
        allowed roots. Does not execute anything."""
        try:
            root = validate_directory(directory, settings.allowed_roots)
        except ValidationError as exc:
            raise fail(exc) from None
        summary, details = integrity.describe_baseline(root, settings.baseline_dir)
        return ApprovalRequest(**gate.create("create_baseline", {"directory": str(root)}, summary, details).public_view())

    # --------------------------------------------------------- kernel modules
    @mcp.tool(annotations=READ_ONLY)
    def list_kernel_modules(name_filter: str | None = None) -> kernel_modules.ModuleList:
        """List loaded kernel modules from /proc/modules, optionally filtered by a name substring."""
        return kernel_modules.list_modules(name_filter)

    @mcp.tool(annotations=READ_ONLY)
    def get_kernel_module_info(module: str) -> dict:
        """Show modinfo fields (description, filename, depends...) for one module name."""
        try:
            name = validate_module_name(module)
            return kernel_modules.module_info(name)
        except (ValidationError, runner.CommandRejected, runner.CommandUnavailable) as exc:
            raise fail(exc) from None

    @mcp.tool(annotations=REQUEST_ONLY)
    def request_module_action(module: str, action: Literal["load", "unload", "reload"]) -> ApprovalRequest:
        """Ask the human to approve loading/unloading/reloading an ALLOWLISTED kernel module.
        Does not execute anything."""
        try:
            name = validate_module_name(module, settings.module_allowlist)
        except ValidationError as exc:
            raise fail(exc) from None
        summary, details = kernel_modules.describe_action(name, action)
        return ApprovalRequest(**gate.create("module_action", {"module": name, "action": action},
                                             summary, details).public_view())

    # --------------------------------------------------------- systemd services
    @mcp.tool(annotations=READ_ONLY)
    def get_service_status(service: str) -> dict:
        """Show load/active/sub state and unit-file state of a systemd service."""
        try:
            return services.service_status(validate_service_name(service))
        except (ValidationError, runner.CommandRejected) as exc:
            raise fail(exc) from None

    @mcp.tool(annotations=REQUEST_ONLY)
    def request_service_action(service: str, action: Literal["restart", "enable"]) -> ApprovalRequest:
        """Ask the human to approve restarting or enabling an ALLOWLISTED systemd service.
        Does not execute anything."""
        try:
            unit = validate_service_name(service, settings.service_allowlist)
        except ValidationError as exc:
            raise fail(exc) from None
        summary, details = services.describe_action(unit, action)
        return ApprovalRequest(**gate.create("service_action", {"service": unit, "action": action},
                                             summary, details).public_view())

    # ------------------------------------------------- host-only approval tools
    @mcp.tool(annotations=HOST_ONLY)
    def apply_approved_change(op_id: str, approval_token: str) -> ChangeResult:
        """HOST ONLY. Execute a pending operation after verifying the human approval token."""
        try:
            op = gate.consume(op_id, approval_token)
        except ApprovalError as exc:
            audit.log("approval_rejected", op_id=op_id, reason=str(exc))
            raise ToolError(f"execution refused: {exc}") from None
        audit.log("approval_verified", op_id=op.op_id, action=op.action, arguments=op.args,
                  dry_run=settings.dry_run)
        try:
            result = _execute(op.action, op.args)
        except (runner.CommandRejected, runner.CommandUnavailable, ValidationError, ValueError, OSError) as exc:
            audit.log("change_failed", op_id=op.op_id, action=op.action, error=str(exc))
            return ChangeResult(op_id=op.op_id, action=op.action, arguments=op.args, status="failed",
                                result={"error": str(exc)})
        if result.get("executed") is False:
            status = "dry_run"
        else:
            status = "executed" if result.get("success", True) else "failed"
        audit.log("change_finished", op_id=op.op_id, action=op.action, status=status,
                  success=result.get("success"))
        return ChangeResult(op_id=op.op_id, action=op.action, arguments=op.args, status=status, result=result)

    @mcp.tool(annotations=HOST_ONLY)
    def cancel_pending_change(op_id: str) -> CancelResult:
        """HOST ONLY. Discard a pending operation the human declined."""
        cancelled = gate.cancel(op_id)
        audit.log("change_cancelled", op_id=op_id, cancelled=cancelled)
        return CancelResult(op_id=op_id, cancelled=cancelled)

    def _execute(action: str, args: dict[str, Any]) -> dict:
        """Fixed mapping from approved action name to implementation; args are re-validated."""
        dry = settings.dry_run
        if action == "set_swappiness":
            return swap.execute_swappiness_change(int(args["value"]), dry)
        if action == "drop_caches":
            return swap.execute_drop_caches(int(args["level"]), dry)
        if action == "create_baseline":
            root = validate_directory(args["directory"], settings.allowed_roots)
            # Writing the baseline is unprivileged, so it really runs even in dry-run mode.
            return {"success": True, "executed": True, **integrity.create_baseline(root, settings.baseline_dir)}
        if action == "module_action":
            name = validate_module_name(args["module"], settings.module_allowlist)
            return kernel_modules.execute_action(name, args["action"], dry)
        if action == "service_action":
            unit = validate_service_name(args["service"], settings.service_allowlist)
            return services.execute_action(unit, args["action"], dry)
        raise ValueError(f"unknown action {action!r}")

    audit.log("server_started", version=__version__, dry_run=settings.dry_run,
              allowed_roots=settings.allowed_roots, module_allowlist=settings.module_allowlist,
              service_allowlist=settings.service_allowlist)
    return mcp


def refuse_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0 and os.environ.get("MSO_ALLOW_ROOT") != "1":
        sys.stderr.write("mso-agent: refusing to run as root. Run as a normal user; privileged actions use "
                         "'sudo -n' per approved command.\n")
        raise SystemExit(2)


def main() -> None:
    refuse_root()
    # stdout carries the MCP protocol, so diagnostics go to a log file, not the terminal.
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING, filename=settings.data_dir / "server.log",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_server().run("stdio")


if __name__ == "__main__":
    main()
