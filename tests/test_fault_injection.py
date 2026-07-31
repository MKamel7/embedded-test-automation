"""Fault injection: overheat and stall must latch FAULT; RESET must recover."""

import pytest

from conftest import settle
from dut_sim.motor_controller import MotorControllerSim
from testbench.driver import ProtocolError


def test_overheat_trips_fault_and_stops_motor(dut, sim, measurements):
    dut.set_speed(3000)
    sim.inject_overheat()
    sim.step()
    assert dut.get_state() == "FAULT"
    assert dut.get_speed() == 0

    # Measure trip latency on an independent probe: steps from the overheat
    # injection to the FAULT state actually landing.
    probe = MotorControllerSim()
    probe.inject_overheat()
    trip_latency = 0
    while trip_latency < 10:
        trip_latency += 1
        probe.step()
        if probe.state == "FAULT":
            break
    measurements.record("test_overheat_trips_fault_and_stops_motor",
                        "overheat_trip_latency", trip_latency, "steps")


def test_fault_rejects_speed_commands(dut, sim):
    sim.inject_overheat()
    sim.step()
    with pytest.raises(ProtocolError, match="STATE"):
        dut.set_speed(1000)


def test_fault_is_latched_until_reset(dut, sim):
    sim.inject_overheat()
    sim.step()
    settle(sim, steps=200)  # plenty of time to cool down
    assert dut.get_state() == "FAULT", "fault must latch, not self-clear"


def test_reset_is_refused_while_the_winding_is_still_hot(dut, sim):
    """A controller cannot cool a motor by clearing a register.

    RESET used to set every thermal node back to ambient, which made the latched
    overtemperature fault defeatable by a RESET loop at a few steps per cycle, at
    any temperature. Overtemperature faults are conventionally inhibited from
    resetting until the measurement falls below a hysteresis point.
    """
    sim.inject_overheat()
    sim.step()
    assert sim.state == "FAULT"
    assert sim.handle_command("RESET") == "ERR STATE"
    assert sim.state == "FAULT", "the fault must still be latched"


def test_reset_recovers_once_the_winding_has_actually_cooled(dut, sim):
    sim.inject_overheat()
    sim.step()
    # let the machine genuinely cool: no torque is commanded in FAULT
    settle(sim, steps=8000)
    dut.reset()
    assert dut.get_state() == "IDLE"
    dut.set_speed(1000)  # must be accepted again


def test_stalled_rotor_overheats_into_fault(dut, sim):
    sim.inject_stall()
    dut.set_speed(5000)
    settle(sim, steps=300)
    assert dut.get_state() == "FAULT"


def test_a_stalled_rotor_heats_whenever_the_drive_is_energised(dut, sim):
    """Premise corrected by review: it is TORQUE that heats, not speed.

    This test used to assert that a stalled rotor commanded to zero rpm stays
    cold, and the model agreed, because heating was gated on commanded SPEED.
    Both were wrong. A servo commanded to hold zero speed against a jammed shaft
    is pushing locked rotor current into a stationary rotor, which is the
    commonest thermal hazard on a servo axis and the one most likely to be
    missed.
    """
    sim.inject_stall()
    dut.set_speed(0)          # energised, holding position
    settle(sim, steps=400)
    assert dut.get_state() == "FAULT", (
        "a stalled rotor held at zero speed draws locked rotor current and must "
        "still trip"
    )


def test_a_stopped_drive_does_not_heat(dut, sim):
    """The genuine no-current case, which is STOP rather than SET_SPEED 0.

    The distinction is the point: SET_SPEED 0 holds position and draws current;
    STOP removes torque and draws none.
    """
    sim.inject_stall()
    dut.set_speed(3000)
    settle(sim, steps=2)
    sim.handle_command("STOP")
    before = sim.temperature_c
    settle(sim, steps=400)
    assert sim.temperature_c <= before, "a stopped drive must not keep heating"


def test_telemetry_still_readable_in_fault(dut, sim):
    sim.inject_overheat()
    sim.step()
    assert dut.get_temperature() > 0  # GET_* must work for diagnostics
