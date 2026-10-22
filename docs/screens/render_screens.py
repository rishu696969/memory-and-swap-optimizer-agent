"""Render terminal-style images from the CAPTURED transcripts in ./evidence.

The text is copied verbatim from the evidence files (long lines are wrapped,
omitted parts are marked "[...]"); only the visual styling is added.

    python docs/screens/render_screens.py      (needs matplotlib)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EV = ROOT / "evidence"
OUT = Path(__file__).resolve().parent
WIDTH = 88  # characters per line
FONT = 8.0  # points; the image is sized so it prints at roughly this size on A4


def cut(rel: str, start: str, end: str | None = None, max_lines: int = 40, skip: int = 0) -> list[str]:
    lines = (EV / rel).read_text().splitlines()
    i = next(n for n, x in enumerate(lines) if start in x) + skip
    out = []
    for x in lines[i:]:
        if end and out and end in x:
            break
        out.append(x)
    return out[:max_lines]


def render(name: str, title: str, lines: list[str]) -> None:
    wrapped: list[str] = []
    for x in lines:
        wrapped.extend(textwrap.wrap(x, WIDTH, subsequent_indent="    ", drop_whitespace=False) or [""])
    n = len(wrapped)
    line_h = 0.165
    h = 0.55 + n * line_h + 0.25
    w = WIDTH * 0.6 * FONT / 72 + 0.5     # monospace glyphs are ~0.6 em wide
    fig = plt.figure(figsize=(w, h), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.04, 0.04), w - 0.08, h - 0.08, boxstyle="round,pad=0,rounding_size=0.12",
                                fc="#1E1E1E", ec="#555555", lw=1))
    ax.add_patch(FancyBboxPatch((0.04, h - 0.46), w - 0.08, 0.42, boxstyle="round,pad=0,rounding_size=0.12",
                                fc="#3A3A3A", ec="none"))
    for k, c in enumerate(("#FF5F56", "#FFBD2E", "#27C93F")):
        ax.add_patch(plt.Circle((0.3 + k * 0.22, h - 0.25), 0.065, color=c))
    ax.text(w / 2, h - 0.25, title, color="#DDDDDD", fontsize=7, ha="center", va="center", family="DejaVu Sans")
    y = h - 0.68
    for x in wrapped:
        color = "#E6E6E6"
        s = x.strip()
        if s.startswith("mso>"):
            color = "#7FD1FF"
        elif "ERROR" in s or "FAILED" in s or "rejected" in s or "blocked" in s:
            color = "#FF8A80"
        elif "APPROVAL REQUIRED" in s or s.startswith("Type 'yes'"):
            color = "#FFD166"
        elif "EXECUTED" in s or "SUSPECTED" in s or "passed" in s or "PASSED" in s:
            color = "#9BE89B"
        elif s.startswith("[demo routing"):
            color = "#B0B0B0"
        ax.text(0.22, y, x, color=color, fontsize=FONT, family="DejaVu Sans Mono", va="center")
        y -= line_h
    fig.savefig(OUT / f"{name}.png", dpi=200, facecolor="white")
    plt.close(fig)
    print("wrote", name, n, "lines")


SCREENS = [
    ("screen1_lagging", "evidence/transcripts/01_memory_diagnosis.txt  (Ubuntu 24.04 container, user student)",
     lambda: cut("transcripts/01_memory_diagnosis.txt", "Memory and Swap Optimizer Agent v", "mso> show the top")),
    ("screen2_leak", "evidence/transcripts/02_leak_detection.txt",
     lambda: cut("transcripts/02_leak_detection.txt", "mso> check pid", "mso> check pid 1 ")),
    ("screen3_approval", "evidence/transcripts/03_swappiness_approval.txt",
     lambda: cut("transcripts/03_swappiness_approval.txt", "mso> set swappiness to 30", "mso> set swappiness to 30")),
    ("screen4_live", "evidence/live_privileged/07_live_privileged.txt  (privileged container, narrow sudoers rule)",
     lambda: cut("live_privileged/07_live_privileged.txt", "Type 'yes' to approve", "mso> set swappiness to 60", 30)
     + ["[...]"] + cut("live_privileged/07_live_privileged.txt", "Change drop_caches", "mso> /quit")),
    ("screen5_integrity", "evidence/transcripts/04b_file_integrity_after_changes.txt",
     lambda: cut("transcripts/04b_file_integrity_after_changes.txt", "mso> verify", "mso> create")),
    ("screen6_security", "evidence/transcripts/06_security_attempts.txt",
     lambda: cut("transcripts/06_security_attempts.txt", "mso> /call apply_approved_change", "mso> /call request_drop_caches")),
    ("screen8_destructive", "evidence/transcripts/09_destructive_requests.txt",
     lambda: cut("transcripts/09_destructive_requests.txt", "mso> Delete everything", "mso> /quit")),
    ("screen7_tests", "evidence/test_results/pytest_linux.txt  (Ubuntu 24.04 container)",
     lambda: cut("test_results/pytest_linux.txt", "test session starts", None, 9) + ["[... 165 more test lines ...]"]
     + cut("test_results/pytest_linux.txt", "test_bad_directories[/proc]", None, 12)),
]

if __name__ == "__main__":
    for name, title, fn in SCREENS:
        render(name, title, fn())
