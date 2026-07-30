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

# --- model parameters -------------------------------------------------------
# REFERENCE DEVICE: Siemens SIMOTICS S-1FK2 permanent-magnet synchronous
# servomotor, article 1FK2105-6AF10-0SA0, on a SINAMICS S210 drive. Values
# marked [DS] are taken from the published Siemens data sheet for that article
# number; values marked [DERIVED] are computed from those; values marked
# [ILLUSTRATIVE] are not published by Siemens and are chosen, not measured.
#
# The point of naming a real device is that the operating envelope and the
# protection thresholds stop being invented. It does NOT make this a validated
# model of that motor, and no such claim is made.
STEP_MS = 1.0              # [DERIVED] one simulation step, see SPEED_TRACKING

MAX_RPM = 6000             # [DS] maximum speed 6,000 rpm
RATED_RPM = 3000           # [DS] rated speed 3,000 rpm
RATED_TORQUE_NM = 6.60     # [DS] rated torque
MAX_TORQUE_NM = 24.00      # [DS] maximum torque
RATED_CURRENT_A = 5.6      # [DS] rated current
MAX_CURRENT_A = 24.0       # [DS] maximum current
ROTOR_INERTIA_KGM2 = 3.5e-4  # [DS] rotor moment of inertia 3.5 kgcm^2

# [DS] Thermal class 155 (F) for this frame size, winding overtemperature
# dT = 100 K per EN/IEC 60034-1, at a rated ambient of 40 C. The protection
# threshold is therefore the permitted winding temperature, not a round number.
AMBIENT_C = 40.0
OVERHEAT_LIMIT_C = AMBIENT_C + 100.0        # 140 C

# [DERIVED] Speed dynamics fitted to the data sheet. At rated torque the
# torque-limited acceleration is M/J = 6.60 / 3.5e-4 = 18857 rad/s^2, so the
# rotor reaches rated speed (314.16 rad/s) in 16.7 ms. SPEED_TRACKING is chosen
# so that this first-order model settles to within +-50 rpm of a 3000 rpm
# setpoint in the same 16.7 ms with STEP_MS = 1.
#
# Deliberate simplification: a real servo under torque limit accelerates
# linearly and then the speed loop closes, so the true profile is a ramp, not an
# exponential. Only the time to reach the setpoint is matched, not the shape.
SPEED_TRACKING = 0.2179

# [ILLUSTRATIVE] Siemens does not publish a thermal time constant for this
# motor, so the thermal dynamics are NOT fitted. Worse, they cannot be: a 2 kW
# servo's winding thermal time constant is minutes, while its mechanical
# response is milliseconds, roughly five orders of magnitude apart. Modelling
# both faithfully on one step size would need millions of steps to reach a
# thermal trip, which no test suite can run. The thermal time scale is therefore
# deliberately COMPRESSED so a thermal fault is reachable in a short test. The
# ratio of thermal to mechanical response here is not physical, and any thermal
# latency from this model is in steps only, never in seconds.
HEATING_PER_KRPM = 0.35    # deg C added per step per 1000 rpm of speed
COOLING_RATE = 0.08        # fraction of excess-over-ambient shed each step

# [DERIVED] A blocked rotor draws locked-rotor current, and resistive heating
# goes as I^2. The data sheet's maximum to rated current ratio is 24.0 / 5.6,
# so stall heating is scaled by its square rather than by a guessed factor.
STALL_HEATING_FACTOR = (MAX_CURRENT_A / RATED_CURRENT_A) ** 2   # ~18.4

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
            self.reset()
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
                    self.temperature_c += (HEATING_PER_KRPM * self.target_rpm / 1000
                                           * STALL_HEATING_FACTOR)
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

    def reset(self) -> None:
        """Return every field to its power-on value.

        Written out rather than calling self.__init__() again: re-running the
        constructor on an instance only happens to work while the class has no
        subclass that adds state, and it silently does the wrong thing the
        moment one does. Setting the fields explicitly is also what a real
        controller's reset vector does.
        """
        self.speed_rpm = 0.0
        self.target_rpm = 0.0
        self.temperature_c = AMBIENT_C
        self.state = "IDLE"
        self._stalled = False
        self._wdg_enabled = False
        self._wdg_budget = 0
        self._wdg_remaining = 0

    # ---- fault injection (test backdoor, not part of the protocol) ----
    def trip_fault(self) -> None:
        self.state = "FAULT"
        self.speed_rpm = 0.0
        self.target_rpm = 0.0

    def inject_overheat(self) -> None:
        self.temperature_c = OVERHEAT_LIMIT_C + 5.0

    def inject_stall(self, stalled: bool = True) -> None:
        self._stalled = stalled
