# Camera wave modelling: where it stands (17 Nov 2026)

Read this first when resuming. Branch: `laser-gauge`.

## The goal

Adrian wants to choose a wave (solitary, sinusoid by height and period, train,
packet, drawn profile) in a UI and have the software work out the piston
commands. That needs a model, validated against the camera, that predicts the
wave from any piston combination: target held-out error 3% (RMS of prediction
minus measurement over RMS of the measured wave, on runs the model never saw).
Wanted later: a tab in the app (stdlib only, lab Windows 7 PC) and a laptop tool.

## The measuring rig

- **Camera:** NexiGo webcam (OpenCV camera 1, DirectShow, MJPG 1920x1080, exposure -7,
  about 33 fps) on a tripod, looking across the channel at one side window. Lights on,
  black sheet behind the far wall.
- **Window position:** 185.5 to 224.5 in along the channel from the piston end. The
  piston side is the right of the picture. Distance along = 241.054 - 0.0379453 x
  column (inches), from the operator's marks.
- **Heights:** red tapes on the glass every 6 in (bottom edges at rows 243.3, 397.0,
  553.4, 707.8, 861.8; 154.8 px per 6 in, so 0.9845 mm/px). The 0 in (floor) mark is at
  row 1017. The 36 in tape stands off the glass and is not used. All of this is in
  `vision/rigs/channel_window_185in.json`.
- **`tools/vision_rig.py`:** the live window and recorder. It finds the waterline
  edge in every column and fits one smooth line (`vision/glassline.py`). It ignores
  the tapes plus 10 px either side, and columns 548-622 and 835-914 (white object
  above the waterline) and 1301-1311 (`analytics/rig/weak_fixed.npy`). It saves
  `analytics/rig/edges-*.npz` every minute and publishes `analytics/rig/live.json`
  (stillness) twice a second. A snapshot of the window goes to
  `analytics/rig/snapshot.png` every 5 s.
- **Window keys:**
  - click, type inches, Enter: a height mark;
  - `h`: cycle height / along-chamber / baseline-click modes;
  - `b`: set the still-water baseline from the water now;
  - `z`: magnifier.

  Stop it cleanly with `touch analytics/rig/STOP`.
- **Starting the window:** pass the still waterline's row and a band that contains it.
  At 406 mm of water that was `--start-row 605` (the default band is 330-900). With
  the water at 833 mm (17 Nov) it is
  `--start-row 173 --band 166 1000 --track 6`, and the rail at row 162 hides any crest
  above about +5 mm. `--auto-baseline` sets the baseline on the first still water.
  Every baseline, with its time and depth, is kept in
  `analytics/rig/annotations.json`; analysis uses the one in force at the time.

## Driving the machine (only through wavectl, see AGENTS.md)

| Tool | What it does |
|---|---|
| `tools/vision_fast.py PLAN` | Fires each test as soon as `live.json` says the water is still. Kinds: `single`, `continuous`, `pulse_down`. Logs every test to `analytics/rig/runs.jsonl`. |
| `tools/vision_plan.py --out P --seed N` | Generates test plans over pistons 1-18. Names repeat across seeds, so make them unique before reusing. |
| `tools/vision_soliton_down.py` | Elevation pulse: raise slowly, settle, push down (via `park`). |
| `wavectl soliton stage --ignore-hot` | Added 16 Nov: passes "Controller Hot" only. |

Only one process may drive the machine. Rows 1-6 (pistons 1-18) are the ones in
use. Ignore piston 26 and rows 7-10.

## Findings that matter

1. **The recorded data cannot validate any model.** The fast campaign (about 150 runs
   on 16 Nov) fired after a 30 s cap at a 2.5 mm stillness threshold, into 5-30 mm of
   leftover sloshing. Repeated identical single-piston strokes measured about 200%
   different. Every model plateaued for that reason:
   - tank physics, uncalibrated: about 94% held-out error;
   - 1-D channel model: 65-80%;
   - learned per-row kernels: about 73%.
2. **Which pistons move water:** only rows whose pipe bottom is below still water.
   - Bottoms run from 127 mm (row 1) to 817 mm (row 10), stepping 76.7 mm per row.
   - Row n is display pistons 3n-2..3n.
   - At 406 mm, rows 1-4 move water; at 833 mm, all ten do.
3. **Raising a disc makes a trough.** It draws water into the pipe. The soliton
   tool's upward "fire" therefore makes a depression wave. A clean crest needs the
   discs to go down: `vision_soliton_down.py` gave single crests of 7, 14 and 20 mm
   from 60, 120 and 180 mm lifts on pistons 1-12.
4. **The gate at the channel end is open.** The strong echo comes from about 27.5 m
   (fitted) with reflection about 0.8.
5. **The physics model (`tank-model`), fed the measured motion:**
   - it gets the first wave's arrival time and shape right;
   - it is about 2x too tall;
   - its echoes are wrong, because the closed gate it assumes is open.
6. **Machine limits:**
   - Curve mode faults the drives: no curves are stored.
   - 900 mm/s full strokes, and continuous 300 mm at 400 mm/s, tripped drives into
     "Switch On Locked". Only a power cycle clears that; `prepare` does not.
   - The "Controller Hot" warnings are unreliable (the operator's judgement).
7. **Camera data:** before 16 Nov 20:25 it is junk (the early tracker). Before
   16 Nov 23:40 the line could stick to the tapes, so readings over +40 mm are
   untrusted.

## Analysis code

- `tools/vision_channel.py`: the fast 1-D channel model, with:
  - per-row gain and delay;
  - dispersion at the marked depth;
  - damping;
  - reflections at the far end and the head;
  - fitted effective depth.

  It builds each run's flux once (`analytics/rig/flux_cache/`), assembles windows from
  every run around them (`load_case2`) and reads only the station columns
  (`stations_only`, about 28 MB per run).
- `tools/analysis/quickfit2.py` (fit on 70%, score 30%) and `tools/analysis/kernelfit.py`
  (empirical kernels). Fitted parameters are in `analytics/rig/channel_params.json`.
- `tools/vision_physics.py` drives `tank-model` with the measured motion.
- `tools/vision_eval.py` is the K-fold evaluator.

## Next steps, in order

1. Raise or tilt the camera so the still waterline sits mid-picture (the water is
   now at about 833 mm, under the rail). Press `b` on still water.
2. Run a clean campaign:
   - fire only when the water is truly still (for example `--still-mm 0.8` and a long
     `--max-wait`, several minutes);
   - test with whole rows and row combinations rather than single pistons;
   - repeat every test 3 times.

   The repeat spread is the noise floor. Report accuracy against it.
3. Refit `vision_channel.py` (and the kernels) on that data; score held-out runs.
4. Run the model backwards (inverse design) to get piston settings for a target wave
   at the window, check on the machine, then build the UI.

## State when this was written

- The `wavectl` live session process was still up.
- The controller cable read "Media disconnected" on this laptop.
- The water was at about 833 mm.
- The user had said not to run trials at this level.
- `tools/vision_rig.py` was open for viewing.
