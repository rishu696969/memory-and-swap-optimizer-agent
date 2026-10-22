"""Runtime configuration read from environment variables (and an optional .env file).

Only plain values live here.  Secrets such as OPENAI_API_KEY are read where they
are needed and are never written to logs or passed to the MCP server process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Load KEY=VALUE lines from a .env file without overriding real env vars.

    A tiny parser is used instead of an extra dependency.  Lines starting with
    '#' and blank lines are ignored; surrounding quotes are stripped.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "mso-agent"


@dataclass
class Settings:
    """All tunable settings with safe defaults."""

    # LLM provider: "demo" (deterministic routing, no model), "ollama" or "openai".
    provider: str = "ollama"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    openai_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 120.0

    # Where baselines and the audit log are stored.
    data_dir: Path = field(default_factory=_default_data_dir)
    audit_log: Path | None = None

    # File-integrity scope: directories must resolve inside one of these roots.
    allowed_roots: list[str] = field(default_factory=lambda: [str(Path.home())])

    # Allowlists for the supporting modules (tight by default).
    module_allowlist: list[str] = field(default_factory=lambda: ["uvcvideo", "snd_dummy", "dummy"])
    service_allowlist: list[str] = field(
        default_factory=lambda: ["cron.service", "nginx.service", "apache2.service", "postgresql.service"]
    )

    # When true, approved privileged commands are only printed, never executed.
    dry_run: bool = False
    # Seconds an approval request stays valid.
    approval_ttl_s: int = 300

    @property
    def audit_path(self) -> Path:
        return self.audit_log or (self.data_dir / "audit.jsonl")

    @property
    def baseline_dir(self) -> Path:
        return self.data_dir / "baselines"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    """Build Settings from MSO_* environment variables."""
    s = Settings()
    env = os.environ
    s.provider = env.get("MSO_PROVIDER", s.provider).strip().lower()
    s.ollama_host = env.get("OLLAMA_HOST", s.ollama_host)
    if not s.ollama_host.startswith(("http://", "https://")):
        s.ollama_host = "http://" + s.ollama_host
    s.ollama_model = env.get("MSO_OLLAMA_MODEL", s.ollama_model)
    s.openai_model = env.get("MSO_OPENAI_MODEL", s.openai_model)
    if env.get("MSO_LLM_TIMEOUT"):
        s.llm_timeout_s = float(env["MSO_LLM_TIMEOUT"])
    if env.get("MSO_DATA_DIR"):
        s.data_dir = Path(env["MSO_DATA_DIR"]).expanduser()
    if env.get("MSO_AUDIT_LOG"):
        s.audit_log = Path(env["MSO_AUDIT_LOG"]).expanduser()
    if env.get("MSO_ALLOWED_ROOTS"):
        s.allowed_roots = [str(Path(p).expanduser()) for p in _split_list(env["MSO_ALLOWED_ROOTS"])]
    if env.get("MSO_MODULE_ALLOWLIST") is not None:
        s.module_allowlist = _split_list(env["MSO_MODULE_ALLOWLIST"])
    if env.get("MSO_SERVICE_ALLOWLIST") is not None:
        s.service_allowlist = _split_list(env["MSO_SERVICE_ALLOWLIST"])
    s.dry_run = _truthy(env.get("MSO_DRY_RUN"))
    if env.get("MSO_APPROVAL_TTL"):
        s.approval_ttl_s = max(30, min(3600, int(env["MSO_APPROVAL_TTL"])))
    return s


# Environment variables forwarded to the MCP server subprocess.  API keys are
# deliberately absent: the tool server never needs them.
SERVER_ENV_KEYS = (
    "MSO_DATA_DIR",
    "MSO_AUDIT_LOG",
    "MSO_ALLOWED_ROOTS",
    "MSO_MODULE_ALLOWLIST",
    "MSO_SERVICE_ALLOWLIST",
    "MSO_DRY_RUN",
    "MSO_APPROVAL_TTL",
    "MSO_ALLOW_ROOT",
)
