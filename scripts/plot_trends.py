"""Read every measurements/*.csv and plot each metric's trend across runs.

Each CSV row is (run_timestamp, test, metric, value, unit), written by
MeasurementLog.flush (see src/testbench/measurements.py). This script has no
dependency on any one run - it aggregates whatever CSVs exist under
measurements/, sorts by run_timestamp, and renders one small-multiple panel
per metric so wildly different units (steps vs degC) never share an axis.

    uv run --group dev python scripts/plot_trends.py
"""

import csv
import glob
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).parent.parent
MEASUREMENTS_DIR = ROOT / "measurements"
OUTPUT_PATH = ROOT / "docs" / "trends.png"

TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

# Chart chrome, from the repo's reference palette (light surface).
COLOR_SERIES = "#2a78d6"
COLOR_SURFACE = "#fcfcfb"
COLOR_PRIMARY_INK = "#0b0b0b"
COLOR_SECONDARY_INK = "#52514e"
COLOR_MUTED = "#898781"
COLOR_GRID = "#e1e0d9"
COLOR_AXIS = "#c3c2b7"


def load_rows(measurements_dir: Path) -> list[dict]:
    """Read and concatenate every CSV under measurements_dir."""
    rows = []
    for csv_path in sorted(glob.glob(str(measurements_dir / "*.csv"))):
        with open(csv_path, newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def group_by_metric(rows: list[dict]) -> dict:
    """metric -> test -> sorted [(timestamp, value, unit), ...]."""
    series = defaultdict(lambda: defaultdict(list))
    for row in rows:
        ts = datetime.strptime(row["run_timestamp"], TIMESTAMP_FORMAT)
        series[row["metric"]][row["test"]].append((ts, float(row["value"]), row["unit"]))
    for tests in series.values():
        for points in tests.values():
            points.sort(key=lambda p: p[0])
    return series


def plot(series: dict, output_path: Path) -> None:
    metrics = sorted(series)
    fig, axes = plt.subplots(
        len(metrics), 1, figsize=(8, 2.6 * len(metrics)), facecolor=COLOR_SURFACE
    )
    if len(metrics) == 1:
        axes = [axes]

    for ax, metric in zip(axes, metrics):
        ax.set_facecolor(COLOR_SURFACE)
        unit = ""
        for test, points in series[metric].items():
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            unit = points[0][2]
            ax.plot(
                xs,
                ys,
                color=COLOR_SERIES,
                linewidth=2,
                marker="o",
                markersize=6,
                markerfacecolor=COLOR_SERIES,
                markeredgecolor=COLOR_SURFACE,
                label=test,
            )
        ax.set_title(metric, color=COLOR_PRIMARY_INK, fontsize=11, loc="left")
        ax.set_ylabel(unit, color=COLOR_SECONDARY_INK, fontsize=9)
        ax.tick_params(colors=COLOR_MUTED, labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(COLOR_AXIS)
        ax.grid(True, axis="y", color=COLOR_GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        if len(series[metric]) > 1:
            ax.legend(fontsize=8, frameon=False, labelcolor=COLOR_SECONDARY_INK)

    fig.suptitle("Measurement trends across test runs", color=COLOR_PRIMARY_INK, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, facecolor=COLOR_SURFACE)
    plt.close(fig)


def main() -> None:
    rows = load_rows(MEASUREMENTS_DIR)
    if not rows:
        raise SystemExit(f"No measurement CSVs found in {MEASUREMENTS_DIR}")
    series = group_by_metric(rows)
    plot(series, OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH} from {len(rows)} measurement rows across {len(series)} metrics.")


if __name__ == "__main__":
    main()
