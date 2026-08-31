"""A model of the protocol, run beside the device and compared after every step.

WHY THIS EXISTS ALONGSIDE `test_protocol_fuzz.py`. That file already drives a
`RuleBasedStateMachine`, and what it checks is that nothing catastrophic
happens: the state stays in its set, the numbers stay finite, FAULT latches.
Those are invariants, and an invariant can only catch a controller that goes
somewhere impossible. It cannot catch one that goes somewhere possible and
wrong, and most protocol defects are exactly that: a command accepted in a
state that should refuse it, a counter reloaded when it should not have been, a
reply that says OK about something that did not happen.

So this file carries a **model**: an independent, deliberately simple
reimplementation of the parts of the contract that can be predicted exactly,
which is then required to agree with the device after every single command.
Disagreement is the failure, rather than a bound being crossed.

WHAT THE MODEL DOES NOT MODEL, and why that is the interesting boundary. It
does not model the thermal physics. Reimplementing the plant would either be a
copy of the code under test, which proves nothing, or a second physics with its
own bugs. So the model predicts the DISCRETE contract exactly and treats a
thermal or overload trip as an event it is allowed to be surprised by:

  the model may not predict a trip the device makes  (physics it cannot see)
  the device may never leave FAULT without a RESET   (a rule, and enforced)
  the model MUST predict a watchdog trip             (pure counting, no physics)

That asymmetry is the honest one. Everything countable is asserted exactly, and
everything physical is bounded rather than predicted.
"""

from __future__ import annotations

from hypothesis import HealthCheck, assume, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from dut_sim.motor_controller import (
    MAX_RPM,
    RESET_HYSTERESIS_K,
    THERMAL_TRIP_C,
    WDG_MAX_STEPS,
    WDG_MIN_STEPS,
    MotorControllerSim,
)

#: Everything the protocol recognises. Used to keep the garbage rule honest.
KNOWN_COMMANDS = frozenset({
    "SET_SPEED", "GET_SPEED", "GET_TEMP", "GET_OVERLOAD", "GET_HOUSING_TEMP",
    "GET_HEALTH", "GET_FAULT", "GET_STATE", "STOP", "RESET", "WDG_EN",
    "WDG_KICK", "WDG_DIS",
})


class ProtocolModel:
    """What the contract says should happen, tracked independently."""

    def __init__(self) -> None:
        self.state = "IDLE"
        self.target_rpm = 0.0
        self.wdg_enabled = False
        self.wdg_budget = 0
        self.wdg_remaining = 0
        #: Set once the device trips for a reason the model cannot predict.
        #: After that the model stops predicting state and only enforces the
        #: latch rule, because a thermal trip changes what every later command
        #: is allowed to do.
        self.surprised = False

    @property
    def faulted(self) -> bool:
        return self.state == "FAULT"

    def set_speed(self, rpm: float) -> str:
        if self.faulted:
            return "ERR STATE"
        if not 0 <= rpm <= MAX_RPM:
            return "ERR RANGE"
        self.target_rpm = rpm
        self.state = "RUNNING" if rpm > 0 else "IDLE"
        return "OK"

    def stop(self) -> str:
        self.target_rpm = 0.0
        if self.state == "RUNNING":
            self.state = "IDLE"
        return "OK"

    def wdg_enable(self, steps: int) -> str:
        if not WDG_MIN_STEPS <= steps <= WDG_MAX_STEPS:
            return "ERR RANGE"
        self.wdg_enabled = True
        self.wdg_budget = steps
        self.wdg_remaining = steps
        return "OK"

    def wdg_kick(self) -> str:
        if not self.wdg_enabled or self.faulted:
            return "ERR STATE"
        self.wdg_remaining = self.wdg_budget
        return "OK"

    def wdg_disable(self) -> str:
        self.wdg_enabled = False
        return "OK"

    def step(self, n: int) -> bool:
        """Advance n steps. Returns True if the WATCHDOG must have tripped."""
        tripped = False
        for _ in range(n):
            if self.wdg_enabled and not self.faulted:
                self.wdg_remaining -= 1
                if self.wdg_remaining <= 0:
                    self.state = "FAULT"
                    tripped = True
        return tripped

    def reset(self) -> None:
        """What RESET clears. The machine, deliberately, is not touched."""
        self.state = "IDLE"
        self.target_rpm = 0.0
        self.wdg_enabled = False
        self.wdg_budget = 0
        self.wdg_remaining = 0
        # The controller is back to a state the model knows exactly, so it can
        # resume predicting even after a thermal trip it could not foresee.
        self.surprised = False

    def observe_fault(self) -> None:
        """The device tripped for a reason outside the model. Accept it."""
        self.state = "FAULT"
        self.surprised = True


