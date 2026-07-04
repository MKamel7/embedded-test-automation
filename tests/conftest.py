from pathlib import Path

import pytest

from dut_sim.motor_controller import MotorControllerSim
from testbench.driver import MotorControllerDriver, SimTransport
from testbench.measurements import MeasurementLog

MEASUREMENTS_DIR = Path(__file__).parent.parent / "measurements"


@pytest.fixture()
def sim() -> MotorControllerSim:
    return MotorControllerSim()


@pytest.fixture()
def dut(sim) -> MotorControllerDriver:
    """Driver connected to a fresh simulated device."""
    return MotorControllerDriver(SimTransport(sim))


@pytest.fixture(scope="session")
def measurements():
    """Collects metrics from instrumented tests, flushed to CSV once per run."""
    log = MeasurementLog()
    yield log
    log.flush(MEASUREMENTS_DIR / f"run-{log.run_timestamp}.csv")


def settle(sim: MotorControllerSim, steps: int = 40) -> None:
    """Let the physics converge (helper used by behavior tests)."""
    sim.step(steps)
