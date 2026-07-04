"""Watchdog: an unkicked timer must latch FAULT, like the thermal protections."""

import pytest

from testbench.driver import ProtocolError


def test_kicked_watchdog_keeps_running(dut, sim):
    dut.set_speed(1000)
    dut._query("WDG_EN 5")
    for _ in range(50):  # far more steps than the budget, but always kicked
        dut._query("WDG_KICK")
        sim.step()
    assert dut.get_state() == "RUNNING"


def test_missing_kicks_trips_fault_at_exactly_budget(dut, sim):
    # SimTransport advances the sim by one step per request (see conftest's
    # dut fixture), so the WDG_EN call itself consumes the first of the 10
    # budgeted steps; read sim.state directly to avoid perturbing the count.
    dut._query("WDG_EN 10")
    sim.step(8)  # 9 steps consumed so far - one short of the budget
    assert sim.state != "FAULT"
    sim.step(1)  # the 10th step exhausts the budget
    assert sim.state == "FAULT"


def test_kick_after_trip_rejected(dut, sim):
    dut._query("WDG_EN 3")
    sim.step(5)  # comfortably past the budget
    assert sim.state == "FAULT"
    with pytest.raises(ProtocolError, match="STATE"):
        dut._query("WDG_KICK")


def test_reset_recovers_and_disables_watchdog(dut, sim):
    dut._query("WDG_EN 3")
    sim.step(5)
    assert sim.state == "FAULT"
    dut.reset()
    assert dut.get_state() == "IDLE"
    sim.step(100)  # far past any budget - watchdog must no longer be armed
    assert sim.state == "IDLE"


@pytest.mark.parametrize("steps", [0, 2000])
def test_wdg_en_out_of_range_rejected(dut, steps):
    with pytest.raises(ProtocolError, match="RANGE"):
        dut._query(f"WDG_EN {steps}")


def test_wdg_kick_without_enable_rejected(dut):
    with pytest.raises(ProtocolError, match="STATE"):
        dut._query("WDG_KICK")
