# Homing speed investigation

Investigation only. No product code was changed. Evidence sources: `Model.py`
(the constants and `_home_motors` / `_prepare_worker` / `_calibrate_worker` /
`_already_homed`), `Motor.py`, `app/drive_status.py`, `app/params.py`,
`app/plc.py`, `app/simulator.py`, `docs/OPERATING.md`, the wavemaker skill
notes, and the lab's real log
`C:\Users\ahito\Desktop\WaveMaker_F25_new\logs\2026-09-15.log`. A short
read-only script was run against `app.plc.SimulatedPlc` (no hardware touched)
to confirm two behavioural claims in section 4/5 that aren't visible from
static reading alone; it is quoted inline below rather than committed.

## 1. Phase breakdown from the real log

Five real (non-mock) Prepare cycles are logged start-to-finish on 2026-09-15.
Each phase's boundary is a log line; `clear_faults()` and `boot_motors()` each
log only once they finish, i.e. *after* their fixed sleep, so the gap between
consecutive lines is the phase duration:

| Time | Clear faults | Boot | Write params | Home | Motors | Total (excl. clear-faults lead-in) |
|---|---|---|---|---|---|---|
| 02:47:03->28 | 5.0s (fixed) | 5s | 0-1s (9 motors) | 20s | 9 | 30s |
| 03:12:26->53 | 5.0s (fixed) | 5s | 1s (15 motors) | 21s | 15 | 31s |
| 07:34:39->35:06 | 5.0s (fixed) | 5s | 1s (15 motors) | 21s | 15 | 31s |
| 07:37:44->38:15 | 5.0s (fixed) | 5s | 0-1s (12 motors) | 26s | 12 | 32s |
| 07:39:54->40:25 | 5.0s (fixed) | 5s | 1s (12 motors) | 25s | 12 | 31s |

(`CLEAR_FAULT_SECONDS` and `BOOT_PULSE_SECONDS` are both hard-coded to exactly
5.0s in `Model.py`, so their duration doesn't need inferring from timestamps —
the log's own "Motion faults cleared" -> "Motors booted" gaps confirm this
exactly, 5s every time.)

**Homing dominates outright.** It is 20-26 of the ~30-32 second total (63-84%),
more than clear-faults + boot + write-parameters combined (11-12s) in every
attempt. Variance across the 5 attempts is small in absolute terms (20 to 26s,
a 6-second spread) but that spread is entirely inside the homing phase — every
other phase is either fixed (5s, 5s) or negligible (<=1s). This matches the
operator's "30-45 seconds" estimate at the low-to-middle end; the top end is
plausibly explained by homing needing an extra poll cycle (see section 3) or a
retry after a piston briefly looked stuck.

One log entry shows the `_already_homed()` fast path: at 07:39:06,
"Parameters written to 12 motors." and "Skipped homing: these pistons are
already homed." land in the same second — homing contributes 0s when it
applies (see section 5).

No calibration or homing *failure* appears anywhere in this log (see section 6).

## 2. Could clear-faults / boot proceed on a readable signal instead of a fixed wait?

Plausibly yes, but **this repository does not contain the evidence to prove
it** — only a starting point.

- `app/drive_status.py` documents the status word bits from the LinMot manual.
  Bit 0 is "Operation Enabled" and bit 3 is "Error" (`ERROR_BIT`). In
  principle, `boot_motors()` could poll status bit 0 until it goes high instead
  of always sleeping 5.0s, and `clear_faults()` could poll bit 3 (or the
  specific warn bits it's meant to clear) until they drop, instead of always
  sleeping 5.0s.
- The wavemaker skill file records one *post*-boot snapshot measured on the
  real array: after a Motor_Boot pulse, status reads `0x40b7` (bit 0 set —
  Operation Enabled already true) with bit 11 (Homed) clear. That's evidence
  the drive **can** be enabled without being homed, which is expected, but it
  says nothing about *when during the 5-second pulse* bit 0 actually went
  high — it's a single sample taken after the pulse completed, not a
  time series through it.
- Nothing in the repo (log, tests, or code) contains a sub-5-second, timestamped
  read of the status word taken *during* a boot or clear-faults pulse. Without
  that, there's no way to know whether the real transition happens in 200ms or
  4.9s, or whether it's even monotonic (a drive could report "enabled" for a
  moment then drop it while faults are still clearing).
