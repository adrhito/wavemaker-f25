"""Concurrency, state-machine and stop/start regressions in Model.py.

Every test here runs against SimulatedPlc or a hand-built fake transport. No
PLC is contacted and no tool in tools/ is imported.

Most of these tests originally pinned down a defect introduced by an
uncommitted concurrency-hardening pass: they were written to FAIL against
that working tree, as evidence of the bug rather than a specification of
desired behaviour. As each defect in Model.py was fixed, its test was
rewritten in place to assert the corrected behaviour instead -- the
docstrings still explain the fault the code now avoids. A handful of tests
near the end of the file (search for "app/plc.py") are unrelated: they pin
down transport-layer behaviour in app/plc.py, owned by a different pass of
work, and are left as they were.
"""

from __future__ import annotations

import threading
import time

import pytest

import Model as model_module
from app import params, tags
from app.plc import PlcError
from Model import MachineState, RunMode


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _make_homed(model, plc, axes=(0, 1, 2)):
    """A model with one group, homed, running commands inline."""
    for axis in axes:
        model.toggle(axis, True)
    model.create_set()
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    assert model.prepare()
    plc.clear_history()
    return model


def _park_writes(plc):
    """Position writes that can only have come from the resting move."""
    return [
        (tag, value)
        for tag, value in plc.history
        if tag.endswith("Pos_1") and value == model_module.PARK_POSITION
    ]


# --------------------------------------------------------------------------
# 1. The resting move is skipped whenever the run worker still holds _busy
# --------------------------------------------------------------------------

def test_stop_during_a_single_stroke_still_rests_the_pistons(model, plc, monkeypatch):
    """`_halt_and_rest` used to refuse to park while `_busy` was held.

    A single stroke and a curve run hold `_busy` for the whole of
    `_hold_and_watch`, so the Stop worker always observes `busy is True` at
    the moment it halts -- the run thread has not unwound yet. Gating the
    resting move on `not self.busy` therefore meant the documented "after a
    stop the pistons return to the bottom of their stroke" behaviour silently
    never happened. `_park_worker`'s own non-blocking `_busy.acquire()` is
    already the correct arbiter of "is something else using the machine", so
    `_halt_and_rest` no longer duplicates that check -- and the resting move
    now runs even though `busy` was True when it was spawned.
    """
    monkeypatch.setattr(model_module, "SINGLE_STROKE_SECONDS", 3.0)
    monkeypatch.setattr(model_module, "GRACEFUL_STOP_SECONDS", 0.3)
    _make_homed(model, plc)

    spawned = []
    busy_when_stopping = []

    def spawn(name, work):
        spawned.append(name)
        threading.Thread(target=work, name=name, daemon=True).start()

    model._spawn = spawn

    real_halt = model._halt_and_rest

    def watched_halt(park, waited):
        busy_when_stopping.append(model.busy)
        return real_halt(park, waited)

    model._halt_and_rest = watched_halt

    assert model.start(RunMode.SINGLE)
    deadline = time.time() + 2.0
    while time.time() < deadline and model.state is not MachineState.RUNNING:
        time.sleep(0.01)
    assert model.state is MachineState.RUNNING

    model.stop()  # graceful, the default
    deadline = time.time() + 3.0
    while time.time() < deadline and model.busy:
        time.sleep(0.01)
    time.sleep(0.3)

    assert busy_when_stopping == [True], (
        "the run worker still owns _busy when the Stop worker halts"
    )
    assert "Rest" in spawned, "the resting move was started despite busy being True"
    assert _park_writes(plc), "the pistons were sent to PARK_POSITION"


# --------------------------------------------------------------------------
# 2. Stop during the continuous staging move: PREPARING breaks parking
#    and throws away every piston's homing
# --------------------------------------------------------------------------

