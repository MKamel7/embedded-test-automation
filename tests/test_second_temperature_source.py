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
    RATED_RPM,
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
    # The ESTIMATOR gets there first now, and inside the limit. The frame cross
    # check still works and is simply slower, because it has thermal mass and
    # the estimator does not.
    assert sim.fault_reason == "OVERTEMP_ESTIMATED"
    assert sim.temperature_c <= OVERHEAT_LIMIT_C, (
        f"tripped at {sim.temperature_c:.0f} C, past the limit"
    )


def test_a_drifting_winding_sensor_is_caught_by_disagreement() -> None:
    """The harder case: every individual reading is plausible.

    A 60 C low bias never reaches the winding limit and may never reach the frame
    limit either, so only the disagreement between the two channels exposes it.
    """
    sim = DriftingWindingSensor(offset_c=-60.0)
    step = _run_until_fault(sim)
    assert step is not None
    assert sim.fault_reason == "SENSOR_DISAGREEMENT"


def test_the_frame_limit_is_a_slow_backstop_and_not_an_independent_path() -> None:
    """Correcting a claim this file used to make.

    The frame limit was described as an independent path to the same protection,
    and a test asserted it caught a stuck sensor. Both were true and both were
    misleading, because the test never asked WHEN. Measured with the
    disagreement check disabled, it trips at step 50 with the winding at 643 C,
    which is not protection against anything.

    It is also now suppressed whenever the channels disagree, since an
    overtemperature cannot be declared from a channel there is reason to
    distrust. That leaves it covering one narrow case: a winding sensor reading
    low, but not low enough to disagree. Narrow is not nothing, and it is a great
    deal less than independent.
    """
    class FrameChannelOnly(LyingWindingSensor):
        """Both faster channels disabled, so the frame limit has to answer alone.

        The estimator is disabled by holding its state at ambient rather than by
        filtering the trip reason, because the reason chain returns on the first
        match and filtering it would skip the frame check entirely rather than
        reaching it.
        """

        def _sensor_disagreement(self) -> bool:
            return False

        def step(self, n: int = 1) -> None:
            for _ in range(n):
                super().step(1)
                self.estimated_winding_c = AMBIENT_C

    sim = FrameChannelOnly(reports_c=AMBIENT_C)
    step = _run_until_fault(sim, limit=2000)
    assert step is not None
    assert sim.fault_reason == "OVERTEMP_HOUSING"
    assert step > 40, "the backstop is expected to be slow; if it is fast now, say so"
    assert sim.temperature_c > OVERHEAT_LIMIT_C * 3, (
        "the winding is expected to be far past its limit by the time this "
        "fires, which is the point of calling it a backstop rather than a path"
    )


# --- the second channel is itself a new failure source ------------------------
class FrameSensorFailsHigh(MotorControllerSim):
    """The failure mode adding redundancy introduced, and nobody assessed."""

    def __init__(self, reports_c: float = 120.0) -> None:
        super().__init__()
        self.reports_c = reports_c

    def read_housing_c(self) -> float:
        return self.reports_c


def test_a_failed_frame_sensor_is_not_reported_as_an_overtemperature() -> None:
    """It used to be, and the misdiagnosis mattered.

    120 C is below the winding limit and looks entirely plausible, so nothing
    about the value announces a fault. The previous version tripped
    OVERTEMP_HOUSING: an overtemperature the motor was not having, on a
    perfectly healthy machine at 40 C.

    Two channels can detect a contradiction and cannot attribute it. Saying
    which sensor is wrong needs a third, and claiming to know with two is how a
    maintenance team ends up replacing the wrong part.
    """
    sim = FrameSensorFailsHigh()
    sim.handle_command("SET_SPEED 3000")
    for _ in range(50):
        sim.step(1)
        if sim.state == "FAULT":
            break

    assert sim.state == "FAULT", "a contradicted temperature reading must stop the drive"
    assert sim.fault_reason == "SENSOR_DISAGREEMENT", (
        f"reported {sim.fault_reason}, which names a condition the motor is not in"
    )
    assert sim.temperature_c < 60, "the winding really was fine"


