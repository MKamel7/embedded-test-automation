# Embedded Test Automation Framework

[![CI](https://github.com/MKamel7/embedded-test-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/MKamel7/embedded-test-automation/actions)
[![Tests](https://img.shields.io/badge/tests-129%20passing-brightgreen)](tests)
[![Mutation score](https://img.shields.io/badge/mutants%20killed-5%2F5-brightgreen)](tests)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://www.python.org)

HIL-style automated testing for an embedded motor controller: a deterministic simulated device under test (DUT), a transport-abstracted device driver, and a pytest suite covering **protocol conformance**, **closed-loop behavior**, and **fault injection**, with HTML/JUnit reports generated on every push by GitHub Actions.

```
┌────────────────┐     ASCII protocol      ┌──────────────────────┐
│  pytest suite  │──▶ driver ──▶ Transport │  Device under test   │
│  5/5 mutants   │            (swappable)  │  (simulated today,   │
│  129 tests     │◀── responses ◀──────────│   real UART later)   │
└────────────────┘                         └──────────────────────┘
```

## 🛠️ Built with

| | |
| --- | --- |
| **Language** | Python |
| **Testing** | pytest, Hypothesis property tests, mutation testing |
| **Architecture** | Transport-abstracted device driver, deterministic simulated DUT |
| **Reporting** | HTML and JUnit reports on every push |
| **Engineering** | GitHub Actions CI, quality gates |

## 🎯 Why this design

- **Transport abstraction is the HIL upgrade path.** Tests talk to a `Transport` interface. Today it binds to an in-process simulator; replacing it with a pyserial implementation runs the *same suite* against real hardware, which is the whole point of hardware-in-the-loop test engineering.
- **Deterministic, step-based physics.** The DUT simulation advances in discrete steps, not wall-clock time, so nothing here waits on a clock: the 105 deterministic tests run in **under three seconds**, never flake in CI, and thermal scenarios (overheat trips, stall heating) are exactly reproducible. The full suite takes about **45 seconds**, and essentially all of that is the property-based search in `test_protocol_fuzz.py` and `test_fuzz_efficacy.py` deliberately spending time looking for counterexamples. That is a budget, not slow physics.
- **Faults are latched, like real motor drivers.** Overheat and stall trip a `FAULT` state that stops the motor, rejects speed commands, and survives cooldown until an explicit `RESET`, and the suite verifies exactly that contract.

## 🧪 Test categories

| File | Covers |
|---|---|
| `tests/test_protocol.py` | Command grammar, range limits (0 to 6000 rpm, boundary-exact), error codes, malformed input |
| `tests/test_control_behavior.py` | Setpoint convergence (<1.7% after settling), monotonic ramp, thermal rise/cooldown |
| `tests/test_fault_injection.py` | Overheat trip, stall-to-overheat cascade, fault latching, command rejection in FAULT, telemetry availability during faults, RESET recovery |
| `tests/test_watchdog.py` | Software watchdog: enable/kick/disable, exact-budget trip, latched fault, RESET recovery, range validation |
| `tests/test_protocol_fuzz.py` | Property-based fuzzing (hypothesis): never-crash contract, state-machine invariants, FAULT-latch invariant, SET_SPEED and watchdog contracts |
| `tests/test_reset_contract.py` | What RESET clears and what it must not: the controller is cleared, the machine is not |
| `tests/test_fuzz_efficacy.py` | Fault seeding: five deliberately broken controllers that the property suite must reject |
| `tests/test_serial_hil.py` | The pyserial path over a real PTY pair from `socat`: the DUT answers the protocol across a kernel tty, not an echo |

## 🖥️ The device under test

The DUT is a simulation, but its envelope and protection thresholds are taken
from a real device rather than invented: a **Siemens SIMOTICS S-1FK2**
permanent-magnet synchronous servomotor, article `1FK2105-6AF10-0SA0`, on a
SINAMICS S210 drive. The data sheet is archived in this repo and cited in full
in [`docs/REFERENCES.md`](docs/REFERENCES.md).

| From the data sheet | Value | Used for |
|---|---|---|
| Maximum speed | 6,000 rpm | `SET_SPEED` upper range limit |
| Rated speed / torque | 3,000 rpm / 6.60 Nm | speed dynamics fit |
| Rotor inertia | 3.5 kgcm² | speed dynamics fit |
| Rated / maximum current | 5.6 A / 24.0 A | stall heating scale |
| Thermal class | 155 (F), dT = 100 K at 40 °C ambient | 140 °C overheat trip |

The speed dynamics are **fitted**: at rated torque the torque-limited
acceleration is 6.60 / 3.5e-4 = 18,857 rad/s², so the rotor reaches rated speed
in 16.7 ms, and the first-order constant is chosen to settle in the same time
with 1 step = 1 ms. Only the time is matched, not the shape, since a real servo
under torque limit ramps linearly rather than exponentially.

The thermal dynamics are **not** fitted and cannot be. Siemens does not publish
a thermal time constant, and more fundamentally a 2 kW servo's winding thermal
constant is minutes while its mechanical response is milliseconds. Modelling
both on one step size would need millions of steps to reach a thermal trip. The
thermal time scale is deliberately compressed so a thermal fault is reachable in
a short test, so thermal latencies are in steps only and never in seconds.

## 📈 Characterization

Beyond pass/fail, the harness *measures* the controller. `scripts/characterize.py` sweeps parameters over fresh device instances and `scripts/plot_characterization.py` renders the curves:

![Controller characterization](docs/characterization.png)

| Sweep | Result |
|---|---|
| Peak winding temperature vs. target speed | Smooth rise 42.0 to 64.1 °C across 500 to 6000 rpm, well under the 140 °C protection limit |
| Settling time vs. target speed | Monotonic 10 to 20 steps (1 step = 1 ms), higher setpoints take longer to reach the ±50 rpm band |
| Watchdog trip latency vs. budget | Exact diagonal (latency = budget) across 2 to 200 steps, verifying watchdog timing precision |

Property-based fuzzing (`test_protocol_fuzz.py`) runs 200 examples per property against fresh device instances and found **no invariant violations**: the protocol never raises on arbitrary input, and `FAULT` provably never clears except immediately after `RESET`.

## 🧬 Mutation score: 5 killed of 5 non-equivalent mutants

**This is the number to read, not the coverage figure.** Coverage says every
line ran. It cannot say an assertion would have noticed if the line were
wrong, and a suite at 100% coverage with no assertions scores exactly the same
as this one. Fault seeding asks the question coverage cannot: if the
controller were broken in a specific, realistic way, would this suite go red?

Five known bugs are seeded into copies of the controller.
`test_fuzz_efficacy.py` asserts the same properties reject each of them while
still passing on the clean implementation, and a further test asserts that
every mutant in the registry has a search, so the score above cannot be
rounded up by adding a mutant and forgetting to hunt it.

All five are non-equivalent by construction: each changes observable behaviour
at a stated input, and the minimal input is recorded below rather than
asserted to exist.

| Seeded defect | Minimal input that exposes it |
|---|---|
| A latched `FAULT` cleared by an unknown command instead of only by `RESET` | any unrecognised line |
| `SET_SPEED` boundary written `<= MAX + 1` | `SET_SPEED 6001` |
| Watchdog countdown compared `< 0` instead of `<= 0`, firing one step late | a 1 step budget |
| `reset()` zeroes the thermal model, so a hot motor reads cold | 1 step at full speed |
| `reset()` restores the state machine but leaves the watchdog armed, so the device reboots into a fault loop | a 1 step budget |

**The fourth one is not hypothetical: it shipped here.** An earlier version of
`reset()` cleared the thermal model while its docstring and a release note both
said it did not, and the suite missed it, because the tests asserted the RESET
*command* is refused while the motor is hot. That is a different property, and
`reset()` is reachable through the driver without passing that gate at all.
`tests/test_reset_contract.py` now tests the postcondition on both paths.

This found a real weakness in the suite. The seeded range defect initially
**survived**: `SET_SPEED` was fuzzed with unbounded floats, and only values in
`(6000, 6001]` expose a one unit boundary error, so the property essentially
never generated one. Both the property and the efficacy search now sample the
neighbourhood of each documented limit, which is boundary value analysis
expressed as a strategy.

## 📐 Test design

Techniques are chosen deliberately, and they are the named ones rather than
whatever the code suggested: equivalence partitioning and boundary value
analysis on the command ranges, state transition testing across
IDLE/RUNNING/FAULT with the FAULT latch rule, fault injection through a test
backdoor, property based testing with a stateful model, and fault seeding to
verify the suite itself.

Those are the techniques ISTQB catalogues and that IEC 61508-3 expects to see
named in a software verification plan. This project claims neither
certification nor compliance with either: it claims that the test design was
made on purpose and can be argued with, which is the part a reviewer can
actually check. Scope, entry and exit criteria, risk based
prioritisation and the honest limits are in
[`docs/TEST_STRATEGY.md`](docs/TEST_STRATEGY.md).

## ✅ Quality gates

CI enforces all of these on Python 3.10 and 3.12, and the build fails on any:

- 129 tests pass
- **5 of 5 seeded defects killed** (`test_fuzz_efficacy.py`), and every mutant in
  the registry has a search, so the score cannot be rounded up by forgetting one
- **100% statement and branch coverage** of the DUT and testbench
  (`--cov-fail-under=100`)
- `ruff check` clean
- `mypy --strict` clean

## 📝 Measurement logging

Behavior and fault tests record real metrics (settling steps, peak temperature, trip latencies) to timestamped CSVs via a session fixture; `scripts/plot_trends.py` charts a metric across runs for regression tracking. See `measurements/sample-run.csv`.

## ▶️ Run it

```bash
uv run --group dev pytest                  # full suite, with coverage
uv run --group dev ruff check .            # lint
uv run --group dev mypy                    # strict type check
uv run --group dev pytest --html=report.html --self-contained-html   # + report
```

(or classic: `pip install pytest && pytest`)

## 💡 What I learned

- **Test the thing you ship, not the thing you simulated.** The transport abstraction
  exists so the same suite runs against the simulator and against real hardware without
  a line changing. Without it I would have had a beautifully tested simulator and no
  evidence about the device.

- **A deterministic device under test is worth the effort it costs.** Every flaky test
  I have ever chased came from something non-deterministic underneath. Making the
  simulated controller reproducible removed a whole category of debugging that
  otherwise eats your evenings.

- **Coverage says a line ran, not that a test would notice it breaking.** Mutation
  testing is what closes that gap. Deliberately breaking the controller in five ways
  and confirming the suite caught all five tells me something 100 percent coverage
  never could.

- **Protocol conformance and closed-loop behaviour are different questions.** One asks
  whether the device speaks correctly, the other whether it acts correctly. Keeping
  them as separate categories stopped me writing tests that quietly checked both and
  proved neither.

## 🔭 Future improvements

- [ ] **A real board** (STM32 or ESP32 class) over UART, with corruption, disconnect and reconnect, power cycling, timing jitter, hardware watchdog and GPIO fault injection. A logic-analyser trace on a failing test would be the best evidence here
- [ ] **Grow the mutant set with `mutmut` or `cosmic-ray`**, after the state-machine suite, so survivors are triaged once rather than twice
- [ ] Hypothesis `target()`-guided fault-state coverage

Not doing: making the DUT more realistic. The determinism is the feature; adding wall-clock behaviour would trade exact reproducibility for realism this does not need.

## 📄 License

MIT · © 2026 Mo Kamel

---

Built by **Mo Kamel**, M.Eng. Mechatronic and Cyber-Physical Systems, Technische
Hochschule Deggendorf.
[Portfolio](https://mkamel7.github.io) · [LinkedIn](https://linkedin.com/in/mo-kamel7)
