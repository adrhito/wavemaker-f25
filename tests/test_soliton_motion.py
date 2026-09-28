"""The soliton trial makes one bounded move and leaves the floor raised."""

import json

import pytest

from Model import MachineState, RunMode
from app import params, tags
from app.plc import PlcError
from app.solitons import BOTTOM_MM, SolitaryTarget, SolitonTrial


@pytest.fixture
def trial():
    return SolitonTrial(SolitaryTarget(30, 1500, 150), 60)


@pytest.fixture
def moving_plc(homed_model, plc, monkeypatch):
    """Make the tag store report arrival when the absolute move starts."""
    original = plc.write

    def write(tag, value):
        original(tag, value)
        if tag == tags.RUN_SINGLE and value == 1:
            for axis in homed_model.live_axes:
                destination = plc.read(params.BY_NAME["Position 1"].tag(axis))
                original(tags.axis_field(axis, tags.ACTUAL_POSITION),
                         params.to_counts(destination))

    monkeypatch.setattr(plc, "write", write)
    return plc


def test_stage_and_fire_are_distinct_one_way_moves(homed_model, moving_plc, trial):
    assert homed_model.stage_soliton(trial)
    assert homed_model.soliton_staged
    assert homed_model.state is MachineState.HOMED
    assert homed_model.fire_soliton(trial, planned_station_mm=2000)
    assert homed_model.state is MachineState.HOMED
    assert not homed_model.soliton_staged
    assert moving_plc.writes_to(tags.RUN_SINGLE) == [1, 0, 1, 0]
    assert 1 not in moving_plc.writes_to(tags.RUN_CONTINUOUS)
    assert 1 not in moving_plc.writes_to(tags.RUN_CURVE)
    assert moving_plc.read(params.BY_NAME["Position 1"].tag(0)) == trial.top_mm
    assert moving_plc.read(tags.axis_field(0, tags.ACTUAL_POSITION)) == (
        params.to_counts(trial.top_mm))
    assert all(not motor.write_success for motor in homed_model.all_motors)
    assert homed_model.last_soliton_record is not None
    assert homed_model.last_soliton_record.exists()
    record = json.loads(homed_model.last_soliton_record.read_text(encoding="utf-8"))
    assert record["pulse_status"] == "completed"
    assert record["source"] == "simulator"
    assert record["planned_measurement_station_mm"] == 2000.0
    assert record["pulse_finished_utc"]
    assert record["actual_end_positions_mm"]["30"] == trial.top_mm
    assert homed_model._soliton_floor_raised


def test_escape_after_completed_pulse_does_not_lower_floor(
        homed_model, moving_plc, trial):
    assert homed_model.stage_soliton(trial)
    assert homed_model.fire_soliton(trial)
    moving_plc.clear_history()
    assert homed_model.emergency_stop()
    assert 1 not in moving_plc.writes_to(tags.RUN_SINGLE)
    assert moving_plc.read(tags.axis_field(0, tags.ACTUAL_POSITION)) == (
        params.to_counts(trial.top_mm))
    assert not homed_model._parking.is_set()


def test_failed_followup_run_does_not_forget_raised_floor(
        homed_model, moving_plc, trial, monkeypatch):
    assert homed_model.stage_soliton(trial)
    assert homed_model.fire_soliton(trial)

    def fail_write(_transport):
        raise PlcError("write failed")

    monkeypatch.setattr(homed_model.all_motors[0], "write_to", fail_write)
    assert homed_model.start(RunMode.SINGLE)
    assert homed_model._soliton_floor_raised
    moving_plc.clear_history()
    assert homed_model.emergency_stop()
    assert 1 not in moving_plc.writes_to(tags.RUN_SINGLE)


def test_fire_without_same_staged_plan_writes_nothing(homed_model, plc, trial):
    assert not homed_model.fire_soliton(trial)
    assert plc.history == []
    assert homed_model.bridge.problems


