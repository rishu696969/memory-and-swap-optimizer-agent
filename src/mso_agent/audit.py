"""Append-only JSON-lines audit log.

Every tool call, validation failure, approval decision and result is recorded
with a UTC timestamp.  Before anything is written, values whose key looks like
a secret (api_key, token, password, ...) and strings that look like API keys
are replaced with "[REDACTED]".
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|passwd|authorization|approval_key)", re.I)
# Strings that look like credentials: OpenAI-style keys, bearer tokens, long hex HMACs.
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|\b[0-9a-f]{48,}\b)")
MAX_FIELD_CHARS = 2000


def redact(value: Any, _key: str = "") -> Any:
    """Return a copy of *value* with secrets removed (recursive)."""
    if _key and SECRET_KEY_RE.search(_key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        text = SECRET_VALUE_RE.sub("[REDACTED]", value)
        if len(text) > MAX_FIELD_CHARS:
            text = text[:MAX_FIELD_CHARS] + f"...[truncated {len(text) - MAX_FIELD_CHARS} chars]"
        return text
    return value


class AuditLogger:
    """Thread-safe JSONL writer.  One instance per process."""

    def __init__(self, path: str | os.PathLike[str], component: str):
        self.path = Path(path)
        self.component = component
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "component": self.component,
            "pid": os.getpid(),
            "event": event,
            **redact(fields),
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            # O_APPEND keeps concurrent writers (client + server) from interleaving lines.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(line) for line in lines if line.strip()]
