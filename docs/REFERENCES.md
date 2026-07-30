# References

Sources the simulated device under test is grounded in. Each one is archived
under `docs/datasheets/` rather than only linked, because a citation that
depends on a third party keeping a URL alive is not a citation. Anyone reading
this repo in a year should be able to check the numbers without a working link.

## D1. Siemens SIMOTICS S-1FK2 servomotor data sheet

| | |
|---|---|
| **Article number** | `1FK2105-6AF10-0SA0` |
| **Product** | SIMOTICS S-1FK2 permanent-magnet synchronous servomotor |
| **Drive** | SINAMICS S210, 3AC 400 V |
| **Publisher** | Siemens AG |
| **Document generated** | 27 June 2023 |
| **Retrieved** | 31 July 2026 |
| **Local archive** | [`datasheets/siemens-simotics-s-1fk2-1fk2105-6af10-0sa0.pdf`](datasheets/siemens-simotics-s-1fk2-1fk2105-6af10-0sa0.pdf) |
| **Source** | Conrad Electronic asset mirror of the Siemens data sheet, `asset.conrad.com/media10/add/160267/c1/-/en/002870894DS01/` |

The same article data is reachable from the Siemens Industry Mall document
service, and the wider series data appears in catalogue D 32 (SIMOTICS S-1FK2
servomotors for SINAMICS S210) and in the SIMOTICS S-1FK2 configuration manual
for SINAMICS S120.

### Values taken from it

Used directly in `src/dut_sim/motor_controller.py`:

| Parameter | Data sheet value | Used for |
|---|---|---|
| Maximum speed | 6,000 rpm | `MAX_RPM`, the `SET_SPEED` upper limit |
| Rated speed | 3,000 rpm | `RATED_RPM`, the speed dynamics fit |
| Rated torque | 6.60 Nm | `RATED_TORQUE_NM`, the speed dynamics fit |
| Maximum torque | 24.00 Nm | `MAX_TORQUE_NM` |
| Rated current | 5.6 A | `RATED_CURRENT_A`, the stall heating scale |
| Maximum current | 24.0 A | `MAX_CURRENT_A`, the stall heating scale |
| Rotor moment of inertia | 3.5 kgcm² | `ROTOR_INERTIA_KGM2`, the speed dynamics fit |
| Rated power | 2.10 kW | context only |
| Static torque | 8.00 Nm | context only |

### Values taken from the series documentation, not this data sheet

The per-article data sheet does not carry thermal data. The following comes
from the SIMOTICS S-1FK2 series documentation, and applies to this frame size
(1FK2.05):

| Parameter | Value | Used for |
|---|---|---|
| Thermal class | 155 (F) | the overheat trip |
| Permitted winding overtemperature | dT = 100 K per EN/IEC 60034-1 | the overheat trip |
| Rated ambient | 40 °C | `AMBIENT_C` |

`OVERHEAT_LIMIT_C` is therefore 40 + 100 = 140 °C, a derived limit rather than a
chosen round number.

### What this source does NOT provide

Recorded so nobody has to rediscover it:

- **No thermal time constant.** Siemens does not publish one for this motor, so
  `HEATING_PER_KRPM` and `COOLING_RATE` are chosen rather than fitted, and are
  marked `[ILLUSTRATIVE]` in the source.
- **No control loop dynamics.** The speed response of the real system depends on
  the SINAMICS drive's loop tuning, not the motor alone. The model fits only the
  torque limited acceleration implied by rated torque and rotor inertia.

## Scope of the grounding

Naming a real device fixes the operating envelope and the protection
thresholds. It does not make this a validated model of that motor, and no such
claim is made anywhere in this repository. See the limitations section of
[`TEST_STRATEGY.md`](TEST_STRATEGY.md) for which results would survive contact
with hardware and which would not.
