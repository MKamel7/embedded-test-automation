"""Simulated device under test: an embedded motor controller.

Deterministic, step-based simulation (no wall clock) so tests are fast and
reproducible in CI. The controller exposes a line-based ASCII command
protocol like a real device would over UART:

    SET_SPEED <rpm>   -> OK | ERR RANGE | ERR STATE
    GET_SPEED         -> OK <rpm>
    GET_TEMP          -> OK <deg_c>       (winding sensor)
    GET_HOUSING_TEMP  -> OK <deg_c>       (frame sensor, independent)
    GET_FAULT         -> OK <reason>|OK NONE
    GET_HEALTH        -> OK OK|OK SENSOR_DISAGREEMENT   (annunciation, no trip)
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
# servomotor, article 1FK2105-6AF10-0SA0, on a SINAMICS S210 drive. The data
# sheet is archived in the repo at docs/datasheets/ and cited in full in
# docs/REFERENCES.md, so the numbers stay checkable without a live URL. Values
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

# --- thermal model ----------------------------------------------------------
# VALIDATED AGAINST THE DATA SHEET IN ITS STEADY STATE, NOT IN ITS DYNAMICS, and
# the split is the point. See docs/VALIDATION.md.
#
# What loss actually is. Winding loss is dominated by I^2*R, so heating follows
# CURRENT, which for a PMSM below field weakening follows TORQUE. It does not
# follow speed. An earlier version of this model heated in proportion to speed,
# which meant an unloaded motor spinning fast cooked while a stalled one at
# locked-rotor current was treated as merely warm. That was backwards, and
# validation against the data sheet is what found it.
#
# [ILLUSTRATIVE] The load the machine is driving, as a fraction of rated. Rated
# is the default because that is the condition the data sheet's own thermal
# rating describes.
LOAD_TORQUE_NM = RATED_TORQUE_NM

# [ILLUSTRATIVE] Thermal time constant, in steps. Siemens publishes none, and it
# could not be used faithfully if they did: a 2 kW servo's winding constant is
# MINUTES while its mechanical response is MILLISECONDS, five orders of magnitude
# apart, and no single step size carries both. This is therefore COMPRESSED, it
# is the part of the thermal model that is NOT validated, and every thermal
# latency derived from it is in steps and never in seconds.
THERMAL_TIME_STEPS = 125.0
COOLING_RATE = 1.0 / THERMAL_TIME_STEPS      # fraction of excess over ambient per step

# [DERIVED] and the one thermal number that IS validated. Under IEC 60034-1 S1
# continuous duty, a machine at its rated output settles at exactly the
# temperature rise its insulation class permits: that is what the rating means.
# So the heating at rated current is fixed by requiring equilibrium at the
# permitted winding temperature, rather than chosen. Anything else would make the
# device contradict its own data sheet at its own rated operating point.
HEAT_AT_RATED_C = COOLING_RATE * (OVERHEAT_LIMIT_C - AMBIENT_C)

# [DERIVED] A blocked rotor draws locked-rotor current, and resistive heating
# goes as I^2. The data sheet's maximum to rated current ratio is 24.0 / 5.6, so
# stall heating is scaled by its square rather than by a guessed factor.
STALL_CURRENT_RATIO = MAX_CURRENT_A / RATED_CURRENT_A
STALL_HEATING_FACTOR = STALL_CURRENT_RATIO ** 2   # ~18.4

# --- second temperature source ----------------------------------------------
# [ILLUSTRATIVE] Losses are generated in the WINDING and flow outward through
# the stator iron to the frame, then to ambient. The frame is therefore a
# second thermal node: cooler than the winding, slower to move, and heated by
# it rather than independently. Siemens publishes no thermal network for this
# motor, so these two coefficients are chosen, not fitted, and they inherit the
# compressed time scale of the winding model above.
#
# The point of the node is not fidelity. It is INDEPENDENCE. A single sensor
# cannot be checked against anything, so a sensor that lies defeats the
# protection completely, which is exactly what the fault campaign measured
# before this existed.
# Scaled to sit just BEHIND the winding's constant, because the frame is the
# larger thermal mass. Their ratio is what the cross check depends on, so the two
# must be rescaled together whenever THERMAL_TIME_STEPS moves; a frame that
# responded faster than the winding would invert the ordering the check rests on.
HOUSING_COUPLING = 0.005   # fraction of the winding-to-frame difference per step
HOUSING_COOLING = 0.002    # fraction of the frame's excess over ambient per step


def _housing_steady_state(winding_c: float) -> float:
    """Frame temperature once heat in equals heat out, for a held winding.

    (W - H) * coupling == (H - A) * cooling, solved for H.
    """
    return ((HOUSING_COUPLING * winding_c + HOUSING_COOLING * AMBIENT_C)
            / (HOUSING_COUPLING + HOUSING_COOLING))


# [DERIVED] The frame's own trip point is where the frame settles when the
# winding is exactly at its permitted limit, so the two thresholds describe the
# same physical condition seen from two places. Deriving it this way rather than
# picking a round number means the second channel cannot be quietly tuned to
# whatever makes a test pass. Works out at 111.4 C.
HOUSING_LIMIT_C = _housing_steady_state(OVERHEAT_LIMIT_C)

# [DERIVED] Heat flows from the winding outward, so while the machine is being
# driven the frame is always the COOLER of the two. A frame reading above the
# winding reading is not a hot motor, it is an impossible one, and the only
# explanation is that the winding sensor is wrong. The margin absorbs the step
# discretisation only; it is not a tolerance band on a real disagreement.
#
# The check is armed only while torque is commanded, and that restriction is
# load bearing rather than cautious. During COOLDOWN the ordering legitimately
# inverts: the winding sheds heat faster than the frame it is buried in, so the
# frame is briefly the hotter node. Checking then would fault a healthy motor
# every time it stopped, and a protection mechanism that fires on normal
# operation gets disabled by whoever is on call.
PLAUSIBILITY_MARGIN_C = 5.0

# Watchdog budget bounds, in simulation steps.
WDG_MIN_STEPS = 1
WDG_MAX_STEPS = 1000


@dataclass
class MotorControllerSim:
    """State machine + physics for the simulated controller."""

    speed_rpm: float = 0.0
    target_rpm: float = 0.0
    #: PHYSICAL winding temperature. What the motor is actually doing, which is
    #: not necessarily what any sensor reports: see read_winding_c().
    temperature_c: float = AMBIENT_C
    #: PHYSICAL frame temperature, the second thermal node.
    housing_temperature_c: float = AMBIENT_C
    #: Load the machine is driving. Rated by default, which is the condition the
    #: data sheet's thermal rating actually describes. Set to 0 for a free
    #: running motor, which then barely heats, as a real one does.
    load_torque_nm: float = LOAD_TORQUE_NM
    state: str = "IDLE"
    #: Highest winding temperature the sensor has reported since reset. Bounds
    #: how hot the frame can legitimately be during cooldown.
    _peak_winding_c: float = field(default=AMBIENT_C, repr=False)
    #: A sensor fault that does not warrant stopping. Annunciated, not acted on.
    health: str = "OK"
    #: Why the drive last tripped, or None. Empty telemetry after a trip means
    #: the cause has to be guessed from a log, which is how a recurring fault
    #: gets reset repeatedly instead of fixed.
    fault_reason: str | None = None
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
            return f"OK {self.read_winding_c():.1f}"
        if cmd == "GET_HOUSING_TEMP":
            return f"OK {self.read_housing_c():.1f}"
        if cmd == "GET_HEALTH":
            return f"OK {self.health}"
        if cmd == "GET_FAULT":
            return f"OK {self.fault_reason or 'NONE'}"
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

    # ---- sensors ------------------------------------------------------
    # A SEAM, deliberately. On this device the reported temperatures equal the
    # physical ones, because its sensors work. Fault injection that wants a
    # lying sensor overrides these methods rather than editing the physics,
    # which keeps the lie where a real fault puts it: in the measurement, not in
    # the motor. Protection reads through here and never touches the fields, so
    # a test cannot accidentally give the protection privileged access to the
    # truth that a real controller would not have.
    def read_winding_c(self) -> float:
        """What the winding sensor reports."""
        return self.temperature_c

    def read_housing_c(self) -> float:
        """What the frame sensor reports. A separate device on a separate node."""
        return self.housing_temperature_c

    def _current_ratio(self) -> float:
        """Winding current as a multiple of rated. Heating goes as its square.

        Torque stands in for current, which holds for a PMSM below field
        weakening and is the reason the data sheet's torque and current ratings
        track each other.

        The ACCELERATION torque is deliberately excluded, and that needs saying
        because it looks like an omission. A real servo does draw peak current
        while accelerating, but it does so for about 17 ms against a winding
        thermal constant measured in minutes, so the contribution is nothing.
        Here the thermal scale is compressed to 125 steps while the mechanical
        response is still 17, so including it would give a routine acceleration
        roughly 180 C of heating and trip a perfectly healthy motor. That would
        be an artifact of the compression, not physics, so the model carries
        steady state load current only.
        """
        if self._stalled:
            return STALL_CURRENT_RATIO if self.target_rpm > 0 else 0.0
        if self.target_rpm <= 0:
            return 0.0
        return self.load_torque_nm / RATED_TORQUE_NM

    def _sensor_disagreement(self) -> bool:
        """Do the two thermal channels contradict each other?

        Heat is generated in the winding and flows outward, so the frame cannot
        be the hotter of the two while heat is flowing. When it reads hotter
        anyway, one of the channels is wrong.

        WHICH one is wrong is NOT knowable from two channels, and pretending
        otherwise was a real defect in the previous version: a frame sensor
        failing high was reported as OVERTEMP_HOUSING, an overtemperature the
        motor was not having. Two channels detect a disagreement; attributing it
        needs a third, which is what 2oo3 voting is for. So the diagnostic says
        what is actually known.
        """
        return self.read_housing_c() > self.read_winding_c() + PLAUSIBILITY_MARGIN_C

    def _disagreement_is_explained_by_cooldown(self) -> bool:
        """During cooldown the frame legitimately becomes the hotter node.

        The winding sheds heat faster than the frame it sits inside, so the
        ordering inverts, and that is not a fault. It is bounded, though: the
        frame can only be as hot as the winding once was. A frame reading above
        anything the winding has ever reached is not a cooling motor, it is a
        broken sensor, and that distinction is what lets an idle drive tell the
        two apart instead of faulting on both.
        """
        return self.read_housing_c() <= self._peak_winding_c + PLAUSIBILITY_MARGIN_C

    def _thermal_trip_reason(self) -> str | None:
        """Which thermal protection, if any, demands a trip this step.

        Order matters and encodes a rule worth stating: AN OVERTEMPERATURE
        CANNOT BE DECLARED FROM A CHANNEL THERE IS REASON TO DISTRUST. So
        disagreement is evaluated first, and a frame reading above its own limit
        is only treated as heat when the two channels agree.

          SENSOR_DISAGREEMENT  the channels contradict each other while torque
                               is commanded. One of them is wrong and the item
                               cannot tell which, so it stops rather than guess.
          OVERTEMP_WINDING     the primary limit, on the sensor closest to the
                               heat. Fastest, and the one that matters when
                               everything works.
          OVERTEMP_HOUSING     a slow backstop, and NOT the independent path it
                               was previously described as. It is suppressed
                               whenever the channels disagree, which is exactly
                               the case a lying winding sensor produces, so it
                               catches only a winding sensor reading slightly
                               low, not one reading absurdly low. Measured
                               alone it trips at step 50 with the winding at
                               643 C, far past any useful budget.

        Disagreement WITHOUT commanded torque does not trip. A stationary drive
        producing no torque is not a thermal hazard, and faulting on it was the
        previous version's other defect: an idle drive with a dead frame sensor
        latched a fault having never moved. It is annunciated instead, via
        GET_HEALTH, so the failure is visible to maintenance without taking the
        machine down.
        """
        winding, housing = self.read_winding_c(), self.read_housing_c()
        disagree = self._sensor_disagreement()

        if disagree and self.target_rpm > 0:
            return "SENSOR_DISAGREEMENT"
        if winding >= OVERHEAT_LIMIT_C:
            return "OVERTEMP_WINDING"
        if housing >= HOUSING_LIMIT_C and not disagree:
            return "OVERTEMP_HOUSING"
        return None

    # ---- physics ------------------------------------------------------
    def step(self, n: int = 1) -> None:
        """Advance the simulation n steps."""
        for _ in range(n):
            if self.state == "FAULT":
                self.speed_rpm = 0.0
            elif self._stalled:
                # Rotor blocked. No motion, and the drive pushes locked-rotor
                # current trying to move it, which is where the heat comes from.
                self.speed_rpm = 0.0
            else:
                self.speed_rpm += (self.target_rpm - self.speed_rpm) * SPEED_TRACKING

            ratio = self._current_ratio()
            self.temperature_c += HEAT_AT_RATED_C * ratio * ratio

            # The frame is heated BY the winding and cooled by ambient. It is a
            # genuinely separate node with its own state, not a value derived
            # from the winding at protection time, which is what makes it usable
            # as a check ON the winding.
            self.housing_temperature_c += (
                (self.temperature_c - self.housing_temperature_c) * HOUSING_COUPLING
                - (self.housing_temperature_c - AMBIENT_C) * HOUSING_COOLING
            )

            self._peak_winding_c = max(self._peak_winding_c, self.read_winding_c())
            self.health = (
                "SENSOR_DISAGREEMENT"
                if self._sensor_disagreement()
                and not self._disagreement_is_explained_by_cooldown()
                else "OK"
            )

            # Watchdog: an unkicked timer trips FAULT exactly like a thermal
            # protection circuit would, once the step budget runs out.
            if self._wdg_enabled and self.state != "FAULT":
                self._wdg_remaining -= 1
                if self._wdg_remaining <= 0:
                    self.trip_fault("WATCHDOG")

            # Trip BEFORE cooling: the protection circuit reacts to the peak
            # winding temperature, not the post-dissipation average.
            reason = self._thermal_trip_reason()
            if reason is not None and self.state != "FAULT":
                self.trip_fault(reason)

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
        self.housing_temperature_c = AMBIENT_C
        self._peak_winding_c = AMBIENT_C
        self.health = "OK"
        self.load_torque_nm = LOAD_TORQUE_NM
        self.state = "IDLE"
        self.fault_reason = None
        self._stalled = False
        self._wdg_enabled = False
        self._wdg_budget = 0
        self._wdg_remaining = 0

    # ---- fault injection (test backdoor, not part of the protocol) ----
    def trip_fault(self, reason: str = "TRIP") -> None:
        self.state = "FAULT"
        self.fault_reason = reason
        self.speed_rpm = 0.0
        self.target_rpm = 0.0

    def inject_overheat(self) -> None:
        self.temperature_c = OVERHEAT_LIMIT_C + 5.0

    def inject_stall(self, stalled: bool = True) -> None:
        self._stalled = stalled
