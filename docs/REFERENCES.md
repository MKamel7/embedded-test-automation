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
| Thermal class | 155 (F) | the insulation limit, 155 °C |
| Permitted winding overtemperature | dT = 100 K | the rated equilibrium, 140 °C |
| Rated ambient | 40 °C | `AMBIENT_C` |

**These are `[SERIES]`, not `[DS]`, and the source previously mislabelled them.**
A review grepped the archived article data sheet for `155`, `thermal`, `class`,
`insulation`, `IEC` and `60034` and found **zero hits for every one**. The only
ambient temperature in that document is 20 °C, in a footnote about brake holding
current. This page always said the article sheet carries no thermal data; the
code contradicted it, and every trip threshold descended from the mislabelled
lines.

**How forced is 100 K?** Less than previously claimed. IEC 60034-1 permits
**105 K by resistance** for class 155 (F); the 100 K used here is Siemens' own
more conservative figure, so it is a round number chosen one level up. What IS
derived is the separation: the rated equilibrium follows from the permitted rise,
the trip follows from the class limit, and they are independent numbers rather
than the same one used twice.
| Thermal class 155 (F), applied to the frame node | dT = 100 K | `HOUSING_LIMIT_C`, via the frame's steady state at that winding limit |

So `RATED_EQUILIBRIUM_C` is 40 + 100 = 140 °C and `OVERHEAT_LIMIT_C` is the class
limit, 155 °C, leaving **15 K of real margin** between normal duty and the trip.
An earlier version set the trip equal to the rated equilibrium, which left
3.4e-13 K: rated duty passed only because a geometric series converges from
below, and a 0.1% cooling degradation tripped a healthy drive.

### What this source does NOT provide

Recorded so nobody has to rediscover it:

- **No thermal time constant.** Siemens does not publish one for this motor, so
  `THERMAL_TIME_STEPS` and therefore `COOLING_RATE` are chosen rather than
  fitted, and are marked `[ILLUSTRATIVE]`. The heating coefficient
  `HEAT_AT_RATED_C` is **not** chosen: it is `[DERIVED]`, forced by requiring
  rated continuous duty to equilibrate at the permitted winding temperature per
  IEC 60034-1 S1 duty. See [`VALIDATION.md`](VALIDATION.md), which records that
  an earlier model failed exactly this criterion by a factor of 8.3.
- **No thermal network.** The data sheet gives one thermal class for the
  winding and nothing about how heat reaches the frame, so the second thermal
  node added in v1.5 is grounded in physics rather than in this document:
  `HOUSING_COUPLING` and `HOUSING_COOLING` are `[ILLUSTRATIVE]`. What *is*
  defensible without the data sheet is the ORDERING, that losses are generated
  in the winding and flow outward, so the frame is the cooler node while the
  machine is driven. The protection rests on that ordering, not on the two
  coefficients, which is why the cross check survives their values being wrong.
  `HOUSING_LIMIT_C` is then `[DERIVED]`: the frame temperature at which the
  winding would be exactly at its permitted 140 °C, so the second channel cannot
  be quietly tuned to whatever makes a test pass.
- **No control loop dynamics.** The speed response of the real system depends on
  the SINAMICS drive's loop tuning, not the motor alone. The model fits only the
  torque limited acceleration implied by rated torque and rotor inertia.

## Scope of the grounding

Naming a real device fixes the operating envelope and the protection
thresholds. It does not make this a validated model of that motor, and no such
claim is made anywhere in this repository. See the limitations section of
[`TEST_STRATEGY.md`](TEST_STRATEGY.md) for which results would survive contact
with hardware and which would not, and [`VALIDATION.md`](VALIDATION.md) for the
executable criteria that check the model against this data sheet, including the
two it originally failed.
