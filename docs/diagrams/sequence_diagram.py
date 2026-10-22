"""Tool-call sequence diagram for "set swappiness to 30" (editable source).

Render:  python docs/diagrams/sequence_diagram.py   -> tool_call_sequence.png / .svg
Only matplotlib is needed.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
LANES = ["User", "REPL /\nAgent loop", "LLM provider\n/ demo router", "MCP client", "MCP server\n+ validator",
         "Approval\nGate", "Runner ->\nLinux"]

# (from, to, label, style)  style: "call", "return", "self", "note"
STEPS = [
    (0, 1, '"set swappiness to 30"', "call"),
    (1, 2, "messages + 15 tool schemas", "call"),
    (2, 1, "request_swappiness_change(30)", "return"),
    (1, 3, "call_tool(...)", "call"),
    (3, 4, "JSON-RPC tools/call over stdio", "call"),
    (4, 4, "strict validation", "self"),
    (4, 5, "create(set_swappiness, {value:30})", "call"),
    (5, 4, "op_id, nonce (nothing run)", "return"),
    (4, 1, "status=approval_required, summary, restore hint", "return"),
    (1, 0, "exact action + arguments", "return"),
    (0, 1, "yes", "call"),
    (1, 1, "token = HMAC(key, op)", "self"),
    (1, 4, "apply_approved_change(op_id, token)  [host-only]", "call"),
    (4, 5, "consume(op_id, token)", "call"),
    (5, 5, "verify: 1 use, TTL", "self"),
    (5, 4, "verified operation", "return"),
    (4, 6, "sudo -n sysctl -w vm.swappiness=30", "call"),
    (6, 4, "exit code, bounded output", "return"),
    (4, 4, "re-read value, audit", "self"),
    (4, 1, "status=executed, before/after, restore command", "return"),
    (1, 2, "result as UNTRUSTED data", "call"),
    (2, 1, "final explanation text", "return"),
    (1, 0, "answer + tool steps", "return"),
]


def draw(out_base: Path) -> None:
    lane_w, step_h = 1.5, 0.42
    width = lane_w * len(LANES)
    height = step_h * (len(STEPS) + 2.4)
    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_xlim(0, width)
    ax.set_ylim(-height + 0.3, 1.2)
    ax.axis("off")
    xs = [lane_w * (i + 0.5) for i in range(len(LANES))]
    colors = ["#EEEEEE", "#DAE3F3", "#DAE3F3", "#DAE3F3", "#E2EFDA", "#FCE4D6", "#FFF2CC"]
    for x, name, c in zip(xs, LANES, colors):
        ax.add_patch(plt.Rectangle((x - 0.7, 0.25), 1.4, 0.85, facecolor=c, edgecolor="#333333", lw=1.1))
        ax.text(x, 0.675, name, ha="center", va="center", fontsize=12.5, fontweight="bold")
        ax.plot([x, x], [0.25, -height + 0.5], color="#999999", lw=1, ls="--", zorder=0)
    for i, (a, b, label, style) in enumerate(STEPS):
        y = -(i + 0.9) * step_h
        ax.text(0.02, y + 0.04, f"{i + 1}", fontsize=11, color="#777777", va="center")
        if style == "self":
            x = xs[a]
            ax.annotate("", xy=(x + 0.05, y - 0.16), xytext=(x + 0.05, y + 0.12),
                        arrowprops=dict(arrowstyle="-|>", connectionstyle="arc3,rad=-1.4", color="#1F3864", lw=1.1))
            # self-call text goes to whichever side has room
            right = a < 4
            ax.text(x + 0.32 if right else x - 0.12, y, label, fontsize=12.5, va="center",
                    ha="left" if right else "right",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9))
            continue
        ls = "-" if style == "call" else (0, (4, 3))
        color = "#1F3864" if style == "call" else "#548235"
        ax.annotate("", xy=(xs[b], y), xytext=(xs[a], y),
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=1.3, linestyle=ls))
        ax.text((xs[a] + xs[b]) / 2, y + 0.05, label, fontsize=12.5, ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.85))
    fig.tight_layout(pad=0.2)
    for ext in ("png", "svg"):
        fig.savefig(out_base.with_suffix("." + ext), dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    draw(HERE / "tool_call_sequence")
    print("wrote", HERE / "tool_call_sequence.png")