def test_stop_while_staging_leaves_pistons_rested_and_still_homed(
    model, plc, monkeypatch
):
    """`_start_worker` runs `_stage_cascade` with the state at PREPARING, the
    same state real homing uses. `_halt_and_rest` used to treat any PREPARING
    stop as "homing was interrupted": it wiped `_homed_axes` and dropped to
    READY, so (a) the pistons were left sitting on their staging points
    instead of being rested (parking requires state HOMED), and (b) the next
    Start re-homed all thirty pistons, a minute of travel, for nothing.

    `_staging` now tells `_halt_and_rest` the two apart: a Stop during
    staging leaves `_homed_axes` alone and rewinds to HOMED, not READY, so
    the resting move can run. `_start_worker` also has to get out of the way
    of that decision -- it used to unconditionally set READY once staging
    returned False, which would race `_halt_and_rest` on another thread and
    could stomp HOMED straight back to READY.

    Stop is spawned on a real thread here (rather than called straight from
    the mocked `plc.write`) so this exercises the genuine cross-thread race,
    not an artificial same-thread reentrancy that `_busy` cannot represent.
    """
    monkeypatch.setattr(model_module, "STAGE_SECONDS", 5.0)
    monkeypatch.setattr(model_module, "PARK_SECONDS", 0.0)
    # Two columns with different Curve Offsets, so a stagger really happens.
    for axis in (0, 3):
        model.toggle(axis, True)
    model.create_set()
    model.sets[0].motors[0].write_params["Curve Offset"] = 0
    model.sets[0].motors[3].write_params["Curve Offset"] = 100
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    assert model.prepare()
    model._homed_axes = set(range(tags.MOTOR_COUNT))  # a full Calibrate All
    plc.clear_history()

    assert model.cascade_targets(), "the test needs a real staging move"

    # Press Stop the moment staging raises the run bit.
    write = plc.write
    stopped = []
    stop_threads = []

    def press_stop(tag, value):
        write(tag, value)
        if tag == tags.RUN_SINGLE and value == 1 and not stopped:
            stopped.append(model.state)
            # model._spawn is inline here, so the resting move this call may
            # trigger (via _halt_and_rest) runs to completion synchronously,
            # inside this same thread's call to stop() -- joining it below
            # therefore waits for parking too, not just the halt.
            t = threading.Thread(
                target=lambda: model.stop(immediate=True), daemon=True
            )
            stop_threads.append(t)
            t.start()

    monkeypatch.setattr(plc, "write", press_stop)
    model.start(RunMode.CONTINUOUS)

    assert stopped == [MachineState.PREPARING], (
        "staging still runs with the state at PREPARING"
    )
    for t in stop_threads:
        t.join(5.0)
        assert not t.is_alive(), "the Stop thread did not finish"

    assert model._homed_axes == set(range(tags.MOTOR_COUNT)), (
        "a Stop during staging must not discard the calibration of all 30 pistons"
    )
    assert model.state is MachineState.HOMED, (
        "a Stop during staging must land on HOMED, not READY, so parking can run"
    )
    assert _park_writes(plc), "the pistons were sent to PARK_POSITION"


# --------------------------------------------------------------------------
# 3. A live speed change holds _motion_lock across every write, so Stop waits
# --------------------------------------------------------------------------

def test_live_speed_change_no_longer_delays_emergency_stop(homed_model, plc, monkeypatch):
    """`change_speed_live` used to hold `_motion_lock` for 2 writes per piston.

    `all_stop` takes the same lock, so Escape / Stop could not drop a single
    run bit until the whole speed sweep had finished. Three pistons is enough
    to show it; the real machine has thirty, i.e. sixty serialized round
    trips. `change_speed_live` now holds the lock only for its initial check,
    not for the writes, so `all_stop` no longer waits on it.

    Parking is disabled for this test: `_halt_and_rest`'s own resting move
    also writes Speed 1/2 (to PARK_SPEED) for every motor, on the same mocked
    `plc.write`, which would otherwise add its own delay to `blocked_for` and
    swamp the signal this test is isolating.
    """
    monkeypatch.setattr(model_module, "PARK_ON_STOP", False)
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS

    started = threading.Event()
    real_write = plc.write

    def slow_write(tag, value):
        if tag.endswith("Spd_1") or tag.endswith("Spd_2"):
            started.set()
            time.sleep(0.05)
        real_write(tag, value)

    plc.write = slow_write
    worker = threading.Thread(target=homed_model.change_speed_live, args=(250,))
    worker.start()
    assert started.wait(2.0)

    begin = time.time()
    homed_model.emergency_stop()
    blocked_for = time.time() - begin
    worker.join(5.0)
    plc.write = real_write

    assert blocked_for < 0.15, (
        "Stop was blocked for {0:.3f}s behind the live speed change".format(
            blocked_for
        )
    )


