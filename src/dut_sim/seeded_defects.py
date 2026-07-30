"""Deliberately defective DUT variants, used to prove the fuzzer has teeth.

A property-based suite that finds nothing tells you the properties ran. It does
not tell you they would catch a real defect. So this module seeds known bugs
into the controller (fault seeding, the classic way to measure a test suite's
detection power) and `tests/test_fuzz_efficacy.py` asserts that the SAME
properties which pass on the clean implementation fail on each mutant, and
records the minimal input hypothesis shrinks to.

The three defects are ones an embedded controller really gets wrong:

  FaultLatchLeak      a latched fault is cleared by something other than RESET
  SpeedRangeOffByOne  a boundary check written <= limit + 1
  WatchdogOffByOne    a countdown compared with < 0 instead of <= 0, so the
                      timer fires one step late

None of these are imported by production code. They exist only so the test
suite can be tested.
"""

from dut_sim.motor_controller import MAX_RPM, MotorControllerSim


class FaultLatchLeak(MotorControllerSim):
    """FAULT stops latching: an unknown command clears it.

    Mirrors a real state machine that resets its error flag on the default
    branch of a command switch instead of leaving it alone.
    """

    def handle_command(self, line: str) -> str:
        response = super().handle_command(line)
        if response == "ERR UNKNOWN" and self.state == "FAULT":
            self.state = "IDLE"          # the defect
        return response


class SpeedRangeOffByOne(MotorControllerSim):
    """SET_SPEED accepts one rpm past the documented maximum.

    The classic boundary defect: `<= MAX` written as `<= MAX + 1`.
    """

    def _cmd_set_speed(self, raw: str) -> str:
        if self.state == "FAULT":
            return "ERR STATE"
        try:
            rpm = float(raw)
        except ValueError:
            return "ERR RANGE"
        if not 0 <= rpm <= MAX_RPM + 1:      # the defect
            return "ERR RANGE"
        self.target_rpm = rpm
        self.state = "RUNNING" if rpm > 0 else "IDLE"
        return "OK"


class WatchdogOffByOne(MotorControllerSim):
    """The watchdog trips one step later than its configured budget.

    A countdown compared with `< 0` rather than `<= 0`. On real hardware this
    is the difference between meeting and missing a timing requirement, and it
    is invisible to any test that only checks "it eventually trips".
    """

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            if self.state == "FAULT":
                self.speed_rpm = 0.0
            elif self._stalled:
                self.speed_rpm = 0.0
                if self.target_rpm > 0:
                    from dut_sim.motor_controller import HEATING_PER_KRPM
                    self.temperature_c += HEATING_PER_KRPM * self.target_rpm / 1000 * 3
            else:
                from dut_sim.motor_controller import HEATING_PER_KRPM, SPEED_TRACKING
                self.speed_rpm += (self.target_rpm - self.speed_rpm) * SPEED_TRACKING
                self.temperature_c += HEATING_PER_KRPM * self.speed_rpm / 1000

            if self._wdg_enabled and self.state != "FAULT":
                self._wdg_remaining -= 1
                if self._wdg_remaining < 0:      # the defect: was <= 0
                    self.trip_fault()

            from dut_sim.motor_controller import (
                AMBIENT_C,
                COOLING_RATE,
                OVERHEAT_LIMIT_C,
            )
            if self.temperature_c >= OVERHEAT_LIMIT_C and self.state != "FAULT":
                self.trip_fault()

            self.temperature_c -= (self.temperature_c - AMBIENT_C) * COOLING_RATE


#: Every mutant, so the efficacy suite can assert none of them survives.
MUTANTS = {
    "fault_latch_leak": FaultLatchLeak,
    "speed_range_off_by_one": SpeedRangeOffByOne,
    "watchdog_off_by_one": WatchdogOffByOne,
}
