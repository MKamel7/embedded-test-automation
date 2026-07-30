"""Render the controller's state machine to docs/state_machine.png.

The protocol grammar is documented in the DUT's module docstring, but the
safety-relevant behaviour lives in the transitions: what drives the controller
into FAULT, and what is allowed to bring it out. That is the diagram a hazard
analysis starts from, so it is generated rather than drawn by hand and kept in
step with the implementation.

    uv run --group dev python scripts/plot_state_machine.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).parent.parent
OUTPUT_PATH = ROOT / "docs" / "state_machine.png"

COLOR_SURFACE = "#fcfcfb"
COLOR_PRIMARY_INK = "#0b0b0b"
COLOR_SECONDARY_INK = "#52514e"
COLOR_MUTED = "#898781"
COLOR_SAFE = "#2a78d6"
COLOR_FAULT = "#c0392b"
COLOR_EDGE = "#7d7c76"

# (name, centre x, centre y, colour, subtitle)
STATES = [
    ("IDLE", 1.7, 4.5, COLOR_SAFE, "motor stopped\ncommands accepted"),
    ("RUNNING", 6.6, 4.5, COLOR_SAFE, "tracking setpoint\nwindings heating"),
    ("FAULT", 4.15, 0.95, COLOR_FAULT, "SAFE STATE: motor stopped,\nspeed commands rejected"),
]

BOX_W, BOX_H = 2.5, 1.15

# (from, to, label, curvature, label offset x, label offset y)
# arc3 bows to the RIGHT of the direction of travel for a positive rad, so the
# two horizontal transitions take the SAME negative sign: IDLE to RUNNING then
# arcs above, and its opposite arcs below. Equal and opposite signs would stack
# them on the same path.
TRANSITIONS = [
    ("IDLE", "RUNNING", "SET_SPEED > 0", -0.30, 0.0, 1.02),
    ("RUNNING", "IDLE", "SET_SPEED 0  |  STOP", -0.30, 0.0, -1.02),
    ("RUNNING", "FAULT", "overheat (T >= 90 C)\nstall heating\nwatchdog timeout",
     -0.10, 1.70, 0.30),
    ("IDLE", "FAULT", "watchdog timeout\n(if enabled)", 0.10, -1.45, 0.55),
    ("FAULT", "IDLE", "RESET\n(only way out)", 0.30, 0.75, -0.30),
]


def _centre(name):
    for state, x, y, _, _ in STATES:
        if state == name:
            return x, y
    raise KeyError(name)


def draw() -> Path:
    fig, ax = plt.subplots(figsize=(9.6, 6.8))
    fig.patch.set_facecolor(COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    for name, x, y, colour, subtitle in STATES:
        ax.add_patch(FancyBboxPatch(
            (x - BOX_W / 2, y - BOX_H / 2), BOX_W, BOX_H,
            boxstyle="round,pad=0.06,rounding_size=0.16",
            linewidth=2.0, edgecolor=colour, facecolor="white", zorder=3))
        ax.text(x, y + 0.22, name, ha="center", va="center", zorder=4,
                fontsize=15, fontweight="bold", color=colour)
        ax.text(x, y - 0.26, subtitle, ha="center", va="center", zorder=4,
                fontsize=8, color=COLOR_SECONDARY_INK, linespacing=1.35)

    for src, dst, label, curve, dx, dy in TRANSITIONS:
        x0, y0 = _centre(src)
        x1, y1 = _centre(dst)
        ax.add_patch(FancyArrowPatch(
            (x0, y0), (x1, y1),
            connectionstyle=f"arc3,rad={curve}",
            arrowstyle="-|>", mutation_scale=16, linewidth=1.5,
            color=COLOR_EDGE, shrinkA=48, shrinkB=48, zorder=5))
        ax.text((x0 + x1) / 2 + dx, (y0 + y1) / 2 + dy, label,
                ha="center", va="center", fontsize=8.5,
                color=COLOR_SECONDARY_INK, linespacing=1.4, zorder=6,
                bbox={"boxstyle": "round,pad=0.28", "facecolor": COLOR_SURFACE,
                      "edgecolor": "none"})

    # FAULT latches: every other command is rejected while it is set.
    ax.annotate("all other commands -> ERR STATE\nstep() holds speed at 0",
                xy=(4.15, 0.10), ha="center", va="top", fontsize=8,
                color=COLOR_FAULT, linespacing=1.4)

    ax.set_title("Simulated motor controller: safety-relevant state machine",
                 fontsize=14, color=COLOR_PRIMARY_INK, pad=16)
    ax.text(4.15, 6.35,
            "FAULT is latched. It survives cooldown and every command except RESET,\n"
            "which is the property the fault-injection campaign has to verify.",
            ha="center", va="center", fontsize=9, color=COLOR_MUTED,
            linespacing=1.5)

    ax.set_xlim(-0.6, 8.9)
    ax.set_ylim(-0.8, 6.9)
    ax.axis("off")
    fig.tight_layout()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PATH, dpi=170, facecolor=COLOR_SURFACE)
    plt.close(fig)
    return OUTPUT_PATH


if __name__ == "__main__":
    print(f"wrote {draw()}")
