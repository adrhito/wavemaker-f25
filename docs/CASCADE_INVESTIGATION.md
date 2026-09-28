# "Rows out of step" — investigation and fixes, 21 September 2026

Live testing against the real array (PLC 192.168.1.1, 1756-L82E/B rev 31.11).
Evidence JSON is in `logs/verification/`.

## The headline

**The cascade software works.** It was verified on the real machine at 9 and at
29 pistons, and at full speed. What broke it was **one mechanically stuck
piston**, plus several software faults that made a *working* cascade look
broken.

## Live runs

| # | pistons | stroke / speed | result | evidence |
|---|---|---|---|---|
| 1 | 1–9 | 180 mm @ 250 | staged 0/90/180, full 179.6 mm travel | `cascade-debug-20260921-153422.json` |
| 2 | 1–9 | 300 mm @ 300 | rows 251 mm apart | `row-cascade-20260921-153646.json` |
| 3 | **1–30** | 300 mm @ 300 | **aborted — piston 26 would not home** | `row-cascade-20260921-153813.json` |
| 4 | 1–25, 27–30 | 300 mm @ 300 | all 10 columns, rows ~250 mm apart | `row-cascade-20260921-153952.json` |
| 5 | 1–9 | 300 mm @ **900** (max) | rows ~246 mm apart | `row-cascade-20260921-154207.json` |

Run 4 matters: the boot+home **cleared** pistons 16–18 out of `Switch On Locked`
and homed 19–30. Piston 26 is the only genuine hardware casualty.

## Root cause

**Piston 26 (axis 4) is mechanically stuck** — "has not moved since homing
began". Homing timed out at 29 of 30, `prepare()` ended in `READY` not `HOMED`,
and the entire run was abandoned before it started. From the operating position
that is indistinguishable from "the wavemaker does not work".

## Hypotheses tested and KILLED

Recorded so nobody spends time on them again:

- **Mixed position scaling across axes.** Wrong. Every read goes through one
  `params.to_mm` (÷10000). `-27` counts is −0.0027 mm, not −27 mm. Axes reading
  near zero were genuinely parked at 0 mm after a boot.
- **Parity-pulse overshoot.** `PARITY_PULSE_SECONDS` is a fixed 0.5 s, and by
  computation any wave whose leg takes ≤0.5 s turns round mid-pulse. Predicted
  to scatter the staging. **Tested at 900 mm/s (0.33 s leg) — did not
  reproduce.** The final `settle()` evidently recovers whatever the pulse
  scrambles. Real observation, no observable consequence.
- **Drives go deaf to Run_1 after a fresh boot+home.** The top candidate from
  the log forensics (`logs/2026-09-15.log`, 19:34). Did **not** reproduce: runs
  1, 2, 4 and 5 all did a full `prepare()` and worked. Still unexplained for
  that logged occasion; may be intermittent.

## Fixes applied

1. **Auto-drop pistons that will not home** — `Model._prepare_worker`. A stuck
   piston is dropped and the rest carry on; `drop_unhomed()` already existed but
   was only reachable through a dialog the operator had to accept. New
   `Model.dropped_axes` keeps them for the display.
2. **Staging failure no longer lies** — `Model._stage_cascade`. It used to log
   "running unstaggered", return `True`, and run anyway on drives that had just
   spent 12 s proving they would not move. It now names the offending pistons
   via the new `Model._outside()` and refuses.
3. **The Operate preview can draw a row cascade** — `operate/Operate.py` and
   `modules/wave_preview.py`. Both `offsets` and `start_fractions` were keyed by
   *column*, so the three rows of a column overwrote one another, every column
   ended at fraction 0.0, and a working row cascade drew the identical picture
   to "All together". Now keyed by piston, with `row_fractions` passed through
   and the rows **averaged** in `_elevation` — which is physically right (all
   three push the same water) and makes the reduced amplitude of a row cascade
   visible.

## Still to do

4. **Sticky Continuous/Curve picker** — `Operate.py:427` builds the run-mode
   `Segmented` with `value=RunMode.CONTINUOUS` and nothing ever resets it
   (read at `Operate.py:798`). Send a cascade while it is still on Curve and
   Start raises `Run_Curve` with `Curve ID = 0`: no staging, run dies after 5 s.
   Fix: have `WaveDesigner.send()` put it back to Continuous — every design that
   tab produces now runs from plain Start.
5. **`cascade_fractions` silent no-op** — `app/waves.py:331` returns `{}` on zero
   spread, `_stage_cascade` returns True immediately with no log. Hit by *one
   column selected + "Columns"* and *one row selected + "Rows"*, while the Wave
   tab still claims it staggered them. Fix in `WaveDesigner.send()`, which knows
   the intent.
6. **`settle()`'s first return is ignored** — `Model.py:1839` handles only
   `None`; a `False` (move to floor timed out) is discarded silently.
7. **`_check_position_scale` false alarm** — `Model.py:2645` via
   `params.looks_like_millimetres` (`app/params.py:65`) treats anything in
   −1000…1000 *counts* as millimetres, so any piston within ±0.1 mm of zero
   trips it. Fires on **every startup since 2026-09-14** and advises setting
   `POSITION_COUNTS_PER_MM` to 1, which would break everything.
8. **The wavemaker skill needs updating** — `.claude/skills/wavemaker/SKILL.md`
   line 109 still lists "Travelling front to back" as a Wave-tab MOVES option.
   It was removed from that tab today.

## Hardware, for the lab not the software

- **Piston 26 will not home.** Mechanical. Nothing here can fix it.
- **9 of 12 surveyed drives report "Controller Hot".** Present before any of
  today's testing.
- Pistons 16–18 were found in `Switch On Locked` with following errors of
  +58 / +9 / +90 mm. A boot+home cleared them.

## Notes for whoever picks this up

- Live tools need `Bash(python tools/live_*.py:*)` in
  `.claude/settings.local.json`. It is there now.
- `tools/`, `tests/` and `pytest.ini` were missing from this working copy and
  were restored from `github.com/adrhito/wavemaker-f25`. That repo is **behind**
  this copy — it has no `cascade_offsets`/`BY_ROW`. None of this session's work
  is pushed anywhere.
- Per the skill: never let a subagent near the PLC, and never run two live tools
  at once.
