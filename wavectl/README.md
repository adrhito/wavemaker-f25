# wavectl: the wavemaker from a terminal

`wavectl` drives the array without the window. It is for sittings where the
commands come from a script or an agent rather than a person at the screen:
each command does one thing and answers on one line.

It issues the same `Model` commands the window's buttons do. The order of
preparation, the stop guards and every limit are the application's own;
nothing here writes a run bit or a parameter by itself.

```
wavectl/wm session start --mock        # or --live for the machine
wavectl/wm select 1-9
wavectl/wm set stroke=120 speed=150
wavectl/wm prepare                     # boots and homes: about a minute
wavectl/wm run --seconds 30            # stops and rests by itself
wavectl/wm status
wavectl/wm session stop
```

Every answer begins `mock:` or `LIVE:`. Read it.

## Why there is a session

`Model` trusts a piston's homed bit only if the same process homed it, and
homing boots the drives, which drops every piston to zero and makes a wave. A
command that started a fresh process each time would either home on every
call or have to bypass that check.

So `session start` leaves one process running that holds the PLC connection
and the model, and every other command is a small client that asks it to do
something. The session outlives the terminal that started it. End it with
`session stop` when the sitting is over; that drops the run bits and
deselects every piston, as closing the window does.

There is one session per computer. Its file, `~/.wavectl/session.json`,
refuses a second one, from this checkout or any other.

## Before the machine

1. **Rehearse on the mock.** `session start --mock` runs the same code against
   the simulated wavemaker. Add `--fast` to shorten its boot and homing waits.
   The mock moves rectangles; it says nothing about water.
2. **Look first.** `wavectl/wm survey` reads the run bits and all thirty
   drives over a connection of its own and writes nothing. It needs no session.
3. **Start live.** `session start --live` refuses if a run bit is already
   high, because that means something else is driving the controller. It then
   does what the window does on opening: drops the run bits, pulses the fault
   clear, deselects every piston. Nothing moves.

The session file stops a second `wavectl` session. It does not stop the
window application or a script under `tools/`, which know nothing about it.
The cable being in one computer is what keeps those off the controller.

## Commands

Pistons are the numbers drawn on the tank, 1 to 30: `1-9`, `1,4,7`, `c1-3`
(whole columns from the front of the chamber), `r1` (a row), `all`, `none`.

| Command | What it does |
|---|---|
| `session start --mock [--fast]` / `--live [--ip A]` | Start the session. |
| `session stop [--force]` | End it. `--force` kills one that will not answer, then drops the run bits itself. |
| `session info` | Which session exists and whether it answers. |
| `status [-p] [--all]` | State, selection, stroke and speed, run bits, drive faults. `-p` adds a line per piston. |
| `survey [-p]` | The same look at the controller with no session. Reads only. |
| `log [-n 10] [--full]` | The last things the machine reported. |
| `select 1-9` | Choose the pistons that will run. Replaces the previous choice. |
| `set stroke=120 speed=150 [--pistons 1-3]` | Change parameters. Also `accel`, `decel`, `jerk`, `dwell`, `p1`, `p2`, `profile`, `offset`, `rest=down\|up\|hold`, or any full name without its space (`speed2`, `curveid`). |
| `params` | All eighteen parameters the selection is set to. |
| `preset NAME [--select]`, `preset --list` | Apply a file from `Presets/`. The start of its name is enough. `--select` chooses the pistons the preset moves. |
| `prepare` | Write parameters, boot and home the selection. About a minute; every selected piston moves. |
| `calibrate` | Home all thirty, whatever is selected. |
| `run --seconds 30` | Run continuously, then stop and rest. Answers once it is running; `--wait` answers when it is over. |
| `run --single` / `--curve` | One stroke, or one curve. Answers with how far the pistons went. |
| `run ... --sample [HZ]` | Also record piston positions to a CSV for the whole run. |
| `stop` | Finish the stroke, then move to rest. `--now` drops the run bits at once and leaves the pistons where they are. |
| `stop --direct` | Write the run bits low on a new connection, whoever raised them. Needs no session. |
| `park` | Move the selection to its resting position. |
| `speed 300`, `stroke 200` | Change a run that is already going. |
| `wait [--timeout 120]` | Wait for a prepare, run or trial to finish and give its answer. |
| `watch --seconds 10 [--hz 10] [--pistons 1-9]` | Record positions to a CSV alongside whatever is happening. Reads only. |
| `soliton plan --crest 40 --depth 406` | What a trial would command. Moves nothing. |
| `soliton stage --crest 40 --depth 406` | Lower the selected floor sections, ready for one pulse. |
| `soliton fire [--station-mm N] [--sample]` | Send the staged pulse and hold the floor up. |
| `soliton observe --crest-rise 31 --width 700 --station-mm N` | Add a measured wave to the last pulse's record. |
| `trial --crest 40 --depth 406 --settle 120 [...]` | Stage, wait for the water, fire, and measure with a camera. See below. |
| `camera N [--rig PATH]` | Open the camera as a trial would and say what it gives. Moves nothing. |

Add `--json` to any command for the whole reply as one JSON object, with the
numbers behind the sentence under `data`.

Exit status: 0 done, 1 refused or failed, 2 no session to ask.

## What it will not do unasked

- **Run for ever.** A continuous run always has a length (30 s if none is
  given, an hour at most) and the session stops it. Nothing may be watching.
- **Home.** `run`, `soliton stage` and `trial` refuse while the pistons need
  homing and say so. Run `prepare`, or add `--prepare`.
- **Move on a stop.** `stop` on a machine that is standing still moves
  nothing. Use `park` to send the pistons to rest.
