# Operating the wavemaker

> **The lab PC is Windows 7.** Everything in this application is written to run
> there. If it will not start on that machine, go straight to
> [Running on Windows 7](#windows-7) — do not install a newer Python to
> "fix" it.
>
> **If a command window flashes up and vanishes and nothing runs, double-click
> `Diagnose Wavemaker.cmd`.** That window stays open and says why.

<a id="windows-7"></a>

## Running on Windows 7

**This is a hard constraint, not a preference. Nothing in this repository may
require anything newer than Python 3.7.**

Windows 7 is the last Windows the lab PC runs, and **Python 3.9 and newer will
not install or run on it at all** — 3.8 is the last version with a Windows 7
installer, and 3.7 also works. So the application targets **Python 3.7**, and
that ceiling governs every line of code in here.

### The rules

- **No syntax newer than Python 3.7.** No `dict | dict` merging (3.9), no
  built-in generics such as `list[str]` in evaluated positions (3.9), no
  `match` statements (3.10), no parenthesised context managers (3.10).
- **No standard-library calls newer than Python 3.7.** The ones that catch
  people out: `str.removeprefix` / `removesuffix`, `math.prod`, `math.dist`,
  `functools.cached_property`, `typing.Protocol`, `typing.Literal`,
  `typing.TypedDict`, `importlib.metadata`, `zoneinfo`, `graphlib`,
  `Path.is_relative_to`, `shlex.join`. `Model.UiBridge` and `app.plc.Transport`
  are plain base classes instead of `typing.Protocol` for exactly this reason —
  leave them that way.
- **No third-party packages.** The only dependency is `pylogix`, vendored under
  `modules/`. Nothing here may need `pip install`.
- **Tk is old there too.** Use the Canvas-based widgets in `modules/widgets.py`
  (`RoundedButton`, `Segmented`) rather than ttk for anything that needs a
  colour or a selected state — ttk on Windows 7 ignores background colours on
  buttons and draws radio buttons as small grey circles that are easy to miss.
  On the Canvas, a `dash` pattern only draws on a **one-pixel** line on
  Windows; widen the line and the dash silently disappears there while still
  looking right on a newer machine.
- **Assume a slow machine.** Anything on a redraw path has to be cheap. Work
  out physics once when the parameters change, not once per animation frame.

### Checking a machine, or a change

Run the environment check with the interpreter that machine actually uses:

```
py -3 docs\check_python.py
```

or, if the `py` launcher is not on PATH:

```
C:\Python37\python.exe docs\check_python.py
```

It prints the Python version, confirms `tkinter` is present, imports every
module of the application, and loads every preset. It contacts no PLC and
changes nothing. Anything that would fail on the lab PC fails there, by name.

To check a change from a *newer* machine, where you cannot simply run 3.7, this
parses every file under the 3.7 grammar and names anything too new:

```
python -c "import ast,pathlib;[ast.parse(p.read_text(encoding='utf-8'),str(p),feature_version=(3,7)) for p in pathlib.Path('.').rglob('*.py') if '__pycache__' not in p.parts]"
```

Silence means every file is valid Python 3.7. It catches new *syntax* only, so
the standard-library list above still has to be checked by eye.

### A command window flashes up and disappears, and nothing runs

**Start here: double-click `Diagnose Wavemaker.cmd`.** It stays open, lists
every Python on the machine, runs the environment check, prints the last
recorded crash, and offers to start the application with a console attached so
the error is visible. Nothing in it touches the PLC unless you answer `Y` at
the last step.

The flash itself is not the fault, which is why this was so hard to see.
`Open Wavemaker.cmd` starts the application with **`pythonw.exe`** — the
windowless interpreter, so no console sits behind the interface — and then
exits, which closes its own window. That flash is the launcher working. But a
windowless interpreter has **no `stdout` and no `stderr`**: if Python or the
application fails on the way up, the traceback is written nowhere. Flash, then
nothing, and no clue anywhere.

Three things now make that failure visible:

- **`main.py` catches it.** Any failure before the window appears is written to
  `logs/startup-error.txt` — with the Python version and the exact interpreter
  that ran — logged to the day's error log, and shown in a message box.
- **`Diagnose Wavemaker.cmd`** shows all of it in a window that stays open.
- **The launchers no longer pick the newest Python.** The `py` launcher's plain
  `-3` selects the **newest** installed Python. If a Python 3.9 or newer has
  ever been installed on that Windows 7 machine, `pyw -3` chose it — and 3.9+
  cannot run on Windows 7 at all. It dies instantly, before printing anything,
  and because it was launched windowless there was nothing to see.
  `Open Wavemaker.cmd` and `Mock Wavemaker (no machine).cmd` now look for a
  known-good 3.8 or 3.7 by full path first, then ask the launcher for `-3.8` or
  `-3.7` **by name** (testing with console `py.exe` before launching with
  windowless `pyw.exe`), and only then fall back to newest-wins, which is right
  on a modern machine.

If a 3.9+ is installed there, the fix is to install **Python 3.8** — the last
version with a Windows 7 installer — with **tcl/tk and IDLE** ticked. Removing
the newer one is not necessary now that the launchers pin a version, but it
removes the trap for good.

**What it actually was, on 21 September 2026.** Not the Python version: the lab
PC had a healthy Python 3.7.0 32-bit and every module imported. The application
was dying while building its first tab, on
`self.low_slider["state"] = "disabled"` — see *Tk on the lab PC is older than
yours* below. Two things had hidden it: the windowless interpreter threw the
traceback away, and the launchers' install-path list did not include the
`Python3x-32` folders the 32-bit installer creates, so Python was only being
found via PATH. Both are fixed, and the list now covers `-32`.

### If it still will not start

1. Run `Diagnose Wavemaker.cmd`, or the environment check above. Between them
   they name the problem in most cases.
2. `Could not find Python on this machine.` — Python is not installed, or not
   on PATH. The launchers already look in `C:\Python37`, `C:\Python38`,
   `C:\Program Files\Python3x`, `C:\Program Files (x86)\Python3x-32` and
   `%LOCALAPPDATA%\Programs\Python\Python3x`. If it is somewhere else, run it
   directly: `C:\<your path>\python.exe "C:\...\WaveMaker_F25_new\main.py"` —
   note `python.exe`, not `pythonw.exe`, so you can see the error.
3. A `SyntaxError` naming a file in this repository means something newer than
   3.7 got committed. That is a bug in the change, not in the machine — fix the
   code, do not upgrade the PC.
4. `FAIL tkinter is not available`, or `ImportError: DLL load failed ...
   _tkinter` — re-run the Python installer and tick **tcl/tk and IDLE**.
5. The window opens but nothing connects — that is a network or controller
   question, not a Windows 7 one. See
   [When something goes wrong](#when-something-goes-wrong).

### Tk on the lab PC is older than yours — check widget options

Python 3.7.0 ships **Tcl/Tk 8.6.6**. A current Python ships 8.6.12 or newer.
Widget options added in between exist on your machine and do not exist there,
and asking for one raises `_tkinter.TclError: unknown option "-..."` — which,
during window construction, means no window at all.

This has already bitten once. `self.low_slider["state"] = "disabled"` worked on
every developer machine and killed the application on the lab PC, because
**`-state` was only added to `ttk::scale` in Tk 8.6.10** (and to
`ttk::progressbar` and `ttk::scrollbar` at the same time).

**Use `modules.widgets.set_state(widget, "disabled")`, not
`widget["state"] = ...`, for any ttk widget that is not a Button, Entry,
Combobox or Checkbutton.** It sets the ttk *state flag*, which every version
understands and which `style.py` already paints a `disabled` look from, then
tries the `-state` option and ignores a refusal.

One caveat, and it matters for safety: on Tk older than 8.6.10 the disabled
flag greys a scale out but does **not** stop it being dragged — the binding
that checks the flag arrived with the option. Where a control is locked because
a value is being written to the machine, call
`modules.widgets.lock_scale(scale, True)` as well; it swallows the press.

To check a change against old Tk without the machine, make the affected widget
classes refuse `-state` and drive the interface through every state — that is
how the fix above was verified across all five tabs and all five machine
states. The Tk release notes are the reference for when an option appeared.

### Never print to the console

The application runs under `pythonw.exe`, where `sys.stdout` and `sys.stderr`
are `None`. A bare `print()` there raises
`AttributeError: 'NoneType' object has no attribute 'write'` — on the lab PC
only, invisibly. **Use the logger, never `print`.** There is no `print` in the
application's own code; the one in the vendored `modules/eip.py` is guarded
with `if sys.stdout is not None`, and any new one must be too.

## Starting up

1. Wall disconnect on.
2. Cabinet switch on. Wait for the supply voltage display to settle.
3. Green button.
4. Double-click **`Open Wavemaker.cmd`**. The interface opens.

That is it. There is no Studio 5000 step, no Go Online step, no keypress, and
no database script to start first.

### Why those steps are gone

- **The MongoDB script** was never required. It only stores analytics runs, and
  the application already writes them to `analytics/<date>.txt` regardless. The
  launcher now starts MongoDB quietly in the background if it is installed, and
  carries on without it if not.
- **Studio 5000 and Go Online** are not required either. The application opens
  its own EtherNet/IP session to the controller. "Go Online" connects Studio
  5000 to the controller; it has no bearing on whether other clients can read
  and write tags.
- **Rem Run is the one thing that genuinely matters** -- the ladder logic must
  be scanning or writing `Run_2` does nothing. But the controller stays in Run
  until somebody changes it, so this is not a per-launch step. It only needs
  attention after somebody has put the controller into Program mode.

If the controller is not reachable, the banner says so and two buttons appear:
**Reconnect**, and **Open Studio 5000** for when you do need to go online and
put it back in Run. You can close Studio 5000 again afterwards.

The banner at the top of the Operate tab says whether you are connected:

- `Connected  ·  192.168.1.1` — the real machine. Pistons will move.
- `Mock wavemaker  ·  nothing physical will move` — you started the mock
  deliberately (see below).
- `Not connected  ·  the PLC did not answer` — press **Reconnect**; if that
  fails, use **Open Studio 5000**, go online, confirm the controller is in
  Rem Run, then Reconnect again.

## Running

1. **Click or drag on the tank** to choose pistons. The selection is simply
   "the pistons that will run" — there is no step to confirm it.
2. Set the **stroke** (from / to, in mm) and the **speed**. These are the two
   things that change between runs. Everything else is behind
   **All parameters**.
3. Choose **One stroke**, **Continuous** or **Curve**, and press **Start**.

**Start does whatever is needed.** It writes any parameters you have changed,
homes the pistons if they are not homed, then runs. Homing physically moves
every piston and takes about a minute, so the first run asks before doing it.
After that Start is immediate.

### Two groups at once

To run two lots of pistons with different parameters at the same time, press
**Add group**. The current selection is frozen as Group 1, and you then select
the pistons for Group 2. An **Editing** box appears so you can switch between
them. A piston belongs to one group only; delete the group to free it.

### Patterns

**Pattern** fills one parameter across a group in a shape — a ramp, a stagger
per position, or mirrored about the centre. Staggering a timing or curve offset
**front to back** is what makes a wave travel along the chamber rather than the
whole array moving together. There is a preview before anything is applied.

## Stopping

**Stop** is in the status bar at the bottom of every tab, and `Escape` does the
same. It drops all three run bits immediately; nothing delays the halt.

**After a stop the pistons return to the bottom of their stroke** (368 mm) at a
gentle 200 mm/s, so the array is left in a known resting state. Pressing Stop
again while they are travelling there leaves them where they are.

The machine stays homed through this, so the next run does not have to home
again — but the parameters are re-sent, because the PLC is holding the parking
values by then.

If a stop cannot be delivered — a network fault, the PLC offline — the
application says so in a dialog rather than reporting success. Use the physical
stop.

Closing the window stops the machine and clears faults first. If pistons are
moving it asks for confirmation.

## Trying it without the machine

Double-click **`Mock Wavemaker (no machine).cmd`**. It runs a simulated
wavemaker on any computer: no PLC is contacted and nothing physical can move.

It is the same application — the only difference is where the piston positions
come from. Homing takes a moment, the pistons really stroke between your
positions at your speeds, parking really returns them to the bottom, and a
front-to-back stagger really does travel along the chamber in the live view.

It does **not** predict how the real machine behaves. It moves rectangles.

## Parameters

| Parameter | Accepted range | Notes |
|---|---|---|
| Position 1, Position 2 | -20 to 368 mm | -20 is the top of the stroke, 368 the bottom. After homing. |
| Speed 1, Speed 2 | 0 to 900 mm/s | The top speed actually reached depends on available current. |
| Accel 1/2, Decel 1/2 | 0 to 20,000 mm/s² | |
| Jerk 1, Jerk 2 | 0 or more | Normally larger than the acceleration and deceleration. |
| Time 1, Time 2 | 0 or more | Dwell at the end of the stroke. Leave at 0 unless you want a pause. |
| Profile | 0 to 3 | Trapezoidal (0), Bestehorn (1), S-Curve (2), Sine (3). |
| Move Type | 0 or 1 | Absolute (0) or Incremental (1). |
| Curve ID, Time Scale, Amplitude Scale, Curve Offset | 0 or more | Used by Start Curve only. |

A value outside these ranges is refused when you type it, with a message under
the boxes saying why. Nothing is sent to the machine until it is valid.

### Two open questions about the limits

**Position: 368 or 370?** The application enforces 368. The GUI manual, the old
tooltips, and `Presets/massive.csv` all say 370. The drive's own configuration
(`LinMot Drive Config`) puts its hard position limits at -57 and 453 mm with the
home position at 390 mm, so 370 is well inside what the drive itself allows.

368 is kept because loosening a limit on this machine is not a documentation
decision. If 370 is correct, change the two `Position` lines in `app/params.py`
— it is a one-line change each and the tooltips and validation follow
automatically. Until then `Presets/massive.csv` will be refused with
`Position 2 must be at most 368 (got 370)`.

**Acceleration: 20,000 or 50,000?** The application enforces 20,000. The old
tooltips said 50,000 while the old code rejected anything above 20,000, so the
tooltip was simply wrong — it has been corrected to match what is enforced.
Whether the true limit is higher is a question for the drive manual.

## Homing runs twice

`Model._home_motors` homes the machine twice: a short settling pass, then the
real one. This is deliberate and predates this work. The original code carried
the comment *"executed twice to prevent homing at a wrong position — need
further investigation on why will the piston home on a certain high position"*.

The reason was never established, so the behaviour is unchanged. If someone
works out why the pistons occasionally home high, the second pass may be able
to go.

## Presets

Preset files live in `Presets/` and are ordinary CSVs you can edit in Excel.
Start from `Preset Outline (COPY ME).csv`.

**One preset is already in force when the application opens:**
`Preset 1-Big Wave Demo2.csv`. Every piston you pick on the tank starts with
its values, so a normal run is select pistons then **Start** — there is no need
to visit Preset Options first. To make a different preset the default, change
`DEFAULT_PRESET` in `app/paths.py` to point at it.

A preset has one row per piston plus a final `All` row. **A row of zeroes means
"this piston was not part of the preset"**, not "hold this piston at zero" —
that is what the application writes for pistons that were not in a set when the
preset was saved. When a preset is applied, a piston uses its own row if that
row commands motion, and otherwise falls back to the `All` row.

This matters because most of the shipped presets define motion for only some
pistons. `curve1.csv`, for example, has real values only for motors 12, 13 and
14; every other piston takes the `All` row.

To apply one: **Preset Options** → *Select Preset...* → tick the sets it should
apply to → *Apply to Selected Groups*. Then press **Start**.

To save what you have built: *Save Current Groups...*. Every group is written,
not just the pistons currently ticked.

## Analytics

Analytics are recorded from the Operate tab. The live view always shows
actual positions while running; analytics keep a record of them.
While the machine runs, the demanded and actual position of every piston in every
set is sampled and written to `analytics/<date>.txt`. Runs on the same day are
appended to the same file with a header between them.

Sampling happens on continuous runs and curve runs.

If MongoDB is running locally the same data is also stored there
(`wavemaker_db.general_collection`), started with `Run_wavemaker_database.cmd`.
If it is not running, the run is still written to the text file and the
application carries on.

Test your parameters without analytics first, so you know they will not fault the
machine, then run again with analytics on.

## When something goes wrong

The **Feedback** tab shows everything the application has done. Everything from
INFO upwards is also written to `logs/<date>.log`. *Copy to Clipboard* on that
tab puts the whole session log on the clipboard for pasting into an email.

| Symptom | Likely cause |
|---|---|
| Banner says Not connected | The controller is unreachable or not in Run. Press Reconnect; if that fails use Open Studio 5000 and check for Rem Run. |
| "Motors did not home within 35 seconds" | A drive is faulted or not enabled. Check the drive, then press Off and Reset and prepare again. |
| A parameter will not apply | It is outside the range in the table above; the reason is shown under the boxes. |
| "Could not read preset" | The CSV is missing a column, or is not a preset file. The message names what is wrong. |
| Start does nothing | No pistons are selected. Click or drag on the tank first. |
