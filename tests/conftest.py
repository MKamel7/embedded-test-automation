import pytest

from dut_sim.motor_controller import MotorControllerSim
from testbench.driver import MotorControllerDriver, SimTransport


@pytest.fixture()
def sim() -> MotorControllerSim:
    return MotorControllerSim()


@pytest.fixture()
def dut(sim) -> MotorControllerDriver:
    """Driver connected to a fresh simulated device."""
    return MotorControllerDriver(SimTransport(sim))


def settle(sim: MotorControllerSim, steps: int = 40) -> None:
    """Let the physics converge (helper used by behavior tests)."""
    sim.step(steps)
