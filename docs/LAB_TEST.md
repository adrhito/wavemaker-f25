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

## Rehearse it on the mock first

Before you go near the tank, run **`Mock Wavemaker (no machine).cmd`** on any
computer. It is the same application with a simulated wavemaker behind it, so
every step below behaves the same way except that nothing physical moves. Doing
the whole sequence once on the mock means the only unknown at the machine is the
machine.

## Test order

Work down this list. Stop at the first thing that misbehaves and keep the log.

| # | Step | What should happen |
|---|---|---|
| 1 | Controller in Rem Run, **Studio 5000 closed**. Double-click `Open Wavemaker.cmd`. | Window in a second or two. Banner reads `Connected  ·  192.168.1.1`. The Feedback tab names the controller. |
| 2 | Click 2–3 pistons on the tank. | They highlight, and the count under the tank updates. No "create" step. |
| 3 | Set a conservative stroke and speed. | The stroke bars above those pistons change to match. |
| 4 | **One stroke**, then **Start**. | Asks "Home the pistons first?" — say yes. Homing runs twice, then one stroke. |
| 5 | **Start** again. | Runs immediately, no homing prompt. |
| 6 | **Continuous**, **Start**, watch the live view. | Pistons animate with their real positions. Then press **Stop**. |
| 7 | **After the stop, watch where the pistons end up.** | **See below — this is the one to watch.** |
| 8 | **Start** again after the stop. | Runs without re-homing; parameters are re-sent first. |
| 9 | Add a second group with different parameters. Start. | Each group moves with its own values. |
| 10 | Pattern → Curve Offset, Stagger, Front to back, step 20. Start continuous. | The wave should reach the back of the chamber after the front. |
| 11 | Preset Options: load a preset, apply to a group, Start. | Values from the preset. |

### Step 7 is the new behaviour

**Stop now returns the pistons to the bottom of their stroke (368 mm).** This is
new — the machine has never done a commanded move after a stop before.

What happens: all three run bits are dropped immediately, then the pistons are
sent to 368 mm at 200 mm/s. Move Type is forced to absolute first, so 368 cannot
be taken as a relative move. Pressing Stop again during the travel cancels it.

**Do this first with two or three pistons, not thirty.** Watch that:

- the halt is immediate — the pistons stop before they start travelling down;
- they travel to the bottom smoothly, not abruptly;
- 368 mm is actually where you want them to rest.

If the resting position should be different, it is one constant —
`PARK_POSITION` at the top of `Model.py`. To switch the behaviour off entirely,
set `PARK_ON_STOP = False` in the same block.

### The old step 6 still applies

`Off and reset` no longer pulses `Run_2` high, which the original code did with
the comment "reset the run Rung". If the ladder needs a falling edge there, the
symptom is that the machine will prepare and home but not start after a reset.
It is a failure to move, not unexpected movement.

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
