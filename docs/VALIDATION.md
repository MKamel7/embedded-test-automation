# Model validation against the data sheet

Verification asks whether the code does what its specification says. Validation
asks whether the specification was the right one. This document is the second
one, and it is the shorter and less comfortable of the two.

Every criterion below is executable, in `tests/test_datasheet_validation.py`, and
runs in CI. A validation report that is not run is a claim about the past.

## Scope, stated before the results

The only external reference available without hardware is the published data for
the reference device: Siemens SIMOTICS S-1FK2, article `1FK2105-6AF10-0SA0`,
archived in `docs/datasheets/` and cited in `REFERENCES.md`.

So this validates the model **against that document**. It does not validate the
model against a motor. Nothing here has been run on real hardware, and the
distinction matters: agreeing with a data sheet is necessary and nowhere near
sufficient.

## Results

| ID | Criterion | Basis | Result |
|---|---|---|---|
| V-01 | Reaches rated speed in the torque limited time | M/J from rated torque and rotor inertia | **pass**, 17.00 ms against 16.66 ms implied |
| V-02 | Rated continuous duty settles at the permitted winding temperature | IEC 60034-1 S1 duty, thermal class 155 (F), dT = 100 K at 40 C | **pass after a model change**, see below |
| V-03 | Heating follows current, not speed | Winding loss is dominated by I<sup>2</sup>R | **pass after a model change**, see below |
| V-03b | Same load at any speed draws the same current | Torque fixes current for a PMSM below field weakening | **pass** |
| V-03c | Stall heating uses the locked rotor current ratio | Data sheet max/rated current 24.0 / 5.6 | **pass** |
| V-04 | Rated duty never trips the protection | A drive that trips on its own rated duty would be returned | **pass** |
| V-04b | A stalled rotor trips quickly and on temperature | Usefulness, not the data sheet | **pass**, 7 steps |

## Two criteria failed on first run, and the model was changed

Recorded rather than quietly fixed, because a validation report where everything
passed first time is either lucky or not really a validation report.

### V-02: rated duty was 8.3x too cool

Under IEC 60034-1 S1 continuous duty, a machine at its rated output settles at
exactly the temperature rise its insulation class permits. That is what the
rating *means*. For this motor: thermal class 155 (F), dT = 100 K, 40 C ambient,
so rated load at rated speed should approach 140 C and stay there.

The model settled at **52.1 C**, a rise of 12.1 K against the 100 K its own data
sheet implies.

The consequence was not cosmetic. It meant the overheat trip was **unreachable
in normal operation at any speed**, which is why every thermal fault in the
downstream campaign needed a stalled rotor to become hazardous at all. That had
been treated as a modelling convenience. It was a symptom.

Fixed by deriving the heating coefficient instead of choosing it:

```
HEAT_AT_RATED_C = COOLING_RATE * (OVERHEAT_LIMIT_C - AMBIENT_C)
```

which forces equilibrium at rated current to land on the permitted temperature.
It is now impossible to tune this number without changing either the cooling rate
or the data sheet's own limit.

### V-03: heating followed the wrong quantity entirely

Winding loss is dominated by I<sup>2</sup>R, so it follows **current**, which for
a PMSM below field weakening follows **torque**. The model heated in proportion
to **speed**.

That is not a calibration error, it is the wrong mechanism, and it had the
physics backwards in both directions: an unloaded motor spinning at 6,000 rpm
was cooking at 64 C, while a stalled rotor at 4.3x rated current was treated as
merely warm.

Fixed by introducing a load torque and computing current from it. An unloaded
motor now sits at ambient however fast it spins, and the same load at double the
speed draws the same current and reaches the same temperature.

**Why this was invisible to the test suite.** Every existing test asserted
behaviour relative to the model's own constants, so all 55 of them passed against
a model whose loss mechanism was wrong. Coverage was 100% throughout. No amount
of verification finds this class of defect, which is the entire argument for
doing validation as a separate activity rather than assuming good tests cover it.

## What is deliberately NOT validated

**The thermal time constant.** Siemens publishes none, and it could not be used
faithfully if they did: a 2 kW servo's winding thermal constant is minutes while
its mechanical response is milliseconds, five orders of magnitude apart, and no
single step size carries both. It is therefore **compressed**, marked
`[ILLUSTRATIVE]` in the source, and every thermal latency derived from it is in
**steps and never in seconds**.

The split is the useful part. The thermal model's **steady state gain is
validated** against the data sheet; its **time constant is not, and cannot be**.
Those are different halves of the same model and conflating them would let the
validated half lend credibility to the unvalidated one.

**Acceleration current is excluded from heating**, and that follows from the
compression rather than from physics. A real servo does draw peak current while
accelerating, for about 17 ms, against a thermal constant measured in minutes, so
the contribution is nothing. Here the thermal scale is compressed to 125 steps
while the mechanical response is still 17, so including it would give a routine
acceleration roughly 180 K of heating and trip a healthy motor. That would be an
artifact of the compression, so the model carries steady state load current only.

**Iron loss is not modelled.** It rises with speed, unlike copper loss, so the
V-03b criterion (same temperature at any speed under the same load) is exactly
true here and only approximately true of a real machine.

**No hardware comparison, and no independence.** Nothing here has been run
against a motor, and every criterion on this page was written by the same person
who wrote the model. A criterion its author chose is a weaker test than one an
assessor chose, and that gap does not close from inside the repository.
