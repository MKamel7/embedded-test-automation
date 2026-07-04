"""Characterize the simulated controller across parameter sweeps.

Unlike the pytest suite (which runs a handful of fixed scenarios once per
CI run), this script sweeps controller parameters over many fresh
MotorControllerSim() instances to trace out the actual response curves:
settling time and thermal load vs. commanded speed, and watchdog trip
latency vs. configured budget. Each data point gets its own fresh sim so
sweeps never carry state between points.

Writes measurements/characterization.csv (columns: sweep_param,
param_value, metric, value, unit). scripts/plot_characterization.py then
renders that into docs/characterization.png.

    uv run python scripts/characterize.py
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dut_sim.motor_controller import MotorControllerSim  # noqa: E402

OUTPUT_PATH = ROOT / "measurements" / "characterization.csv"

CSV_FIELDS = ["sweep_param", "param_value", "metric", "value", "unit"]

# 500..6000 rpm in 500rpm steps -> 12 points.
SPEED_SWEEP_RPM = list(range(500, 6001, 500))

# Watchdog step budgets to sweep. Chosen to span roughly an order of
# magnitude while staying well inside WDG_MIN_STEPS..WDG_MAX_STEPS.
WATCHDOG_BUDGETS = [2, 5, 10, 20, 50, 100, 200]

SETTLING_BAND_RPM = 50
SETTLING_CAP_STEPS = 300
THERMAL_SOAK_STEPS = 150
WATCHDOG_TRIP_CAP_STEPS = 1000


def measure_settling_steps(target_rpm: float) -> int:
    """Steps for a fresh sim to converge within SETTLING_BAND_RPM of target."""
    sim = MotorControllerSim()
    sim.target_rpm = target_rpm
    sim.state = "RUNNING"
    steps = SETTLING_CAP_STEPS
    for i in range(1, SETTLING_CAP_STEPS + 1):
        sim.step()
        if abs(sim.speed_rpm - target_rpm) < SETTLING_BAND_RPM:
            steps = i
            break
    return steps


def measure_peak_temperature(target_rpm: float) -> float:
    """Peak winding temperature over a sustained run at target_rpm."""
    sim = MotorControllerSim()
    sim.target_rpm = target_rpm
    sim.state = "RUNNING"
    peak = sim.temperature_c
    for _ in range(THERMAL_SOAK_STEPS):
        sim.step()
        peak = max(peak, sim.temperature_c)
    return peak


def measure_watchdog_latency(budget_steps: int) -> int:
    """Steps from WDG_EN to a latched FAULT, for a given step budget."""
    sim = MotorControllerSim()
    reply = sim.handle_command(f"WDG_EN {budget_steps}")
    assert reply == "OK", f"WDG_EN {budget_steps} rejected: {reply}"
    steps = WATCHDOG_TRIP_CAP_STEPS
    for i in range(1, WATCHDOG_TRIP_CAP_STEPS + 1):
        sim.step()
        if sim.state == "FAULT":
            steps = i
            break
    return steps


def main() -> None:
    rows = []

    for target_rpm in SPEED_SWEEP_RPM:
        settling_steps = measure_settling_steps(target_rpm)
        rows.append(("target_speed_rpm", target_rpm, "settling_steps", settling_steps, "steps"))

    for target_rpm in SPEED_SWEEP_RPM:
        peak_temp = measure_peak_temperature(target_rpm)
        rows.append(("target_speed_rpm", target_rpm, "peak_temperature", peak_temp, "degC"))

    for budget_steps in WATCHDOG_BUDGETS:
        latency = measure_watchdog_latency(budget_steps)
        rows.append(("watchdog_budget_steps", budget_steps, "watchdog_trip_latency", latency, "steps"))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        writer.writerows(rows)

    print(f"Wrote {OUTPUT_PATH} with {len(rows)} rows across 3 sweeps.")


if __name__ == "__main__":
    main()
