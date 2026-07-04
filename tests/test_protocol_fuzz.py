"""Property-based protocol fuzzing (hypothesis).

Each property below builds a FRESH MotorControllerSim per example - never the
session fixtures - so failures are minimal and independent of test order.

Fuzzing run log: no real defect found. Every property below (never-crash,
state-machine invariant, FAULT latch, SET_SPEED contract, watchdog contract)
held on the current sim implementation across max_examples=200 runs,
including edge cases like NaN/inf speeds, unicode/control-character and
500-char text lines, and watchdog gaps landing exactly on the budget
boundary. This file therefore documents confirmation, not a fix.
"""

import math

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dut_sim.motor_controller import MAX_RPM, MotorControllerSim

VALID_WORDS = [
    "SET_SPEED",
    "GET_SPEED",
    "GET_TEMP",
    "GET_STATE",
    "STOP",
    "RESET",
    "WDG_EN",
    "WDG_KICK",
    "WDG_DIS",
]

# Args mixing plausible numbers with outright garbage.
garbage_args = st.one_of(
    st.integers(min_value=-10_000, max_value=10_000).map(str),
    st.floats(allow_nan=True, allow_infinity=True).map(str),
    st.text(max_size=10),
)

# Valid command words paired with 0-2 garbage/plausible args.
command_words_strategy = st.builds(
    lambda word, args: " ".join([word, *args]),
    st.sampled_from(VALID_WORDS),
    st.lists(garbage_args, max_size=2),
)

# Anything at all: unicode, control chars, empty, very long strings.
free_text_strategy = st.text(max_size=500)

any_line_strategy = st.one_of(command_words_strategy, free_text_strategy)


# ---- never-crash property ----------------------------------------------
@given(line=any_line_strategy)
@settings(max_examples=200, deadline=None)
def test_handle_command_never_crashes(line):
    sim = MotorControllerSim()
    response = sim.handle_command(line)
    assert isinstance(response, str)
    assert response.startswith("OK") or response.startswith("ERR")


# ---- SET_SPEED contract --------------------------------------------------
@given(v=st.floats(allow_nan=True, allow_infinity=True))
@settings(max_examples=200, deadline=None)
def test_set_speed_contract(v):
    sim = MotorControllerSim()
    response = sim.handle_command(f"SET_SPEED {v!r}")
    accepted = math.isfinite(v) and 0 <= v <= MAX_RPM
    if accepted:
        assert response == "OK"
        assert sim.target_rpm == v
    else:
        assert response.startswith("ERR")


# ---- watchdog contract ----------------------------------------------------
def _predict_watchdog_fault(budget: int, gaps: list[int]) -> bool:
    """Independent oracle: FAULT iff some gap (from enable or from a kick)
    reaches the budget before the next kick resets it."""
    remaining = budget
    for i, gap in enumerate(gaps):
        if gap >= remaining:
            return True
        if i < len(gaps) - 1:
            remaining = budget  # a kick follows every gap but the last
    return False


@given(
    budget=st.integers(min_value=1, max_value=1000),
    gaps=st.lists(st.integers(min_value=0, max_value=1050), min_size=1, max_size=5),
)
@settings(max_examples=200, deadline=None)
def test_watchdog_contract(budget, gaps):
    sim = MotorControllerSim()
    assert sim.handle_command(f"WDG_EN {budget}") == "OK"

    for i, gap in enumerate(gaps):
        sim.step(gap)
        if i < len(gaps) - 1:
            sim.handle_command("WDG_KICK")

    expected_fault = _predict_watchdog_fault(budget, gaps)
    assert (sim.state == "FAULT") == expected_fault


# ---- state-machine + FAULT latch invariants (stateful) --------------------
class ProtocolStateMachine(RuleBasedStateMachine):
    """Random valid+invalid commands interleaved with random step() counts."""

    def __init__(self):
        super().__init__()
        self.sim = MotorControllerSim()

    @rule(line=any_line_strategy)
    def send_command(self, line):
        was_fault = self.sim.state == "FAULT"
        first_word = line.strip().split()[0].upper() if line.strip() else ""
        is_reset = first_word == "RESET"
        self.sim.handle_command(line)
        if was_fault and not is_reset:
            assert self.sim.state == "FAULT", "FAULT must latch except for RESET"

    @rule(n=st.integers(min_value=0, max_value=20))
    def advance(self, n):
        was_fault = self.sim.state == "FAULT"
        self.sim.step(n)
        if was_fault:
            assert self.sim.state == "FAULT", "step() must never clear FAULT"

    @invariant()
    def state_and_telemetry_are_sane(self):
        assert self.sim.state in ("IDLE", "RUNNING", "FAULT")
        assert math.isfinite(self.sim.speed_rpm)
        assert math.isfinite(self.sim.temperature_c)
        assert 0 <= self.sim.speed_rpm <= MAX_RPM * 1.01


ProtocolStateMachine.TestCase.settings = settings(max_examples=200, deadline=None, stateful_step_count=30)
TestProtocolStateMachine = ProtocolStateMachine.TestCase
