"""Closed-loop behavior: convergence, tracking, and thermal response."""

from conftest import settle


def test_speed_converges_to_setpoint(dut, sim):
    dut.set_speed(3000)
    settle(sim)
    assert abs(dut.get_speed() - 3000) < 50  # within ~1.7% after settling


def test_speed_approach_is_monotonic(dut, sim):
    dut.set_speed(4000)
    readings = []
    for _ in range(12):
        sim.step()
        readings.append(sim.speed_rpm)
    assert readings == sorted(readings), "speed must ramp without oscillation"


def test_setpoint_change_tracks_downward(dut, sim):
    dut.set_speed(5000)
    settle(sim)
    dut.set_speed(1000)
    settle(sim)
    assert abs(dut.get_speed() - 1000) < 50


def test_running_motor_heats_up_above_ambient(dut, sim):
    cold = dut.get_temperature()
    dut.set_speed(5000)
    settle(sim, steps=30)
    assert dut.get_temperature() > cold


def test_stopped_motor_cools_toward_ambient(dut, sim):
    dut.set_speed(5000)
    settle(sim, steps=30)
    hot = dut.get_temperature()
    dut.stop()
    settle(sim, steps=60)
    assert dut.get_temperature() < hot