def test_live_speed_change_is_rejected_while_a_graceful_stop_is_waiting(
    homed_model, plc, monkeypatch
):
    """`change_speed_live` used to consult only `_state` and `_stop_requested`.

    Between `stop()` setting `_stopping` and the Stop worker setting
    `_stop_requested` (up to GRACEFUL_STOP_SECONDS later) the state is still
    RUNNING, so the machine used to happily accept a brand new speed while it
    was supposed to be coming to rest. `change_speed_live` now also checks
    `_stopping` (and `_parking`, for the same reason once the resting move has
    started) and rejects the change instead.
    """
    monkeypatch.setattr(model_module, "GRACEFUL_STOP_SECONDS", 1.0)
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    # Sit the pistons mid-stroke so the graceful stop really has to wait.
    for motor in homed_model.all_motors:
        plc.write(
            tags.axis_field(motor.axis, tags.ACTUAL_POSITION), params.to_counts(175)
        )
    homed_model._spawn = lambda name, work: threading.Thread(
        target=work, name=name, daemon=True
    ).start()

    plc.clear_history()
    assert homed_model.stop()  # graceful; the worker is now waiting
    time.sleep(0.05)
    assert homed_model._stopping.is_set()

    with pytest.raises(ValueError):
        homed_model.change_speed_live(900)
    assert plc.writes_to(params.BY_NAME["Speed 1"].tag(0)) == [], (
        "the run must not be re-sped while it is being stopped"
    )
    homed_model._stop_requested.set()
    time.sleep(0.3)


# --------------------------------------------------------------------------
# 4. clear_faults / boot_motors stopped being stop-aware
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command,constant",
    [("clear_faults", "CLEAR_FAULT_SECONDS"), ("boot_motors", "BOOT_PULSE_SECONDS")],
)
def test_pulse_commands_honour_a_requested_stop(model, monkeypatch, command, constant):
    """`clear_faults`/`boot_motors` wait with `self._sleep`, not a raw
    `time.sleep`. `self._sleep` is `_stop_requested.wait`, so with a stop
    already pending the pulse must return almost immediately -- the whole
    reason `shutdown()` sets `_stop_requested` before calling these is to cut
    the pulse short, and it runs on the Tk thread, so a pulse that sits
    through the wait regardless freezes the window for its full duration.
    """
    monkeypatch.setattr(model_module, constant, 0.4)
    model._stop_requested.set()

    begin = time.time()
    model._sleep(0.4)
    stop_aware = time.time() - begin

    begin = time.time()
    getattr(model, command)()
    raw = time.time() - begin

    assert stop_aware < 0.05, "_sleep is stop-aware"
    assert raw < 0.05, (
        "{0} sat through the whole pulse with a stop pending".format(command)
    )


def test_shutdown_cuts_the_fault_clearing_pulse_short(model, monkeypatch):
    """View calls `shutdown()` on the Tk thread, so the window would freeze
    for the whole pulse if it were not cut short.

    `shutdown` sets `_stop_requested` first precisely so `clear_faults`'s wait
    (`self._sleep`, i.e. `_stop_requested.wait`) returns almost at once.
    """
    monkeypatch.setattr(model_module, "CLEAR_FAULT_SECONDS", 0.4)
    begin = time.time()
    model.shutdown()
    assert time.time() - begin < 0.05


# --------------------------------------------------------------------------
# 5. The pending live-stroke queue can never drain again
# --------------------------------------------------------------------------

