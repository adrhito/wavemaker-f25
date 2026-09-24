# Agent guide for Wavemaker

This repository controls 30 physical LinMot pistons through an Allen-Bradley ControlLogix PLC. Read this file before changing code, and treat motion control as safety critical. The current implementation and tests take precedence when older prose in `README.md` or `docs/` disagrees with them.

## Where to work

- `main.py` parses `--mock` / `--simulate`, `--ip`, `--slot`, and `--fresh-connection`, then builds `Model` and `View`.
- `Model.py` owns application state, groups, worker commands, homing, running, stopping, resting, monitoring, and analytics. `Motor.py` owns one piston's parameter cache, validation, writes, and status reads.
- `app/plc.py` supplies the locked real `PlcClient` and the in-memory `SimulatedPlc`; `app/simulator.py` adds moving mock pistons. `app/tags.py` owns PLC tag names and axis/display conversion. `app/params.py` owns the 18 parameter specs, limits, defaults, and write order. `app/patterns.py` and `app/waves.py` implement patterns and wave math.
- `View.py` owns the Tk window and its callback queue. The current tabs are `operate/`, `wave/`, `preset_options/`, `diagnostics/`, and `feedback/`. Shared drawing and logging code lives in `modules/`.
- `preset_options/PresetProcessor.py` reads and writes `Presets/*.csv`. `tests/` is the active suite. `archive/` contains superseded code and documentation. `tools/live_*.py` are hardware-facing experiments, not routine tests.

## Run and verify

- Work from the repository root. Install test dependencies with `python -m pip install -r requirements-dev.txt` (use `python3` or `py -3` where appropriate), then run `python -m pytest -q`. The active tests use simulated transports and do not need a PLC; `tests/conftest.py` shortens command waits and runs model commands inline.
- For a GUI rehearsal, use `python main.py --mock`. This mode does not contact hardware. A plain `python main.py` attempts the real PLC at `192.168.1.1`; its startup and later commands can write to the machine. Do not use it as a smoke test.
- Add focused simulator tests for changes to motion, tags, limits, presets, threading, or UI state. Tests prove what the application sends and what the mock does; they do not prove how real drives respond.
- Runtime logs and analytics are written beneath `logs/` and `analytics/`. Keep generated files, virtual environments, and local test artifacts out of commits.

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
- Vendored pylogix lives in `modules/eip.py` and `modules/lgxDevice.py`; avoid editing it for application behavior.

## When work explicitly involves the real array

Coordinate with the operator and make sure no other app or live tool is controlling the PLC: the software has no exclusive ownership check. Never run two live sessions concurrently. Check command bits and drive status before motion, use a small conservative selection first, and arrange cleanup that drops all run and home bits even on error or interruption. Inspect the resulting logs and held parameters; keep simulator results distinct from physical observations. Do not run live tools merely to validate a code change.