class ModelledProtocol(RuleBasedStateMachine):
    """Drive the device and the model together, and require them to agree."""

    def __init__(self) -> None:
        super().__init__()
        self.sim = MotorControllerSim()
        self.model = ProtocolModel()

    # ---- helpers ------------------------------------------------------------

    def _sync_after(self, expected_reply: str, actual_reply: str, what: str) -> None:
        """Compare replies, then let the model absorb an unpredicted trip."""
        device_faulted = self.sim.state == "FAULT"

        if not self.model.surprised and not (device_faulted and not self.model.faulted):
            assert actual_reply == expected_reply, (
                f"{what}: device said {actual_reply!r}, contract says "
                f"{expected_reply!r}")

        if device_faulted and not self.model.faulted:
            # Thermal or overload: physics the model deliberately does not
            # carry. Absorb it and keep enforcing the latch from here.
            self.model.observe_fault()

    # ---- commands -----------------------------------------------------------

    @rule(rpm=st.one_of(
        st.integers(min_value=-50, max_value=MAX_RPM + 50),
        st.sampled_from([0, 1, MAX_RPM - 1, MAX_RPM, MAX_RPM + 1]),
    ))
    def set_speed(self, rpm: int) -> None:
        expected = self.model.set_speed(float(rpm))
        actual = self.sim.handle_command(f"SET_SPEED {rpm}")
        self._sync_after(expected, actual, f"SET_SPEED {rpm}")

    @rule()
    def stop(self) -> None:
        expected = self.model.stop()
        actual = self.sim.handle_command("STOP")
        self._sync_after(expected, actual, "STOP")

    @rule(steps=st.one_of(
        st.integers(min_value=WDG_MIN_STEPS, max_value=WDG_MAX_STEPS),
        st.sampled_from([WDG_MIN_STEPS - 1, WDG_MIN_STEPS, WDG_MAX_STEPS,
                         WDG_MAX_STEPS + 1]),
    ))
    def wdg_enable(self, steps: int) -> None:
        expected = self.model.wdg_enable(steps)
        actual = self.sim.handle_command(f"WDG_EN {steps}")
        self._sync_after(expected, actual, f"WDG_EN {steps}")

    @rule()
    def wdg_kick(self) -> None:
        expected = self.model.wdg_kick()
        actual = self.sim.handle_command("WDG_KICK")
        self._sync_after(expected, actual, "WDG_KICK")

    @rule()
    @precondition(lambda self: self.sim.state == "FAULT" and self.sim._wdg_enabled)
    def kick_while_faulted(self) -> None:
        """A kick must not reload the budget of a latched machine.

        Reached by a precondition rather than by hope. Adding the RESET rule
        made the machine leave FAULT quickly, so a random walk stopped landing
        here often enough to catch a seeded defect that removes the guard, and
        a check that only sometimes runs is not a check. This is what
        preconditions are for: naming the state the property is about.
        """
        before = self.sim._wdg_remaining
        reply = self.sim.handle_command("WDG_KICK")

        assert reply == "ERR STATE", f"kick accepted in FAULT: {reply!r}"
        assert self.sim._wdg_remaining == before, (
            "a refused kick still reloaded the watchdog budget")

    @rule()
    def wdg_disable(self) -> None:
        expected = self.model.wdg_disable()
        actual = self.sim.handle_command("WDG_DIS")
        self._sync_after(expected, actual, "WDG_DIS")

    @rule(n=st.integers(min_value=1, max_value=25))
    def advance(self, n: int) -> None:
        must_trip = self.model.step(n)
        self.sim.step(n)

        if must_trip:
            # Pure counting, no physics involved, so this one is exact: a
            # watchdog whose budget has been consumed MUST have tripped.
            assert self.sim.state == "FAULT", (
                f"watchdog budget exhausted over {n} steps and the device is "
                f"{self.sim.state}")
        if self.sim.state == "FAULT" and not self.model.faulted:
            self.model.observe_fault()

    @rule()
    def reset(self) -> None:
        """RESET is gated on temperature, so the model asks the device first.

        The model deliberately carries no thermal state, so it cannot predict
        whether the gate will refuse. What it CAN do is insist the refusal is
        for the stated reason: refused only while hot, accepted only while
        cool. That keeps the check real without reimplementing the physics.
        """
        hot = self.sim.read_winding_c() >= THERMAL_TRIP_C - RESET_HYSTERESIS_K
        reply = self.sim.handle_command("RESET")

        if hot:
            assert reply == "ERR STATE", (
                f"RESET accepted at {self.sim.read_winding_c():.1f} C, which is "
                f"inside the hysteresis band")
        else:
            assert reply == "OK", f"RESET refused while cool: {reply!r}"
            self.model.reset()

    @rule()
    @precondition(lambda self: True)
    def read_telemetry(self) -> None:
        """Diagnosability: telemetry answers in every state, including FAULT."""
        for command in ("GET_SPEED", "GET_TEMP", "GET_STATE", "GET_HEALTH",
                        "GET_FAULT", "GET_OVERLOAD", "GET_HOUSING_TEMP"):
            assert self.sim.handle_command(command).startswith("OK "), command

    @rule(line=st.text(max_size=12))
    def send_garbage(self, line: str) -> None:
        """Unrecognised input must not disturb anything the model tracks.

        Only genuinely unrecognised lines are sent. The first version did not
        filter, so when the generator happened to produce a real command the
        device acted on it while the model knew nothing, and the machine then
        reported a disagreement that was its own fault rather than the
        device's. That is the failure mode a model based test has and an
        invariant based one does not: the model can be wrong too.
        """
        first = line.strip().split()[:1]
        assume(not first or first[0].upper() not in KNOWN_COMMANDS)

        before = (self.sim.state, self.sim.target_rpm)
        reply = self.sim.handle_command(line)

        assert reply == "ERR UNKNOWN", f"{line!r} -> {reply!r}"
        assert (self.sim.state, self.sim.target_rpm) == before, (
            f"an unrecognised line changed the machine: {line!r}")

    # ---- invariants ---------------------------------------------------------

    @invariant()
    def the_device_never_leaves_fault_on_its_own(self) -> None:
        """The one rule that holds whether or not the model saw the cause."""
        if self.model.faulted:
            assert self.sim.state == "FAULT", (
                "the device left FAULT without a RESET")

    @invariant()
    def the_reported_state_matches_the_model_until_physics_intervenes(self) -> None:
        if not self.model.surprised:
            assert self.sim.state == self.model.state, (
                f"device {self.sim.state}, contract {self.model.state}")

    @invariant()
    def the_watchdog_registers_agree_while_the_model_is_still_predicting(self) -> None:
        if self.model.surprised or self.model.faulted:
            return
        assert self.sim._wdg_enabled == self.model.wdg_enabled
        if self.model.wdg_enabled:
            assert self.sim._wdg_budget == self.model.wdg_budget
            assert self.sim._wdg_remaining == self.model.wdg_remaining, (
                f"remaining: device {self.sim._wdg_remaining}, "
                f"contract {self.model.wdg_remaining}")