def test_changed_target_requires_restaging(homed_model, moving_plc, trial):
    assert homed_model.stage_soliton(trial)
    moving_plc.clear_history()
    changed = SolitonTrial(SolitaryTarget(40, 1500, 150), 60)
    assert not homed_model.fire_soliton(changed)
    assert moving_plc.history == []


def test_existing_run_bit_rejects_pulse_before_moving(homed_model, moving_plc, trial):
    assert homed_model.stage_soliton(trial)
    moving_plc.write(tags.RUN_CONTINUOUS, 1)
    moving_plc.clear_history()
    assert homed_model.fire_soliton(trial)
    assert homed_model.bridge.problems
    assert 1 not in moving_plc.writes_to(tags.RUN_SINGLE)
    assert moving_plc.read(tags.RUN_CONTINUOUS) == 1
    record = json.loads(homed_model.last_soliton_record.read_text(encoding="utf-8"))
    assert record["pulse_status"] == "failed"
    assert "run bit" in record["pulse_error"].lower()


def test_no_pulse_when_trial_cannot_be_saved(
        homed_model, moving_plc, trial, monkeypatch):
    assert homed_model.stage_soliton(trial)
    moving_plc.clear_history()

    def fail_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.soliton_records.save_trial", fail_save)
    assert homed_model.fire_soliton(trial)
    assert homed_model.last_soliton_record is None
    assert 1 not in moving_plc.writes_to(tags.RUN_SINGLE)
    assert homed_model.state is MachineState.HOMED
    assert "disk full" in homed_model.bridge.problems[-1][1]


def test_move_timeout_clears_run_bit_and_forgets_temporary_parameters(
        homed_model, plc, trial):
    with pytest.raises(ValueError, match="did not reach"):
        homed_model._soliton_move(
            tuple(homed_model.live_axes), trial.pulse_parameters(),
            trial.top_mm, 0.0, 2.0)
    assert plc.writes_to(tags.RUN_SINGLE) == [1, 0]
    assert all(not motor.write_success for motor in homed_model.all_motors)


def test_failed_run_bit_clear_reports_physical_stop(homed_model, plc, trial,
                                                     monkeypatch):
    original = plc.write

    def fail_clear(tag, value):
        if tag == tags.RUN_SINGLE and value == 0:
            raise PlcError("connection lost")
        original(tag, value)

    monkeypatch.setattr(plc, "write", fail_clear)
    with pytest.raises(PlcError):
        homed_model._soliton_move(
            tuple(homed_model.live_axes), trial.pulse_parameters(),
            trial.top_mm, 0.0, 2.0)
    assert homed_model.bridge.problems
    assert "physical stop" in homed_model.bridge.problems[-1][1].lower()


def test_stop_during_pulse_does_not_park_or_wait_for_stroke(
        homed_model, moving_plc, trial, monkeypatch):
    assert homed_model.stage_soliton(trial)
    before = len(moving_plc.writes_to(tags.RUN_SINGLE))

    def stop_on_arrival(_targets, _tolerance):
        if homed_model._soliton_running.is_set():
            homed_model.stop()
            return False
        return True

    monkeypatch.setattr(homed_model, "_all_within", stop_on_arrival)
    assert homed_model.fire_soliton(trial)
    assert homed_model.state is MachineState.HOMED
    assert moving_plc.writes_to(tags.RUN_SINGLE)[before:] == [1, 0, 0]
    assert not homed_model._parking.is_set()
    assert not homed_model.soliton_staged
    record = json.loads(homed_model.last_soliton_record.read_text(encoding="utf-8"))
    assert record["pulse_status"] == "interrupted"


def test_trial_parameters_do_not_exceed_existing_gentle_preset(trial):
    for values in (trial.stage_parameters(), trial.pulse_parameters()):
        assert not params.validate_all(values)
        assert values["Speed 1"] <= 200
        assert values["Decel 1"] <= 4000
        assert values["Jerk 1"] <= 2000
        assert values["Position 1"] <= BOTTOM_MM
