"""Closed-loop behavior: convergence, tracking, and thermal response."""

from conftest import settle
from dut_sim.motor_controller import MotorControllerSim


def test_speed_converges_to_setpoint(dut, sim, measurements):
    dut.set_speed(3000)
    settle(sim)
    assert abs(dut.get_speed() - 3000) < 50  # within ~1.7% after settling

    # Measure settling time on an independent probe so the timed run above
    # is untouched: steps until speed is within 50rpm of the 3000rpm target.
    probe = MotorControllerSim()
    probe.target_rpm = 3000
    probe.state = "RUNNING"
    settling_steps = 0
    for settling_steps in range(1, 201):
        probe.step()
        if abs(probe.speed_rpm - 3000) < 50:
            break
    measurements.record("test_speed_converges_to_setpoint", "settling_steps", settling_steps, "steps")


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


def test_running_motor_heats_up_above_ambient(dut, sim, measurements):
    cold = dut.get_temperature()
    dut.set_speed(5000)
    settle(sim, steps=30)
    assert dut.get_temperature() > cold

    # Keep running toward thermal equilibrium to capture the peak
    # temperature reached during this 5000rpm run.
    peak_temp = sim.temperature_c
    for _ in range(100):
        sim.step()
        peak_temp = max(peak_temp, sim.temperature_c)
    measurements.record("test_running_motor_heats_up_above_ambient", "peak_temperature", peak_temp, "degC")


def test_stopped_motor_cools_toward_ambient(dut, sim):
    dut.set_speed(5000)
    settle(sim, steps=30)
    hot = dut.get_temperature()
    dut.stop()
    settle(sim, steps=60)
    assert dut.get_temperature() < hot
