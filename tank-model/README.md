# Wave tank model

A model of the UNC Joint Fluids Lab wave tank and its 30-piston wavemaker, end to end:
the geometry (head, piston bank, channel, reservoir), the piston motion the
controller makes, the air trapped in each piston's pipe, and the waves that come
down the channel. Use it to predict a run before doing it, compare it with what
the camera measures, and calibrate it on those measurements.

Every dimension lives in `geometry.json` with its range and how it was obtained.
`MEASUREMENTS.md` explains each one and lists the tape measurements that would
tighten the model most.

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
  It is 1.08 m deep: the 42.5 in window height, taken as floor to rim. The
  backward wave from the pistons spreads out there.
* **Piston bank**: 30 pipes of 8 in Sch 10S stainless (bore 211.6 mm), 3 across
  the 673 mm channel width by 10 along it, standing in the head right at the
  channel mouth. Tops are level at 1422 mm (56 in) above the floor. Bottoms
  step down 3 in per row, from row 1 at 813 mm to row 10 at 127 mm. William's
  row n is display pistons 3n-2, 3n-1 and 3n.
* **Channel**: 673 mm wide and about 15.4 m long, leaving from the middle of
  the head's channel-side wall.
* **Reservoir**: the tall blue dam-break column behind a gate at the far end.
  For piston waves it is only a reflecting end wall.

A disc with a rubber seal runs in the top of each pipe, with air trapped
between the disc and the water (`wavemodel/air.py`). So:

* **A row moves water only if its pipe bottom is under the still water.** At
  0.30 m that is rows 8-10; at 0.40 m rows 7-10; at 0.60 m rows 4-10. The other
  rows move only air. **Depth is an input to every command; it is never a
  constant.**
* In a wet row the trapped air is a stiff spring: the water inside follows the
  disc at about 95% of its travel.
* **Venting.** If a downstroke drives the inner water down to the pipe bottom,
  the rest of the stroke blows air out of the bottom and moves no water.
  Whether that happens on every stroke depends on the disc seal, and that is
  not known yet. The model keeps both possibilities as a switch, `--seal`:
  * `tight` (the default): no air passes the disc. The pistons park at 370 mm,
    the bottom of their travel, and a run lifts them before stroking. So the
    inner water never reaches the pipe bottom, and every stroke of a wet row
    moves its full volume.
  * `leaky`: air leaks in past the seal on the upstroke. The water inside can
    then never rise above the outside level, so **each downstroke moves at most
    the pipe bottom's submergence** before it vents. At 0.30 m that caps row 10
    at 173 mm, row 9 at 97 mm and row 8 at 21 mm, whatever the stroke.

  To tell them apart: watch the row-10 pipe bottoms during a 300 mm stroke at
  0.30 m. Bubbles on every downstroke mean `leaky`. A single measured wave
  height also settles it; see Calibration below.

The water is solved with a frequency-domain linear wave model over the whole T
plan (`wavemodel/tank.py`, checked against the 1-D analytic answer to within
2%). The flow under the wet rows runs in a layer roofed by their stepped pipe
bottoms. The piston motion (`wavemodel/motion.py`) reproduces the LinMot
profiles; its Bestehorn jerk factor of 1.249 matches the measured 3.834 s cycle.

## Commands

```bash
.venv/bin/python wm.py volumes --depth 0.30
```
Capacity against the published 13,500 gal, water volume against depth, and a per-row
table for that depth: wet or dry, submergence, trapped air, water inside each pipe.

```bash
.venv/bin/python wm.py predict --pistons all --stroke 300 --speed 900 --accel 20000 --jerk 7500 --depth 0.30 --plot
```
Predicted waves at stations along the channel (default 1, 3, 5, 8, 11 and 14 m from the
mouth), with a per-row table of how much water each row really moves. Options:

* `--pistons`: `all`, display numbers `1-30` or `1,4,7`, William's rows `row8-10`, or lanes
  across the tank `lane2`. The older `c8-10` / `r2` forms still work.
* `--seal tight|leaky`, `--row10-end channel|head`, and `--disc-zero` (disc height at
  0 mm) override what geometry.json says.
* `--preset "../Presets/<name>.csv"` runs one of the lab's presets.
* `--stations 1.5,5`, `--profile`, `--cycles`, `--column-delay`, and `--position1` /
  `--position2` (instead of `--stroke`) are also available.

Columns: `first H` is the leading wave, crest to trough, the first thing a camera at
that station sees. `incident H` is the largest wave before the reflection from the gate
returns. `T` is the period and `L` the wavelength. The output also flags breaking, and
cnoidal waves (Ursell number > 26), where linear theory underpredicts the crests.

```bash
.venv/bin/python wm.py sweep                 # 0.30, 0.40, 0.50, 0.60 m; both seals; lab fast settings
.venv/bin/python wm.py sweep --depth 0.30,0.40,0.50,0.60,0.70
.venv/bin/python wm.py sweep --row10-end head --sets "all;row8-10" --speeds 300,900
.venv/bin/python wm.py chart                 # wave height against stroke, from the sweep tables
.venv/bin/python wm.py chart --speed 300
```
`sweep` writes `results/sweep_<depth>cm.csv` (and `..._row10-head.csv`). Each table
covers the piston sets, strokes from 50 to 350 mm, commanded speeds from 100 to 900 mm/s
and six stations, for both seals. It defaults to the lab's fast settings: Bestehorn,
accel 20,000, jerk 7,500. `chart` turns the tables into
`results/wave_height_vs_stroke_<speed>mms.png` (one panel per depth, one line per piston
set, solid for a tight seal, dashed for leaky) and `results/row10_end_effect_<speed>mms.png`.

