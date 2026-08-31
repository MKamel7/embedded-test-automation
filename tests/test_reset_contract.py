"""What RESET clears, and what it must not.

`MotorControllerSim.reset()` carries an in-line note recording that an earlier
version cleared the thermal model while its docstring said it did not, and that
no test caught it. Two reasons it slipped through, both worth stating because
they generalise:

1. The existing tests assert the RESET **command** is refused while the motor
   is hot. That is a different property. It says nothing about what reset does
   once it is allowed to run, and the gate only bites within
   RESET_HYSTERESIS_K of the trip.
2. `reset()` is reachable directly, and through `MotorControllerDriver.reset()`
   at any temperature below that gate, so the guard those tests exercise is not
   on the only path in.

So this file tests the postcondition rather than the guard, on both paths. The
split it pins is the one the docstring states: the controller is cleared, the
machine is not. A controller cannot cool a motor, unjam a shaft or remove a
load by clearing a register.
"""

import pytest

from dut_sim.motor_controller import AMBIENT_C, MotorControllerSim

# Fields reset() owns and must clear, with the value it must clear them to.
CONTROLLER_STATE = {
    "speed_rpm": 0.0,
    "target_rpm": 0.0,
    "drive_enabled": False,
    "overload_accumulator": 0.0,
    "health": "OK",
    "state": "IDLE",
    "fault_reason": None,
    "_wdg_enabled": False,
    "_wdg_budget": 0,
    "_wdg_remaining": 0,
}

# Fields that describe the physical machine. reset() must leave these alone.
MACHINE_STATE = [
    "temperature_c",
    "housing_temperature_c",
    "_peak_winding_c",
    "cooling_scale",
    "load_torque_nm",
    "_stalled",
]


def hot_and_faulted() -> MotorControllerSim:
    """Run hard, then stall into a latched overload, with a watchdog armed.

    Every field in both lists above is left at something other than its
    constructed value, which is what makes the assertions below mean anything.
    """
    sim = MotorControllerSim()
    sim.handle_command("SET_SPEED 6000")
    sim.step(150)
    sim.inject_stall(True)
    sim.step(60)
    sim.handle_command("WDG_EN 50")
    return sim


def dirty_controller() -> MotorControllerSim:
    """Every controller field at something reset() must change.

    Running the machine is not enough on its own. A trip already zeroes the
    speed, the setpoint and the drive enable, and by the time the accumulator
    has been charged and the fault has latched, the accumulator has decayed
    back to zero: five of the ten fields below arrive at their post-reset value
    before reset is ever called, so asserting them after a realistic run is
    asserting nothing.

    So the fields are set directly. reset() has a postcondition, and a
    postcondition is supposed to hold from any state rather than only from the
    handful an ordinary duty cycle happens to reach.
    """
    sim = hot_and_faulted()
    sim.speed_rpm = 1234.5
    sim.target_rpm = 2345.6
    sim.drive_enabled = True
    sim.overload_accumulator = 7.5
    sim.health = "SENSOR_DISAGREEMENT"
    sim.state = "FAULT"
    sim.fault_reason = "OVERLOAD_I2T"
    sim._wdg_enabled = True
    sim._wdg_budget = 50
    sim._wdg_remaining = 17
    return sim


def test_the_fixture_actually_produces_state_worth_preserving():
    # A preservation test against a cold idle controller would pass trivially,
    # so this is what stops the machine-state half of the file being vacuous.
    sim = hot_and_faulted()
    assert sim.temperature_c > AMBIENT_C + 20.0
    assert sim.housing_temperature_c > AMBIENT_C
    assert sim._peak_winding_c > sim.temperature_c
    assert sim.load_torque_nm > 0.0
    assert sim._stalled is True


@pytest.mark.parametrize("field,expected", sorted(CONTROLLER_STATE.items()))
def test_no_controller_assertion_is_vacuous(field, expected):
    """Each field must start somewhere reset() has to move it away from.

    Without this, a reset() that did nothing at all would pass the clearing
    test below for any field whose dirty value already happened to equal its
    cleared value. That is the exact shape of a test that cannot fail, and it
    was the shape this file had on its first draft.
    """
    assert getattr(dirty_controller(), field) != expected


@pytest.mark.parametrize("field", MACHINE_STATE)
def test_reset_preserves_the_physical_state_it_does_not_own(field):
    sim = hot_and_faulted()
    before = getattr(sim, field)

    sim.reset()

    assert getattr(sim, field) == before, (
        f"reset() changed {field}, which describes the machine rather than the "
        "controller. A register write cannot cool a motor."
    )


@pytest.mark.parametrize("field,expected", sorted(CONTROLLER_STATE.items()))
def test_reset_clears_the_controller_state_it_does_own(field, expected):
    sim = dirty_controller()

    sim.reset()

    assert getattr(sim, field) == expected, (
        f"reset() left {field} at {getattr(sim, field)!r}, expected {expected!r}"
    )


def test_reset_does_not_leave_the_watchdog_counting():
    """Clearing the three fields has to mean the timer is really off.

    Asserting the fields alone would pass against a controller that cleared
    them and re-armed from a shadow register, so this steps well past the old
    budget and requires no trip.
    """
    sim = MotorControllerSim()
    sim.handle_command("WDG_EN 5")
    sim.step(2)

    sim.reset()
    sim.step(100)

    assert sim.state != "FAULT", f"the watchdog survived reset: {sim.fault_reason}"


def test_the_same_contract_holds_over_the_protocol(dut):
    """The direct call is not the only path, and RESET is accepted while warm.

    Each driver request advances the simulation one step, so this allows a
    degree of drift rather than demanding equality; the defect it exists to
    catch discards roughly thirty degrees, not a fraction of one.
    """
    dut.set_speed(3000)
    for _ in range(120):
        dut.get_speed()

    before = dut.get_temperature()
    assert before > AMBIENT_C + 20.0, "the motor never got warm enough to matter"

    dut.reset()
    after = dut.get_temperature()

    assert after == pytest.approx(before, abs=1.0), (
        f"temperature moved from {before:.2f} to {after:.2f} across a RESET "
        "issued over the protocol"
    )
    assert dut.get_state() == "IDLE"
