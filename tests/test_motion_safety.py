"""Offline regression tests for cancellation, isolation, and failed cleanup."""

import pytest

import Model as model_module
from Model import MachineState, RunMode
from app import params, tags
from app.plc import PlcError


def test_staging_failure_cannot_start_continuous(homed_model, plc, monkeypatch):
    monkeypatch.setattr(homed_model, "_stage_cascade", lambda: False)
    homed_model.start(RunMode.CONTINUOUS)
    assert 1 not in plc.writes_to(tags.RUN_CONTINUOUS)
    assert homed_model.state is MachineState.READY


def test_no_staging_needed_can_start(homed_model, plc):
    homed_model.start(RunMode.CONTINUOUS)
    assert plc.writes_to(tags.RUN_CONTINUOUS) == [1]


def test_run_homes_then_starts_without_second_command(homed_model, monkeypatch):
    calls = []
    homed_model._set_state(MachineState.READY)
    monkeypatch.setattr(homed_model, "_already_homed", lambda: False)
    monkeypatch.setattr(
        homed_model, "_prepare_worker",
        lambda: homed_model._set_state(MachineState.HOMED),
    )
    monkeypatch.setattr(homed_model, "_start_worker", lambda mode: calls.append(mode))
    homed_model._run_worker(RunMode.CONTINUOUS)
    assert calls == [RunMode.CONTINUOUS]


def test_stop_during_staging_restores_every_motor(homed_model, plc, monkeypatch):
    monkeypatch.setattr(homed_model, "cascade_targets", lambda: {0: 10, 1: 20})
    restored = []
    for motor in homed_model.all_motors:
        def restore(transport, axis=motor.axis):
            restored.append(axis)
            if axis == 0:
                raise PlcError("restore failed")
        monkeypatch.setattr(motor, "write_to", restore)
    def stop_on_poll(*args):
        homed_model.stop(immediate=True)
        return False
    monkeypatch.setattr(homed_model, "_all_within", stop_on_poll)
    assert not homed_model._stage_cascade()
    assert restored == [0, 1, 2]
    assert plc.writes_to(tags.RUN_SINGLE)[-1] == 0
    assert homed_model.bridge.problems


def test_stop_preparing_never_claims_homed_or_parks(homed_model, plc):
    homed_model._set_state(MachineState.PREPARING)
    homed_model.bridge.states.clear()
    homed_model.stop(immediate=True)
    assert MachineState.HOMED not in homed_model.bridge.states
    assert homed_model.state is MachineState.READY
    assert 1 not in plc.writes_to(tags.RUN_SINGLE)
    assert plc.writes_to(tags.HOME_BUTTON)[-1] == 0


@pytest.mark.parametrize("method,constant,tag", [
    ("clear_faults", "CLEAR_FAULT_SECONDS", tags.CLEAR_MOTOR_ERROR),
    ("boot_motors", "BOOT_PULSE_SECONDS", tags.MOTOR_BOOT),
    ("motors_off", "CLEAR_FAULT_SECONDS", tags.CLEAR_MOTOR_ERROR),
])
def test_stop_event_cuts_pulses_short(model, plc, monkeypatch, method, constant, tag):
    """A requested stop must end these pulses early, and still drop the bit.

    This test used to assert the opposite -- that the full wait happened anyway
    -- which matched the code while these used a raw ``time.sleep``. But
    ``shutdown()`` sets ``_stop_requested`` precisely in order to cut them
    short, and it runs on the Tk thread from the window's close handler, so
    ignoring the stop froze the interface for five seconds on the way out.

    What must not change either way is the ``try/finally``: the command bit is
    returned to 0 whether the wait ran to completion or was cut off.
    """
    import time as _time

    monkeypatch.setattr(model_module, constant, 5.0)
    model._stop_requested.set()
    started = _time.time()
    getattr(model, method)()
    assert _time.time() - started < 1.0, "the pulse ignored the requested stop"
    assert plc.writes_to(tag) == [1, 0]


