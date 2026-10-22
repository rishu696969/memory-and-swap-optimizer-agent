"""Supplementary regex screen for obviously destructive command text.

IMPORTANT: this is *not* the main security boundary.  The real boundary is that
the model can only call fixed tools whose arguments are validated and mapped to
fixed executables (see validation.py and execution/runner.py).  This screen is a
second net that catches shell metacharacters or well-known destructive patterns
if a bug ever let such text reach the command runner.
"""

from __future__ import annotations

import re

DESTRUCTIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("recursive delete", re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+")),
    ("filesystem format", re.compile(r"\bmkfs(\.\w+)?\b")),
    ("raw disk copy", re.compile(r"\bdd\s+.*\bif=")),
    ("fork bomb", re.compile(r":\s*\(\s*\)\s*\{")),
    ("raw disk write", re.compile(r">\s*/dev/(sd|nvme|hd|vd|xvd|mmcblk)")),
    ("swap disable", re.compile(r"\bswapoff\b")),
    ("process kill", re.compile(r"\b(kill|pkill|killall)\b")),
    ("shutdown", re.compile(r"\b(shutdown|reboot|poweroff|halt)\b")),
    ("persistent sysctl", re.compile(r"/etc/sysctl")),
    ("permission change on root", re.compile(r"\bchmod\s+(-R\s+)?[0-7]{3,4}\s+/(\s|$)")),
]

# Shell metacharacters have no meaning with shell=False, so their presence in an
# argument means something tried to smuggle a shell command through.
SHELL_META_RE = re.compile(r"[;&|`$<>\\\n\r]|\$\(")


def screen_text(text: str) -> str | None:
    """Return a reason string if *text* matches a destructive pattern, else None."""
    for reason, pattern in DESTRUCTIVE_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def screen_argv(argv: list[str]) -> str | None:
    """Screen an argument vector.  Each argument is checked on its own and joined."""
    for arg in argv:
        if SHELL_META_RE.search(arg):
            return f"shell metacharacter in argument {arg!r}"
    return screen_text(" ".join(argv))
