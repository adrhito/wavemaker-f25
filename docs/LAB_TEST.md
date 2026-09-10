# Testing this on the lab PC

The lab PC is Windows 7 with no internet. **Nothing needs to be installed** —
the application uses only the Python standard library plus the copy of pylogix
already inside `modules/`.

## Getting the files there

1. Copy the `WaveMaker_F25` folder onto a USB stick.
2. On the lab PC, paste it onto the Desktop **as a new folder named
   `WaveMaker_F25_new`**.

   Do not overwrite the existing `WaveMaker_F25`. Leaving it untouched means
   falling back is instant: close the new one, open the old shortcut.

3. The folder must sit next to `WaveMaker Programs`, i.e. both on the Desktop.
   That is how the *Open Studio 5000* button finds the `.ACD` project.
4. Double-click `Open Wavemaker.cmd` inside the new folder.

Nothing else. No `pip install`, no database script, no Studio 5000.

## Python version

Windows 7's last supported Python is **3.8**, and this repository's history
suggests the lab PC has **3.7**. The application is written to run on 3.7 and
later, and `docs/check_python.py` will confirm it before you try:

```
py -3 docs\check_python.py
```

or, if `py` is not on PATH:

```
C:\Python37\python.exe docs\check_python.py
```

It prints the Python version, whether tkinter is present, and whether every
module of the application imports. If that passes, the application will start.

## Test order

Work down this list. Stop at the first thing that misbehaves and keep the log.

| # | Step | What should happen |
|---|---|---|
| 1 | Controller in Rem Run, **Studio 5000 closed**. Double-click the launcher. | Window in a second or two. Banner reads `Connected to PLC at 192.168.1.1`. Feedback tab names the controller. |
| 2 | Define Motors: tick 2–3 pistons, *Create Set from Selection*. Conservative parameters. | Set appears in the Editing list and coloured on Control Home. |
| 3 | Control Home: *Prepare Motor(s)*. | Status walks through selecting, clearing faults, booting, writing, then homing twice. Ends `Motors homed and ready to run.` |
| 4 | *One stroke*, then *Start Motor(s)*. | One stroke. Returns to ready. |
| 5 | *Continuous*, *Start*, then *Stop*. | Starts, and stops on command. |
| 6 | ***Off and Reset*, then Prepare and Start again.** | **The most important test — see below.** |
| 7 | Second set with different parameters. Prepare, run. | Each set moves with its own parameters. |
| 8 | Preset Options: load a preset, apply to a set, Prepare, run. | Values from the preset. |

### Step 6 is the one to watch

The old code's `motor_off` wrote `Run_2 = 1` then `Run_2 = 0`, with a comment
saying it was there to *"reset the run Rung"*. That pulse has been removed,
because it meant pressing Stop commanded a moment of motion first.

If the ladder resets that rung on a **falling edge** of `Run_2`, writing `0`
when it is already `0` produces no edge and the rung will not reset.

**Symptom if so:** after *Off and Reset*, the machine prepares and homes
normally but will not start — nothing moves. It is a failure to move, not
unexpected movement.

If that happens, say so and the edge can be reinstated without commanding
motion (pulsing `Run_1`, or a dedicated reset bit if the ladder has one).

## If something looks wrong

**Reads returning the wrong values, writes landing on the wrong parameter.**
The application now holds one PLC connection open instead of opening a new one
per read and write. To rule that out, run it the old way:

```
Open Wavemaker.cmd --fresh-connection
```

or from a command prompt in the folder:

```
py -3 main.py --fresh-connection
```

That restores exactly the original one-session-per-operation behaviour.

**A parameter change that does not take effect.** The application now sends only
*changed* parameters rather than all eighteen. If the ladder needs the whole
block, this is where it would show. Report it — the fix is one line.

**It will not connect.** Press *Reconnect*. If that fails, *Open Studio 5000*,
go online, confirm the controller is in Rem Run, then *Reconnect*. You can
close Studio 5000 again afterwards.

**It will not start at all.** Run from a command prompt so you can see the
error:

```
cd /d "%USERPROFILE%\Desktop\WaveMaker_F25_new"
py -3 main.py
```

## What to bring back

- `logs\<date>.log` from the new folder — it records everything from INFO up,
  including the connection attempt and every command.
- `analytics\<date>.txt` if analytics were recorded.
- Which step above you reached, and what the screen said.

## Falling back

Close the new application and open the old shortcut. The old folder was never
touched. Nothing this version does changes the PLC program or the drive
configuration — only tag values, exactly as the old version did.
