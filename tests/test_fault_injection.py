"""Fault injection: overheat and stall must latch FAULT; RESET must recover."""

import pytest

from conftest import settle
from dut_sim.motor_controller import AMBIENT_C, MotorControllerSim
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


def test_reset_recovers_from_fault(dut, sim):
    sim.inject_overheat()
    sim.step()
    dut.reset()
    assert dut.get_state() == "IDLE"
    dut.set_speed(1000)  # must be accepted again


def test_stalled_rotor_overheats_into_fault(dut, sim):
    sim.inject_stall()
    dut.set_speed(5000)
    settle(sim, steps=300)
    assert dut.get_state() == "FAULT"


def test_stalled_rotor_at_zero_command_does_not_heat(dut, sim):
    """A blocked rotor with no speed commanded draws no current, so no heating.

    The complement of the stall-to-overheat case: stall heating is driven by
    the commanded current, not by the stall itself. Without this the "is
    anything commanded" branch of the stall path is never taken false, and a
    controller that heated a stopped motor would pass the suite.
    """
    sim.inject_stall()
    dut.set_speed(0)
    settle(sim, steps=300)
    assert dut.get_state() != "FAULT"
    assert dut.get_temperature() <= AMBIENT_C + 0.5


def test_telemetry_still_readable_in_fault(dut, sim):
    sim.inject_overheat()
    sim.step()
    assert dut.get_temperature() > 0  # GET_* must work for diagnostics
