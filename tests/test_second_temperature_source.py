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

import dut_sim.motor_controller as M
from dut_sim.motor_controller import (
    AMBIENT_C,
    OVERHEAT_LIMIT_C,
    RATED_EQUILIBRIUM_C,
    RATED_RPM,
    RATED_TORQUE_NM,
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
    assert sim.handle_command("GET_FAULT") == "OK OVERLOAD_I2T"


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


def test_the_frame_settles_below_the_winding_at_the_derived_point() -> None:
    """Steady state, which is what HOUSING_LIMIT_C is derived from.

    Rewritten after review: the previous version held the winding by writing to
    it every step and suppressed the trip by editing `state`, which tested the
    manipulation more than the model. Rated duty reaches the same steady state
    on its own.
    """
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(30000):
        sim.step(1)

    assert sim.state != "FAULT"
    assert sim.housing_temperature_c < sim.temperature_c, (
        "the frame must be the cooler node at steady state"
    )
    assert sim.housing_temperature_c == pytest.approx(
        M._frame_steady_state(sim.temperature_c), abs=0.5)


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
    # The accumulated overload channel gets there first, and by a wide margin:
    # it sees locked rotor current immediately, while the frame has to physically
    # heat up before it can contradict anything.
    assert sim.fault_reason == "OVERLOAD_I2T"
    assert sim.temperature_c < RATED_EQUILIBRIUM_C, (
        f"tripped at {sim.temperature_c:.0f} C; a locked rotor should be stopped "
        f"long before the winding approaches its rating"
    )


def test_a_drifting_winding_sensor_is_caught_by_disagreement() -> None:
    """The case only the cross check can reach.

    Deliberately a MILD overload, 1.05x rated, which the accumulated overload
    channel ignores by design because such a load is not on its own hazardous.
    The winding still creeps past its trip point, and a sensor drifting far low
    hides that. Only the contradiction between the two sensors exposes it.

    The drift has to be large, and the reason is a real cost rather than a
    choice: the frame sits about 40 K below the winding at rated, and the
    plausibility margin adds 25 K on top, so drifts smaller than roughly 65 K
    cannot be distinguished from a legitimately cooler frame. Sizing the margin
    to survive cooldown buys that insensitivity directly.
    """
    sim = DriftingWindingSensor(offset_c=-90.0)
    sim.load_torque_nm = RATED_TORQUE_NM * 1.05
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(30000):
        sim.step(1)
        if sim.state == "FAULT":
            break

    assert sim.state == "FAULT", "a far-low sensor under a mild overload went unnoticed"
    assert sim.fault_reason == "SENSOR_DISAGREEMENT", (
        f"caught by {sim.fault_reason}; this case exists to exercise the cross "
        f"check specifically"
    )


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
        """Both faster channels disabled, so the frame limit answers alone.

        The overload channel is disabled by holding its accumulator at zero
        rather than by filtering the trip reason, because the reason chain
        returns on the first match and filtering would skip the frame check
        entirely rather than reaching it.
        """

        def _sensor_disagreement(self) -> bool:
            return False

        def step(self, n: int = 1) -> None:
            for _ in range(n):
                super().step(1)
                self.overload_accumulator = 0.0

    sim = FrameChannelOnly(reports_c=AMBIENT_C)
    step = _run_until_fault(sim, limit=2000)
    assert step is not None
    assert sim.fault_reason == "OVERTEMP_HOUSING"
    assert step > 40, "the backstop is expected to be slow; if it is fast now, say so"
    assert sim.temperature_c > OVERHEAT_LIMIT_C, (
        "the winding is expected to be past its limit by the time this fires, "
        "which is the point of calling it a backstop rather than a path"
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


def test_the_frame_never_overtakes_the_winding_even_on_cooldown() -> None:
    """The invariant the whole cross check rests on, now actually invariant.

    It did not always hold. With the winding cooling straight to ambient and the
    frame warmed separately from it, energy was not conserved and on cooldown the
    frame legitimately became the hotter node by about 20 K, which is what made a
    healthy stop-and-restart fault the drive.

    Making the network series, so the winding's only exit is through the frame,
    removed the phenomenon instead of accommodating it: the winding cannot fall
    below the node it is dumping heat into. Correct physics retired a workaround.
    """
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(30000):
        sim.step(1)
    sim.handle_command("STOP")

    worst = -99.0
    for _ in range(4000):
        sim.step(1)
        worst = max(worst, sim.housing_temperature_c - sim.temperature_c)
        assert sim.state != "FAULT", "a healthy motor faulted while cooling down"

    assert worst <= 0.1, (
        f"the frame overtook the winding by {worst:.2f} K; in a series network "
        f"that cannot happen, so either the topology or the ordering has changed"
    )


def test_a_healthy_machine_survives_stop_and_restart() -> None:
    """The nuisance trip an independent review found, pinned.

    Run, stop, wait, restart. The frame is still the hotter node from the
    previous run, so the channels contradict each other legitimately. The health
    line always knew that; the TRIP path did not consult it, and faulted a
    perfectly healthy machine on the first step of the restart.
    """
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(30000):
        sim.step(1)
    sim.handle_command("STOP")

    for pause in (30, 80, 200, 400, 800):
        restart = MotorControllerSim()
        restart.temperature_c = sim.temperature_c
        restart.housing_temperature_c = sim.housing_temperature_c
        restart._peak_winding_c = sim.temperature_c
        for _ in range(pause):
            restart.step(1)
        assert restart.handle_command(f"SET_SPEED {RATED_RPM}") == "OK"
        for _ in range(5):
            restart.step(1)
        assert restart.state != "FAULT", (
            f"healthy machine faulted on restart after a {pause} step pause: "
            f"{restart.fault_reason}"
        )


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


def test_the_overload_channel_catches_what_no_sensor_can() -> None:
    """Both temperature sensors lying, which redundancy alone cannot survive.

    Two channels are two channels only while they fail independently. This one
    reads current rather than temperature, so a shared supply, reference or
    harness taking out both thermometers leaves it entirely unaffected.
    """
    sim = BothSensorsLying(reports_c=AMBIENT_C)
    step = _run_until_fault(sim)
    assert step is not None, "both sensors lying and nothing noticed"
    assert sim.fault_reason == "OVERLOAD_I2T"
    assert sim.temperature_c < RATED_EQUILIBRIUM_C


def test_the_overload_channel_misses_what_the_sensors_catch() -> None:
    """The other half of the diversity argument, and the half usually omitted.

    A channel driven by current only knows what the drive is DOING. When the
    plant itself degrades, an obstructed or fouled installation, the machine runs
    hotter at the same current and this channel sees nothing at all. It is blind
    to exactly the class of fault the sensors exist for.

    Neither kind is sufficient. That is what makes this diversity rather than
    redundancy, and it is why a third thermometer would have bought much less.
    """
    sim = MotorControllerSim()
    sim.cooling_scale = 0.35
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(40000):
        sim.step(1)
        if sim.state == "FAULT":
            break

    assert sim.state == "FAULT"
    assert sim.fault_reason in {"OVERTEMP_WINDING", "OVERTEMP_HOUSING"}, (
        f"degraded cooling was caught by {sim.fault_reason}; it must be caught "
        f"by measurement, since the overload channel cannot see the plant"
    )
    assert sim.overload_accumulator == pytest.approx(0.0, abs=1e-9), (
        "the overload channel registered something; it should be at zero, "
        "because the current never left rated"
    )


def test_the_overload_channel_does_not_trip_a_healthy_machine() -> None:
    """A third channel is a third thing that can nuisance trip."""
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(40000):
        sim.step(1)
    assert sim.state != "FAULT"
    assert sim.overload_accumulator == pytest.approx(0.0, abs=1e-9), (
        "at rated current the accumulator must sit at zero; that headroom is "
        "the entire reason this channel replaced a predicted temperature"
    )


def test_the_overload_channel_tolerates_current_measurement_error() -> None:
    """The property that retired the predicted-temperature channel.

    A predicted absolute temperature had ZERO tolerance to under-reading: a
    class 155 machine at a 100 K rated rise runs at 87 percent of its insulation
    limit, so there is no headroom. An accumulator sits at zero during rated
    duty, so it has the headroom that a temperature prediction never gets.
    """
    class MisreadCurrent(LyingWindingSensor):
        def __init__(self, err: float) -> None:
            super().__init__(reports_c=AMBIENT_C)
            self.err = err

        def _current_ratio(self) -> float:
            return super()._current_ratio() * self.err

    for err in (0.6, 0.8, 1.0):
        sim = MisreadCurrent(err)
        step = _run_until_fault(sim)
        assert step is not None, f"under-reading current by {1 - err:.0%} lost protection"
        assert sim.temperature_c < RATED_EQUILIBRIUM_C

    healthy = MotorControllerSim()
    healthy.handle_command(f"SET_SPEED {RATED_RPM}")
    for _ in range(40000):
        healthy.step(1)
    assert healthy.state != "FAULT"


def test_the_accumulator_is_readable_over_the_protocol() -> None:
    sim = MotorControllerSim()
    assert sim.handle_command("GET_OVERLOAD") == "OK 0.00"
