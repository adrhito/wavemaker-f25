# Tank model: where it stands (7 Nov 2026)

Goal (William, 26 Oct): from the 11 photos (`photos/`, IMG_2458-2468) and the piston
specs, an end-to-end model of the wave maker. It should give the geometry and the expected
wave heights, so predictions can be compared with measured runs and the model adjusted
for stroke, speed and which pistons run. `README.md` says how to run everything;
`MEASUREMENTS.md` says where each number came from.

## The blueprints are in (7 Nov)

The lab's shared Drive held the wavemaker's 2013-15 design files and the tank's 2009 IIHR
construction drawings. They are copied to `Wave_maker/Wavemaker_Drive/` (next to this
repository); `BLUEPRINTS.md` lists every file, which ones the model now rests on, and what
each gave. Applied:

- **The bank is a duct block.** Each cylinder empties into its own 9.8 x 3.0 in stainless duct,
  which runs to an outlet in the block's front face at the channel mouth: 3 lanes by 10
  layers, row 1's at the bottom with a 2.61 m duct, row 10's at the top with a 0.37 m one.
  The model had the pipe bottoms open into a roofed layer under the bank. `tank.py` now puts
  each piston's flux into the channel at its outlet with a slot's coupling
  (`linear.source_coupling_band`), and treats the block as solid below its steps. Its front
  is 0.82 m high, so below that depth the channel is walled off from the head. The first
  wave about doubled: 246 mm against 123 mm for all 30 at 0.30 m, 350 mm
  (`results/drawings_effect_*.png`).
- **The water has inertia.** The duct water is about 185 kg referred to row 1's disc, and with
  the trapped air it rings at 0.9-1.1 Hz (rows 1-3), up to 2.5 Hz (row 8). `predict` now
  integrates air and water in time (`air.pipes_dynamic`, `drive.py`); `--no-air-dynamics`
  gives the old quasi-static answer. The damping is partly estimated (`duct_zeta` = 0.05).
- **Drawn dimensions.** Pitch 9.8 in both ways (was 9.75); the block is 11 pitches (2.738 m);
  steps 3.02 in, one duct layer (was 3); duct 184 cm² inside, 1.91 times faster than the
  disc.
- **Not from the archive: the cylinders.** The archive's cylinders, flanges and seals are an
  outdated PVC design; William (7 Nov): the built cylinders are metal. Their bore (stencil),
  lengths and tops (William's 51-24 in, 56 in) stay as measured.
- **The tank's heights.** From the IIHR sections: the rim is 1.153 m (was the 1.08 m window
  height), the maximum water depth 1.026 m, and the floor 0.597 m above the lab floor. At
  maximum depth the tank holds 14,000 gal against the published 13,500.
- **The motors.** LinMot P01-48x360F with the 800 mm slider: 1020 N peak, 200 N continuous
  (340 N fan), 2 m/s, 510 mm travel. `wm.py pistons` reports what each row moves and how it
  rings. Every `predict` checks the motor force (peak and rms) against these limits.
- **Disc at 0 mm.** Re-estimated at 1.35 m from the LinMot geometry (was an assumed 1.30 m).

What the drawings could not settle: the bore to the millimetre, the support pads'
thickness (in a VariCAD file), the disc's mass, and the seal's behaviour. Next: the camera runs in MEASUREMENTS.md, "Measure these next".

## Before the blueprints (26-28 Oct)

This model was an approximation built from the photos and William's description. Only the
channel width (tape) and the pipe bore (the stencil and the pipe standard) were firm.
Every other dimension was estimated from the photos or from William's rough description,
and no prediction has been checked against a measured wave yet. When more drawings or tape
measurements arrive:

1. In `geometry.json`, replace each estimate with the drawn value, narrow its `range`
   to the drawing's tolerance, and change `how` to name the drawing and sheet. Update
   `MEASUREMENTS.md` to match; its first table ranks which numbers move the predictions
   most.
2. Where the drawings contradict the model's picture of the tank, change the code, not
   only the numbers, as was done for the duct block on 7 Nov.
3. Regenerate what depends on the geometry: the sweeps and charts, `drawings/` and
   `model/` (README, Commands), and the table in README's "Results at a glance". The
   cached tank solutions are keyed on the geometry, so a change re-solves them.
4. The drawings cannot settle the seal behaviour or the overall gain. Those still need
   measured runs and `wm.py calibrate --compare` (README, Calibration loop).

## Done before the blueprints (26-28 Oct; the section at the top supersedes it where they differ)

- **Stepped pipes.** The single `outlet_height` and the duct under the bank are gone.
  Each row of three has its own pipe bottom at 5 + 3(n - 1) in, with tops level at
  1422 mm (row order corrected on 28 Oct, below). The solver roofs the flow under each wet row at that row's bottom, and leaves
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
- **Channel width taped (28 Oct): 29.25 in inside**, not the 26.5 in in `app/solitons.py`. The
  bank now fills it too: three pipes at a 9.75 in pitch (IMG_2459 shows about 26 mm between
  pipes, which 9.75 in gives and 26.5 in / 3 would not), so the bank is 743 mm wide and 2.48 m
  long. The first wave fell 8-16%: about 9% from spreading the same water over a wider
  channel, and the rest from the longer bank. Capacity 14,500 gal (1.08 of the published).
- **Row order corrected (28 Oct).** William: row 1 has the longest pipes and row 10 the
  shortest, the reverse of the 26 Oct notes ("turn the thing 180°"). Row n's bottom is now
  5 + 3(n - 1) in. William then confirmed that row 10 is at the channel end and row 1 at the
  head end, so the long pipes moved from the channel mouth to the far end of the bank,
  which is what the old "row 10 at the head end" variant modelled. `calibrate --compare`
  now fits only the seal, not the bank's end. At 0.30 m rows 1-3 are the wet ones. All 30 with a 350 mm stroke now make
  a 16-30% larger first wave at 5 m (125 mm at 0.30 m, 266 mm at 0.70 m, tight seal). The
  sweep sets swap "row 10 only" for "row 1 only", and the end-effect chart compares rows 1-3.

## Still open (William)

1. Whether display pistons 1-3 are William's row 1 (assumed: row n is pistons 3n-2..3n).
   It does not change a run of all 30, only which pistons a selection like `row1-3` means.
2. Seal behaviour: a burst of air from the bottom outlets on every slow downstroke, or not.
   Up to 2.7x at shallow depth.
3. The ringing after a stroke: its period and decay, from one filmed run (fits `duct_zeta`).
4. Tape: a cylinder's circumference (bore), row 1's bottom, the disc height at 0 mm, one
   channel window (see MEASUREMENTS.md).
5. The depths the lab actually runs, and the first measured wave heights, for
   `wm.py calibrate --compare`.

Committed to wavemaker-f25 as `tank-model/` on 27 Oct 2026. The HEIC originals in
`photos/originals/` stay on William's Mac; `photos/jpg/` has the same 11 photos.

Not fixed here, belongs to wavemaker-f25: its soliton trial lifts the "floor" from 370
to 290 mm to make a crest. In this disc-in-pipe picture, lifting the discs draws water
into the pipes (back through the ducts from the channel), so that move would make a
trough, not a crest.
