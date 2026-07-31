"""The second thermal channel, and the checks it makes possible.

A redundant sensor is only worth its cost if it is INDEPENDENT of the one it
backs up. These tests are written to attack that independence rather than to
demonstrate the happy path: a lying winding sensor is simulated by overriding the
sensor read, so the physics is untouched and the protection has no privileged
access to a truth a real controller would not have.

The false-positive tests matter as much as the detection ones. A protection
mechanism that fires during normal stopping gets disabled by whoever is on call,
and then it protects nothing.
"""

import pytest

from dut_sim.motor_controller import (
    AMBIENT_C,
    HOUSING_LIMIT_C,
    OVERHEAT_LIMIT_C,
    PLAUSIBILITY_MARGIN_C,
    MotorControllerSim,
)


class LyingWindingSensor(MotorControllerSim):
    """A controller whose winding sensor reports a fixed safe value.

    The lie lives in the SENSOR READ, not in the physics, which is the whole
    point: the winding really does get hot, and the controller really cannot see
    it. Editing `temperature_c` instead would simulate a cool motor rather than a
    broken sensor, and the protection would be right to stay quiet.
    """

    def __init__(self, reports_c: float = AMBIENT_C) -> None:
        super().__init__()
        self.reports_c = reports_c

    def read_winding_c(self) -> float:
        return self.reports_c


class DriftingWindingSensor(MotorControllerSim):
    """Reads low by a fixed offset. Harder than stuck: every value is plausible."""

    def __init__(self, offset_c: float = -60.0) -> None:
        super().__init__()
        self.offset_c = offset_c

    def read_winding_c(self) -> float:
        return self.temperature_c + self.offset_c


def _run_until_fault(sim: MotorControllerSim, rpm: int = 5000,
                     limit: int = 400) -> int | None:
    sim.handle_command(f"SET_SPEED {rpm}")
    sim.inject_stall(True)
    for step in range(1, limit + 1):
        sim.step(1)
        if sim.state == "FAULT":
            return step
    return None


# --- the channel exists and is readable --------------------------------------
def test_both_sensors_are_readable_over_the_protocol() -> None:
    sim = MotorControllerSim()
    assert sim.handle_command("GET_TEMP") == f"OK {AMBIENT_C:.1f}"
    assert sim.handle_command("GET_HOUSING_TEMP") == f"OK {AMBIENT_C:.1f}"


def test_the_trip_cause_is_reported() -> None:
    """SR-09's real requirement: a trip nobody can explain gets reset, not fixed."""
    sim = MotorControllerSim()
    assert sim.handle_command("GET_FAULT") == "OK NONE"
    _run_until_fault(sim)
    assert sim.handle_command("GET_FAULT") == "OK OVERTEMP_WINDING"


def test_reset_clears_both_the_second_node_and_the_cause() -> None:
    sim = MotorControllerSim()
    _run_until_fault(sim)
    assert sim.housing_temperature_c > AMBIENT_C
    sim.handle_command("RESET")
    assert sim.housing_temperature_c == AMBIENT_C
    assert sim.fault_reason is None


# --- the frame is a real second node, not a copy ------------------------------
def test_the_frame_lags_the_winding_and_never_leads_it_while_driving() -> None:
    """The invariant the plausibility check depends on.

    Heat is generated in the winding and flows outward, so while the machine is
    being driven the frame is the cooler node. If this ever stopped holding, the
    plausibility check would be resting on nothing.
    """
    sim = MotorControllerSim()
    sim.handle_command("SET_SPEED 4000")
    for _ in range(200):
        sim.step(1)
        if sim.state == "FAULT":
            break
        assert sim.housing_temperature_c <= sim.temperature_c, (
            "the frame overtook the winding while heating, which is not physical"
        )


def test_the_frame_settles_below_the_winding() -> None:
    """Steady state, which is what HOUSING_LIMIT_C is derived from."""
    sim = MotorControllerSim()
    sim.temperature_c = 140.0
    for _ in range(2000):
        sim.housing_temperature_c += 0.0        # hold the winding, advance frame
        sim.temperature_c = 140.0
        sim.step(1)
        sim.temperature_c = 140.0
        if sim.state == "FAULT":
            sim.state = "RUNNING"               # ignore the winding trip here
    assert pytest.approx(sim.housing_temperature_c, abs=0.5) == HOUSING_LIMIT_C