def test_pending_live_stroke_drops_a_piston_that_leaves_the_selection(
    homed_model, plc
):
    """`_apply_pending_live_stroke_locked` used to `return False` outright
    for a missing axis.

    Editing the tank selection during a run is explicitly allowed (it never
    interrupts a run), but it can remove an axis the queued stroke change is
    still waiting for. Aborting the whole scan on that one axis used to wedge
    the queue on every later monitor tick, silently, for every OTHER axis
    too, until the next Stop. The axis is now dropped from the pending
    request instead, reported once, and the rest of the queue still drains.
    """
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    for motor in homed_model.all_motors:
        motor.write_params["Position 1"] = 100
        motor.write_params["Position 2"] = 300
        plc.write(
            tags.axis_field(motor.axis, tags.ACTUAL_POSITION), params.to_counts(200)
        )
    plc.clear_history()

    assert homed_model.change_stroke_live(40)
    assert homed_model._pending_live_stroke == 40, "queued, not applied"

    homed_model.toggle(0, False)  # operator deselects piston 1 mid-run
    assert homed_model.state is MachineState.RUNNING
    assert 0 not in [m.axis for m in homed_model.all_motors]

    # Every remaining piston is now sitting exactly on its endpoint.
    for motor in homed_model.all_motors:
        plc.write(
            tags.axis_field(motor.axis, tags.ACTUAL_POSITION), params.to_counts(100)
        )
    assert homed_model._apply_pending_live_stroke() is True

    applied = [t for t, v in plc.history if t.endswith("Pos_2")]
    assert applied, "the remaining pistons must still get the new stroke"
    assert homed_model._pending_live_stroke is None, (
        "the request must complete once every remaining axis is applied"
    )
    assert 0 not in homed_model._pending_live_stroke_axes
    assert homed_model.bridge.messages, "the dropped piston must be reported"
    assert any("30" in m for m in homed_model.bridge.messages), (
        "the status should name piston 30 (axis 0's display number), the "
        "one that left the selection"
    )


# --------------------------------------------------------------------------
# 6. A cancelled start is reported to the operator as a machine fault
# --------------------------------------------------------------------------

def test_stop_during_start_is_not_reported_as_nothing_moved(homed_model, plc):
    """`_hold_and_watch` used to ignore the result of `_begin_motion`.

    When Stop lands between `_start_worker`'s check and the run bit going
    high, `_begin_motion` correctly refuses to command motion -- but the old
    watch loop ran anyway, measured zero travel, and raised the "Nothing
    moved ... the controller is not in Run" dialog at the operator, even
    though the "failure" was a stop they asked for themselves.
    `_hold_and_watch` now returns None when `_begin_motion` refuses, and
    `_start_worker` reports a plain cancellation instead of calling
    `_report_travel` at all.
    """
    homed_model._set_state(MachineState.HOMED)
    real_begin = homed_model._begin_motion

    def stop_first(tag):
        homed_model._stop_requested.set()
        return real_begin(tag)

    homed_model._begin_motion = stop_first
    plc.clear_history()

    homed_model._start_worker(RunMode.SINGLE)

    assert plc.writes_to(tags.RUN_SINGLE) == [0], "motion was correctly refused"
    assert homed_model.bridge.problems == [], (
        "a cancelled start must not be shown as a controller fault"
    )
    assert homed_model.bridge.messages[-1] == "Stroke cancelled."


# --------------------------------------------------------------------------
# 7. States and commands the new _command guard locks out
# --------------------------------------------------------------------------

def test_reset_and_reconnect_leave_running_but_calibrate_does_not(
    homed_model, plc, monkeypatch
):
    """`_command`'s RUNNING guard now exempts Reset and Reconnect.

    A PlcError out of a run leaves the state at RUNNING (see the next test),
    and Reset and Reconnect are exactly how an operator recovers from that --
    so refusing them while RUNNING, which is precisely the state a recovery
    command exists for, made a wedged machine permanently wedged. Calibrate
    is not a recovery command: it is still refused while RUNNING, because
    running a fresh homing cycle underneath a run in progress is the same
    "two commands fighting over the ladder" problem `_command` exists to
    prevent in the first place.
    """
    # reconnect() would otherwise open a real socket to the PLC via
    # plc_module.connect(); this proves the _command guard, not the
    # transport, so replace it with a no-op.
    monkeypatch.setattr(
        model_module.plc_module, "connect", lambda *a, **k: (plc, True)
    )

    homed_model._set_state(MachineState.RUNNING)
    assert homed_model.calibrate_all() is False
    assert homed_model.state is MachineState.RUNNING

    assert homed_model.reconnect() is True

    homed_model._set_state(MachineState.RUNNING)
    assert homed_model.reset() is True


