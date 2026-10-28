# Tank model: where it stands (26 Oct 2026)

Goal (William, 26 Oct): from the 11 photos (`photos/`, IMG_2458-2468) and the piston
specs, an end-to-end model of the wave maker. It should give the geometry and the expected
wave heights, so predictions can be compared with measured runs and the model adjusted
for stroke, speed and which pistons run. `README.md` says how to run everything;
`MEASUREMENTS.md` says where each number came from.

## For the agent with the blueprints

This model is an approximation and a good start, not the tank's true geometry. Only
the channel width (tape) and the pipe bore (the stencil and the pipe standard) are
firm. Every other dimension was estimated from the photos or from William's rough
description, and no prediction has been checked against a measured wave yet. When you
are given the tank's blueprints, bring the model to the exact measurements:

1. In `geometry.json`, replace each estimate with the drawn value, narrow its `range`
   to the drawing's tolerance, and change `how` to name the drawing and sheet. That
   includes `row_bottoms` (per-row pipe bottoms), `pipe_top_height`, `pitch_along`,
   `disc_face_at_0mm` and `row_10_end`. Update `MEASUREMENTS.md` to match; its first
   table ranks which numbers move the predictions most.
2. Where the drawings contradict the model's picture of the tank, change the code, not
   only the numbers. The T layout with the channel on the head's centre line, the square
   pitch of the bank, the side plates under it, the open gaps between pipes, and the
   reservoir as a plain end wall all came from the photos or William's description.
3. Regenerate what depends on the geometry: the sweeps and charts, `drawings/` and
   `model/` (README, Commands), and the table in README's "Results at a glance". The
   cached tank solutions are keyed on the geometry, so a change re-solves them.
4. The drawings cannot settle the seal behaviour or the overall gain. Those still need
   measured runs and `wm.py calibrate --compare` (README, Calibration loop).

## Done

- **Stepped pipes.** The single `outlet_height` and the duct under the bank are gone.
  Each row of three has its own pipe bottom at 5 + 3(10 - n) in, with tops level at
  1422 mm. The solver roofs the flow under each wet row at that row's bottom, and leaves
  open water under dry rows. The source coupling uses the roof height where each
  piston's water leaves the bank toward the channel.
- **Trapped air** (`wavemodel/air.py`). A row moves water only if its bottom is under
  the entered still-water depth. The air is a stiff spring, so the water follows the
  disc at about 95%. Venting at the pipe bottom is modelled, as are two seal
  behaviours (`--seal tight|leaky`; see README). `cylinder_water()` and
  `predict._flux` go through it.
- **Physics finding.** The stroke cap at the submergence (in the handoff before this
  one) holds on every stroke only if air leaks back past the seal on the upstroke
  (`leaky`). With an airtight disc, the pistons parking at 370 mm (`Model.py`
  `PARK_POSITION`) means nothing vents in a normal run and every wet row moves its full
  stroke (`tight`, the default). Either a measured run or watching for bubbles settles it.
- **Leading-wave metric.** `first H` used to be the first zero-crossing above a
  threshold, which could jump between waves. It is now the first crest and the trough
  after it, which comes before any reflection from the head. Calibration fits on it.
- **Gain outside the cache.** `gain` is applied after the cached tank solution, so
  calibration no longer re-solves the tank for every step. A synthetic check recovered
  a planted gain of 0.8 and the planted seal and row-10 end.
- **Sweeps.** 0.30-0.70 m, both seals, the lab's fast settings (accel 20,000, jerk
  7,500), in `results/sweep_*cm.csv`. A row-10-at-head variant is in
  `results/sweep_*cm_row10-head.csv`. Charts:
  `results/wave_height_vs_stroke_{900,300}mms.png` and
  `results/row10_end_effect_{900,300}mms.png`.
- **3D model** (`model/`): `tank_viewer.html`, `tank_model.glb` and `tank_model.obj`.
  **Drawings** (`drawings/`): plans and sections, PNG and PDF.
- **Head taped (28 Oct).** William's measurements replaced the photo estimates. The back wall is
  three windows between four 5 in beams: 125 in over the corners, 119.75 in (3.04 m)
  inside. Each long side is eight 39.25 in windows with beams of 5, 4, 5, 4, 5, 4 and 5 in:
  350.75 in (8.91 m) inside, with the corners read like the back wall's. The windows are
  42.5 in high, taken as a 1.08 m rim for the head and the channel. The 3D frame draws
  these beams. Capacity is now 13,700 gal against the published 13,500. The first wave
  moved about 1%; the later waves of a run moved -24% to +120%.
- **Channel counted (28 Oct).** 16 windows a side, not 15. The beams between them alternate
  4, 5, 4, ... 4 in from the head-end corner to the reservoir. The window width is assumed
  equal to the head's 39.25 in, so the channel is 699.75 in (17.77 m), not the 15.4 m
  perspective fit. The 3D frame draws these beams. Capacity is 14,200 gal (1.05 of the
  published figure). The first wave moved +1-3%, and the gate's reflection now gets back
  to x = 5 m at 17.8 s instead of 15.0 s (0.30 m water).

## Still open (William)

1. Which end row 10 (the longest pipes) is at, and whether pistons 28-30 are in it.
   Assumed: the channel end. It changes wave heights by 10-40%.
2. Seal behaviour: bubbles from the pipe bottoms on every downstroke, or not. Up to
   2.6x at shallow depth.
3. Tape: rim height (now inferred from the window height), one channel window's width (the
   channel length rests on assuming it equals the head's 39.25 in), the corner beams, and the
   disc height at 0 mm (in order of how much each matters; see MEASUREMENTS.md).
4. The depths the lab actually runs, and the first measured wave heights, for
   `wm.py calibrate --compare`.

Committed to wavemaker-f25 as `tank-model/` on 27 Oct 2026. The HEIC originals in
`photos/originals/` stay on William's Mac; `photos/jpg/` has the same 11 photos.

Not fixed here, belongs to wavemaker-f25: its soliton trial lifts the "floor" from 370
to 290 mm to make a crest. In this disc-in-pipe picture, lifting the discs draws water
into the pipes, so that move would make a trough, not a crest.