- **Verdict: needs a live test.** The concrete test: during `boot_motors()`,
  poll `Motor(axis).read_status(plc).state` (or the raw status word) at e.g.
  10 Hz for the full 5 seconds on a few axes, and separately for
  `clear_faults()` watch bit 3 / the warn word. Whatever this measures becomes
  the new poll condition, with the *current* 5.0s kept as a hard ceiling/
  timeout so a drive that never raises the bit doesn't hang the sequence
  forever. This cannot be done safely from a description of the mechanism
  alone; it needs the array.

## 3. How much of homing is genuine travel vs. polling/fixed-wait overhead?

Structurally, `_home_motors` runs two passes —
`(HOME_SETTLE_POLLS=2, final=False)` then `(HOME_POLLS=7, final=True)` — and
**each pass begins with a fixed `HOME_POLL_SECONDS` (5.0s) sleep with
Home_Button held low, before Home_Button is even raised**:

```
self.plc.write(tags.HOME_BUTTON, 0)
self._sleep(HOME_POLL_SECONDS)          # <- 5s dead wait, button not yet high
...
self._begin_motion(tags.HOME_BUTTON)    # NOW it goes high
for poll in range(1, polls + 1):
    self._sleep(HOME_POLL_SECONDS)      # <- poll granularity, 5s steps
    if all(m.is_homed(...) ...): break
```

So the floor for a fully successful two-pass home, even if every drive is
already sitting at its home position and reports homed instantly once the
button goes high, is:

`2 passes x (5s dead wait + 1 poll of 5s)` = **20 seconds**, and that is
*exactly* what the fastest real attempt measured (02:47:08 -> 02:47:28). The
25-26s attempts are consistent with one of the two passes needing a second
5-second poll rather than genuine extra travel time — there is no log evidence
of a piston travelling for 20+ continuous seconds; `app/simulator.py`'s own
`_home()` models the actual physical move as 2.0 seconds
(`HOME_SECONDS = 2.0`, travelling at a fixed 200 mm/s), and the travel limits
put home at 390mm with a normal working range of -20..370mm, so worst case
physical travel is well under 5 seconds even at that deliberately gentle
mock rate.

**Breakdown for the fastest (20s) real attempt:**
- Fixed "let the button sit low" dead time: 2 x 5s = **10s that does no
  polling and cannot detect an early finish, by construction.**
- Poll granularity: 2 x 5s = **10s**, but the underlying motion likely
  finishes well inside that window — the polling interval, not the drive, sets
  the 5s "chunks."
- Genuine unavoidable drive travel: bounded above by the simulator's stand-in
  of ~2s, almost certainly a few seconds in reality (a full home-to-limit
  travel plus settle) — **but this repo has no per-axis position trace during a
  real homing pass to say precisely how many seconds are travel vs. drive
  settling vs. margin.** That's the one number that needs a live measurement:
  read `Motor.read_position` at e.g. 4 Hz through one real homing pass and see
  when the position (and the homed bit) actually settle.

**What can be said without the machine:** the *majority* of the homing phase
as currently coded is fixed dead-time and polling granularity, not travel —
of the 20s floor, at most a few seconds is credibly travel; the rest (roughly
15-18s) is `_sleep()` calls that don't check anything or that check on a coarse
5-second cadence. Shrinking `HOME_POLL_SECONDS` (e.g. to 1s) is a pure
polling-frequency change, testable and provable in the simulator (it already
is — `tests/conftest.py` zeroes it for exactly this reason) and low risk on the
real machine, since it only affects *how often* the code asks a question it
already asks, not what it asks or what it commands.