```bash
.venv/bin/python wm.py drawings --depth 0.30
.venv/bin/python wm.py model3d --depth 0.30 --disc-mm 370
```
`drawings/`: dimensioned plans (`plan_whole`, `plan_head_bank`) and sections
(`side_whole`, `side_bank`), as PNG and PDF.

`model/`: `tank_viewer.html` opens in a browser. It has sliders for the depth and the
disc position, a switch for which end row 10 is at, and a table of the air and water in
every pipe. `tank_model.glb` imports into Blender (File > Import > glTF 2.0);
`tank_model.obj` (with its `.mtl`) is the same model for anything else. Both are in
metres, Y up, one named object per part. Motors, frame and stand are approximate.

## Calibration loop

1. `.venv/bin/python wm.py template` writes `observations_template.csv`. Its five
   example runs were chosen to tell the seal behaviours and the row-10 end apart at
   0.30 m and 0.40 m.
2. Do the runs. For each run and each station, fill in `observed_height_mm`: the
   first wave's crest-to-trough height at `station_x_m`, measured along the channel
   from its mouth. Enter the run's still-water depth `depth_m` every time. Leave a
   cell blank if you did not measure it.
3. `.venv/bin/python wm.py calibrate observations.csv --compare` fits the overall
   `gain` for every combination of seal (`tight` / `leaky`) and row-10 end
   (`channel` / `head`). It ranks the four by how well they explain the runs and
   prints observed against predicted for the best one. The result goes to
   `results/calibration.json`.
4. Put what won into the model: `row_10_end` in geometry.json, and `--seal` / `--gain`
   on later predictions. Fit more parameters once there are more runs:
   `--fit gain,channel_damping` needs the same run seen at two stations;
   `--fit gain,bottom_offset` needs runs at several depths.

A synthetic check of this loop recovered a planted gain of 0.8 exactly and picked out
the planted seal and row-10 end. Scores: leaky/channel 0.001, leaky/head 0.136,
tight/channel 0.216 (rms log error).

## Results at a glance

First wave, crest to trough, at 5 m. Lab fast settings, 900 mm/s commanded (the jerk
limit holds the real peak speed to 155-567 mm/s for 50-350 mm strokes). Row 10 at the
channel end. Each cell is tight / leaky seal, in mm.

| depth | pistons | 100 mm stroke | 200 mm | 350 mm |
|---|---|---|---|---|
| 0.30 m | all 30 (only rows 8-10 wet) | 36 / 29 | 73 / 46 | 117 / 45 |
| 0.30 m | row 10 only | 15 / 15 | 31 / 29 | 48 / 29 |
| 0.40 m | all 30 | 38 / 35 | 81 / 66 | 136 / 81 |
| 0.50 m | all 30 | 40 / 39 | 90 / 81 | 157 / 118 |
| 0.60 m | all 30 | 51 / 47 | 115 / 99 | 203 / 154 |
| 0.70 m | all 30 | 49 / 48 | 113 / 104 | 199 / 168 |

If row 10 is at the head end instead, all 30 make a larger first wave in deeper water
(+12% at 0.30 m up to +44% at 0.70 m for 350 mm). With row 10 at the mouth, its 127 mm
bottoms form a low roof there, which sends more of the flow back into the head.

## Files

| | |
|---|---|
| `geometry.json` | every dimension, its range and source |
| `wm.py` | the command line |
| `wavemodel/geometry.py` | dimensions, stepped rows, volumes |
| `wavemodel/air.py` | trapped air, venting, the two seal behaviours |
| `wavemodel/motion.py` | LinMot motion profiles |
| `wavemodel/tank.py` | the plan-view wave solver (transfer functions cached in `cache/`) |
| `wavemodel/predict.py` | run -> piston flux -> waves at stations, metrics |
| `wavemodel/calibrate.py` | template, fit, compare |
| `wavemodel/charts.py`, `drawings.py`, `model3d.py` | charts, drawings, 3D model |
| `photos/` | the 11 photos (HEIC originals, JPEG copies) the geometry came from |
| `results/`, `drawings/`, `model/` | generated output |

## Limits

* Linear waves. Above Ursell 26 real crests are sharper and higher than predicted, and
  `predict` says so. Waves above the breaking limit are flagged.
* The air model is quasi-static. Air spring and water ring at 3-5 Hz, above the piston
  motion, so this costs a few per cent at most.
* Forcing above 2 Hz is left out, and `predict` reports what share that is. It grows
  with the leaky seal, whose venting cuts the flow off sharply.
* The head is taped (corners aside), but how much it absorbs is a guess. Its size does
  not touch the first wave, which reaches the stations before anything returns from the
  head, but it shapes the later waves of a long run: replacing the photo-based 3.8 m by
  5.2 m head with the measured 8.91 m by 3.04 m one moved H1/3 at 5 m by -17% to +120%.
