"""Simulated device under test: an embedded motor controller.

Deterministic, step-based simulation (no wall clock) so tests are fast and
reproducible in CI. The controller exposes a line-based ASCII command
protocol like a real device would over UART:

    SET_SPEED <rpm>   -> OK | ERR RANGE | ERR STATE
    GET_SPEED         -> OK <rpm>
    GET_TEMP          -> OK <deg_c>       (winding sensor)
    GET_HOUSING_TEMP  -> OK <deg_c>       (frame sensor, independent)
    GET_OVERLOAD      -> OK <acc>         (accumulated overload, not a temperature)
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

# --- thermal ratings --------------------------------------------------------
# [SERIES] and NOT [DS], which matters. The per-article data sheet carries no
# thermal data at all: grep it for "155", "thermal", "class", "insulation" or
# "60034" and every one returns nothing. These come from the SIMOTICS S-1FK2
# series documentation for this frame size, and mislabelling them [DS] was a
# provenance error found in review. The only ambient temperature in the archived
# article sheet is 20 C, in a footnote about brake holding current.
#
# Also worth stating because it bounds how forced these numbers are: IEC 60034-1
# permits 105 K by resistance for thermal class 155 (F). The 100 K used here is
# Siemens' own more conservative figure, so it is a chosen round number one level
# up rather than a derived one.
AMBIENT_C = 40.0                     # [SERIES] rated ambient
RATED_RISE_K = 100.0                 # [SERIES] permitted rise at rated output
INSULATION_LIMIT_C = 155.0           # [SERIES] thermal class 155 (F) is 155 C

# Where rated CONTINUOUS duty settles, per IEC 60034-1 S1: a machine at its rated
# output reaches the rise its class permits.
RATED_EQUILIBRIUM_C = AMBIENT_C + RATED_RISE_K            # 140 C

# The trip. It is the INSULATION limit, not the rated equilibrium, and keeping
# them apart is the entire point of this block. An earlier version set the trip
# equal to the rated equilibrium, which left 3.4e-13 K of margin: rated duty
# passed only because a geometric series converges from below and floating point
# rounded the last bit down, and a 0.1 percent cooling degradation tripped the
# drive. Protection must have somewhere to sit above normal duty.
OVERHEAT_LIMIT_C = INSULATION_LIMIT_C                     # 155 C
THERMAL_MARGIN_K = OVERHEAT_LIMIT_C - RATED_EQUILIBRIUM_C  # 15 K, real

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

# --- thermal model: a two node network --------------------------------------
# Losses are generated in the WINDING and leave through the FRAME. On this
# reference motor that is not a simplification, it is the only path: the data
# sheet specifies natural cooling, IP64, so there is no fan and no direct
# winding to air route.
#
# An earlier version cooled the winding straight to ambient AND separately
# warmed the frame from the winding, which conserved nothing: at rated
# equilibrium the winding shed 0.79 K per step to ambient while the frame gained
# 0.14 K per step from it, 118 percent of the loss. The frame was a read only
# observer of winding history rather than part of the path, so a hot frame never
# slowed winding cooling and repeated duty heat accumulation was understated.
#
# Now: winding -> frame -> ambient, in series.
#
# [ILLUSTRATIVE] The two time constants are chosen, not published. Siemens gives
# no thermal network. What IS grounded is the ORDERING and the topology: heat
# flows outward, the frame is the larger mass, and the only exit is through it.
# The protection rests on those, not on the coefficients.
# [ILLUSTRATIVE] The load the machine is driving, as a fraction of rated. Rated
# is the default because that is the condition the thermal rating describes.
LOAD_TORQUE_NM = RATED_TORQUE_NM

# Both are COMPRESSED. A 2 kW servo's winding thermal constant is minutes while
# its mechanical response is milliseconds, five orders of magnitude apart, and no
# single step size carries both. Every thermal latency derived from these is in
# STEPS and never in seconds, and comparisons BETWEEN latencies inherit the same
# problem: the ratio of thermal to mechanical response here is not physical
# either, so "the protection is fast relative to the machine" is not a statement
# this model can support.
WINDING_TIME_STEPS = 125.0
FRAME_TIME_STEPS = 300.0

#: Fraction of the rated rise that appears across the frame to ambient, the rest
#: appearing across the winding to frame. Fixes where the frame sits at rated.
FRAME_RISE_FRACTION = 0.60

K_WH = 1.0 / WINDING_TIME_STEPS                      # winding to frame
K_HA = 1.0 / FRAME_TIME_STEPS                        # frame to ambient
K_HW = K_HA * (FRAME_RISE_FRACTION / (1.0 - FRAME_RISE_FRACTION))

# [DERIVED] Heating at rated current, fixed by requiring rated continuous duty to
# settle at the permitted rise. Derived from the RATED EQUILIBRIUM, deliberately
# not from the trip: deriving it from the trip is what collapsed the margin to
# nothing and turned the rated duty validation criterion into a tautology.
HEAT_AT_RATED_C = K_WH * RATED_RISE_K * (1.0 - FRAME_RISE_FRACTION)


def _frame_steady_state(winding_c: float) -> float:
    """Frame temperature once heat in equals heat out, for a held winding."""
    return (K_HW * winding_c + K_HA * AMBIENT_C) / (K_HW + K_HA)


# [DERIVED] Worst case temperature rise in a single step, which is locked rotor
# current. Protection samples once per step, so a threshold placed exactly at the
# limit is always crossed from up to this far below it and the winding lands up
# to this far above. Every thermal threshold therefore sits one worst case step
# below the limit. This is derived from the model, not chosen.
#
# It also fixes what the margin is FOR. The 15 K between rated duty and the
# insulation limit has to cover both this discretisation and any inaccuracy in a
# model based channel, which yields a quantified accuracy requirement:
#
#     a predicting channel must be accurate to better than
#     (THERMAL_MARGIN_K - MAX_STEP_RISE_K) / RATED_RISE_K
#
# or it will either overshoot the limit or nuisance trip at rated duty. That is a
# real design constraint on the estimator rather than a caveat about it.
MAX_STEP_RISE_K = 0.0        # filled in below, needs STALL_HEATING_FACTOR

# [DERIVED] A blocked rotor draws locked rotor current, and resistive heating
# goes as I^2. The data sheet's maximum to rated current ratio is 24.0 / 5.6.
STALL_CURRENT_RATIO = MAX_CURRENT_A / RATED_CURRENT_A
STALL_HEATING_FACTOR = STALL_CURRENT_RATIO ** 2   # ~18.4
MAX_STEP_RISE_K = HEAT_AT_RATED_C * STALL_HEATING_FACTOR

#: How far below the trip the winding must fall before a RESET is accepted.
RESET_HYSTERESIS_K = 20.0

#: Where the thermal protections actually trip. One worst case step below the
#: insulation limit, so the winding cannot land above it between samples.
THERMAL_TRIP_C = OVERHEAT_LIMIT_C - MAX_STEP_RISE_K

#: Retained as the DERIVATION that retired the predicted-temperature channel.
#: See the third channel block below for what replaced it and why.
#:
#: Two constraints bound it from opposite sides. Under-predict and the winding
#: passes the insulation limit before the estimate reaches the threshold:
#:
#:     (THERMAL_TRIP_C - AMBIENT + MAX_STEP_RISE) / e  <=  OVERHEAT_LIMIT - AMBIENT
#:
#: Over-predict and the estimate reaches the threshold during ordinary rated
#: duty, tripping a healthy machine:
#:
#:     e * RATED_RISE_K + AMBIENT + MAX_STEP_RISE  <=  THERMAL_TRIP_C
#:
#: The binding one is whichever is tighter. Worth stating plainly: a class 155
#: machine at a 100 K rated rise normally runs at 87 percent of its absolute
#: insulation limit, so there is very little room, and once single step
#: granularity is paid for there is almost none. This is precisely why real
#: drives protect with an I^2t accumulator on MEASURED current rather than a
#: predicted absolute temperature: an accumulator sits at zero during rated duty
#: and therefore has the full headroom that a temperature prediction does not.
ESTIMATOR_UNDER_TOLERANCE = 1.0 - (
    (THERMAL_TRIP_C - AMBIENT_C + MAX_STEP_RISE_K)
    / (OVERHEAT_LIMIT_C - AMBIENT_C))
ESTIMATOR_OVER_TOLERANCE = (
    (THERMAL_TRIP_C - AMBIENT_C - MAX_STEP_RISE_K) / RATED_RISE_K) - 1.0
ESTIMATOR_ACCURACY_REQUIRED = min(ESTIMATOR_UNDER_TOLERANCE,
                                  ESTIMATOR_OVER_TOLERANCE)

# [DERIVED] The frame's own trip point: where the frame settles when the winding
# is at its trip threshold. Both then describe the same physical condition seen
# from two places.
HOUSING_LIMIT_C = _frame_steady_state(THERMAL_TRIP_C)

# [ILLUSTRATIVE] Sensor tolerance band for the cross check. Heat flows outward,
# so the frame is ALWAYS the cooler node and a frame reading above the winding
# is not a hot motor but an impossible one.
#
# Worth recording why this number moved twice. It began at 5 K, documented as
# absorbing step discretisation only. A review showed that was wrong: with the
# old model the winding cooled straight to ambient, bypassing the frame, so on
# cooldown the frame legitimately became the hotter node by about 20 K, and an
# ordinary stop and restart faulted a healthy machine. The margin went to 25 K
# to cover it.
#
# Making the network SERIES removed the phenomenon rather than accommodating it.
# When the winding's only heat exit is through the frame, the winding cannot fall
# below it, and the measured cooldown inversion is now -0.01 K. So this margin no
# longer covers a physical inversion, because there is not one. It covers sensor
# tolerance, which is what a margin between two independent measurements should
# cover, and it is sized well under the roughly 40 K the two nodes differ by at
# rated so the check retains sensitivity.
PLAUSIBILITY_MARGIN_C = 10.0


# --- third channel: accumulated overload on measured current ----------------
# DIVERSE, not redundant. The two temperature sensors are the same KIND of thing
# measured in two places, so anything that defeats measurement defeats both. This
# channel does not measure temperature at all: it integrates current above rated.
#
# It replaced a channel that predicted absolute winding temperature, and the
# reason is worth keeping, because the first design looked obviously right and
# was not. A class 155 machine at a 100 K rated rise normally runs at 87 percent
# of its absolute insulation limit. A channel predicting absolute temperature
# therefore has almost no headroom by construction, and once single step sampling
# granularity is paid for it has NONE: solving the two bounding constraints, the
# tolerable prediction error came out at 0.00 percent. It had to be exactly
# right, which no real estimator is, and the version of this file that shipped it
# was only passing because its estimator shared the plant's own coefficients.
#
# An accumulator has the headroom a temperature prediction cannot: during rated
# duty it sits at ZERO rather than at 87 percent of anything. Measured
# tolerances, same faults: about +7 percent before it nuisance trips rated duty,
# and 46 to 75 percent of under-reading before it stops protecting. Real drives
# measure phase current to a few percent, so that is comfortably achievable.
#
# [DERIVED] With decay constant TAU the accumulator settles at TAU*(r^2 - 1), so
# a budget of TAU*(R2_AT_LIMIT - 1) trips exactly when sustained current is high
# enough that the machine's own equilibrium would reach the insulation limit.
# Nothing here is chosen to make a test pass.
OVERLOAD_TAU = WINDING_TIME_STEPS
R2_AT_LIMIT = (OVERHEAT_LIMIT_C - AMBIENT_C) / RATED_RISE_K
OVERLOAD_BUDGET = OVERLOAD_TAU * (R2_AT_LIMIT - 1.0)

#: Current measurement error this channel tolerates. The binding side is
#: over-reading, which nuisance trips a healthy machine at rated duty.
OVERLOAD_OVER_TOLERANCE = R2_AT_LIMIT ** 0.5 - 1.0

#: Third channel on/off, so a test can ask what the other two do alone.
OVERLOAD_CHANNEL_ENABLED = True

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
    #: Is the drive energised? A servo holding zero speed against a load draws
    #: rated current and heats; a stopped drive draws none. Keying heating on
    #: commanded SPEED made zero-speed holding torque, the commonest thermal
    #: hazard on a servo axis, produce no heat at all, while SET_SPEED 0.4 (which
    #: reads back as 0 rpm) produced full rated heating. Both were wrong and they
    #: were the same bug.
    drive_enabled: bool = False
    #: Load the machine is driving. Rated by default, which is the condition the
    #: data sheet's thermal rating actually describes. Set to 0 for a free
    #: running motor, which then barely heats, as a real one does.
    load_torque_nm: float = LOAD_TORQUE_NM
    state: str = "IDLE"
    #: Accumulated overload above rated current. Not a temperature.
    overload_accumulator: float = 0.0
    #: Actual cooling relative to nominal, clamped to [0, 1] in use. 1.0 is a
    #: clean machine. Below that is an obstructed or fouled installation, which
    #: the overload channel cannot see because it assumes nominal. Values above 1
    #: would cool the winding below ambient and a negative value turned the
    #: cooling term into a heater, so both are clamped away.
    cooling_scale: float = 1.0
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
        if cmd == "GET_OVERLOAD":
            return f"OK {self.overload_accumulator:.2f}"
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
            self.drive_enabled = False      # STOP removes torque; SET_SPEED 0 does not
            if self.state == "RUNNING":
                self.state = "IDLE"
            return "OK"
        if cmd == "RESET":
            if self.read_winding_c() >= THERMAL_TRIP_C - RESET_HYSTERESIS_K:
                # A controller cannot cool a motor by clearing a register.
                # Without this, the latched overtemperature fault the device
                # advertises was defeatable by a RESET loop at a few steps per
                # cycle, at any temperature.
                return "ERR STATE"
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
        # Energised even at zero setpoint: that is a servo holding position.
        self.drive_enabled = True
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
        if not self.drive_enabled:
            return 0.0
        if self._stalled:
            return STALL_CURRENT_RATIO
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

        if (disagree and self._current_ratio() > 0.0
                and not self._disagreement_is_explained_by_cooldown()):
            # All three conditions matter. Gating on CURRENT rather than
            # commanded speed keeps a holding servo covered. Consulting the
            # cooldown explanation is what the annunciation path already did:
            # without it here, an ordinary stop and restart faulted a perfectly
            # healthy machine on its first step, because the frame is still the
            # hotter node from the previous run. The health line said the
            # disagreement was legitimate cooldown while the protection stopped
            # the machine anyway.
            return "SENSOR_DISAGREEMENT"
        if winding >= THERMAL_TRIP_C:
            return "OVERTEMP_WINDING"
        if OVERLOAD_CHANNEL_ENABLED and self.overload_accumulator >= OVERLOAD_BUDGET:
            # Ranked below the winding sensor because a working sensor measures
            # the actual quantity of interest, and above the frame because this
            # channel has no thermal mass and therefore no lag.
            return "OVERLOAD_I2T"
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

            # The third channel. It reads current, never a sensor, which is what
            # makes it diverse; and it knows nothing about the plant's actual
            # cooling, which is what keeps it blind to a degraded one.
            self.overload_accumulator = max(0.0, self.overload_accumulator
                                            + (ratio * ratio - 1.0)
                                            - self.overload_accumulator / OVERLOAD_TAU)

            # Series network: the winding's ONLY exit is through the frame, and
            # the frame's only exit is to ambient. cooling_scale degrades the
            # frame to ambient path, because on a natural cooling IP64 machine
            # that is the dominant thermal resistance and the one a dirty or
            # obstructed installation actually degrades.
            scale = min(1.0, max(0.0, self.cooling_scale))
            drop = self.temperature_c - self.housing_temperature_c
            self.housing_temperature_c += (
                K_HW * drop - K_HA * (self.housing_temperature_c - AMBIENT_C) * scale
            )

            # Decays toward the present reading at the FRAME time constant,
            # which is the rate at which a legitimate cooldown inversion
            # actually shrinks. Latching it forever meant that after any
            # ordinary duty cycle every frame reading below peak plus margin was
            # "explained by cooldown", so the annunciation path was dead for the
            # rest of the machine's life.
            reading = self.read_winding_c()
            self._peak_winding_c = max(
                reading,
                self._peak_winding_c - (self._peak_winding_c - reading) * K_HA)
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

            # Cooled AFTER the trip check: the protection reacts to the peak.
            self.temperature_c -= K_WH * drop

    def reset(self) -> None:
        """Clear the CONTROLLER. Thermal state is physical and is not cleared.

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
        self.cooling_scale = 1.0
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
        # De-energise. Without this the safe state removed the SETPOINT but not
        # the torque, so a tripped drive carried on drawing rated current and
        # heating: with cooling fully obstructed the winding continued past
        # 375 C after the protection had already fired. A safe state that does
        # not remove energy is not a safe state.
        self.drive_enabled = False

    def inject_overheat(self) -> None:
        self.temperature_c = OVERHEAT_LIMIT_C + 5.0

    def inject_stall(self, stalled: bool = True) -> None:
        self._stalled = stalled
