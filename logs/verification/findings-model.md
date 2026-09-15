# Model.py and app/plc.py — concurrency and state audit

Against the uncommitted Codex changes, 14 September 2026. Proofs are in
`tests/test_concurrency.py`; each test passed by reproducing the defect, and is
inverted as the defect is fixed.

## HIGH

1. **Stop never rests the pistons when the run worker still holds `_busy`.**
   `Model.py:1871`, the `and not self.busy` condition. A single stroke, a curve,
   and an analytics-recording continuous run all hold `_busy` for the whole run,
   so the Stop worker finds `busy` True, skips the resting move, and tells the
   operator "Motors stopped." with the pistons part-way up. Contradicts README.
   Fix: drop the condition; `_park_worker`'s own non-blocking acquire is the
   arbiter. Tests: `test_stop_during_a_single_stroke_never_rests_the_pistons`.

2. **Escape/Stop blocked behind a live speed change.** `Model.py:1356` holds
   `_motion_lock` across two PLC writes per piston — 60 serialized round trips on
   the real array — and `all_stop` takes the same lock. Measured over 150 ms with
   only three pistons. `_apply_pending_live_stroke_locked` has the same shape.
   Fix: hold the lock only around the run-bit transition.

3. **A PlcError during a run leaves RUNNING, which `_command` now refuses to
   leave.** `Model.py:616-619` plus `_recover_after_failure` at 653-658, which
   only rewinds PREPARING. One failed `plc.write(tag, 0)` wedges the machine:
   Prepare, Start, Calibrate, Reset and Reconnect all return False for ever.

4. **Stop during continuous staging leaves pistons unrested and un-homes all
   30.** `Model.py:1518` moved `_stage_cascade` into PREPARING, which
   `_halt_and_rest` reads as interrupted homing — so it clears `_homed_axes`,
   discarding a full Calibrate All. A 12-second window.

## MEDIUM

5. **`clear_faults`/`boot_motors` are no longer stop-aware**, and closing the
   window hangs 5 s. `Model.py:670`, `731` swapped `self._sleep` for raw
   `time.sleep`; `shutdown()` sets `_stop_requested` specifically to cut these
   short, and runs on the Tk thread.

6. **`probe_movement` clears `_stop_requested` and can command motion after
   shutdown.** `Model.py:1152`. Diagnostics probes run on their own daemon
   thread, and `PlcClient._attempt` silently reopens a closed session.

7. **The pending live-stroke queue wedges for ever if a piston leaves the
   selection.** `Model.py:1438-1440`. Editing the tank mid-run is allowed, and
   doing so silently stops the queue draining for every axis until the next Stop.

8. **Live speed/stroke changes accepted while a graceful Stop is in flight.**
   `Model.py:1341`, `1398` do not check `_stopping`, which is set up to 6 s
   before `_stop_requested`.

9. **`PlcClient.connect()` costs two socket timeouts; a None read rebuilds the
   session twice.** `app/plc.py:157` and `201-206`. One bad tag path in
   `_poll_positions` — 30 reads at 4 Hz — becomes a reconnect storm.

10. **One unreadable axis makes every graceful Stop run the full 6 s.**
    `Model.py:1820-1825` turned a `continue` into a `break`.

11. **Staging that does not complete now cancels the whole continuous run.**
    `Model.py:1633-1635`. The deleted comment stated the intent: "an unstaggered
    wave is worth more than no wave."

## LOW

12. **A cancelled start is reported as a controller fault.** `Model.py:1695`
    discards `_begin_motion`'s return, so a Stop during start raises the
    "Nothing moved ... the controller is not in Run" dialog.
13. **`probe_movement` takes `_busy` without setting `_current_command`**
    (`Model.py:1146`) — blocked commands log "Ignored Start: None is still
    running."
14. **`_run_mode` left set after a cancelled continuous start** (1524-1526).
15. **`_halt_and_rest` clears the pending-stroke fields without `_motion_lock`**
    (1855-1857).
16. **`_stop_serial += 1` is not atomic** (1764).

## Suspicions that did not pan out

- `write_params[...]` KeyError in the live-stroke path is not reachable:
  `Motor.write_params` is a complete `params.defaults()` dict and nothing
  deletes a key.
- `write_success` bookkeeping on failure is harmless — `is_synced` also compares
  `current_params` element by element.
- `_probe_movement_worker`'s `finally` ordering is correct: `Run_1 = 0` first,
  Live_Motors restored only if that clear succeeded, errors re-raised.
- `_calibrate_worker`'s explicit `_set_state` is a **fix**, not a regression:
  `_refresh_idle_state` early-returns while PREPARING, so Calibrate All used to
  leave the machine stuck there.