def test_a_failed_run_recovers_to_homed_so_commands_work_again(
    homed_model, plc, monkeypatch
):
    """A PlcError out of `_hold_and_watch` used to leave the state at RUNNING
    for ever: `_recover_after_failure` only rewound PREPARING, and `_command`
    refuses every ordinary command while the state is RUNNING -- so Prepare,
    Start and Calibrate were all dead, and even Stop's own `all_stop()` call
    reuses the same write that just failed. `_recover_after_failure` now
    rewinds RUNNING to HOMED, the same place a normal stop leaves it (the
    drives are still homed; nothing about the failure un-homes them), so a
    fresh Start can be issued immediately instead of the machine needing a
    successful Stop it may not be able to make.
    """
    monkeypatch.setattr(model_module, "SINGLE_STROKE_SECONDS", 0.0)
    real_write = plc.write
    boom = {"armed": True}

    def flaky(tag, value):
        if tag == tags.RUN_SINGLE and value == 0 and boom["armed"]:
            boom["armed"] = False
            raise PlcError("transient write failure")
        real_write(tag, value)

    monkeypatch.setattr(plc, "write", flaky)
    assert homed_model.start(RunMode.SINGLE)

    assert homed_model.state is MachineState.HOMED, (
        "a failed run must rewind out of RUNNING, not wedge there"
    )
    assert [title for title, _m in homed_model.bridge.problems] == ["Machine error"]
    assert homed_model.start(RunMode.SINGLE) is True, (
        "and a fresh Start must be accepted straight away"
    )


def test_probe_movement_names_the_command_it_is_holding(
    homed_model, plc, monkeypatch, caplog
):
    """`probe_movement` used to take `_busy` without setting
    `_current_command`, so anything it blocked was logged as 'Ignored X:
    None is still running.' -- true, but useless for working out what was
    actually in the way. It now sets `_current_command` for the duration,
    the same as every other holder of `_busy`.
    """
    monkeypatch.setattr(model_module, "PROBE_SECONDS", 0.3)
    started = threading.Event()
    real_begin = homed_model._begin_motion

    def watch_begin(tag):
        started.set()
        return real_begin(tag)

    homed_model._begin_motion = watch_begin
    worker = threading.Thread(
        target=lambda: homed_model.probe_movement(0), daemon=True
    )
    worker.start()
    assert started.wait(2.0), "the probe must actually start moving the piston"

    assert homed_model._current_command == "Movement test"
    with caplog.at_level("WARNING"):
        assert homed_model.start(RunMode.SINGLE) is False
    assert any(
        "Movement test is still running" in record.message
        for record in caplog.records
    ), "the blocked command must be able to say what it was blocked by"

    worker.join(3.0)
    assert not worker.is_alive()
    assert homed_model._current_command is None


def test_the_resting_move_runs_immediately_when_busy_is_already_free(
    model, plc, monkeypatch
):
    """Companion to the previous test: the ordinary case, not the race.

    `_halt_and_rest` no longer checks `busy` at all, and `_park_worker` waits
    up to `PARK_ACQUIRE_TIMEOUT_SECONDS` for `_busy` to come free -- but only
    because the run thread it is chasing is usually still unwinding. When
    nothing holds `_busy` (the common case: the previous command has already
    finished by the time Stop is pressed), the resting move must not sit out
    that grace period regardless -- it should be spawned and complete
    promptly, exactly as before the guard was ever added.
    """
    monkeypatch.setattr(model_module, "PARK_SECONDS", 0.0)
    _make_homed(model, plc)
    assert not model.busy, "nothing should be running after prepare() returns"

    spawned = []
    model._spawn = lambda name, work: (
        spawned.append(name),
        threading.Thread(target=work, name=name, daemon=True).start(),
    )

    begin = time.time()
    model.stop(immediate=True)
    deadline = time.time() + 3.0
    while time.time() < deadline and "Rest" not in spawned:
        time.sleep(0.01)
    elapsed = time.time() - begin

    assert "Rest" in spawned
    assert elapsed < 1.0, (
        "the resting move waited as though _busy were contended: {0:.2f}s".format(
            elapsed
        )
    )
    assert _park_writes(plc), "the pistons were sent to PARK_POSITION"


# --------------------------------------------------------------------------
# 8. app/plc.py: what counts as a dropped session, and what does not
# --------------------------------------------------------------------------

