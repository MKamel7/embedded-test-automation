"""Read measurements/characterization.csv and plot each parameter sweep.

Each CSV row is (sweep_param, param_value, metric, value, unit), written by
scripts/characterize.py, which sweeps controller parameters over many fresh
MotorControllerSim() instances (settling time and thermal load vs. target
speed, watchdog trip latency vs. configured budget). This renders one
small-multiple panel per (sweep_param, metric) pair so the different sweeps
and units never share an axis.

    uv run python scripts/plot_characterization.py
"""

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).parent.parent
CSV_PATH = ROOT / "measurements" / "characterization.csv"
OUTPUT_PATH = ROOT / "docs" / "characterization.png"

# Chart chrome, matching scripts/plot_trends.py's reference palette (light surface).
COLOR_SERIES = "#2a78d6"
COLOR_SURFACE = "#fcfcfb"
COLOR_PRIMARY_INK = "#0b0b0b"
COLOR_SECONDARY_INK = "#52514e"
COLOR_MUTED = "#898781"
COLOR_GRID = "#e1e0d9"
COLOR_AXIS = "#c3c2b7"

# Human-readable axis labels for each sweep parameter.
SWEEP_LABELS = {
    "target_speed_rpm": "Target speed (rpm)",
    "watchdog_budget_steps": "Watchdog budget (steps)",
}

# Human-readable panel titles for each metric.
METRIC_TITLES = {
    "settling_steps": "Settling time to ±50rpm",
    "peak_temperature": "Peak winding temperature (150-step soak)",
    "watchdog_trip_latency": "Watchdog trip latency",
}


def load_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def group_by_panel(rows: list[dict]) -> dict:
    """(sweep_param, metric) -> sorted [(param_value, value, unit), ...]."""
    panels = defaultdict(list)
    for row in rows:
        key = (row["sweep_param"], row["metric"])
        panels[key].append((float(row["param_value"]), float(row["value"]), row["unit"]))
    for points in panels.values():
        points.sort(key=lambda p: p[0])
    return panels


def plot(panels: dict, output_path: Path) -> None:
    keys = sorted(panels)
    fig, axes = plt.subplots(len(keys), 1, figsize=(8, 2.8 * len(keys)), facecolor=COLOR_SURFACE)
    if len(keys) == 1:
        axes = [axes]

    for ax, key in zip(axes, keys):
        sweep_param, metric = key
        points = panels[key]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        unit = points[0][2]

        ax.set_facecolor(COLOR_SURFACE)
        ax.plot(
            xs,
            ys,
            color=COLOR_SERIES,
            linewidth=2,
            marker="o",
            markersize=6,
            markerfacecolor=COLOR_SERIES,
            markeredgecolor=COLOR_SURFACE,
        )
        ax.set_title(
            METRIC_TITLES.get(metric, metric), color=COLOR_PRIMARY_INK, fontsize=11, loc="left"
        )
        ax.set_xlabel(SWEEP_LABELS.get(sweep_param, sweep_param), color=COLOR_SECONDARY_INK, fontsize=9)
        ax.set_ylabel(unit, color=COLOR_SECONDARY_INK, fontsize=9)
        ax.tick_params(colors=COLOR_MUTED, labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(COLOR_AXIS)
        ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.8)
        ax.set_axisbelow(True)

    fig.suptitle(
        "Controller characterization: parameter sweeps", color=COLOR_PRIMARY_INK, fontsize=13
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, facecolor=COLOR_SURFACE)
    plt.close(fig)


def main() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"{CSV_PATH} not found - run scripts/characterize.py first.")
    rows = load_rows(CSV_PATH)
    if not rows:
        raise SystemExit(f"No rows found in {CSV_PATH}")
    panels = group_by_panel(rows)
    plot(panels, OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH} from {len(rows)} rows across {len(panels)} panels.")


if __name__ == "__main__":
    main()
