"""Validation of the model against the data sheet it claims to be grounded in.

Separate from every other test file here, because it asks a different question.
The rest of the suite is VERIFICATION: does the code do what its specification
says. This file is VALIDATION: is the specification the right one, judged against
the only external reference available without hardware, the published data for
the Siemens SIMOTICS S-1FK2 1FK2105-6AF10-0SA0.

These criteria are executable rather than prose because a validation report that
is not run is a claim about the past. Two of them failed when first written, and
the model was changed rather than the criteria:

  V-02  at rated continuous duty the model settled 12 K above ambient where the
        data sheet's own rating implies 100 K, understating the rise by 8.3x.
  V-03  heating followed SPEED, but winding loss is dominated by I^2*R and so
        follows CURRENT, which follows torque. An unloaded motor spinning fast
        was cooking while a stalled one at locked-rotor current was merely warm.

What is NOT validated here, and is stated in docs/VALIDATION.md rather than
quietly omitted: the thermal TIME CONSTANT. Siemens publishes none, and it could
not be used faithfully if they did, so it is deliberately compressed and every
thermal latency is in steps and never in seconds.
"""

import math

import pytest

from dut_sim.motor_controller import (
    AMBIENT_C,
    HEAT_AT_RATED_C,
    MAX_CURRENT_A,
    MAX_RPM,
    RATED_CURRENT_A,
    RATED_EQUILIBRIUM_C,
    RATED_RPM,
    RATED_TORQUE_NM,
    ROTOR_INERTIA_KGM2,
    STEP_MS,
    THERMAL_MARGIN_K,
    THERMAL_TRIP_C,
    WINDING_TIME_STEPS,
    MotorControllerSim,
)

#: Long enough for the compressed thermal model to reach equilibrium several
#: times over, so a steady state reading is a steady state and not a transient.
SETTLE_STEPS = int(WINDING_TIME_STEPS * 30)


def _settle(rpm: float, load_nm: float | None = None,
            stalled: bool = False) -> MotorControllerSim:
    sim = MotorControllerSim()
    if load_nm is not None:
        sim.load_torque_nm = load_nm
    sim.handle_command(f"SET_SPEED {rpm}")
    if stalled:
        sim.inject_stall(True)
    for _ in range(SETTLE_STEPS):
        sim.step(1)
        if sim.state == "FAULT":
            break
    return sim


# --- V-01  mechanical dynamics ----------------------------------------------
def test_v01_reaches_rated_speed_in_the_torque_limited_time() -> None:
    """Acceleration is bounded by the data sheet's torque and inertia.

    M/J gives the torque-limited acceleration, and that fixes how long the rotor
    can possibly take to reach rated speed. The model is fitted to that time, so
    this checks the fit actually holds rather than trusting the comment claiming
    it does.
    """
    expected_ms = ((RATED_RPM * 2 * math.pi / 60)
                   / (RATED_TORQUE_NM / ROTOR_INERTIA_KGM2)) * 1000

    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    steps = 0
    while sim.speed_rpm < RATED_RPM - 50 and steps < 1000:
        sim.step(1)
        steps += 1

    assert steps * STEP_MS == pytest.approx(expected_ms, abs=1.0), (
        f"model takes {steps * STEP_MS:.2f} ms to reach rated speed, data sheet "
        f"torque and inertia imply {expected_ms:.2f} ms"
    )


# --- V-02  the one thermal number that IS validated -------------------------
def test_v02_rated_continuous_duty_settles_at_the_permitted_rise() -> None:
    """Kept, but no longer load bearing, and the reason is worth recording.

    This criterion once caught the model understating rated duty heating by a
    factor of 8.3. Fixing it by DERIVING the heating coefficient from the
    permitted rise turned the criterion into a tautology: it now verifies that
    x/(x/100) == 100 and cannot fail for any value of the time constant. The fix
    for V-02 retired V-02.

    That is exactly the failure mode a validation suite has to guard against, so
    it is said here rather than left for a reviewer, and the real work has moved
    to V-02b below, which relates two independently sourced numbers.
    """
    sim = _settle(RATED_RPM)
    assert sim.state != "FAULT", "rated continuous duty must not trip the drive"
    rise = sim.temperature_c - AMBIENT_C
    assert rise == pytest.approx(RATED_EQUILIBRIUM_C - AMBIENT_C, rel=0.02)


def test_v02b_rated_duty_sits_a_real_margin_below_the_trip() -> None:
    """The criterion that replaced V-02, and it can genuinely fail.

    It relates two numbers the model does NOT derive from each other: the
    permitted rise at rated output (100 K) and the insulation class limit
    (155 C). Both come from the series documentation; neither is computed from
    the other.

    The version of this model that shipped had the trip AT the rated
    equilibrium, leaving 3.4e-13 K of margin. Rated duty passed only because a
    geometric series converges from below and floating point rounded the last
    bit down, and a 0.1 percent cooling degradation tripped a healthy drive.
    """
    sim = _settle(RATED_RPM)
    margin = THERMAL_TRIP_C - sim.temperature_c
    assert margin > 5.0, (
        f"only {margin:.3f} K between rated continuous duty and the trip; "
        f"protection needs somewhere to sit above normal operation"
    )
    assert THERMAL_MARGIN_K > 0, "the trip must not equal the rated equilibrium"


