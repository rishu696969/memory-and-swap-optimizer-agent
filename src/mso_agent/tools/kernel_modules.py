"""SUPPORTING MODULE: kernel modules.

Read-only: list loaded modules from /proc/modules, show `modinfo <name>`.
Approved actions: load / unload / reload a module that is on the allowlist
(default: uvcvideo, snd_dummy, dummy) via "sudo -n modprobe [-r] <name>".
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..execution import runner
from . import memory

PROC_MODULES = "/proc/modules"


class KernelModule(BaseModel):
    name: str
    size_bytes: int
    used_by_count: int | None = Field(description="reference count; None when the kernel prints '-'")
    used_by: list[str]
    state: str


class ModuleList(BaseModel):
    available: bool
    count: int
    modules: list[KernelModule]
    note: str = ""


def parse_proc_modules(text: str) -> list[KernelModule]:
    mods = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        used_by = [u for u in parts[3].strip(",").split(",") if u and u != "-"]
        # The kernel prints '-' as the reference count when module unloading is not supported.
        refcount = int(parts[2]) if parts[2].isdigit() else None
        mods.append(KernelModule(name=parts[0], size_bytes=int(parts[1]), used_by_count=refcount,
                                 used_by=used_by, state=parts[4]))
    return mods


def list_modules(name_filter: str | None = None) -> ModuleList:
    text = memory._read(PROC_MODULES)
    if text is None:
        return ModuleList(available=False, count=0, modules=[], note="/proc/modules is not readable on this host")
    mods = parse_proc_modules(text)
    if name_filter:
        mods = [m for m in mods if name_filter.lower() in m.name.lower()]
    note = "" if mods or name_filter else "No loadable modules listed (e.g. a container or a kernel without modules)."
    return ModuleList(available=True, count=len(mods), modules=mods[:200], note=note)


def module_info(name: str) -> dict:
    result = runner.run_command("modinfo", [name])
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            if k in {"filename", "description", "author", "license", "version", "depends", "alias"} and k not in fields:
                fields[k] = v.strip()
    return {"module": name, "found": result.ok, "fields": fields,
            "stderr": result.stderr.strip()[:500], "returncode": result.returncode}


def describe_action(module: str, action: str) -> tuple[str, dict]:
    loaded = any(m.name == module for m in list_modules().modules)
    cmds = {"load": [f"sudo -n modprobe {module}"], "unload": [f"sudo -n modprobe -r {module}"],
            "reload": [f"sudo -n modprobe -r {module}", f"sudo -n modprobe {module}"]}[action]
    summary = f"{action.capitalize()} kernel module '{module}' (currently {'loaded' if loaded else 'not loaded'})."
    return summary, {"module": module, "action": action, "currently_loaded": loaded, "commands": cmds,
                     "warning": "Unloading a driver disconnects any device using it until it is loaded again."}


def execute_action(module: str, action: str, dry_run: bool) -> dict:
    steps = []
    if action in ("unload", "reload"):
        steps.append(runner.run_command("modprobe_remove", [module], dry_run=dry_run))
    if action in ("load", "reload") and (not steps or steps[-1].ok):
        steps.append(runner.run_command("modprobe_load", [module], dry_run=dry_run))
    loaded_after = any(m.name == module for m in list_modules().modules)
    return {"module": module, "action": action, "success": all(s.ok for s in steps),
            "executed": not any(s.dry_run for s in steps),
            "steps": [s.as_dict() for s in steps], "loaded_after": loaded_after}
