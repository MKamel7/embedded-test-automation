"""Protocol conformance: command grammar, ranges, and error codes."""

import pytest

from testbench.driver import ProtocolError


def test_set_and_get_speed_roundtrip(dut):
    dut.set_speed(1500)
    assert dut.get_state() == "RUNNING"


def test_initial_state_is_idle(dut):
    assert dut.get_state() == "IDLE"
    assert dut.get_speed() == 0


@pytest.mark.parametrize("rpm", [-1, 6001, 99999])
def test_out_of_range_speed_rejected(dut, rpm):
    with pytest.raises(ProtocolError, match="RANGE"):
        dut.set_speed(rpm)


def test_boundary_speeds_accepted(dut):
    dut.set_speed(0)
    dut.set_speed(6000)  # exactly MAX_RPM must be legal


def test_unknown_command_errors(dut):
    with pytest.raises(ProtocolError, match="UNKNOWN"):
        dut._query("SELF_DESTRUCT")


def test_malformed_speed_argument_rejected(dut):
    with pytest.raises(ProtocolError, match="RANGE"):
        dut._query("SET_SPEED fast")


def test_stop_returns_to_idle(dut):
    dut.set_speed(2000)
    dut.stop()
    assert dut.get_state() == "IDLE"