**What cannot be cut without risk:** the two-pass structure itself.
`docs/OPERATING.md` records that it exists because "the piston[s] occasionally
home on a certain high position" and the root cause was *never established*.
Removing the settling pass would save ~10-15s but risks reintroducing whatever
bug that pass exists to paper over, and there is no way to confirm from the
repository whether that bug still applies to the current drives/firmware —
only a live test (home repeatedly with the settling pass disabled and watch
for pistons homing at the wrong position) could tell you that.

## 4. "Parameters written to N motors" — round trips, serialisation, and whether the skip actually works

- Each `Motor.write_to()` call writes one PLC tag per parameter via
  `Transport.write()`, and `Transport.write()` (`app/plc.py`) is a single
  blocking call per tag (`plc.Read`/`plc.Write` via pylogix) — there is no
  batched multi-tag CIP message anywhere in this path. `Transport.write_many()`
  exists but is **not real batching**: it just loops calling `self.write()` one
  tag at a time under one lock (`app/plc.py` lines ~229-233). It saves nothing
  in round trips; it only holds the lock a bit longer. `Motor.write_to()`
  doesn't call it anyway — it writes tags directly in a loop.
- `app/params.py` defines 18 parameters per motor (`WRITE_ORDER`), each its own
  tag, so a full, unconditional write of one motor is 18 round trips, and
  `_write_all_parameters()` writes motors one at a time, in order — genuinely
  serialised, no overlap. Writing 30 motors' worth of untouched parameters
  fresh would be 540 individual writes.
- **The observed log timings say this is not actually happening in practice.**
  Writing 9, 12 or 15 motors' parameters over the real Ethernet link always
  completed in 0-1 second in this log — far too fast for hundreds of
  individually round-tripped CIP writes at any realistic per-write latency.
  That is strong indirect evidence that `Motor.write_to()`'s
  skip-unchanged-parameters logic (`pending_changes()` against
  `current_params`) *is* actually suppressing almost all the writes in these
  particular runs, i.e. the operator was mostly re-running the same preset with
  the same values.
- **Does `_stage_cascade` clearing `current_params` break that?** Verified
  directly against `SimulatedPlc` (script summarised below, not committed):
  - A first `prepare()` writes every parameter for every selected motor (124
    writes total across selection/param/home/boot tags for 3 pistons).
  - A second, unchanged `prepare()` right after writes **zero** motor-parameter
    tags — confirming the skip does work in the ordinary case.
  - Manually reproducing what `_stage_cascade`'s cleanup does — `current_params
    = {}` + `write_success = False` for every motor in the selection, then
    `motor.write_to(plc)`, exactly as its `finally` block does — forces a full
    18-params-per-motor rewrite (54 writes for 3 motors), as expected.
  - Critically, that cleanup step **re-populates `current_params` as it
    writes**, so the very next `prepare()` afterwards again writes **zero**
    motor-parameter tags. The skip is not permanently defeated by
    `_stage_cascade` — it recovers itself immediately.
  - So the real cost of `_stage_cascade`'s wipe is not to `Prepare`'s timing at
    all (it happens during a *run's* teardown, not during Prepare); it's that
    every row/travelling-wave (cascade) continuous run forces one full,
    unconditional 18-param rewrite of **every motor in `self.all_motors`**
    (the whole active selection, whether or not staging actually succeeded —
    the "Staging did not complete... running unstaggered" cases in the log
    still hit this cleanup) as that run finishes. It doesn't inflate the
    homing-phase numbers in section 1, but it is real, avoidable PLC traffic on
    every cascade run's stop, and it means the *first* Prepare after a cascade
    run always looks (from the log's own "Parameters written" line, which
    doesn't distinguish 0 writes from 500) the same regardless of whether
    anything actually needed rewriting.
- **Batching verdict:** genuine multi-tag batching would need the transport
  layer itself to build one CIP message for several tags (pylogix supports
  this in principle), which this codebase does not currently do anywhere.
  Whether that's worth doing depends on the real per-write latency, which
  isn't measured directly in this repo (only inferred, generously, from the
  aggregate 0-1s figures above) — a live test timing `Transport.write()` calls
  individually would confirm the real per-tag cost and thus whether true
  batching is worth building.

## 5. Homing fewer pistons / not re-homing already-homed ones

`Model._already_homed()`:

```python
live = self.live_axes
if not all(axis in self._homed_axes for axis in live):
    return False
