---
name: wavemaker
description: Operating, testing and debugging the UNC Fluids Lab wavemaker - 30 LinMot pistons on an Allen-Bradley PLC. Use for any work in this repo: running the app or the mock, driving the real array over Ethernet, the piston numbering and front/back convention, the Prepare/boot/home sequence, live stress testing, and the drift behaviour. Load this before touching Model.py, app/, or anything in tools/live_*.py.
---

# Wavemaker

Control software for 30 LinMot pistons in 3 rows x 10 columns, driven by an
Allen-Bradley ControlLogix PLC at **192.168.1.1**. `Model.py` holds the state
and every machine command; `View.py` routes worker-thread callbacks onto the Tk
thread; `app/` holds paths, tags, transport, parameters, patterns and the mock.

## Environment

- Python is on PATH as **`python`** (3.11). **`py` does not exist here** -- the
  README's `py -3` is wrong for this machine.
- Tests: `python -m pytest -q`. Everything runs against `app.plc.SimulatedPlc`;
  `tests/conftest.py` zeroes the machine's wait states so the suite is fast.
- The mock: `python main.py --mock`. Safe anywhere, contacts no hardware.

## Safety rules for the real machine

1. Anything in `tools/live_*.py` **moves real pistons**. So does
   `python main.py` without `--mock`.
2. Never run two live tools at once, and never let a subagent near the PLC.
   Fan agents out over the simulator; drive the real array yourself, serially.
   **The application does not detect that another session already owns the
   controller.** Two instances will happily drive it at the same time, writing
   over each other's parameters, and neither says a word. A 15-minute soak was
   silently ruined this way: another instance homed 12 pistons and started its
   own continuous run half-way through, and the only sign was a recorded stroke
   of 369.7 mm against a commanded 350. Before any live run, check
   `logs/<date>.log` for a second "Connected to PLC" and confirm with the
   operator that nothing else is open. Re-read the held Position 1/Position 2
   afterwards and throw the run away if they changed.
3. Every live tool must drop `Run_1`, `Run_2`, `Run_Curve` and `Home_Button` in
   a `finally`, including on Ctrl-C.
4. Gate before starting: refuse if a run bit is already high (something else is
   driving the machine), and refuse if a selected drive reports a problem.
5. Running a live tool needs a permission rule in `.claude/settings.local.json`
   (`Bash(python tools/live_*.py:*)`). The model cannot add it -- ask the user.

## The sequence that actually works

**Booting the drives clears the homed reference and drops every piston to
zero.** Measured on this array: pistons parked at 369.8 mm, `homed: true`,
`enabled: false`, status `0x830`; after a `Motor_Boot` pulse they read
-0.06 mm, `homed: false`, warn bit 7 set, status `0x40b7`.

So the order is not negotiable, and it is what `Model._prepare_worker` does:

```
select (_mark_live_motors)  ->  clear_faults  ->  boot_motors  ->  home  ->  run
```

- **Mark Live_Motors first.** The ladder boots the *selected* pistons. Boot
  before writing the selection and nothing energises, and the array sits there
  looking broken.
- **Home after booting, never before.** Homing first is wasted: the boot undoes
  it.
- **Never force `_homed_axes` and `MachineState.HOMED`** to skip the minute of
  homing. Several older scripts in `tools/` do this
  (`live_big_wave.py`, `live_stroke_stress.py`); it only worked because they
  inherited an already-energised array. Do it after a boot and the run is
  commanded on unhomed drives, which move nothing at all. Call
  `model.prepare()` and wait for `MachineState.HOMED`.
- `Model._already_homed()` is correct and worth trusting: it requires both that
  *this session* homed the axis and that the drive still says so.

### "The wavemaker is not moving at all"

Check in this order:

1. `python tools/live_survey.py` -- read-only, contacts nothing else.
2. `enabled: false` (status bit 0 clear) means the power stage is off. Prepare.
3. `homed: false` means it needs homing, which Prepare also does.
4. A run bit already high means another session owns the machine.

## Piston numbering -- the thing that bites

Display number is **not** the axis number.

```
display_number(axis) = 30 - 3*(axis // 3) - (axis % 3)
axis_from_display(n) = (9 - (n-1)//3) * 3 + (2 - (n-1)%3)
```

- Piston 1 is axis 29, drawn top-left. Piston 30 is axis 0, bottom-right.
- **The FRONT of the chamber, nearest the operator, is display column 1** --
  pistons 1, 2, 3, i.e. `(display_number(axis) - 1) // ROWS_PER_COLUMN == 0`.
  Settled by commit `c87654a`, which swapped the tank labels "so they match
  where the pistons actually are from the operating position".
- Raw `axis // 3` is the **old, mirrored** convention. Treat every occurrence as
  suspect. `app/patterns.py` and `operate/Operate.py` are the correct reference.
- Not every raw `axis // 3` is a bug: a dict both *built and read* with the same
  expression is only a relabelling. Only conversions read against the drawing,
  or against another module's convention, are wrong.
- Always show the operator `tags.display_number` / `tags.display_list`, never a
  raw axis.

## Making the array move as a pattern

Three modes, on the Wave tab under MOVES, and all three are also reachable from
Operate -> Pattern:

| Mode | What differs | How to run it |
|---|---|---|
| All together | nothing | Start |
| Travelling front to back | each **column** delayed | **Start Curve** |
| Rows out of step | each **row** delayed | Start |