def test_v02c_a_small_cooling_degradation_does_not_trip_a_healthy_drive() -> None:
    """The consequence of V-02b, measured rather than argued.

    With zero margin a 0.1 percent degradation tripped at step 861. Normal
    installation variation must not look like a fault.
    """
    for scale in (0.999, 0.99):
        sim = MotorControllerSim()
        sim.cooling_scale = scale
        sim.handle_command(f"SET_SPEED {RATED_RPM}")
        for _ in range(SETTLE_STEPS):
            sim.step(1)
        assert sim.state != "FAULT", (
            f"{(1 - scale) * 100:.1f} percent cooling degradation tripped the drive"
        )


# --- V-03  the loss mechanism -----------------------------------------------
def test_v03_an_unloaded_motor_barely_heats_however_fast_it_spins() -> None:
    """Winding loss is I^2*R, so it follows current, not speed.

    A real servo spinning an unloaded shaft draws almost no current and stays
    near ambient. The original model heated in proportion to speed, so it had
    this exactly backwards.
    """
    sim = _settle(MAX_RPM, load_nm=0.0)
    assert sim.temperature_c == pytest.approx(AMBIENT_C, abs=1.0), (
        f"unloaded at {MAX_RPM} rpm the model reached {sim.temperature_c:.1f} C; "
        f"with no load there is no current and so no I^2*R loss"
    )


def test_v03_the_same_load_at_any_speed_draws_the_same_current() -> None:
    """Corollary, and the sharpest form of the same criterion.

    Torque fixes current, so doubling the speed at constant load does not change
    the copper loss. Iron loss does rise with speed, and is not modelled: that is
    a stated simplification, not an oversight.
    """
    slow = _settle(RATED_RPM).temperature_c
    fast = _settle(MAX_RPM).temperature_c
    assert slow == pytest.approx(fast, abs=1.0), (
        f"same load at {RATED_RPM} rpm gives {slow:.1f} C and at {MAX_RPM} rpm "
        f"gives {fast:.1f} C; current is the same, so the loss should be too"
    )


def test_v03_stall_heating_follows_the_locked_rotor_current_ratio() -> None:
    """A blocked rotor draws locked-rotor current, and heating goes as I squared.

    The ratio is the data sheet's own maximum to rated current, so this is
    checkable rather than chosen.
    """
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    sim.inject_stall(True)
    before = sim.temperature_c
    sim.step(1)
    stall_rise = sim.temperature_c - before

    expected = HEAT_AT_RATED_C * (MAX_CURRENT_A / RATED_CURRENT_A) ** 2
    assert stall_rise == pytest.approx(expected, rel=0.01)


# --- V-04  consequences that must follow from the above ---------------------
def test_v04_normal_operation_never_trips_the_thermal_protection() -> None:
    """The protection must not fire on the duty the machine is rated for.

    Rated duty sits at the permitted temperature by definition, so the trip is
    always close. Close is correct; crossing is not, and a drive that trips
    during its own rated duty would be returned.
    """
    for rpm in (1000, RATED_RPM, MAX_RPM):
        sim = _settle(rpm)
        assert sim.state != "FAULT", (
            f"the drive tripped at {rpm} rpm under rated load, which is duty it "
            f"is rated for"
        )


def test_v04_a_stalled_rotor_trips_well_inside_the_campaign_budget() -> None:
    """The hazard must be reachable, or the protection is untestable.

    Not a data sheet criterion, a usefulness one: locked-rotor current is roughly
    18 times rated heating, so a stall must reach the limit quickly, and it must
    do so by tripping on temperature rather than on anything incidental.
    """
    sim = MotorControllerSim()
    sim.handle_command(f"SET_SPEED {RATED_RPM}")
    sim.inject_stall(True)
    for step in range(1, 101):
        sim.step(1)
        if sim.state == "FAULT":
            # The accumulated overload channel gets there first, and by a long
            # way: locked rotor current is 4.3x rated, so the accumulator crosses
            # its budget in a couple of steps while the winding is still cool.
            # That is the correct treatment of a locked rotor, which is an
            # OVERCURRENT event rather than a thermal one.
            assert sim.fault_reason == "OVERLOAD_I2T"
            assert step <= 5, f"stall took {step} steps to trip"
            assert sim.temperature_c < RATED_EQUILIBRIUM_C, (
                "the drive should stop a locked rotor long before the winding "
                "is anywhere near its rating"
            )
            return
    pytest.fail("a stalled rotor at locked-rotor current never tripped")


# --- what validation deliberately does NOT cover ----------------------------
def test_the_thermal_time_constant_is_marked_as_unvalidated() -> None:
    """A guard on the honesty of the model, not on its behaviour.

    The steady state is validated against the data sheet; the time constant
    cannot be, because Siemens publishes none and a faithful one would be five
    orders of magnitude away from the mechanical response. If someone ever fits
    the constant to something real, this test should be deleted along with the
    warnings it guards, deliberately and not by accident.
    """
    import inspect

    from dut_sim import motor_controller

    assert motor_controller.WINDING_TIME_STEPS > 0
    text = inspect.getsource(motor_controller)
    assert "compressed" in text.lower(), (
        "the thermal time scale is compressed and the source must say so, or a "
        "reader will take latencies in steps for latencies in time"
    )
    assert "never in seconds" in text