ModelledProtocol.TestCase.settings = settings(
    max_examples=250, deadline=None, stateful_step_count=40,
    suppress_health_check=[HealthCheck.filter_too_much],
)
TestModelledProtocol = ModelledProtocol.TestCase


# ---- contracts that must not depend on a random walk finding them -----------

def test_a_kick_is_refused_in_fault_and_does_not_reload_the_budget():
    """The stateful machine explores this; this test guarantees it is checked.

    A precondition-guarded rule only fires when the engine happens to pick it
    in a state where the precondition holds, and adding the RESET rule made the
    machine leave FAULT quickly enough that a seeded defect removing the guard
    survived a full run. A contract this specific deserves a test that runs
    every time rather than one that usually does.
    """
    sim = MotorControllerSim()
    assert sim.handle_command("WDG_EN 3") == "OK"
    sim.step(5)
    assert sim.state == "FAULT" and sim.fault_reason == "WATCHDOG"
    assert sim._wdg_enabled, "the fixture needs the watchdog still armed"

    before = sim._wdg_remaining
    reply = sim.handle_command("WDG_KICK")

    assert reply == "ERR STATE", f"kick accepted in FAULT: {reply!r}"
    assert sim._wdg_remaining == before, (
        "a refused kick still reloaded the watchdog budget, so a supervisor "
        "that kept kicking a latched machine would look alive")


def test_a_kick_is_refused_when_the_watchdog_was_never_enabled():
    sim = MotorControllerSim()

    assert sim.handle_command("WDG_KICK") == "ERR STATE"
