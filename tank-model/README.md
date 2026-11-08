# Wave tank model

A model of the UNC Joint Fluids Lab wave tank and its 30-piston wavemaker, end to end:
the geometry (head, piston bank, channel, reservoir), the piston motion the
controller makes, the air trapped in each piston's pipe, and the waves that come
down the channel. Use it to predict a run before doing it, compare it with what
the camera measures, and calibrate it on those measurements.

Every dimension lives in `geometry.json` with its range and how it was obtained.
`MEASUREMENTS.md` explains each one and lists the tape measurements that would
tighten the model most. Since 7 Nov the piston bank's duct block, the motors and the
tank's heights come from the 2013-15 design files and the 2009 IIHR tank drawings:
`BLUEPRINTS.md` says which files those are and what each one gave. The design's PVC
cylinders were never built (the cylinders are metal), so nothing about the cylinders comes
from it.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install numpy scipy matplotlib pillow
```

All commands below run from `tank-model/`.

## What the model is

The tank is a T, with no turns:

* **Head**: the wide tank holding the piston bank, taped on 28 Oct. It is 3.04 m
  across: the back wall is three windows and four steel beams, 125 in over the
  corner beams and 119.75 in inside. It is 8.91 m along the axis: each long side
  is eight 39.25 in windows with beams of 5, 4, 5, 4, 5, 4 and 5 in between them.
  It is 1.15 m deep to the rim, and holds at most 1.03 m of water (IIHR drawings).
* **Piston bank**: 30 cylinders of 8 in Sch 10S stainless (bore 211.6 mm) on a
  9.8 in pitch both ways, 3 across and 10 along. They stand on a stepped block of
  stainless ducts, one duct per cylinder, 9.8 x 3.0 in outside (246 x 75 mm
  inside). Each cylinder empties into its own duct, which turns horizontal and
  runs to the block's front face at the channel mouth. There all 30 outlets open
  into the channel: 3 lanes by 10 layers, row 1's at the bottom (51-126 mm up) and
  row 10's at the top (741-816 mm). The block is 11 pitches (2.74 m) long. Row 1's
  duct is 2.61 m long, row 10's 0.37 m. The cylinders' tops are level at 1422 mm
  (56 in, William). Their bottoms step up one duct layer (3.02 in) per row, from row 1
  (the longest, about 51 in) at 127 mm to row 10 (about 24 in) at 817 mm. Row 1 is at the
  head end, row 10 at the channel mouth. William's row n is display pistons 3n-2,
  3n-1 and 3n.
* **Motors**: LinMot P01-48x360F linear motors, one per cylinder, standing on a
  9.6 in plate on its top: 1020 N peak, 200 N continuous (340 N with a fan),
  2 m/s, and 510 mm of travel, which is the drive's -57..453 mm.
* **Channel**: 29.25 in (743 mm) wide inside (taped) and 17.8 m long: 16 windows a side with beams
  alternating 4 and 5 in (William, 28 Oct), the window width assumed equal to the
  head's 39.25 in. It leaves from the middle of
  the head's channel-side wall.
* **Reservoir**: the tall blue dam-break column behind a gate at the far end.
  For piston waves it is only a reflecting end wall.

A disc with a rubber seal runs in the top of each pipe, with air
trapped between the disc and the water (`wavemodel/air.py`). So:

* **A row moves water only if its pipe bottom (its duct's ceiling) is under the
  still water.** At 0.30 m that is rows 1-3; at 0.40 m rows 1-4; at 0.60 m rows
  1-7. The other rows move only air, out of their outlets. **Depth is an input to
  every command; it is never a constant.**
* In a wet row the trapped air is a spring, and the water in the pipe and the
  whole duct is its mass: about 185 kg for row 1, referred to the disc. Slowly,
  the water follows the disc at about 95% of its travel. But the pair rings at
  0.9-1.1 Hz for rows 1-3 (up to 2.5 Hz for row 8), inside the band the strokes
  drive. So the water lags and overshoots each stroke, and swings on for a few
  seconds after the disc stops (`wavemodel/drive.py`). The predictions integrate
  this in time.
* **Venting.** If a downstroke drives the inner water down to the pipe bottom,
  the rest of the stroke blows air along the duct and out of the outlet, and
  moves no water. Whether that happens on every stroke depends on the disc seal,
  and that is not known yet (the archive's seals were for the outdated PVC
  cylinders). The model keeps both possibilities as a switch, `--seal`:
  * `tight` (the default): no air passes the disc. The pistons park at 370 mm,
    the bottom of their travel, and a run lifts them before stroking. So the
    inner water never reaches the pipe bottom, and every stroke of a wet row
    moves its full volume.
  * `leaky`: air leaks in past the seal on the upstroke. The water inside can
    then never rise above the outside level, so **each downstroke moves at most
    the pipe bottom's submergence** before it vents. At 0.30 m that caps row 1
    at 173 mm, row 2 at 97 mm and row 3 at 21 mm, whatever the stroke.

  To tell them apart, run rows 1-3 slowly at 0.30 m (300 mm at 100 mm/s, jerk
  2000) and watch the bottom three outlets at the mouth. A big burst of air on
  every downstroke means `leaky`. With `tight` the model still expects a small
  puff at the end of a downstroke: the swinging duct water overshoots the 12 mm
  of water left above row 1's duct ceiling. The wave settles it too: 63 mm at 5 m
  tight against 23 mm leaky for that run. See Calibration below.

The water is solved with a frequency-domain linear wave model over the whole T
plan (`wavemodel/tank.py`, checked against the 1-D analytic answer to within
2%). Each piston's water enters the channel at its duct's outlet, with the
far-field coupling of a slot at that height. The duct block is solid below its
steps, and its front stands 0.82 m high across the channel mouth. **So in any
water shallower than that, the channel is walled off from the head, and all the
water the pistons move goes down the channel.** Before the drawings the model let
it split between the channel and the head, and predicted about half the wave.
The piston motion (`wavemodel/motion.py`) reproduces the LinMot profiles; its
Bestehorn jerk factor of 1.249 matches the measured 3.834 s cycle.

## Commands

```bash
.venv/bin/python wm.py volumes --depth 0.30
```
Capacity against the published 13,500 gal, water volume against depth, and a per-row
table for that depth: wet or dry, submergence, trapped air, water inside each pipe.

```bash
.venv/bin/python wm.py pistons --depth 0.30
.venv/bin/python wm.py pistons --depth 0.50 --stroke 300 --speed 900 --pistons all
```
What the pistons are and can do. It reports:

* the motors' stroke, force and speed limits;
* the volume per millimetre of travel;
* the duct speed-up (the water leaves the outlet 1.9 times faster than the disc moves);
* per row: duct length, outlet height, trapped air, water mass, air spring, ring frequency.

With `--stroke` it also runs that stroke. It reports each row's peak and rms motor force against
the LinMot's 1020 N peak and 200 N (340 N with fan) continuous ratings, and the outlet speed.

```bash
.venv/bin/python wm.py predict --pistons all --stroke 300 --speed 900 --accel 20000 --jerk 7500 --depth 0.30 --plot
```
Predicted waves at stations along the channel (default 1, 3, 5, 8, 11 and 14 m from the
mouth), with a per-row table of how much water each row really moves. Options:

* `--pistons`: `all`, display numbers `1-30` or `1,4,7`, William's rows `row1-3`, or lanes
  across the tank `lane2`. The older `c8-10` / `r2` forms still work.
* `--seal tight|leaky` and `--disc-zero` (disc height at 0 mm) override what
  geometry.json says.
* `--duct-zeta` sets the damping of the air/duct-water swing (0.05, estimated).
* `--no-air-dynamics` and `--layout open` bring back the model as it was before the
  drawings, for comparison: quasi-static air, and an open bank.
* `--preset "../Presets/<name>.csv"` runs one of the lab's presets.
* `--stations 1.5,5`, `--profile`, `--cycles`, `--column-delay`, and `--position1` /
  `--position2` (instead of `--stroke`) are also available.

The row table gives each wet row's ring frequency and damping, its outlet speed, and the
motor force, peak / rms, with the peak as a share of what the motor gives at that point of
its stroke. Warnings flag a force over the limit and an rms over the continuous rating.
Columns: `first H` is the leading wave, crest to trough, the first thing a camera at
that station sees. `incident H` is the largest wave before the reflection from the gate
returns. `T` is the period and `L` the wavelength. The output also flags breaking, and
cnoidal waves (Ursell number > 26), where linear theory underpredicts the crests.

```bash
.venv/bin/python wm.py sweep                 # 0.30, 0.40, 0.50, 0.60 m; both seals; lab fast settings
.venv/bin/python wm.py sweep --depth 0.30,0.40,0.50,0.60,0.70
.venv/bin/python wm.py sweep --depth 0.30,0.40,0.50,0.60,0.70 --layout open --no-air-dynamics --sets "all;row1-3" --seal tight
.venv/bin/python wm.py chart                 # wave height against stroke, from the sweep tables
.venv/bin/python wm.py chart --speed 300
```
`sweep` writes `results/sweep_<depth>cm.csv`. With `--layout open --no-air-dynamics` it
writes `..._open_quasistatic.csv` instead: the model before the drawings. Each table
covers the piston sets, strokes from 50 to 350 mm, commanded speeds from 100 to 900 mm/s
and six stations, for both seals. It also gives the largest motor force, peak and rms,
and the outlet speed. It defaults to the lab's fast settings: Bestehorn, accel 20,000,
jerk 7,500. `chart` turns the tables into two charts:

* `results/wave_height_vs_stroke_<speed>mms.png`: one panel per depth, one line per piston
  set, solid for a tight seal, dashed for leaky.
* `results/drawings_effect_<speed>mms.png`: what the drawings changed.

```bash
.venv/bin/python wm.py drawings --depth 0.30
.venv/bin/python wm.py model3d --depth 0.30 --disc-mm 370
```
`drawings/`: dimensioned plans (`plan_whole`, `plan_head_bank`) and sections
(`side_whole`, `side_bank`), as PNG and PDF.

`model/`: `tank_viewer.html` opens in a browser. It has sliders for the depth and the
disc position, a switch to hide the duct block, and views of the bank side-on and of
the outlets. Its table gives every row's outlet height, trapped air and ring frequency.
`tank_model.glb` imports into Blender (File > Import > glTF 2.0); `tank_model.obj` (with
its `.mtl`) is the same model for anything else. Both are in metres, Y up, one named
object per part. The bank is as drawn:

* the duct block, every duct and outlet, and the flange, cover and base plates;
* the offset-tee rails and their pads, and the corner angles and gussets;
* the cylinders, the motor plates, the LinMot stators, and the sliders and discs.

The tank's posts, the motors' flanges and connectors, and where the rails end are approximate.

## Calibration loop

1. `.venv/bin/python wm.py template` writes `observations_template.csv`. Its five
   example runs were chosen to tell the seal behaviours apart at 0.30 m and 0.40 m.
2. Do the runs. For each run and each station, fill in `observed_height_mm`: the
   first wave's crest-to-trough height at `station_x_m`, measured along the channel
   from its mouth. Enter the run's still-water depth `depth_m` every time. Leave a
   cell blank if you did not measure it.
3. `.venv/bin/python wm.py calibrate observations.csv --compare` fits the overall
   `gain` for both disc seals (`tight` / `leaky`). It ranks them by how well they
   explain the runs and prints observed against predicted for the better one. The
   result goes to `results/calibration.json`.
4. Put what won into the model: `--seal` / `--gain` on later predictions. Fit more
   parameters once there are more runs:
   * `--fit gain,channel_damping` needs the same run seen at two stations.
   * `--fit gain,bottom_offset` needs runs at several depths.
   * `--fit gain,duct_zeta` needs a run whose trailing ringing is filmed: one row, one
     stroke, then the water at the mouth swinging at about 1 Hz.

A synthetic check of this loop, made when it still compared the two bank ends, recovered
a planted gain of 0.8 exactly and picked out the planted seal and end. Scores: leaky/channel 0.001, leaky/head 0.136,
tight/channel 0.216 (rms log error).

## Results at a glance

First wave, crest to trough, at 5 m. Lab fast settings, 900 mm/s commanded (the jerk
limit holds the real peak speed to 155-567 mm/s for 50-350 mm strokes). The drawn duct
block, with the water's inertia. Each wave cell is tight / leaky seal, in mm; the force
is the largest any selected row needs at 350 mm, tight seal, peak / rms while moving.

| depth | pistons | 100 mm stroke | 200 mm | 350 mm | motor force at 350 mm |
|---|---|---|---|---|---|
| 0.30 m | all 30 (only rows 1-3 wet) | 78 / 44 | 157 / 59 | 246 / 59 | 563 / 281 N |
| 0.30 m | row 1 only | 25 / 21 | 50 / 35 | 79 / 35 | 560 / 275 N |
| 0.40 m | all 30 (rows 1-4 wet) | 82 / 60 | 173 / 96 | 280 / 106 | 542 / 276 N |
| 0.50 m | all 30 (rows 1-5 wet) | 85 / 71 | 185 / 123 | 308 / 155 | 552 / 280 N |
| 0.60 m | all 30 (rows 1-7 wet) | 105 / 82 | 232 / 147 | 393 / 198 | 593 / 284 N |
| 0.70 m | all 30 (rows 1-8 wet) | 105 / 90 | 236 / 169 | 405 / 237 | 631 / 294 N |
| 0.70 m | row 1 only | 12 / 12 | 27 / 26 | 47 / 41 | 742 / 293 N |

Many of the larger waves are past the breaking limit, and `predict` flags them: real
waves will be smaller.

The drawings roughly doubled the predictions: the model before them gave 123 mm (0.30 m)
to 263 mm (0.70 m) for all 30 at 350 mm (`results/drawings_effect_*.png`). The pistons'
water now all goes down the channel, since the block walls it off from the head. The duct
water's swing adds to the first crest.

**The motors.** Across the whole sweep (0.30-0.70 m, 50-350 mm, 100-900 mm/s, both seals)
no run needs more than 742 N, 73% of the LinMot's 1020 N peak. But 377 of the 2520 average
more than its 200 N continuous rating without a fan while moving, mostly strokes of 200 mm
and up at 500 mm/s and above; none pass the 340 N rating with a fan. A short run is fine.
Minutes at those settings need the fans, or they heat the motors toward a fault. The
force is mostly air pressure under the disc: the duct water lags, and the air takes up the
difference.

## Files

| | |
|---|---|
| `geometry.json` | every dimension, its range and source |
| `wm.py` | the command line |
| `wavemodel/geometry.py` | dimensions, stepped rows, volumes |
| `BLUEPRINTS.md` | the design archive: which files the model rests on, and what each gave |
| `wavemodel/air.py` | trapped air, venting, the two seal behaviours; the dynamic integration |
| `wavemodel/drive.py` | duct mass, air spring, ringing, duct losses, the motors' limits |
| `wavemodel/motion.py` | LinMot motion profiles |
| `wavemodel/tank.py` | the plan-view wave solver (transfer functions cached in `cache/`) |
| `wavemodel/predict.py` | run -> piston flux (with the water's inertia) -> waves at stations, motor force, metrics |
| `wavemodel/calibrate.py` | template, fit, compare |
| `wavemodel/charts.py`, `drawings.py`, `model3d.py` | charts, drawings, 3D model |
| `photos/` | the 11 photos (HEIC originals, JPEG copies) the geometry came from |
| `results/`, `drawings/`, `model/` | generated output |

## Limits

* Linear waves. Above Ursell 26 real crests are sharper and higher than predicted, and
  `predict` says so. Waves above the breaking limit are flagged.
* The swing of air and duct water is integrated in time, but its damping is estimated:
  the duct's losses (exit, the elbow's turn, friction) and an extra damping ratio of 0.05
  (`--duct-zeta`). With less damping the ringing after each stroke lasts longer, and so does the
  trailing wave train. A filmed run that shows it would fit it.
* The motor force check estimates the moving mass (6.5 kg: the slider's 3.56 kg plus a
  disc whose mass is not known) and leaves out seal friction.
* Forcing above 2 Hz is left out, and `predict` reports what share that is. It grows
  with the leaky seal, whose venting cuts the flow off sharply.
* The head is taped (corners aside), but how much it absorbs is a guess. Its size does
  not touch the first wave, which reaches the stations before anything returns from the
  head, but it shapes the later waves of a long run: replacing the photo-based 3.8 m by
  5.2 m head with the measured 8.91 m by 3.04 m one moved H1/3 at 5 m by -17% to +120%.
