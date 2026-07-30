"""Prove the fuzzer catches real defects, not just that it runs.

`test_protocol_fuzz.py` finds no violations against the shipped controller.
On its own that is weak evidence: a suite of properties that never fail could
mean the implementation is correct, or it could mean the properties are too
loose to fail at all. The way to tell the two apart is fault seeding: inject
known defects and check the same properties reject them.

Each test below searches for a minimal counterexample against a mutant from
`dut_sim.seeded_defects` and asserts one exists, then asserts that the SAME
search finds nothing against the clean implementation. That second half is what
makes the result meaningful: it shows the property discriminates between the
defective and the correct controller rather than failing on everything.

hypothesis.find() shrinks to the smallest failing input, so the assertions also
record the exact boundary each defect breaks, which is the number worth quoting
in a defect report.
"""

import math

import pytest
from hypothesis import HealthCheck, find, settings
from hypothesis import strategies as st
from hypothesis.errors import NoSuchExample

from dut_sim.motor_controller import MAX_RPM, MotorControllerSim
from dut_sim.seeded_defects import (
    FaultLatchLeak,
    SpeedRangeOffByOne,
    WatchdogOffByOne,
)

# find() drives its own search; the health checks about function-scoped
# fixtures and slow data generation do not apply here.
SEARCH = settings(max_examples=500, deadline=None,
                  suppress_health_check=list(HealthCheck))


def _search(strategy, predicate):
    """Minimal input satisfying predicate, or None if there is none."""
    try:
        return find(strategy, predicate, settings=SEARCH)
    except NoSuchExample:
        return None


# ---- defect 1: a latched fault that does not latch -------------------------
def _clears_fault_without_reset(cls) -> "callable":
    def violates(line: str) -> bool:
        sim = cls()
        sim.trip_fault()
        if line.strip().split()[:1] == ["RESET"]:
            return False            # RESET is allowed to clear it
        sim.handle_command(line)
        return sim.state != "FAULT"
    return violates


def test_fault_latch_leak_is_detected():
    lines = st.text(max_size=40)
    counterexample = _search(lines, _clears_fault_without_reset(FaultLatchLeak))
    assert counterexample is not None, (
        "the FAULT-latch property failed to detect a controller that clears "
        "FAULT on an unknown command"
    )
    # The shrunk input is whatever the parser treats as unknown; the empty
    # string is the smallest such line.
    assert _clears_fault_without_reset(FaultLatchLeak)(counterexample)


def test_fault_latch_property_passes_on_clean_implementation():
    lines = st.text(max_size=40)
    assert _search(lines, _clears_fault_without_reset(MotorControllerSim)) is None


# ---- defect 2: an off-by-one range boundary --------------------------------
# Boundary value analysis, expressed as a strategy. A uniform draw over the
# whole range is almost useless here: exactly one value out of ~6200 triggers a
# one-unit boundary defect, so a 500 example search finds it about 8% of the
# time. Seeding this defect is what exposed that blind spot, and the same
# neighbourhood sampling is now used by the SET_SPEED property itself.
RPM_STRATEGY = st.one_of(
    st.integers(min_value=-100, max_value=MAX_RPM + 100),   # the broad range
    st.integers(min_value=MAX_RPM - 3, max_value=MAX_RPM + 3),  # upper boundary
    st.integers(min_value=-3, max_value=3),                     # lower boundary
)


def _accepts_out_of_range(cls) -> "callable":
    def violates(rpm: int) -> bool:
        sim = cls()
        accepted = sim.handle_command(f"SET_SPEED {rpm}") == "OK"
        return accepted and not 0 <= rpm <= MAX_RPM
    return violates


def test_speed_range_off_by_one_is_detected():
    counterexample = _search(RPM_STRATEGY, _accepts_out_of_range(SpeedRangeOffByOne))
    assert counterexample is not None, (
        "the SET_SPEED range property failed to detect a controller accepting "
        "a speed above MAX_RPM"
    )
    # Shrinking pins the defect to the exact boundary, which is the number a
    # defect report should carry.
    assert counterexample == MAX_RPM + 1, (
        f"expected the minimal violation at {MAX_RPM + 1} rpm, got {counterexample}"
    )


def test_speed_range_property_passes_on_clean_implementation():
    assert _search(RPM_STRATEGY, _accepts_out_of_range(MotorControllerSim)) is None


# ---- defect 3: a watchdog that trips one step late -------------------------
def _watchdog_trips_late(cls) -> "callable":
    def violates(budget: int) -> bool:
        sim = cls()
        sim.handle_command(f"WDG_EN {budget}")
        sim.step(budget)            # exactly the budget: must have tripped
        return sim.state != "FAULT"
    return violates


def test_watchdog_off_by_one_is_detected():
    budgets = st.integers(min_value=1, max_value=1000)
    counterexample = _search(budgets, _watchdog_trips_late(WatchdogOffByOne))
    assert counterexample is not None, (
        "the watchdog timing property failed to detect a controller that trips "
        "one step after its budget"
    )
    assert counterexample == 1, (
        f"expected the minimal violation at a 1 step budget, got {counterexample}"
    )


def test_watchdog_property_passes_on_clean_implementation():
    budgets = st.integers(min_value=1, max_value=1000)
    assert _search(budgets, _watchdog_trips_late(MotorControllerSim)) is None


# ---- every seeded defect must be caught by something ------------------------
@pytest.mark.parametrize(
    "name,cls,strategy,predicate_factory",
    [
        ("fault_latch_leak", FaultLatchLeak, st.text(max_size=40),
         _clears_fault_without_reset),
        ("speed_range_off_by_one", SpeedRangeOffByOne,
         RPM_STRATEGY, _accepts_out_of_range),
        ("watchdog_off_by_one", WatchdogOffByOne,
         st.integers(min_value=1, max_value=1000), _watchdog_trips_late),
    ],
)
def test_no_seeded_defect_survives(name, cls, strategy, predicate_factory):
    """Mutation score: no mutant may survive the property suite."""
    assert _search(strategy, predicate_factory(cls)) is not None, (
        f"seeded defect '{name}' survived the property suite"
    )


def test_clean_controller_survives_none_of_the_searches():
    """Sanity check the searches are not simply always-true."""
    checks = [
        (st.text(max_size=40), _clears_fault_without_reset(MotorControllerSim)),
        (RPM_STRATEGY, _accepts_out_of_range(MotorControllerSim)),
        (st.integers(min_value=1, max_value=1000),
         _watchdog_trips_late(MotorControllerSim)),
    ]
    for strategy, predicate in checks:
        assert _search(strategy, predicate) is None
    assert math.isfinite(MotorControllerSim().temperature_c)
