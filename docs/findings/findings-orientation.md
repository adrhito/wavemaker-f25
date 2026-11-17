# Piston identity, orientation and diagnostics audit

Against the uncommitted Codex changes, 14 September 2026. Proofs are in
`tests/test_orientation_defects.py`; each was written as a strict xfail and is
converted to a plain passing test as the defect is fixed.

## Ground truth

```
display_number(axis) = 30 - 3*(axis // 3) - (axis % 3)
axis_from_display(n) = (9 - (n-1)//3) * 3 + (2 - (n-1)%3)
```

`TankView._cell_centre` draws axis `a` at drawing row `2 - a%3`, drawing column
`9 - a//3`:

```
drawing row 0:  1  4  7 10 13 16 19 22 25 28     axes: 29 26 23 20 17 14 11  8  5  2
drawing row 1:  2  5  8 11 14 17 20 23 26 29           28 25 22 19 16 13 10  7  4  1
drawing row 2:  3  6  9 12 15 18 21 24 27 30           27 24 21 18 15 12  9  6  3  0
               left                    right
```

So **drawing column (0 = left) = `(display_number - 1) // 3`**, the mirror of
the axis column, and the left of the drawing is labelled `FRONT - nearest you`.

**Which end is the front was settled by commit `c87654a`**, whose message says
the labels were swapped "so they match where the pistons actually are from the
operating position". Front = display column 1 = pistons 1, 2, 3. Raw `axis // 3`
is the old, mirrored convention.

Note that a dict both built and read with the same expression is only a
relabelling, not a bug — `Model.cascade_targets` looks wrong and is not.

## HIGH

1. **The Wave tab's travelling wave runs opposite to the Pattern tool.**
   `wave/WaveDesigner.py:336` indexes `waves.column_offsets` — whose key 0 is the
   front — with `motor.axis // 3`, so piston 1 gets offset 180 and piston 30 gets
   0, and the crest starts at the back. The Pattern tool does the opposite.

2. **Every shipped preset is mirrored end for end.** `Presets/*.csv` were
   generated before the convention was corrected, so they contradict their own
   filenames. Press "Select front 4 columns", load "Shallow water - front four
   columns", and all twelve selected pistons get `Position 1 = Position 2 = 0`:
   nothing moves at all.

3. **A movement test that finds no movement is silently dropped.**
   `app/diagnostics.py:451`, `463`, `473`. Codex's `and not found` guard lets any
   earlier finding — including the routine "not holding the requested
   parameters", which fires before every Prepare — suppress the result of a test
   the operator opted into and which physically moved the machine.

## MEDIUM

4. **A drive holding no stroke at all is reported as "ok".**
   `app/diagnostics.py:209-214`. A piston sitting still *because* Position 1 ==
   Position 2 generates no setpoints, so the Motion Active bit is clear and the
   check reads "Stroke held on the drive: 0 mm — ok", in green.

5. **Active-low status bits can never be reported.** `app/drive_status.py:92-108`.
   `_set_bits` returns only set bits, but bit 5 means "quick stop is *not*
   active" when set — so a drive held in quick stop says nothing at all.
   `drive_status.summary(0, 0x0801)` returns "homed, no faults". Bit 5's polarity
   is inferred from the table's own wording; no vendor PDF is in the repository,
   so it wants confirming against the manual.

## LOW

6. **"Diagnose All" lists the pistons backwards.** `diagnostics/Diagnostics.py:163`
   and `:173` iterate axis order, which is reverse display order.
7. **`Motor.row` / `Motor.column` are mirrored against the picture.**
   `Motor.py:103-104`. `Motor(29)` reports row 3, column 10; piston 1 is drawn
   top-left. Reachable through `__repr__`, so it is a log-reading hazard.
8. **A failed movement test hides "the power stage is not enabled".**
   `app/diagnostics.py:429-449` appends the probe error before the `not enabled`
   check, so the likeliest explanation for the failure is never printed.

## Checked and found correct

`app/tags.py` in full — `display_number` is a bijection and `axis_from_display`
its exact inverse for all 30. `modules/tank_view.py` — rotation, ruler, arrow and
`describe_place` are all consistent with the labels, and Codex's corrections
there fixed a genuine inconsistency in HEAD. `app/patterns.py` — `_index_of` is
correct under the display convention for both axes, and was wrong before Codex.
`operate/PatternDialog.py` — both changes correct. `Model.cascade_targets` —
looks wrong, is not. `app/drive_status.py` bit tables — `HOMED_BIT = 11` is
confirmed against the original `Motor.homed()` and the archived documentation.