# --- detection: what the second source actually buys --------------------------
def test_a_lying_winding_sensor_no_longer_defeats_the_protection() -> None:
    """The finding that motivated this channel, now closed.

    Before the second source the winding reached 422 C while the sensor reported
    40 C and the drive kept running. The single source meant there was nothing to
    disagree with the lie.
    """
    sim = LyingWindingSensor(reports_c=AMBIENT_C)
    step = _run_until_fault(sim)
    assert step is not None, "the drive never tripped despite a lying sensor"
    assert sim.temperature_c < OVERHEAT_LIMIT_C * 1.5, (
        f"tripped, but only after the winding reached {sim.temperature_c:.0f} C"
    )
    assert sim.fault_reason in {"SENSOR_IMPLAUSIBLE", "OVERTEMP_HOUSING"}


def test_a_drifting_winding_sensor_is_caught_by_disagreement() -> None:
    """The harder case: every individual reading is plausible.

    A 60 C low bias never reaches the winding limit and may never reach the frame
    limit either, so only the disagreement between the two channels exposes it.
    """
    sim = DriftingWindingSensor(offset_c=-60.0)
    step = _run_until_fault(sim)
    assert step is not None
    assert sim.fault_reason in {"SENSOR_IMPLAUSIBLE", "OVERTEMP_HOUSING"}


def test_the_frame_limit_alone_would_catch_a_stuck_sensor() -> None:
    """Independence check: disable the cross check, the frame limit still trips.

    Two mechanisms that both depend on the same comparison would be one
    mechanism wearing two names.
    """
    class NoCrossCheck(LyingWindingSensor):
        def _thermal_trip_reason(self) -> str | None:
            reason = super()._thermal_trip_reason()
            return None if reason == "SENSOR_IMPLAUSIBLE" else reason

    sim = NoCrossCheck(reports_c=AMBIENT_C)
    assert _run_until_fault(sim) is not None
    assert sim.fault_reason == "OVERTEMP_HOUSING"


# --- false positives, which is where a second channel usually goes wrong ------
def test_a_healthy_motor_running_hard_never_trips_on_disagreement() -> None:
    sim = MotorControllerSim()
    sim.handle_command("SET_SPEED 6000")
    for _ in range(60):
        sim.step(1)
    assert sim.fault_reason != "SENSOR_IMPLAUSIBLE"


def test_cooldown_does_not_trip_even_though_the_frame_ends_up_hotter() -> None:
    """The reason the cross check is armed only while torque is commanded.

    On cooldown the winding sheds heat faster than the frame it sits inside, so
    the frame legitimately becomes the hotter node. An ungated check would fault
    a healthy motor every single time it stopped.
    """
    sim = MotorControllerSim()
    sim.handle_command("SET_SPEED 6000")
    for _ in range(100):                     # settle at the steady running point
        sim.step(1)
    sim.handle_command("STOP")

    worst = -99.0
    for _ in range(400):
        sim.step(1)
        worst = max(worst, sim.housing_temperature_c - sim.temperature_c)

    # Measured at 5.15 C from full speed, which is past the 5.0 C margin. So the
    # gate is doing real work: without it this healthy stop would fault, and the
    # margin alone would not save it.
    assert worst > PLAUSIBILITY_MARGIN_C, (
        f"the frame only overtook the winding by {worst:.2f} C, under the "
        f"{PLAUSIBILITY_MARGIN_C} C margin, so this test is no longer exercising "
        f"the case it was written for and the gate now looks unnecessary"
    )
    assert sim.state != "FAULT", "a healthy motor faulted while cooling down"


def test_an_idle_cold_motor_does_not_trip() -> None:
    sim = MotorControllerSim()
    for _ in range(500):
        sim.step(1)
    assert sim.state == "IDLE"
    assert sim.fault_reason is None
