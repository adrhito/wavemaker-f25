# Agent guide for Wavemaker

This repository controls 30 physical LinMot pistons through an Allen-Bradley ControlLogix PLC. Read this file before changing code, and treat motion control as safety critical. The current implementation and tests take precedence when older prose in `README.md` or `docs/` disagrees with them.

For machine-specific history and operating observations, also read `.claude/skills/wavemaker/SKILL.md` before changing motion code.
For water-height or soliton work, read `docs/CALIBRATION_NOTES.md`; piston stroke is not a measured water-wave height.

## Where to work

- `main.py` parses `--mock` / `--simulate`, `--ip`, `--slot`, and `--fresh-connection`, then builds `Model` and `View`.
- `Model.py` owns application state, groups, worker commands, homing, running, stopping, resting, monitoring, and analytics. `Motor.py` owns one piston's parameter cache, validation, writes, and status reads.
- `app/plc.py` supplies the locked real `PlcClient` and the in-memory `SimulatedPlc`; `app/simulator.py` adds moving mock pistons. `app/tags.py` owns PLC tag names and axis/display conversion. `app/params.py` owns the 18 parameter specs, limits, defaults, and write order. `app/patterns.py` and `app/waves.py` implement patterns and wave math.
- `View.py` owns the Tk window and its callback queue. The current tabs are `operate/`, `wave/` (including Soliton), `preset_options/`, `diagnostics/`, and `feedback/`. Shared drawing and logging code lives in `modules/`.
- `preset_options/PresetProcessor.py` reads and writes `Presets/*.csv`. `tests/` is the active suite. `archive/` contains superseded code and documentation. `tools/live_*.py` are hardware-facing experiments, not routine tests.
- `vision/` measures the water with a camera looking through the tank's side wall, and is the only part of the repository that needs NumPy and OpenCV (`requirements-vision.txt`). Nothing the application imports may depend on it, so the Windows 7 launch is unaffected. `tools/vision_*.py` are its entry points; they read a camera or a recorded clip and never contact the PLC. `vision/synthetic.py` draws a tank with a known wave, which is all the measuring has been checked against so far: treat its accuracy as unproven on the real tank until footage says otherwise.
- `tank-model/` is an approximate model of the tank and the waves the pistons make. Like `vision/` it needs NumPy and SciPy (its own `.venv`; see its README), and nothing the application imports may depend on it. Read `tank-model/HANDOFF.md` before relying on its numbers; if you have been given the tank's blueprints, it says how to bring the model to the exact measurements.
  - **Trust the piston system first.** The most accurate numbers in the model are the ones for the piston system, because they were physically measured. They are the pipe size (8 in Sch 10S, read off the stencil, so a 211.6 mm bore and a 351.5 cm² sealed disc face), the stepped pipe lengths, the level pipe tops, the row 1 bottom height (row 1 is the longest row, row 10 the shortest), and which end is which (row 1 at the head end, row 10 at the channel mouth). The channel's inside width (29.25 in, taped 28 Oct) is also measured. The 26.5 in in `app/solitons.py` is out of date. Nearly everything else about the tank was estimated by Claude from a series of photos. That covers the channel length, the rim height, the head's size, the reservoir, and the spacing of the bank along the tank.
  - When a new measurement arrives, record it in `tank-model/geometry.json` with its source in `how` and a narrowed `range`, then update `MEASUREMENTS.md`. When the model and the tank disagree, change the photo-derived estimates first. Don't bend the measured piston figures to absorb the error.
- `app/solitons.py` describes the requested water profile and bounded one-way floor command; `wave/SolitonDesigner.py` provides the Soliton tab. `app/soliton_records.py` saves local trial/observation JSON; `app/soliton_calibration.py` suggests floor lift only between repeated, matched hardware observations. A requested water height is never inferred from motor stroke without calibration.

## Run and verify

