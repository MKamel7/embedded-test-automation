# Embedded Test Automation Framework

![tests](https://github.com/MKamel7/embedded-test-automation/actions/workflows/ci.yml/badge.svg)

HIL-style automated testing for an embedded motor controller: a deterministic simulated device under test (DUT), a transport-abstracted device driver, and a pytest suite covering **protocol conformance**, **closed-loop behavior**, and **fault injection** — with HTML/JUnit reports generated on every push by GitHub Actions.

```
┌────────────────┐     ASCII protocol      ┌──────────────────────┐
│  pytest suite  │──▶ driver ──▶ Transport │  Device under test   │
│  20 tests      │            (swappable)  │  (simulated today,   │
│  3 categories  │◀── responses ◀──────────│   real UART later)   │
└────────────────┘                         └──────────────────────┘
```

## Why this design

- **Transport abstraction is the HIL upgrade path.** Tests talk to a `Transport` interface. Today it binds to an in-process simulator; replacing it with a pyserial implementation runs the *same suite* against real hardware — which is the whole point of hardware-in-the-loop test engineering.
- **Deterministic, step-based physics.** The DUT simulation advances in discrete steps, not wall-clock time: the full suite runs in ~0.1 s, never flakes in CI, and thermal scenarios (overheat trips, stall heating) are exactly reproducible.
- **Faults are latched, like real motor drivers.** Overheat and stall trip a `FAULT` state that stops the motor, rejects speed commands, and survives cooldown until an explicit `RESET` — and the suite verifies exactly that contract.

## Test categories

| File | Covers |
|---|---|
| `tests/test_protocol.py` | Command grammar, range limits (0–6000 rpm boundary-exact), error codes, malformed input |
| `tests/test_control_behavior.py` | Setpoint convergence (<1.7% after settling), monotonic ramp, thermal rise/cooldown |
| `tests/test_fault_injection.py` | Overheat trip, stall-to-overheat cascade, fault latching, command rejection in FAULT, telemetry availability during faults, RESET recovery |

## Run it

```bash
uv run --group dev pytest                  # full suite
uv run --group dev pytest --html=report.html --self-contained-html   # + report
```

(or classic: `pip install pytest && pytest`)

## Roadmap

- [ ] pyserial `Transport` + hardware profile for a real motor driver board
- [ ] Property-based protocol fuzzing (hypothesis)
- [ ] Measurement logging + trend plots across test runs
- [ ] Watchdog / communication-timeout test scenarios

## License

MIT — © 2026 Mo Kamel
