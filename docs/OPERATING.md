# Operating the wavemaker

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
