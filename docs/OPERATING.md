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

### Soliton trials

The **Soliton** tab is for a single experimental pulse of the vertically
moving floor sections. Its requested **crest rise** is above the still-water
line. **Width** is the desired longitudinal full width of that crest at half
its height. Enter the still-water depth for this run. These are water-wave
targets, not measured outcomes or guaranteed results.

**Floor lift** is a separate motor command in millimetres. There is no measured
conversion from floor travel to water height yet, so the software does not
calculate floor lift from crest rise. The preview shows the requested water
shape. At fixed depth, a true first-order solitary wave has a specific width
for each height; the tab shows the theoretical width when the chosen values
disagree. The theory is from [this numerical and experimental solitary-wave
study](https://www.mdpi.com/2077-1312/11/1/35), but its horizontal-paddle
motion equation is **not** used for this vertical-floor machine.

1. Select the floor sections on **Operate**. Check that the tank is clear, the
   water depth is measured, and no other controller session is running.
2. Set crest target, width target, depth, and an initial floor lift. The trial
   restricts lift to 120 mm and command speed to 200 mm/s. It uses 4,000
   mm/s² acceleration/deceleration and an S-curve profile, all within the
   shipped gentle preset. These are conservative starting bounds; the lab has
   not established the fastest safe stop for the assembly.
3. Press **Stage floor**. This prepares/homes if needed and lowers the selected
   sections to 370 mm. Let the water become still before the next step.
4. Press **Fire one pulse**. The controller makes one absolute upward move and
   decelerates into its endpoint. The software waits for position arrival
   before clearing the command. The floor stays raised; there is no automatic
   return pulse. To lower it for another trial, press **Stage floor** again.
5. Measure the resulting wave at a marked station and press **Record observed
   wave**. Enter crest rise and/or crest-to-trough height, the observed
   longitudinal half-height width if measured, station distance, and a photo
   or note reference. Records are saved locally in
   `analytics/soliton-trials/` as JSON files with target, command, pulse
   outcome, depth, final motor positions, and observation in separate fields.
   A trial file is created before motion, so a stopped or faulted pulse is
   retained too. Transfer those files from the offline lab computer with the
   code and any photos when analyzing calibration.

During a soliton pulse, **Stop** and Escape clear the run command immediately
and do not automatically lower the floor. A commanded pulse is an experimental
forcing, not proof that a solitary wave was formed. Increase aggressiveness
only after physical inspection and measured trials establish safe limits and a
height/width calibration.

Follow [the offline lab validation procedure](SOLITON_LAB_VALIDATION.md) for
the first water trials and the measurements to bring back for calibration.

## Stopping

**Stop** is in the status bar at the bottom of every tab. On an ordinary run,
the first press can wait briefly for the current stroke to finish; a second
press halts immediately. `Escape` halts immediately. During a soliton pulse,
either Stop or Escape halts immediately.

For ordinary runs, the selected resting position controls what happens after
the run bits drop. The bottom resting position is 370 mm. A soliton trial is
different: Stop clears its run command promptly and leaves the floor where it
stopped. The floor also stays raised after a completed pulse until the operator
stages another trial.

After an ordinary parked stop, the machine stays homed and the next run sends
its own parameters again. Inspect floor position before changing modes after a
soliton trial.

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
| Position 1, Position 2 | -20 to 370 mm | -20 is the top of the stroke, 370 the bottom. After homing. |
| Speed 1, Speed 2 | 0 to 900 mm/s | The top speed actually reached depends on available current. |
| Accel 1/2, Decel 1/2 | 0 to 20,000 mm/s² | |
| Jerk 1, Jerk 2 | 0 or more | Normally larger than the acceleration and deceleration. |
| Time 1, Time 2 | 0 or more | Dwell at the end of the stroke. Leave at 0 unless you want a pause. |
| Profile | 0 to 3 | Trapezoidal (0), Bestehorn (1), S-Curve (2), Sine (3). |
| Move Type | 0 or 1 | Absolute (0) or Incremental (1). |
| Curve ID, Time Scale, Amplitude Scale, Curve Offset | 0 or more | Used by Start Curve only. |

A value outside these ranges is refused when you type it, with a message under
the boxes saying why. Nothing is sent to the machine until it is valid.

### Notes on limits

**Position limit:** The application accepts positions from -20 through 370 mm.
The drive's own configured envelope (`LinMot Drive Config`) is -57 through
453 mm, with home at 390 mm. The application limit is narrower than the drive
envelope; do not infer clearance for the assembled floor from either value.

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