@pytest.mark.parametrize("start,distance", [
    (float("nan"), 10), (float("inf"), 10), (-21, 10), (371, 10),
    (0, float("nan")), (0, float("inf")), (0, -10), (0, 1000),
])
def test_invalid_probe_has_no_writes(model, plc, start, distance):
    plc.write(tags.axis_field(0, tags.ACTUAL_POSITION), params.to_counts(start))
    plc.clear_history()
    with pytest.raises(ValueError):
        model.probe_movement(0, distance)
    assert plc.history == []


def test_probe_isolates_axis_and_restores_selection(model, plc, monkeypatch):
    monkeypatch.setattr(model_module, "PROBE_SECONDS", 0)
    plc.write(tags.live_motor(1), 1)
    plc.write(tags.live_motor(2), 1)
    original = [plc.read(tags.live_motor(a)) for a in range(tags.MOTOR_COUNT)]
    seen = []
    write = plc.write
    def observe(tag, value):
        if tag == tags.RUN_SINGLE and value == 1:
            seen.append([a for a in range(tags.MOTOR_COUNT) if plc.read(tags.live_motor(a))])
        write(tag, value)
    monkeypatch.setattr(plc, "write", observe)
    model.probe_movement(0)
    assert seen == [[0], [0]]
    assert [plc.read(tags.live_motor(a)) for a in range(tags.MOTOR_COUNT)] == original
    assert plc.read(tags.RUN_SINGLE) == 0


def test_probe_never_reselects_if_run_clear_fails(model, plc, monkeypatch):
    monkeypatch.setattr(model_module, "PROBE_SECONDS", 0)
    plc.write(tags.live_motor(1), 1)
    write = plc.write
    def fail_clear(tag, value):
        if tag == tags.RUN_SINGLE and value == 0:
            raise PlcError("clear failed")
        write(tag, value)
    monkeypatch.setattr(plc, "write", fail_clear)
    with pytest.raises(PlcError):
        model.probe_movement(0)
    assert plc.read(tags.live_motor(1)) == 0
    assert model.bridge.problems


def test_probe_rejected_while_running(homed_model, plc):
    homed_model._set_state(MachineState.RUNNING)
    with pytest.raises(ValueError):
        homed_model.probe_movement(0)
    assert plc.history == []


def test_homing_cancel_during_wait_does_not_raise_home_again(homed_model, plc, monkeypatch):
    def cancel(seconds):
        homed_model.stop(immediate=True, park=False)
    monkeypatch.setattr(homed_model, "_sleep", cancel)
    homed_model._set_state(MachineState.PREPARING)
    assert not homed_model._home_motors()
    assert 1 not in plc.writes_to(tags.HOME_BUTTON)
    assert not homed_model.bridge.problems


def test_staging_timeout_restores_parameters_and_runs_unstaggered(
    homed_model, plc, monkeypatch
):
    """Staging that does not finish must not cancel the run.

    This asserted that the run was blocked, which is what the code did and what
    a deleted comment had argued against: an unstaggered wave is worth more
    than no wave. One flaky axis is enough to make ``_all_within`` return False,
    so blocking meant a single unreadable piston could stop the array running
    at all.

    The parameters must still be put back -- staging moves pistons by writing
    Position 1, and leaving those behind would silently change the stroke.
    """
    monkeypatch.setattr(model_module, "STAGE_SECONDS", 0)
    monkeypatch.setattr(homed_model, "cascade_targets", lambda: {0: 10})
    homed_model.start(RunMode.CONTINUOUS)
    assert 1 in plc.writes_to(tags.RUN_CONTINUOUS), "the run was cancelled"
    assert plc.read(params.BY_NAME["Position 1"].tag(0)) == 0