- Work from the repository root. Install test dependencies with `python -m pip install -r requirements-dev.txt` (use `python3` or `py -3` where appropriate), then run `python -m pytest -q`. The active tests use simulated transports and do not need a PLC; `tests/conftest.py` shortens command waits and runs model commands inline.
- For a GUI rehearsal, use `python main.py --mock`. This mode does not contact hardware. A plain `python main.py` attempts the real PLC at `192.168.1.1`; its startup and later commands can write to the machine. Do not use it as a smoke test.
- Add focused simulator tests for changes to motion, tags, limits, presets, threading, or UI state. Tests prove what the application sends and what the mock does; they do not prove how real drives respond.
- Runtime logs and analytics are written beneath `logs/` and `analytics/`. Keep generated files, virtual environments, and local test artifacts out of commits.

## Driving the array from a terminal: `wavectl`

`wavectl/` is the command-line way to drive the machine, for work where the commands come from an agent or a script rather than a person at the window. Use it instead of writing another `tools/live_*.py` script, and read `wavectl/README.md` before the first live use.

One long-lived session process holds the PLC connection and the `Model`; `wavectl/wm <command>` asks it to do one thing and answers on one line beginning `mock:` or `LIVE:`. It exists because `Model` trusts a homed bit only from the process that did the homing, so a command that started a new process each time would home on every call. Every operation ends in the same `Model` command the window uses, so the invariants below hold unchanged.

```
wavectl/wm session start --mock --fast    # rehearse; --live for the machine
wavectl/wm survey                         # read-only look at the controller; needs no session
wavectl/wm select 1-9                     # display numbers; c1-3 is the front three columns
wavectl/wm set stroke=120 speed=150
wavectl/wm prepare                        # boots and homes: about a minute, every selected piston moves
wavectl/wm run --seconds 30 --sample      # stops and rests by itself; --single for one stroke
wavectl/wm status                         # state, selection, run bits, drive faults
wavectl/wm stop                           # --now halts at once; --direct needs no session
wavectl/wm soliton stage --crest 40 --depth 406
wavectl/wm soliton fire                   # or `trial`, which adds the wait for the water and the camera
wavectl/wm session stop                   # always, when the sitting is over
```

