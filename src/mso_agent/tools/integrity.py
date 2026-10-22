"""SUPPORTING MODULE: file integrity with SHA-256 baselines (hashlib).

* create_baseline(dir)  - hash every regular file under an allowed directory and
                          store {relative path: sha256, size} as JSON in the
                          agent's data directory (state-changing -> needs approval).
* verify_baseline(dir)  - re-hash and report modified, missing and new files
                          (read-only).
Symbolic links are never followed (a link could point outside the allowed root);
they are listed as skipped.  Change from Review 2: the baseline is stored in the
agent data directory instead of a hidden file inside the monitored directory,
so the act of baselining does not itself modify the monitored tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from ..security import policy


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(policy.HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_directory(root: Path, max_files: int = policy.MAX_FILES) -> tuple[dict[str, dict], list[str]]:
    """Return ({relpath: {sha256, size}}, skipped) for regular files under root."""
    files: dict[str, dict] = {}
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                skipped.append(os.path.relpath(os.path.join(dirpath, d), root) + " (symlink dir)")
                dirnames.remove(d)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                st = os.lstat(full)
            except OSError as exc:
                skipped.append(f"{rel} ({exc.strerror})")
                continue
            if not stat.S_ISREG(st.st_mode):
                skipped.append(f"{rel} (not a regular file)")
                continue
            if st.st_size > policy.MAX_FILE_BYTES:
                skipped.append(f"{rel} (larger than limit)")
                continue
            if len(files) >= max_files:
                raise ValueError(f"more than {max_files} files; choose a smaller directory")
            try:
                files[rel] = {"sha256": sha256_file(full), "size": st.st_size}
            except OSError as exc:
                skipped.append(f"{rel} ({exc.strerror})")
    return files, skipped


def compare(baseline: dict[str, dict], current: dict[str, dict]) -> dict[str, list[str]]:
    """Pure comparison used by verify_baseline and the unit tests."""
    modified = sorted(p for p in baseline.keys() & current.keys() if baseline[p]["sha256"] != current[p]["sha256"])
    missing = sorted(baseline.keys() - current.keys())
    new = sorted(current.keys() - baseline.keys())
    return {"modified": modified, "missing": missing, "new": new}


def baseline_path(store: Path, root: Path) -> Path:
    key = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return store / f"{key}.json"


class BaselineInfo(BaseModel):
    directory: str
    created_at: str
    file_count: int
    store_file: str


class VerifyReport(BaseModel):
    directory: str
    baseline_created_at: str
    files_checked: int
    modified: list[str]
    missing: list[str]
    new: list[str]
    unchanged_count: int
    skipped: list[str]
    status: str


def describe_baseline(root: Path, store: Path) -> tuple[str, dict]:
    existing = baseline_path(store, root)
    details = {"directory": str(root), "store_file": str(existing)}
    if existing.exists():
        old = json.loads(existing.read_text())
        details["replaces_baseline_created_at"] = old.get("created_at")
        summary = f"REPLACE the existing SHA-256 baseline for {root} (created {old.get('created_at')})."
    else:
        summary = f"Create a new SHA-256 baseline for {root}."
    return summary, details


def create_baseline(root: Path, store: Path) -> dict:
    files, skipped = scan_directory(root)
    store.mkdir(parents=True, exist_ok=True)
    out = baseline_path(store, root)
    record = {
        "directory": str(root),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "algorithm": "sha256",
        "files": files,
    }
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True))
    os.replace(tmp, out)            # atomic replace: no half-written baseline
    return {"directory": str(root), "file_count": len(files), "skipped": skipped,
            "store_file": str(out), "created_at": record["created_at"]}


def verify_baseline(root: Path, store: Path) -> VerifyReport:
    path = baseline_path(store, root)
    if not path.exists():
        raise FileNotFoundError(f"no baseline exists for {root}; request one with request_baseline_creation")
    record = json.loads(path.read_text())
    current, skipped = scan_directory(root)
    diff = compare(record["files"], current)
    changed = len(diff["modified"]) + len(diff["missing"]) + len(diff["new"])
    return VerifyReport(
        directory=str(root), baseline_created_at=record["created_at"], files_checked=len(current),
        modified=diff["modified"], missing=diff["missing"], new=diff["new"],
        unchanged_count=len(record["files"].keys() & current.keys()) - len(diff["modified"]),
        skipped=skipped,
        status="unchanged" if changed == 0 else f"{changed} change(s) detected",
    )


def list_baselines(store: Path) -> list[BaselineInfo]:
    out = []
    if store.is_dir():
        for f in sorted(store.glob("*.json")):
            try:
                rec = json.loads(f.read_text())
                out.append(BaselineInfo(directory=rec["directory"], created_at=rec["created_at"],
                                        file_count=len(rec["files"]), store_file=str(f)))
            except (OSError, ValueError, KeyError):
                continue
    return out
