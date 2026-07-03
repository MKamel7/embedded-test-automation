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
    (anything else)   -> ERR UNKNOWN

Faults (overheat, stall) latch the FAULT state: the motor stops and refuses
speed commands until RESET, mirroring how real motor drivers behave.
"""

from dataclasses import dataclass, field

MAX_RPM = 6000
OVERHEAT_LIMIT_C = 90.0
AMBIENT_C = 25.0

# First-order dynamics constants per simulation step.
SPEED_TRACKING = 0.25      # fraction of the speed error closed each step
HEATING_PER_KRPM = 0.35    # deg C added per step per 1000 rpm of speed
COOLING_RATE = 0.08        # fraction of excess-over-ambient shed each step


@dataclass
class MotorControllerSim:
    """State machine + physics for the simulated controller."""

    speed_rpm: float = 0.0
    target_rpm: float = 0.0
    temperature_c: float = AMBIENT_C
    state: str = "IDLE"
    _stalled: bool = field(default=False, repr=False)

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