class _DeadPlc:
    """A pylogix stand-in that never answers. Nothing leaves this process."""

    reads = []
    closes = []

    def __init__(self):
        self.IPAddress = None
        self.ProcessorSlot = None
        self.Socket = None

    def Read(self, tag):
        _DeadPlc.reads.append(tag)
        raise OSError("timed out")

    def Close(self):
        _DeadPlc.closes.append(1)


class _SilentPlc(_DeadPlc):
    """Answers, but with no value -- what pylogix returns for a bad tag path."""

    def Read(self, tag):
        _DeadPlc.reads.append(tag)
        return None


def test_connect_probe_costs_one_socket_timeout(monkeypatch):
    """`connect()` probes with a single direct `Read`, not through `read()`.

    `read()` retries on a fresh session, and a machine that is switched off or
    unplugged answers nothing at all, so every attempt costs a full socket
    timeout. Startup against a dead machine must therefore wait one timeout
    and fall back to the simulation, not two -- this is the exact situation in
    which the application most wants to give up quickly."""
    import app.plc as plc_module

    _DeadPlc.reads = []
    _DeadPlc.closes = []
    monkeypatch.setattr(plc_module, "PLC", _DeadPlc)
    client = plc_module.PlcClient("203.0.113.1", 1)

    assert client.connect() is False
    assert len(_DeadPlc.reads) == 1, "the probe was attempted once"
    assert not client.connected


def test_a_read_that_returns_no_value_keeps_the_session(monkeypatch):
    """A response with no value is a bad tag path, not a dropped session.

    The None check lives outside `_attempt`, so such a read is reported once,
    naming the tag, with the connection left alone. Were it raised inside the
    retry body, `except Exception` would read it as a dropped session and
    rebuild the connection twice per read -- and `_poll_positions` does 30
    reads at 4 Hz, so one bad tag path would become a reconnect storm whose
    error message is double-wrapped and names the wrong cause."""
    import app.plc as plc_module

    _DeadPlc.reads = []
    _DeadPlc.closes = []
    monkeypatch.setattr(plc_module, "PLC", _SilentPlc)
    client = plc_module.PlcClient("203.0.113.1", 1)

    with pytest.raises(PlcError) as caught:
        client.read("Program:Wave_Control.Nope")

    assert len(_DeadPlc.reads) == 1, "the read was not retried"
    assert _DeadPlc.closes == [], "the session was left intact"
    assert "returned no value" in str(caught.value)
    assert "Program:Wave_Control.Nope" in str(caught.value)
    assert str(caught.value).count("Read ") == 1, "message is not double-wrapped"


def test_keepalive_retries_a_dropped_session_but_not_a_bad_tag(monkeypatch):
    """`keepalive()` is `read(PROBE_TAG)`, called on every homing poll.

    It must therefore keep the retry that covers a genuinely dropped session
    -- that is the whole point of `_attempt` -- while a response carrying no
    value costs one attempt and no teardown, because at one keepalive per poll
    the wrong answer there is a reconnect storm during homing."""
    import app.plc as plc_module

    _DeadPlc.reads = []
    _DeadPlc.closes = []
    monkeypatch.setattr(plc_module, "PLC", _DeadPlc)
    with pytest.raises(PlcError):
        plc_module.PlcClient("203.0.113.1", 1).keepalive()
    assert len(_DeadPlc.reads) == 2, "a dropped session is retried once"

    _DeadPlc.reads = []
    _DeadPlc.closes = []
    monkeypatch.setattr(plc_module, "PLC", _SilentPlc)
    with pytest.raises(PlcError):
        plc_module.PlcClient("203.0.113.1", 1).keepalive()
    assert len(_DeadPlc.reads) == 1, "a missing value is not a dropped session"
    assert _DeadPlc.closes == []


# --------------------------------------------------------------------------
# 9. Staging that does not complete now cancels the whole continuous run
# --------------------------------------------------------------------------

