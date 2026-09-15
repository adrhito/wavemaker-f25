"""Live speed changes are PLC writes only; no run bit may be toggled."""

import pytest

from Model import MachineState, RunMode
from app import params, tags
from app.plc import PlcError


def test_live_speed_updates_selected_motor_speed_without_restart(homed_model, plc):
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    plc.clear_history()
    assert homed_model.change_speed_live(75)
    for motor in homed_model.all_motors:
        assert plc.writes_to(params.BY_NAME["Speed 1"].tag(motor.axis)) == [75]
        assert plc.writes_to(params.BY_NAME["Speed 2"].tag(motor.axis)) == [75]
    assert not any(tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)
                   and value != 0 for tag, value in plc.history)
    assert homed_model.state is MachineState.RUNNING


@pytest.mark.parametrize("value", [0, -1, 901, "fast"])
def test_live_speed_rejects_invalid_values_without_writes(homed_model, plc, value):
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    plc.clear_history()
    with pytest.raises((ValueError, TypeError)):
        homed_model.change_speed_live(value)
    assert plc.history == []


def test_live_speed_rejected_when_idle(homed_model, plc):
    plc.clear_history()
    with pytest.raises(ValueError):
        homed_model.change_speed_live(100)
    assert plc.history == []


def test_live_speed_partial_failure_is_reported(homed_model, plc, monkeypatch):
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    original = plc.write
    failed = {"done": False}
    def flaky(tag, value):
        if not failed["done"] and tag.endswith("Spd_2"):
            failed["done"] = True
            raise PlcError("offline")
        original(tag, value)
    monkeypatch.setattr(plc, "write", flaky)
    with pytest.raises(PlcError):
        homed_model.change_speed_live(100)
    assert homed_model.bridge.problems
    assert not homed_model.all_motors[0].write_success
    assert homed_model.all_motors[0].pending_changes()["Speed 2"] == 100


def test_live_stroke_updates_position_without_restart(homed_model, plc):
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    for motor in homed_model.all_motors:
        motor.write_params["Position 1"] = 100
        plc.write(tags.axis_field(motor.axis, tags.ACTUAL_POSITION), params.to_counts(100))
    plc.clear_history()
    assert homed_model.change_stroke_live(40)
    for motor in homed_model.all_motors:
        assert plc.writes_to(params.BY_NAME["Position 2"].tag(motor.axis)) == [140]
    assert not any(tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)
                   and value != 0 for tag, value in plc.history)


def test_live_stroke_waits_for_common_endpoint(homed_model, plc):
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    for motor in homed_model.all_motors:
        motor.write_params["Position 1"] = 100
        plc.write(tags.axis_field(motor.axis, tags.ACTUAL_POSITION), params.to_counts(130))
    plc.clear_history()
    assert homed_model.change_stroke_live(40)
    assert homed_model._pending_live_stroke == 40
    assert not any("Pos_2" in tag for tag, _value in plc.history)
    for motor in homed_model.all_motors:
        plc.write(tags.axis_field(motor.axis, tags.ACTUAL_POSITION), params.to_counts(100))
    assert homed_model._apply_pending_live_stroke()
    assert all(plc.writes_to(params.BY_NAME["Position 2"].tag(m.axis)) == [140]
               for m in homed_model.all_motors)


def test_live_stroke_rejects_out_of_range_without_writes(homed_model, plc):
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    homed_model.all_motors[0].write_params["Position 1"] = 350
    plc.clear_history()
    with pytest.raises(ValueError):
        homed_model.change_stroke_live(40)
    assert plc.history == []
    assert all(value == 0 for tag, value in plc.history
               if tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE))