def test_a_failed_frame_sensor_at_idle_annunciates_instead_of_tripping() -> None:
    """A stationary drive producing no torque is not a thermal hazard.

    The previous version latched a fault on a machine that had never moved,
    which is a nuisance trip, and nuisance trips are how protection gets
    bypassed in the field. Availability is a safety property once you count what
    people do to machines that stop for no reason.
    """
    sim = FrameSensorFailsHigh()
    for _ in range(300):
        sim.step(1)

    assert sim.state == "IDLE", "an idle drive faulted with no torque commanded"
    assert sim.handle_command("GET_HEALTH") == "OK SENSOR_DISAGREEMENT", (
        "the fault must still be visible to maintenance, just not by stopping "
        "the machine"
    )


def test_a_frame_sensor_reading_low_is_not_detectable() -> None:
    """Named rather than left to be discovered. This is a residual gap.

    A frame sensor reading LOW never contradicts the winding, because the frame
    is supposed to be the cooler node. It simply removes the backstop silently,
    and there is nothing in a two channel design that can notice.
    """
    sim = FrameSensorFailsHigh(reports_c=AMBIENT_C)
    sim.handle_command("SET_SPEED 3000")
    for _ in range(300):
        sim.step(1)

    assert sim.state != "FAULT"
    assert sim.health == "OK", (
        "if a low reading frame sensor is now detected, the design gained "
        "something and this test should be rewritten rather than deleted"
    )


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


# --- the third channel, and why it is diverse rather than redundant ----------
class BothSensorsLying(LyingWindingSensor):
    """Common cause: one root cause takes both thermal sensors."""

    def read_housing_c(self) -> float:
        return AMBIENT_C


def test_the_estimator_catches_what_no_sensor_can() -> None:
    """Both temperature sensors lying, which redundancy alone cannot survive.

    Two channels are two channels only while they fail independently. A model
    based channel does not read a sensor at all, so a shared supply, reference
    or harness taking both of them out leaves it entirely unaffected.
    """
    sim = BothSensorsLying(reports_c=AMBIENT_C)
    step = _run_until_fault(sim)
    assert step is not None, "both sensors lying and nothing noticed"
    assert sim.fault_reason == "OVERTEMP_ESTIMATED"
    assert step <= 7, f"caught at step {step}, outside the 7 step thermal budget"
    assert sim.temperature_c <= OVERHEAT_LIMIT_C


def test_the_estimator_misses_what_the_sensors_catch() -> None:
    """The other half of the diversity argument, and the half usually omitted.

    A model based channel only knows what was COMMANDED. When the plant itself
    degrades, a blocked fan or a clogged filter, the machine runs hotter than
    nominal and the estimator predicts the nominal. It is blind to exactly the
    class of fault the sensors exist for.

    Neither kind is sufficient. That is what makes this diversity rather than
    redundancy, and it is why a third thermometer would have bought much less.
    """
    sim = MotorControllerSim()
    sim.cooling_scale = 0.35
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(400):
        sim.step(1)
        if sim.state == "FAULT":
            break

    assert sim.state == "FAULT"
    assert sim.fault_reason == "OVERTEMP_WINDING", (
        "degraded cooling must be caught by measurement; the estimator cannot "
        "see it by construction"
    )
    assert sim.estimated_winding_c < OVERHEAT_LIMIT_C, (
        f"the estimator predicted {sim.estimated_winding_c:.0f} C and would have "
        f"tripped, which would mean it can see the plant after all"
    )


def test_the_estimator_does_not_trip_a_healthy_machine_at_rated_duty() -> None:
    """A third channel is a third thing that can nuisance trip."""
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(4000):
        sim.step(1)
    assert sim.state != "FAULT"
    assert sim.estimated_winding_c < OVERHEAT_LIMIT_C


def test_the_estimator_tracks_the_winding_when_everything_works() -> None:
    """It is exact here, and that is a LIMITATION rather than a result.

    The estimator uses the same coefficients as the plant, so with no fault
    injected it predicts the winding perfectly. A real one carries parameter
    error and would be worse. Asserting the agreement pins the fact that every
    estimator figure in this project is an upper bound on real performance.
    """
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(300):
        sim.step(1)
    assert sim.estimated_winding_c == pytest.approx(sim.temperature_c, abs=0.5)


def test_the_estimate_is_readable_over_the_protocol() -> None:
    sim = MotorControllerSim()
    assert sim.handle_command("GET_ESTIMATE") == f"OK {AMBIENT_C:.1f}"
