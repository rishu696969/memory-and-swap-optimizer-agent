"""SUPPORTING MODULE: systemd services.

Read-only: `systemctl show <unit> --property=...` for any valid unit name.
Approved actions: restart / enable a unit on the allowlist via
"sudo -n systemctl restart|enable <unit>".  stop, disable, mask and arbitrary
unit files are intentionally not offered.
"""

from __future__ import annotations

from ..execution import runner


def parse_show(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def service_status(unit: str) -> dict:
    try:
        result = runner.run_command("systemctl_show", [unit])
    except runner.CommandUnavailable as exc:
        return {"service": unit, "available": False, "error": str(exc)}
    props = parse_show(result.stdout)
    hint = ""
    if not result.ok:
        hint = "systemctl failed; systemd may not be running (e.g. inside a container)."
    elif props.get("LoadState") == "not-found":
        hint = "The unit does not exist on this host."
    return {"service": unit, "available": result.ok, "properties": props,
            "stderr": result.stderr.strip()[:500], "hint": hint}


def describe_action(unit: str, action: str) -> tuple[str, dict]:
    status = service_status(unit)
    props = status.get("properties", {})
    summary = f"{action.capitalize()} systemd service '{unit}' (runs: sudo -n systemctl {action} {unit})."
    details = {"service": unit, "action": action, "active_state": props.get("ActiveState"),
               "unit_file_state": props.get("UnitFileState"),
               "warning": ("restart briefly interrupts the service" if action == "restart"
                           else "enable makes the service start at boot (a persistent change)")}
    return summary, details


def execute_action(unit: str, action: str, dry_run: bool) -> dict:
    key = {"restart": "systemctl_restart", "enable": "systemctl_enable"}[action]
    result = runner.run_command(key, [unit], dry_run=dry_run)
    after = service_status(unit) if not result.dry_run else None
    return {"service": unit, "action": action, "success": result.ok, "executed": not result.dry_run,
            "command": result.as_dict(), "status_after": after}
