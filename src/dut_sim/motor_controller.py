"""Simulated device under test: an embedded motor controller.

Deterministic, step-based simulation (no wall clock) so tests are fast and
reproducible in CI. The controller exposes a line-based ASCII command
protocol like a real device would over UART:

    SET_SPEED <rpm>   -> OK | ERR RANGE | ERR STATE
    GET_SPEED         -> OK <rpm>
    GET_TEMP          -> OK <deg_c>
    GET_STATE         -> OK IDLE|RUNNING|FAULT
    STOP              -> OK
    RESET             -> OK
    WDG_EN <steps>    -> OK | ERR RANGE
    WDG_KICK          -> OK | ERR STATE
    WDG_DIS           -> OK
    (anything else)   -> ERR UNKNOWN

Faults (overheat, stall) latch the FAULT state: the motor stops and refuses
speed commands until RESET, mirroring how real motor drivers behave. A
software watchdog works the same way: once enabled, missing WDG_KICK for
longer than the configured step budget also trips a latched FAULT, like a
communication-timeout fault on a real controller.
"""

from dataclasses import dataclass, field

MAX_RPM = 6000
OVERHEAT_LIMIT_C = 90.0
AMBIENT_C = 25.0

# First-order dynamics constants per simulation step.
SPEED_TRACKING = 0.25      # fraction of the speed error closed each step
HEATING_PER_KRPM = 0.35    # deg C added per step per 1000 rpm of speed
COOLING_RATE = 0.08        # fraction of excess-over-ambient shed each step

# Watchdog budget bounds, in simulation steps.
WDG_MIN_STEPS = 1
WDG_MAX_STEPS = 1000


@dataclass
class MotorControllerSim:
    """State machine + physics for the simulated controller."""

    speed_rpm: float = 0.0
    target_rpm: float = 0.0
    temperature_c: float = AMBIENT_C
    state: str = "IDLE"
    _stalled: bool = field(default=False, repr=False)
    _wdg_enabled: bool = field(default=False, repr=False)
    _wdg_budget: int = field(default=0, repr=False)
    _wdg_remaining: int = field(default=0, repr=False)

    # ---- protocol ----------------------------------------------------
    def handle_command(self, line: str) -> str:
        parts = line.strip().split()
        if not parts:
            return "ERR UNKNOWN"
        cmd, args = parts[0].upper(), parts[1:]

        if cmd == "SET_SPEED" and len(args) == 1:
            return self._cmd_set_speed(args[0])
        if cmd == "GET_SPEED":
            return f"OK {self.speed_rpm:.0f}"
        if cmd == "GET_TEMP":
            return f"OK {self.temperature_c:.1f}"
        if cmd == "GET_STATE":
            return f"OK {self.state}"
        if cmd == "STOP":
            self.target_rpm = 0.0
            if self.state == "RUNNING":
                self.state = "IDLE"
            return "OK"
        if cmd == "RESET":
            self.__init__()  # back to factory state
            return "OK"
        if cmd == "WDG_EN" and len(args) == 1:
            return self._cmd_wdg_en(args[0])
        if cmd == "WDG_KICK":
            return self._cmd_wdg_kick()
        if cmd == "WDG_DIS":
            self._wdg_enabled = False
            return "OK"
        return "ERR UNKNOWN"

    def _cmd_set_speed(self, raw: str) -> str:
        if self.state == "FAULT":
            return "ERR STATE"
        try:
            rpm = float(raw)
        except ValueError:
            return "ERR RANGE"
        if not 0 <= rpm <= MAX_RPM:
            return "ERR RANGE"
        self.target_rpm = rpm
        self.state = "RUNNING" if rpm > 0 else "IDLE"
        return "OK"

    def _cmd_wdg_en(self, raw: str) -> str:
        try:
            steps = int(raw)
        except ValueError:
            return "ERR RANGE"
        if not WDG_MIN_STEPS <= steps <= WDG_MAX_STEPS:
            return "ERR RANGE"
        self._wdg_enabled = True
        self._wdg_budget = steps
        self._wdg_remaining = steps
        return "OK"

    def _cmd_wdg_kick(self) -> str:
        if not self._wdg_enabled or self.state == "FAULT":
            return "ERR STATE"
        self._wdg_remaining = self._wdg_budget
        return "OK"

    # ---- physics ------------------------------------------------------
    def step(self, n: int = 1) -> None:
        """Advance the simulation n steps."""
        for _ in range(n):
            if self.state == "FAULT":
                self.speed_rpm = 0.0
            elif self._stalled:
                # rotor blocked: no motion, current keeps heating the windings
                self.speed_rpm = 0.0
                if self.target_rpm > 0:
                    self.temperature_c += HEATING_PER_KRPM * self.target_rpm / 1000 * 3
            else:
                self.speed_rpm += (self.target_rpm - self.speed_rpm) * SPEED_TRACKING
                self.temperature_c += HEATING_PER_KRPM * self.speed_rpm / 1000

            # Watchdog: an unkicked timer trips FAULT exactly like a thermal
            # protection circuit would, once the step budget runs out.
            if self._wdg_enabled and self.state != "FAULT":
                self._wdg_remaining -= 1
                if self._wdg_remaining <= 0:
                    self.trip_fault()

            # Trip BEFORE cooling: the protection circuit reacts to the peak
            # winding temperature, not the post-dissipation average.
            if self.temperature_c >= OVERHEAT_LIMIT_C and self.state != "FAULT":
                self.trip_fault()

            self.temperature_c -= (self.temperature_c - AMBIENT_C) * COOLING_RATE

    # ---- fault injection (test backdoor, not part of the protocol) ----
    def trip_fault(self) -> None:
        self.state = "FAULT"
        self.speed_rpm = 0.0
        self.target_rpm = 0.0

    def inject_overheat(self) -> None:
        self.temperature_c = OVERHEAT_LIMIT_C + 5.0

    def inject_stall(self, stalled: bool = True) -> None:
        self._stalled = stalled