def test_one_unreadable_piston_runs_unstaggered_instead_of_cancelling(
    model, plc, monkeypatch
):
    """`_stage_cascade` used to return False on a staging timeout and cancel
    the whole continuous run -- and `_all_within` gives up if a *single*
    position read fails, so one flaky axis used to cost the whole run after a
    STAGE_SECONDS wait, contradicting a deleted comment's intent: "an
    unstaggered wave is worth more than no wave". "Could not stage" (a
    timeout, possibly caused by one bad read) is now told apart from "was
    stopped": the former runs anyway, unstaggered, and says so; only the
    latter still cancels the run.
    """
    monkeypatch.setattr(model_module, "STAGE_SECONDS", 0.2)
    for axis in (0, 3):
        model.toggle(axis, True)
    model.create_set()
    model.sets[0].motors[0].write_params["Curve Offset"] = 0
    model.sets[0].motors[3].write_params["Curve Offset"] = 100
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    assert model.prepare()

    real_read = plc.read

    def flaky_read(tag):
        if tag == tags.axis_field(3, tags.ACTUAL_POSITION):
            raise PlcError("axis 4 position unavailable")
        return real_read(tag)

    monkeypatch.setattr(plc, "read", flaky_read)
    plc.clear_history()

    model.start(RunMode.CONTINUOUS)

    assert plc.writes_to(tags.RUN_CONTINUOUS) == [1], (
        "the run must go ahead unstaggered rather than being cancelled"
    )
    assert model.state is MachineState.RUNNING
    assert not model.bridge.problems, "a staging timeout is not a problem dialog"
    assert model.bridge.messages, "the operator must be told it is running unstaggered"


# --------------------------------------------------------------------------
# 10. probe_movement clears a stop that shutdown had set
# --------------------------------------------------------------------------

def test_probe_movement_refuses_motion_after_shutdown(model, plc, monkeypatch):
    """`probe_movement` used to clear `_stop_requested` unconditionally.

    `shutdown()` sets that flag, calls `motors_off()` and closes the
    transport; it takes neither `_busy` nor `_motion_lock`. A diagnosis with
    "movement test" ticked runs on its own thread, so it used to be able to
    clear the flag and raise Run_1 after the machine had been shut down --
    and PlcClient silently reopens a session on the next write. `probe_movement`
    now checks a dedicated `_shutdown` flag and never clears `_stop_requested`
    itself (only `_command` does that), so it stays refused.
    """
    monkeypatch.setattr(model_module, "PROBE_SECONDS", 0.0)
    monkeypatch.setattr(model_module, "CLEAR_FAULT_SECONDS", 0.0)
    _make_homed(model, plc)

    model.shutdown()
    assert model._stop_requested.is_set()
    assert model._shutdown.is_set()
    assert plc.read(tags.RUN_SINGLE) == 0
    plc.clear_history()

    with pytest.raises(ValueError):
        model.probe_movement(0)

    assert 1 not in plc.writes_to(tags.RUN_SINGLE), (
        "the movement test must not raise Run_1 after shutdown"
    )
    assert model._stop_requested.is_set(), "and must not clear the shutdown's stop flag"


# --------------------------------------------------------------------------
# 11. One unreadable axis makes every graceful Stop wait the full timeout
# --------------------------------------------------------------------------

def test_an_unreadable_axis_does_not_make_the_graceful_stop_wait(
    homed_model, plc, monkeypatch
):
    """`_wait_for_stroke_end` used to treat a read failure as "not at rest"
    (`at_rest = False; break`), rather than "cannot judge this axis". A
    single flaky axis -- the exact fault this application exists to cope
    with -- therefore forced every graceful Stop to sit out the whole of
    GRACEFUL_STOP_SECONDS, even when every readable piston was already
    parked on an endpoint. The unreadable axis is now skipped (and recorded
    on `unreadable_axes` for the display) instead of being read as motion.
    """
    monkeypatch.setattr(model_module, "GRACEFUL_STOP_SECONDS", 0.5)
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS

    real_read = plc.read

    def flaky_read(tag):
        if tag == tags.axis_field(2, tags.ACTUAL_POSITION):
            raise PlcError("axis 3 position unavailable")
        return real_read(tag)

    monkeypatch.setattr(plc, "read", flaky_read)

    begin = time.time()
    assert homed_model._wait_for_stroke_end() is True
    waited = time.time() - begin
    assert waited < 0.2, (
        "waited {0:.2f}s despite every readable piston already being at rest"
        .format(waited)
    )
    assert homed_model.unreadable_axes == [2]
