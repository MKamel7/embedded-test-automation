# Test strategy

What is tested, how it is tested, and what this suite deliberately does not
cover. Written the way a validation team would scope a device: technique first,
then the honest limits.

## 1. Scope and item under test

The item under test is a motor controller exposing a line based ASCII command
protocol over a byte transport. Today the implementation is a deterministic
simulation (`dut_sim.motor_controller`); the same suite is intended to run
against a physical board over UART without modification.

In scope: protocol conformance, closed loop speed behaviour, thermal
protection, stall handling, the software watchdog, and the transport layer.

Out of scope: electrical characteristics, EMC, mechanical load, timing in
wall clock units (the DUT advances in discrete steps), and anything requiring
real silicon.

## 2. Test levels

| Level | Where | What it establishes |
|---|---|---|
| Unit / component | `test_protocol.py` | Each command is parsed and answered per the grammar |
| Integration | `test_serial_transport.py` | Driver and transport agree on framing and timeouts |
| System / behavioural | `test_control_behavior.py`, `test_fault_injection.py`, `test_watchdog.py` | The controller behaves correctly as a closed loop under normal and fault conditions |
| Suite verification | `test_fuzz_efficacy.py` | The tests themselves can detect defects |

## 3. Techniques used

These are chosen deliberately, not ad hoc:

- **Equivalence partitioning.** Speed commands split into rejected below range,
  accepted in range, and rejected above range; each partition has cases.
- **Boundary value analysis.** The documented limits (0 and `MAX_RPM`) are
  tested exactly on, immediately inside and immediately outside. The
  property based strategies also sample the neighbourhood of each limit, after
  fault seeding showed that a uniform draw over the whole range almost never
  generates the one value that exposes a one unit boundary defect.
- **State transition testing.** IDLE, RUNNING and FAULT with the legal and
  illegal transitions between them, including the rule that FAULT latches and
  only `RESET` clears it.
- **Fault injection.** Overheat and stall are injected through a test backdoor
  that is not part of the protocol, so the fault paths are reachable without
  waiting for physics.
- **Property based testing.** Invariants that must hold for all inputs, checked
  by hypothesis against generated commands, including a stateful model that
  interleaves random commands with random time advances.
- **Fault seeding (mutation testing).** Known defects are injected into copies
  of the DUT and the suite must fail on each. See section 5.
- **Measurement.** Behavioural tests record real numbers (settling steps, peak
  temperature, trip latency) rather than only asserting pass or fail.

## 4. Entry and exit criteria

Entry: the package imports and the DUT answers `GET_STATE`.

Exit, enforced in CI on Python 3.10 and 3.12:

- every test passes
- statement and branch coverage of the DUT and testbench is 100%, gated with
  `--cov-fail-under=100`
- `ruff check` reports nothing
- `mypy --strict` reports nothing
- no seeded defect survives the property suite

## 5. Verifying the tests themselves

A property suite that never fails proves only that it ran. To show the
properties can actually detect defects, `dut_sim.seeded_defects` contains three
deliberately broken controllers, each a realistic embedded bug:

| Seeded defect | Nature | Minimal input that exposes it |
|---|---|---|
| `FaultLatchLeak` | A latched fault is cleared by an unknown command instead of only by `RESET` | any unrecognised line |
| `SpeedRangeOffByOne` | Boundary written as `<= MAX + 1` | `SET_SPEED 6001` |
| `WatchdogOffByOne` | Countdown compared `< 0` instead of `<= 0`, so the timer fires one step late | a 1 step budget |

`test_fuzz_efficacy.py` asserts each defect is found, and that the same search
finds nothing against the clean implementation. That second half matters: it
shows the property discriminates between a broken and a correct controller
rather than failing on everything.

This is also how the boundary blind spot in section 3 was found. The seeded
range defect initially survived, because the strategy drew uniformly across
roughly 6200 values of which exactly one exposes the bug.

## 6. Risk based prioritisation

Ordered by consequence on real hardware:

1. **Fault latching.** A protection fault that silently clears can destroy
   hardware. Covered by dedicated tests, a stateful invariant, and a seeded
   defect.
2. **Watchdog timing.** A watchdog that fires late fails its purpose. Covered
   by an exact budget test, a property with an independent oracle, and a seeded
   off by one.
3. **Range limits.** Out of range acceptance drives the motor beyond its rating.
   Covered by boundary cases, a property, and a seeded defect.
4. **Convergence and thermal behaviour.** Wrong but not dangerous. Covered
   behaviourally and by characterisation sweeps.

## 7. Known limitations

Stated because they bound what the results mean:

- **The DUT is a simulation, partially grounded in a real device.** The
  operating envelope and protection thresholds come from the Siemens SIMOTICS
  S-1FK2 data sheet (article `1FK2105-6AF10-0SA0`): 6,000 rpm maximum speed,
  and a 140 °C overheat trip from thermal class 155 (F) with dT = 100 K at 40 °C
  ambient. The speed dynamics are fitted to the data sheet's rated torque and
  rotor inertia. Nothing here validates a real driver stage, and this is not a
  validated model of that motor.

  Worth separating, because it decides which results would survive contact with
  hardware. The command grammar, the range checks, the FAULT latch and the
  watchdog are state machine and counter logic, so their verification is
  independent of the physics and transfers unchanged. The overheat trip, the
  stall heating path, and any latency expressed in real time units depend on the
  model and would not.

- **The thermal time scale is deliberately compressed.** A 2 kW servo's winding
  thermal time constant is minutes; its mechanical response is milliseconds.
  Modelling both faithfully on one step size would need millions of steps to
  reach a thermal trip. The thermal rates are therefore chosen, not fitted, and
  the ratio of thermal to mechanical response in this model is not physical.
  Thermal latencies are reported in steps and must never be quoted in seconds.
- **Time is in steps, not seconds.** This makes the suite fast and perfectly
  reproducible, and it means every latency figure is in simulation steps. They
  become meaningful in real units only against real hardware.
- **Measurements have no spread.** The simulation is deterministic, so repeated
  runs give identical numbers and the CSV trend has zero variance by
  construction. On real hardware the same logging would show a distribution,
  and reporting a mean with a spread would then be the honest form.
- **The serial path is tested against a loopback**, not a physical device. It
  verifies framing, encoding and timeout handling, not electrical behaviour.
- **Coverage is not correctness.** 100% branch coverage means every branch ran,
  not that every behaviour is right. The property tests and seeded defects
  exist because coverage alone is a weak signal.