def test_probe_restore_failure_attempts_remaining_selection(model, plc, monkeypatch):
    monkeypatch.setattr(model_module, "PROBE_SECONDS", 0)
    for axis in (1, 2):
        plc.write(tags.live_motor(axis), 1)
    write = plc.write
    def fail_restore(tag, value):
        if tag == tags.live_motor(1) and value == 1:
            raise PlcError("selection restore failed")
        write(tag, value)
    monkeypatch.setattr(plc, "write", fail_restore)
    with pytest.raises(PlcError):
        model.probe_movement(0)
    assert plc.read(tags.live_motor(2)) == 1
    assert plc.read(tags.RUN_SINGLE) == 0
    assert model.bridge.problems


def test_second_stop_invalidates_queued_graceful_worker(homed_model, plc):
    homed_model._set_state(MachineState.RUNNING)
    queued = []
    homed_model._spawn = lambda name, work: queued.append(work)
    homed_model.stop()
    homed_model.stop()
    for work in queued:
        work()
    assert 1 not in plc.writes_to(tags.RUN_SINGLE)
    assert 1 not in plc.writes_to(tags.RUN_CONTINUOUS)


def test_stop_during_prepare_boot_never_homes(homed_model, plc, monkeypatch):
    homed_model._homed_axes.clear()
    def boot():
        homed_model.stop(immediate=True)
    monkeypatch.setattr(homed_model, "boot_motors", boot)
    homed_model.prepare()
    assert homed_model.state is MachineState.READY
    assert 1 not in plc.writes_to(tags.HOME_BUTTON)
    assert 1 not in plc.writes_to(tags.RUN_SINGLE)


def test_nan_feedback_cannot_complete_staging(model, plc):
    plc.write(tags.axis_field(0, tags.ACTUAL_POSITION), float("nan"))
    assert not model._all_within({0: 10}, 5)


def test_staging_clear_failure_never_restores_motion_parameters(homed_model, plc, monkeypatch):
    monkeypatch.setattr(model_module, "STAGE_SECONDS", 0)
    monkeypatch.setattr(homed_model, "cascade_targets", lambda: {0: 10})
    restored = []
    for motor in homed_model.all_motors:
        monkeypatch.setattr(motor, "write_to", lambda transport: restored.append(True))
    write = plc.write
    def fail_clear(tag, value):
        if tag == tags.RUN_SINGLE and value == 0:
            raise PlcError("stop failed")
        write(tag, value)
    monkeypatch.setattr(plc, "write", fail_clear)
    assert not homed_model._stage_cascade()
    assert restored == []
    assert all(not m.is_synced for m in homed_model.all_motors)
    assert homed_model.bridge.problems


def test_stop_attempts_every_command_after_failure(homed_model, plc, monkeypatch):
    homed_model._set_state(MachineState.PREPARING)
    write = plc.write
    attempted = []
    def fail_first(tag, value):
        attempted.append(tag)
        if tag == tags.RUN_SINGLE:
            raise PlcError("first stop failed")
        write(tag, value)
    monkeypatch.setattr(plc, "write", fail_first)
    assert not homed_model.stop(immediate=True)
    assert attempted == [tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE, tags.HOME_BUTTON]


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_parking_always_invalidates_written_cache(homed_model, plc, monkeypatch, cancel):
    write = plc.write
    def interrupted_write(tag, value):
        write(tag, value)
        if tag == params.BY_NAME["Position 1"].tag(0):
            if cancel:
                homed_model._cancel_park.set()
            else:
                raise PlcError("parking write failed")
    monkeypatch.setattr(plc, "write", interrupted_write)
    if cancel:
        assert not homed_model._park_moves()
    else:
        with pytest.raises(PlcError):
            homed_model._park_moves()
    assert all(not m.is_synced for m in homed_model.all_motors)
    assert plc.writes_to(tags.RUN_SINGLE)[-1] == 0


def test_calibration_rejected_during_continuous_run(homed_model, plc):
    homed_model.start(RunMode.CONTINUOUS)
    plc.clear_history()
    assert not homed_model.calibrate_all()
    assert plc.history == []