return all(motor.is_homed(self.plc) for motor in self.all_motors)
```

(`self.all_motors` / `self.live_axes` are both derived from `self.sets`, i.e.
the *current selection* — not literally all 30 pistons.)

- **When it saves the full ~20-26s:** shrinking or repeating an identical
  selection that this session already homed, and that still reports homed on
  the drives. Confirmed live in the log at 07:39:06 ("Skipped homing: these
  pistons are already homed") — homing contributed 0 seconds that time.
  Confirmed again in the simulator script: a second identical `prepare()`
  skips `clear_faults`/`boot_motors`/`_home_motors` entirely.
- **When it does *not* save time, and why that's not simply a bug:** growing
  the selection to include even one axis this session hasn't homed forces a
  full re-home of the *entire new selection*, including pistons that were just
  homed a moment ago. Verified directly: adding one never-homed axis to an
  already-homed 3-piston selection flips `_already_homed()` to `False`, and the
  subsequent `prepare()` writes `Motor_Boot` and pulses `Home_Button` again for
  the whole set, not just the new axis.
  This is not a naive missed optimisation. `MOTOR_BOOT` and `HOME_BUTTON`
  (`app/tags.py`) are single, global tags — not per-axis — and per the
  wavemaker skill notes, a `Motor_Boot` pulse "clears the homed reference and
  drops every piston to zero" for every axis currently marked live
  (`Live_Motors`). Since `_prepare_worker` must mark the *whole new selection*
  live before it can boot the newly-added piston, that same boot pulse would
  also de-home the pistons that were already fine. So there is currently no
  safe way, given this ladder design, to home only the delta without first
  re-booting (and therefore re-homing) everyone else in the selection too — a
  smarter "home only the new axis" path would need to boot only the new axis
  on its own (marking it live in isolation before the rest), which is a real
  design change, not implemented today, and would need to be proven safe on
  the real ladder before trusting it (does booting one axis in isolation
  really leave neighbours' homed state untouched? plausible from the tag
  design, not proven here).
- **Net effect:** the existing all-or-nothing shortcut is worth keeping and
  already captures the main win (repeat/no-op preps cost ~0s instead of ~30s).
  The remaining gap — adding one piston to a set forces re-homing the rest —
  is real but expensive and risky to fix without hardware access to confirm
  that per-axis booting is actually safe.

## 6. "It barely even calibrates"

The log contains **no calibration activity at all** — `grep -i "calibrat"`
over the whole file returns nothing. `Model.calibrate_all()` /
`_calibrate_worker()` (which would log "Calibrated all 30 pistons." or "N of 30
pistons calibrated...") was never invoked in this session. Every homing seen
in the log went through `Prepare` on a partial selection (9, 12 or 15
pistons), not the dedicated "Calibrate all 30" path.

There is also **no homing failure** anywhere in this log — every "Motors
homed" line is a `SUCCESS`, and `_report_homing_failure`'s messages ("X of Y
pistons homed... did not home... Homing timed out") never appear.

What *does* appear repeatedly, and is plausibly the source of "it barely even
calibrates":

- Twice, right after Continuous motion started, **every piston in the current
  selection** (not specifically 10/11/12) logged "covered only 0 mm of its
  stroke over the last 3 seconds. It may be dragging or stuck" — at 07:35:21
  (pistons 1-15) and 07:38:30 (pistons 1-12). Both times this followed
  "Staging did not complete within 12.0 seconds; running unstaggered" a few
  seconds earlier, and both times *every single piston in the set* triggered
  the warning simultaneously, not a specific subset. That pattern — the whole
  selection, at once, immediately after Run starts — looks like the drag/stuck
  monitor sampling its first 3-second window right as motion begins (before
  any piston has had time to move far), rather than 10-12 genuinely stuck
  pistons; the same run later reports "Motors stopped" (by the operator,
  presumably reacting to the warning) rather than any drive fault.
  **This log alone does not establish pistons 10/11/12 specifically as worse
  than their neighbours** — whenever they appear in a warning, so does
  everyone else selected that run. Confirming (or ruling out) 10/11/12 as
  genuinely different needs either a longer-running log with more samples, or
  a live, read-only survey (`tools/live_survey.py`) comparing their status/warn
  words against the rest of the array.
- Given there is no evidence in this log of `calibrate_all()` ever being run
  or failing, "it barely even calibrates" cannot be substantiated or refuted
  from this log; it needs either more/older logs that actually invoke
  Calibrate, or a live test of `calibrate_all()` itself with `check_drives()`
  run first (which the code already does, logging any drive that "reports a
  fault" *before* homing starts) to see whether 10/11/12 report a problem up
  front.

## Summary of proposed speedups

| # | Change | Expected saving | Risk | Provable without the machine? |
|---|---|---|---|---|
| A | Shrink `HOME_POLL_SECONDS` polling granularity (e.g. 5.0s -> 1.0s) | Up to ~8s per pass in the worst case that needs 2 polls; ~a few seconds typical, since it only trims how late the code notices "already homed" | Low — same commands, same order, just asked about more often; `keepalive()` inside the loop already assumes frequent polling is fine | Yes, in the simulator (tests already zero this constant); real risk is only "does polling faster stress the PLC/network," which the real array should still confirm once, briefly |
| B | Replace the two 5s "let Home_Button sit low" dead waits with something shorter | ~8s (leaves a small margin instead of a full poll interval) | Low-medium — unclear why the wait is `HOME_POLL_SECONDS` specifically rather than a separate, smaller constant; no comment explains it | No firm proof in repo that it can go to zero; a live test lowering it and watching for mis-homes would confirm |
| C | Drop the settling (first) homing pass entirely | ~10-15s | High — `docs/OPERATING.md` says this pass exists because pistons were observed to "home at a wrong position" for a reason "never established" | No — this needs a live test explicitly designed to catch a wrong-position home, run enough times to trust a negative result |
| D | Poll a status bit (e.g. status bit 0, Operation Enabled) instead of always sleeping the full `BOOT_PULSE_SECONDS`/`CLEAR_FAULT_SECONDS` | Up to ~4s each, ~8s total, if the real transition is fast | Medium — needs to keep the current 5s as a timeout fallback; behaviour during the transition is unverified | No — nothing in the repo times the transition inside the pulse; needs a live, timestamped read of the status word during a real pulse |
| E | Trust `_already_homed()` more aggressively (already implemented) | Already captures ~30s of savings on a no-op re-Prepare; no further code change needed here | None — already shipped, confirmed in log and in the simulator | Already proven, in this repo, right now |
| F | Home only newly-added axes when growing a selection | Would save re-homing the previously-good pistons | High — `Motor_Boot`/`Home_Button` are single global tags; booting the new axis without disturbing already-live ones is a real ladder-level question, not something the Python code alone can answer safely | No — needs a live test of whether marking only one new axis live and pulsing boot leaves neighbours' homed state intact |
| G | Real multi-tag batching for `_write_all_parameters` | Unclear — the log suggests parameter writing is *already* fast (0-1s) once the unchanged-parameter skip is doing its job; batching would mainly help the first-ever write of a fresh selection (up to 540 writes for 30 motors) | Low-medium — would need a new Transport method that actually builds one CIP message instead of looping | Partially — the *skip* behaviour is already proven in the simulator; whether batching is worth building depends on real per-tag write latency, which needs a live timing test |

**Honest gaps:** the repository does not contain a timestamped, sub-5-second
trace of the status word during a real boot or clear-faults pulse, nor a
per-axis position trace through a real homing pass, nor any log of
`calibrate_all()` ever running (successfully or not). All three would need a
short, read-only or homing-only session at the machine to fill in — none of
that was done here, per the instruction not to contact the PLC.