- Rehearse any new sequence on the mock first. `WAVECTL_MOCK_ONLY=1` in the environment makes every route to the real controller fail before a socket opens; the test suite sets it.
- One session per computer, enforced by `~/.wavectl/session.json`. That stops a second `wavectl` session, not the window application or a `tools/live_*.py` script: never start `--live` while either is running. A live session refuses to start if a run bit is already high.
- A continuous run always has a length and the session stops it. `run`, `soliton stage` and `trial` refuse to home on their own account: run `prepare`, or pass `--prepare`.
- Before motion a command refuses, with nothing written, if a run bit is high that the session did not raise, if a selected drive reports a fault, if the drives no longer report homed, or if the controller's selection is no longer the session's. Do not work round a refusal; find out what else touched the controller. `--ignore-warnings` is for a drive fault that preparing is meant to clear, and for nothing else.
- If the session dies or hangs, `wavectl/wm stop` writes the run bits low on a connection of its own. Run it after anything abrupt.
- Dropping the run bits does not freeze a piston: measured on 12 October 2026, each one finished the move it was on, 15 to 185 mm further, and more than the stroke itself when the stop came while the pistons were still travelling from their resting place onto it. `stop --now` waits for them and reports where they came to rest. Plan on a piston completing whatever move it has begun.
- The fault-clear pulse switches off the drives that are selected, and a piston whose drive goes off above the bottom of its travel sinks (120 mm to as low as 260 mm that day). Starting a session writes nothing for that reason, and deselecting alone switches nothing off. Ending a session pulses the fault clear with no length, which switched off 25 of 29 selected drives and left 4 holding: `session stop` says how they were left. `park` before `session stop` unless the pistons are meant to stay where they are.
- Nearly every command has been run on the array; `wavectl/README.md` has the table. Not run there: `run --curve`, `soliton observe`, and anything with a camera, which macOS refused until the program that starts the session is allowed to use it.
- Commands that outlast a tool's timeout take `--no-wait`; `wavectl/wm wait` then gives the outcome. `--json` gives any reply in full.
- Evidence is written under `logs/wavectl/` (position samples), `analytics/soliton-trials/` (the application's own pulse records) and `analytics/vision/` (clips and what was read from them). Report those paths, not the samples.
- `trial` with a camera, and `camera`, have only been run against `vision`'s drawn tank. Treat their first use with a real camera as a test of them.
- When changing `wavectl/`: operations call `Model` commands and never write a run bit or a parameter themselves, apart from `live.stop_directly`, which only ever writes the command bits low. Add tests beside the others in `tests/test_wavectl_*.py`.

## Commit practice

- Make a concise, meaningful commit after each significant step of work in this project, including work requested in another chat. For substantial implementation or testing, make intermittent commits as milestones are completed instead of waiting until the end.
- Keep commit messages short and descriptive. Never mention Codex or Credit AI in a commit message, and never use emojis. Tables are welcome when they make a longer commit description clearer.

## Invariants to preserve

- Screens call `Model` commands; they do not access a PLC transport directly. PLC operations can run on workers; only the Tk main thread may touch widgets. Route callbacks through `UiBridge` / `View.post`, and queue log records before displaying them.
- Preserve `Model`'s state and cancellation guards. Stop must remain available while another command is busy, drop `Run_1`, `Run_2`, and `Run_Curve`, and report a failed stop. A normal first Stop waits briefly for a stroke end and may move pistons to their chosen resting position; Escape or a second Stop halts immediately. Resting temporarily overwrites PLC parameters, so invalidate the written-parameter cache afterward.
- Preparation selects `Live_Motors`, clears faults, boots drives, writes parameters, then homes. Booting clears the homed reference. Do not skip homing by forcing `MachineState.HOMED` or `_homed_axes`. The two homing passes are intentional pending machine investigation. Keep command pulses interruptible and clear asserted bits in `finally` paths.
- Use `app/tags.py` for every tag and displayed piston number. Internal axes are `0..29`; the pictured pistons are `1..30`, with display piston 1 on axis 29. The chamber's front starts at display column 1. Use `tags.display_number` / `axis_from_display` instead of assuming `axis + 1` is the operator's number.
- Position parameters are written in millimetres, but actual and demanded positions read back in counts of 10,000 per millimetre. Use `params.to_mm` / `to_counts`. The current accepted position range is `-20..370` mm, speed is `0..900` mm/s, and acceleration/deceleration are `0..20,000` mm/s²; read `PARAMS` for the complete current limits.
- `PARAMS` order is the preset CSV column order; do not reorder it. `WRITE_ORDER` separately puts Move Type and Profile first for PLC writes. Preserve the preset convention that a zero placeholder row falls back to the `All` row.
- Soliton trials stage the vertically moving floor separately, wait for the operator to fire, and hold raised after one pulse. Stop during a pulse clears the run bit promptly without automatically lowering the floor. The conservative speed, lift, acceleration and deceleration bounds in `app/solitons.py` are starting limits, not verified safe maxima; do not raise them or claim a calibrated soliton without physical measurements and lab review.
- Vendored pylogix lives in `modules/eip.py` and `modules/lgxDevice.py`; avoid editing it for application behavior.

## When work explicitly involves the real array

Coordinate with the operator and make sure no other app or live tool is controlling the PLC: the software has no exclusive ownership check. Never run two live sessions concurrently. Check command bits and drive status before motion, use a small conservative selection first, and arrange cleanup that drops all run and home bits even on error or interruption. Inspect the resulting logs and held parameters; keep simulator results distinct from physical observations. Do not run live tools merely to validate a code change.

The development Mac can drive the array directly when the controller's Ethernet cable is plugged into it instead of the lab computer. This was first done on 12 October 2026: with the Mac's adapter set by hand to `192.168.1.200`, mask `255.255.255.0` and no router, `tools/live_one_stroke_probe.py --piston 1 2 3` homed and stroked three pistons. The piston drives answer on low addresses of the same network (`192.168.1.2` to `.28` were seen), so do not pick an address down there. The cable can only be in one computer, which is what keeps two sessions off the controller; if a switch is ever added, that protection is gone.

The lab computer is a separate Windows machine without internet access. The application must keep running there, so keep its runtime requirements to the standard library and plan for offline transfer when code has to reach it. The mock's piston animation is not a model of water height.