- **Join in.** Before motion it refuses if a run bit is high that this
  session did not raise, if a selected drive reports a fault
  (`--ignore-warnings` overrides that one), or if the controller's selection
  is no longer the one this session wrote.
- **Change things mid-run.** `select`, `set` and `preset` wait for the
  machine to stop. `speed` and `stroke` are the two changes a run allows.

## When the session cannot be asked

Stop means stop. If a live session has died, has hung, or could not stop the
machine itself, `wavectl/wm stop` opens its own connection and writes the run
bits low. `session stop --force` does the same after killing the process.
`stop --direct` does it regardless of who raised them.

A session killed outright cannot drop its own run bits, so after anything
abrupt run `wavectl/wm stop` before walking away. The physical stop is still
the physical stop.

## A soliton trial

`soliton stage` then `soliton fire` are the Soliton tab's two buttons. The
trial is built as the tab's automatic mode builds it: the first-order width
for the crest and depth, and the floor travel that moves one soliton's worth
of water with the selected pistons. `--width` and `--lift` override either.
After the pulse the floor is held raised; staging again lowers it.

`trial` joins the steps when the timing between them matters:

```
wavectl/wm camera 1 --rig vision/rigs/tank.json      # check it first
wavectl/wm trial --crest 40 --depth 406 --settle 120 \
    --camera 1 --rig vision/rigs/tank.json --still-mm 0.5 --no-wait
wavectl/wm wait --timeout 300
```

In order: open the camera and find the rig (before anything moves), stage the
floor, wait for the water, start recording, wait `--pre` seconds so the clip
opens on still water, fire, keep recording until `--post` seconds after the
pulse, measure the clip.

- `--settle` is how long the water is left. With `--still-mm` it is the
  longest to wait: the pulse goes as soon as the camera reads the surface
  moving less than that, and does not go at all if it never does.
- A `stop`, or any edit, while it waits abandons the trial without firing.
- Without `--rig` the clip is recorded and not measured. Without `--camera`
  it is stage, wait, fire.
- `--camera synthetic` uses `vision`'s drawn tank instead of a camera, so the
  whole thing can be rehearsed on the mock. The wave it shows has nothing to
  do with the pulse commanded.

What a trial writes:

| File | Holds |
|---|---|
| `analytics/soliton-trials/trial-*.json` | The application's own record of the pulse: what was commanded, start and end positions, timing. |
| `analytics/vision/trial-*.mp4` | The clip. |
| `analytics/vision/trial-*.json` | Beside the clip: the camera, the arrival time of every picture, when the pulse went relative to the first picture, the measurement, and the path of the record above. |
| `analytics/vision/trial-*.surface.npz`, `.crest.png` | What `tools/vision_measure.py` writes: every surface reading, and the crest picture. Look at the picture before believing the numbers. |

The measurement is reported and saved beside the clip. It is entered in the
trial's own record, where `app/soliton_calibration.py` would use it, only
with `--record-observation`, and never from the mock or the drawn tank.

## Not yet proven

- **No real camera has been through `trial` or `camera`.** They have been run
  only against the drawn tank. Expect the first sitting to turn up something:
  the camera permission belongs to whichever program started the session, the
  phone's real frame rate, whether an mp4 at full size keeps up.
- **Clip timing.** `vision` takes a clip's times from the frame rate in its
  header. That rate is measured from the pictures as they arrive rather than
  taken from the camera's word, and the answer warns when the two disagree or
  pictures were dropped. Arrival is still not exposure: a phone delivers each
  picture a little late, by an amount nothing here knows, so the moment of
  the pulse within the clip is good to a frame or few and no better.
- **Windows.** Starting and ending the session has only been run on macOS.

## Files and settings

| | |
|---|---|
| `~/.wavectl/session.json` | That a session exists, and how to reach it. |
| `logs/wavectl/session.log` | Anything the session process printed, such as a traceback. |
| `logs/wavectl/samples-*.csv` | Positions from `--sample` and `watch`: seconds, then millimetres per piston. |
| `logs/<date>.log` | The application's own log, which the session writes to as the window does. |
| `WAVECTL_MOCK_ONLY=1` | Every attempt to reach a real controller fails before a socket is opened. The tests set it. |
| `WAVECTL_HOME`, `WAVECTL_DATA` | Put the session file, and the logs and analytics, somewhere else. |
| `WAVECTL_PYTHON` | The interpreter `wm` uses. Default: `.venv/bin/python` at the top of the checkout if it exists, else `python3`. |

The session needs the Python the application runs on, tkinter included,
because the model imports the logging the window uses. Camera commands also
need `requirements-vision.txt`.

Without the launcher: `python -m wavectl ...` from the top of the checkout.

## Layout

| Module | Holds |
|---|---|
| `cli.py`, `client.py`, `wire.py` | The commands, and their one-line-of-JSON conversation with the session. |
| `daemon.py` | The session process. |
| `sessionfile.py`, `places.py` | The lock file, and where things are written. |
| `service.py` | What a session can be asked: status, selection, parameters, presets, and the waiting. |
| `motion.py` | Prepare, run, stop, park, and the checks before motion. |
| `soliton.py`, `trials.py`, `camera.py` | Stage and fire; the whole trial; the camera's part of it. |
| `sampling.py` | Position recording. |
| `live.py` | Every connection to the real controller, the direct stop and the survey. |
| `journal.py`, `jobs.py`, `settings.py`, `pistons.py`, `timing.py` | The model's messages, long operations, parameter names, piston lists, timeouts. |

Tests are `tests/test_wavectl_*.py`. They run against the simulator, with
`WAVECTL_MOCK_ONLY` set for the whole suite.
