"""Unticking a piston while the machine runs stops that piston, and only it.

It used to leave the group and nothing more: its Live_Motors bit stayed set, so
the controller carried on driving it, and the Stop that followed ignored it.
"""

from __future__ import annotations

import time

import pytest

import Model as model_module
from Model import MachineState, Model, RunMode
from app import params, tags
from app.plc import PlcError
from app.simulator import SimulatedMachine

RUN_BITS = (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)


@pytest.fixture(autouse=True)
def quick_release(monkeypatch):
    monkeypatch.setattr(model_module, "RELEASE_POLL_SECONDS", 0.0)
    monkeypatch.setattr(model_module, "RELEASE_STILL_SECONDS", 0.0)


@pytest.fixture
def running(homed_model):
    homed_model._set_state(MachineState.RUNNING)
    homed_model._run_mode = RunMode.CONTINUOUS
    homed_model.plc.write(tags.RUN_CONTINUOUS, 1)
    homed_model.plc.clear_history()
    return homed_model


def live_writes(plc):
    return [(tag, value) for tag, value in plc.history if ".Live_Motors." in tag]


def test_deselecting_clears_only_that_pistons_live_bit(running, plc):
    running.toggle(1, False)

    assert live_writes(plc) == [(tags.live_motor(1), 0)]
    assert not any(tag in RUN_BITS for tag, _value in plc.history)
    assert plc.read(tags.RUN_CONTINUOUS) == 1
    assert running.state is MachineState.RUNNING
    assert running.live_axes == [0, 2]
    assert not running.bridge.problems
    assert "Piston(s) {0} stopped".format(tags.display_number(1)) in running.bridge.messages[-1]


def test_a_drag_that_drops_several_releases_each_of_them(running, plc):
    running.set_selection([0])

    assert sorted(live_writes(plc)) == [(tags.live_motor(1), 0), (tags.live_motor(2), 0)]
    assert plc.read(tags.RUN_CONTINUOUS) == 1


def test_deselecting_while_idle_writes_nothing(homed_model, plc):
    homed_model.toggle(1, False)
    assert plc.history == []


def test_deselecting_the_last_piston_stops_the_run(running, plc):
    running.set_selection([])

    assert sorted(live_writes(plc)) == [(tags.live_motor(a), 0) for a in (0, 1, 2)]
    for tag in RUN_BITS:
        assert plc.read(tag) == 0
    assert running.state is MachineState.IDLE


def test_a_failed_release_tells_the_operator_to_press_stop(running, plc, monkeypatch):
    original = plc.write

    def refuse(tag, value):
        if tag == tags.live_motor(1):
            raise PlcError("offline")
        original(tag, value)

    monkeypatch.setattr(plc, "write", refuse)
    running.toggle(1, False)

    title, message = running.bridge.problems[-1]
    assert title == "Piston not stopped"
    assert "Press Stop" in message


def test_a_piston_that_keeps_moving_is_reported(running, plc, monkeypatch):
    monkeypatch.setattr(model_module, "MAX_STROKE_SECONDS", 0.05)
    monkeypatch.setattr(model_module, "RELEASE_POLL_SECONDS", 0.005)
    monkeypatch.setattr(model_module, "RELEASE_STILL_SECONDS", 0.02)
    original = plc.read
    ticks = {"n": 0}

    def creeping(tag):
        if tag == tags.axis_field(1, tags.ACTUAL_POSITION):
            ticks["n"] += 1
            return params.to_counts(ticks["n"] * 10)
        return original(tag)

    monkeypatch.setattr(plc, "read", creeping)
    running.toggle(1, False)

    title, message = running.bridge.problems[-1]
    assert title == "Piston still moving"
    assert str(tags.display_number(1)) in message


def test_the_next_start_reselects_on_the_plc(running, plc):
    """A piston put back after a mid-run change must be live again next run.

    The Stop leaves the machine HOMED, so without this Start went straight to
    Run_2 with the piston's bit still cleared and it sat there unmoving.
    """
    running.toggle(1, False)
    running.toggle(1, True)
    running.stop(immediate=True, park=False)
    assert running.state is MachineState.HOMED
    assert running.needs_homing is False  # all three were homed already
    plc.clear_history()

    running.start(RunMode.SINGLE)

    assert (tags.live_motor(1), 1) in live_writes(plc)
    assert plc.writes_to(tags.HOME_BUTTON) == []  # no homing for the same three


def test_a_piston_added_mid_run_is_homed_before_it_runs(running, plc):
    running.toggle(5, True)
    running.stop(immediate=True, park=False)
    assert running.needs_homing is True


def test_the_mock_keeps_the_others_running(monkeypatch):
    """End to end on the moving simulator."""
    import app.simulator as sim_module

    monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
    for name in ("BOOT_PULSE_SECONDS", "CLEAR_FAULT_SECONDS", "HOME_POLL_SECONDS"):
        monkeypatch.setattr(model_module, name, 0.05)
    monkeypatch.setattr(model_module, "RELEASE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(model_module, "RELEASE_STILL_SECONDS", 0.2)

    machine = SimulatedMachine(tick=0.005)
    try:
        model = Model(transport=machine, is_live=False)
        model._spawn = lambda name, work: work()
        for axis in range(3):
            model.toggle(axis, True)
        group = model.sets[0]
        group.set_param("Position 1", 0)
        group.set_param("Position 2", 300)
        group.set_param("Speed 1", 400)
        group.set_param("Speed 2", 400)
        model.run(RunMode.CONTINUOUS)
        assert model.state is MachineState.RUNNING
        time.sleep(0.2)

        model.toggle(1, False)

        before = machine.snapshot()
        time.sleep(0.3)
        after = machine.snapshot()
        assert after[1] == before[1], "the deselected piston kept moving"
        assert after[0] != before[0] and after[2] != before[2], "the others stopped"
        assert model.state is MachineState.RUNNING
        assert not model.bridge.problems
    finally:
        model.stop(immediate=True, park=False)
        machine.close()
