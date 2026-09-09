# Developing

## Running the tests

```
py -3 -m pip install -r requirements-dev.txt
py -3 -m pytest
```

The whole suite runs against an in-memory PLC simulator and never opens a
socket, so it is safe to run anywhere. It takes well under a second.

To exercise the interface without the machine:

```
py -3 main.py --simulate
```

## How it fits together

```
main.py            parses arguments, sets up logging, builds Model then View
  |
  +-- Model.py     application state and every machine command.
  |                 Owns the worker thread. Knows nothing about Tk.
  |     |
  |     +-- Motor.py        one piston: parameters, validation, drive state
  |     +-- app/plc.py      PlcClient (real) and SimulatedPlc (offline)
  |     +-- app/tags.py     every PLC tag name
  |     +-- app/params.py   the eighteen parameters: names, ranges, defaults
  |     +-- app/paths.py    where logs, analytics and presets live
  |
  +-- View.py      the window. Implements Model's UiBridge and marshals every
                   callback onto the Tk thread via root.after.
      |
      +-- control_home/ define_motors/ preset_options/ feedback/
```

Three rules keep it out of trouble:

1. **Only `Model` talks to the machine.** Screens call model commands; they never
   touch a PLC object.
2. **Only the main thread touches Tk.** Model commands run on a worker and report
   back through `UiBridge`; `View` queues each callback and drains it from a
   timer. The same applies to logging — `TextboxLogHandler` queues records rather
   than writing to the widget from whichever thread logged.
3. **Button states come from `MachineState`, in one place.** `ControlHome.refresh`
   is the only thing that enables or disables the run buttons.

## The PLC transport

`app/plc.py` holds one connection open and guards it with a lock. Both classes
expose the same interface:

- `PlcClient` — the real machine. Raises `PlcError` on failure, and reconnects
  once before giving up, because a ControlLogix session can be dropped by the PLC.
- `SimulatedPlc` — an in-memory dictionary. Writes are stored, so reads return
  what was written, and `history` / `writes_to(tag)` let tests assert on exactly
  what reached the machine.

`modules/eip.py` and `modules/lgxDevice.py` are vendored pylogix 0.2.0. **Leave
them alone**; they are third-party code. Two things about that version are worth
knowing:

- `Write` returns `None` on success and raises on a non-zero CIP status.
- Reading an unknown tag raises `ValueError` from `InitialRead`, so a mistyped
  tag name does fail loudly — provided something catches it. That is why tag
  names live in `app/tags.py` rather than being formatted at each call site.

## Adding a parameter

Add one `ParamSpec` to `PARAMS` in `app/params.py`. The entry boxes, tooltips,
defaults, validation, preset columns and PLC writes all follow from it.

Note that `PARAMS` is in **display and CSV order**, which is part of the preset
file format and must not be reordered. The order values are written to the PLC
is `WRITE_ORDER`, which puts Move Type and Profile first.

## Timings

The PLC acts on a command bit while it is held high, so the application sets a
bit, waits, and clears it. Those waits are the constants at the top of `Model.py`
(`BOOT_PULSE_SECONDS`, `HOME_POLL_SECONDS`, and so on). Tests set them to zero.
Do not replace them with bare `time.sleep` calls again.

## Things deliberately left as they are

- **Homing runs twice.** See docs/OPERATING.md. Unexplained, so unchanged.
- **The write order.** Move Type and Profile before everything else, as the
  original code did, in case the ladder logic depends on it.
- **Position limit 368, acceleration limit 20,000.** What the code has always
  enforced. See docs/OPERATING.md for the discrepancy with the manual.
- **`modules/eip.py`.** Vendored third-party code.

## What still needs the real machine

Everything below is verified against the simulator only. The simulator proves
*what the application sends*; it cannot prove how the machine responds.

- That homing completes, and how long it really takes.
- That a stop physically stops the pistons.
- That the corrected `Run_2` tag name starts continuous motion — the old code
  wrote `Program:wave_Control.Run_2` (lower-case w), which cannot have worked, so
  how continuous runs were being started in practice is worth checking.
- That parameters written per-set land on the right drives.
- Whether the position limit is 368 or 370.
