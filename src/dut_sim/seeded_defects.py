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

    _granted: bool = False

    def step(self, n: int = 1) -> None:
        """Seed ONLY the off-by-one, by borrowing one step of budget back.

        This used to reimplement the whole of step() with `< 0` substituted for
        `<= 0`, which meant a copy of the physics living in a file nobody reads
        until it fails. It duly went stale the moment the thermal model changed,
        and the efficacy suite failed on an ImportError rather than on the defect
        it exists to detect: a seeded defect that cannot even be constructed
        proves nothing about the test suite.

        Granting one extra count before the real countdown runs produces exactly
        the same observable behaviour, a trip one step late, while inheriting
        every other behaviour from the device under test.
        """
        for _ in range(n):
            if self._wdg_enabled and self.state != "FAULT" and not self._granted:
                self._wdg_remaining += 1
                self._granted = True
            super().step(1)

    def _cmd_wdg_kick(self) -> str:
        reply = super()._cmd_wdg_kick()
        if reply == "OK":
            self._granted = False       # the extra count is granted per interval
        return reply


#: Every mutant, so the efficacy suite can assert none of them survives.
MUTANTS = {
    "fault_latch_leak": FaultLatchLeak,
    "speed_range_off_by_one": SpeedRangeOffByOne,
    "watchdog_off_by_one": WatchdogOffByOne,
}