The thing to understand is where the delay lives, because it decides which
button runs it:

- **Curve Offset is per-leg timing, and only the controller's curve feature
  reads it.** During an ordinary continuous run the controller ignores it
  completely, which is why a staggered design used to come out as thirty
  pistons slapping in unison.
- So for continuous motion the intent is converted into **a different starting
  position per piston** -- `waves.cascade_starts`, applied by
  `Model.cascade_targets` and staged by `Model._stage_cascade`. Every piston
  runs the identical stroke at the identical speed; started from different
  points, the phase difference between them is fixed and stays fixed.
- The achievable spread is **one leg of the stroke, not a whole cycle**, because
  every piston sets off towards Position 2 the instant Run_2 goes high. For
  three rows that is plenty: bottom, middle and top.
- A row cascade therefore must **not** set Curve ID -- doing so sends the
  operator to Start Curve for something Start handles.

Curve Offset is an integer in hundredths of a second, so a third of a cycle
rarely divides exactly (a 0.5 s period gives 0, 17, 33 and 17/33 is 0.515). The
staging normalises to the leg anyway, so the ends are exact and the middle row
lands within a couple of millimetres.

## Positions and units

Positions are **written in millimetres** (0..370) but **read back in drive
counts of 0.1 um** -- ten thousand times the millimetre value. Use
`params.to_mm` / `params.to_counts`. Comparing a raw count against a millimetre
threshold makes every healthy piston look thousands of millimetres out.

Travel limits: Position 1/2 are -20..370 mm; the drive's own configured
envelope is -57..453 mm with home at 390 mm. Speeds are 0..900 mm/s.

## Known machine behaviour: the pistons drift apart

Measured with `tools/live_sync_drift.py` on `BIGGER WAVE.csv` (pistons 1-9,
350 mm stroke, 500 mm/s, Curve Offset 0, so they are commanded to move as one),
over a clean 900 s run of 234 cycles with the machine to itself:

- Every piston travels the full stroke correctly, 349.76-349.83 mm of 350.
- They sit at **fixed phase offsets** of up to ~64 ms, about 1.7% of the
  3.834 s cycle. That alone is not drift and does not grow.
- On top of that they **separate slowly**. Worst honest case is piston 2 at
  **435 ms/hour**; most are under 200 ms/hour and two drift backwards. The
  array goes a tenth of a cycle out after roughly **an hour**.
- **Piston 1 is the outlier and the real problem.** Its cycle is 3.8181 s
  against 3.834 s for every other piston -- 16 ms faster, every cycle -- so it
  laps the array about once every four minutes. In a 900 s run it had gone more
  than a full cycle, which is why it cannot be given a drift figure at all.

Three traps when measuring this, all of which caught an attempt here:

- **Position spread is not drift.** It oscillates through every cycle by
  construction: pistons a hair out of phase are furthest apart at mid-stroke,
  where they move fastest, and together at both ends. Spread ranged 0.1 to
  74.2 mm within one steady run.
- **Cycle time inferred from reversals is noisy.** 45 s put the spread across
  the array at 15 ms; 240 s put it at 2.6 ms. A prediction built on the short
  run was wrong by a factor of six.
- **Phase lag paired by reversal index silently breaks** once two pistons are
  more than half a cycle apart -- the k-th reversal of one stops being the
  partner of the k-th of the other. `analyse_sync_drift.py` now prints LAPPED
  instead of a number when that happens.

Measure phase lag against a reference piston, early versus late, with
`tools/analyse_sync_drift.py` -- it re-analyses a saved run, so it costs no
machine time.

This is inherent to free-running independent axes, not a software fault, but it
is the answer to "why does the wave fall apart on a long run". Any fix has to
re-phase the axes periodically rather than trust them to stay together, and
piston 1 wants looking at mechanically before anything else.

## Tools

| Tool | What it does |
|---|---|
| `tools/live_survey.py` | Read-only. Status, warn, position, homed for pistons 1-12. Safe. |
| `tools/live_sync_drift.py` | Runs a big-wave preset and measures cycle time and spread over the run. `--dry-run` contacts nothing. |
| `tools/live_motion_campaign.py` | Staged home -> stroke -> speed -> wave -> stop-abuse, aborting on any new drive fault. |
| `tools/make_presets.py` | Regenerates the shipped `Presets/*.csv` from `app/patterns.py`. |

Prefer `--dry-run` first on anything new. Write evidence to
`logs/verification/` as JSON; print a summary, not the raw samples.

## Screenshotting the GUI

Tk windows will not be found by `FindWindow` reliably here; enumerate by PID
instead. Launching the app and capturing must happen in **one** shell call --
the tool kills child processes when the call returns. There is a working
capture script pattern in the session scratchpad approach: `Start-Process
-PassThru`, sleep, `EnumWindows` filtered by that PID, `CopyFromScreen`.

To drive the UI without a mouse, import `View`, schedule the action with
`root.after(...)`, and monkeypatch `messagebox.askyesno` to return True so the
"home the pistons first?" confirmation does not block.

## House style

Comments explain **why**, in plain prose, often naming the fault the code
exists to prevent. Match it -- it is the most valuable thing in this codebase.
Keep operator-facing strings plain and specific; name the piston by its display
number and say what to do next.
