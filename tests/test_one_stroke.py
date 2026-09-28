"""One stroke is one full cycle: up to the top of the stroke and back down.

The pistons rest at the bottom of travel (``PARK_POSITION``) after a stop, so
they are almost never standing on the stroke when One stroke is pressed. Sent
"out to Position 2, then back to Position 1" from there, a piston resting at
350 mm on a stroke of 0 to 150 went 350 -> 150 -> 0 at the machine: up, and up
again. The first leg only got it onto the stroke, so the operator saw half a
cycle and never the return.

These tests use a controller that obeys Run_1 the way the real one does -- an
absolute move to Position 1, nothing more -- and record every destination the
application commands, so the path a piston takes can be read back.
"""

from __future__ import annotations

import pytest

from app import params, tags
from app.plc import SimulatedPlc
import Model as model_module
from Model import RunMode

#: Smaller millimetres are higher: -20 is the top of travel, 370 the bottom.
REST = 370
TOP = 0
BOTTOM = 150


class ObedientPlc(SimulatedPlc):
    """Run_1 moves every live piston to its Position 1 at once.

    Each rising edge of Run_1 is recorded as ``{axis: (target, speed 1,
    speed 2)}``, so a test can see where each leg was sent and how fast.
    """

    def __init__(self) -> None:
        super().__init__()
        self.pulses = []

    def write(self, tag, value):
        rising = tag == tags.RUN_SINGLE and value and not self.read(tag)
        super().write(tag, value)
        if not rising:
            return
        legs = {}
        for axis in range(tags.MOTOR_COUNT):
            if not self.read(tags.live_motor(axis)):
                continue
            target = self.read(params.BY_NAME["Position 1"].tag(axis))
            legs[axis] = (
                target,
                self.read(params.BY_NAME["Speed 1"].tag(axis)),
                self.read(params.BY_NAME["Speed 2"].tag(axis)),
            )
            super().write(tags.axis_field(axis, tags.ACTUAL_POSITION),
                          params.to_counts(target))
        self.pulses.append(legs)

    def place(self, axis, mm):
        super().write(tags.axis_field(axis, tags.ACTUAL_POSITION),
                      params.to_counts(mm))

    def path(self, axis):
        """Every destination this piston was sent to, in order."""
        return [legs[axis][0] for legs in self.pulses if axis in legs]


@pytest.fixture
def plc() -> ObedientPlc:
    return ObedientPlc()


def stroke(model, top=TOP, bottom=BOTTOM, speed=400):
    for motor in model.all_motors:
        motor.set_param("Position 1", top)
        motor.set_param("Position 2", bottom)
        motor.set_param("Speed 1", speed)
        motor.set_param("Speed 2", speed)


def rest_at(plc, model, mm=REST):
    for motor in model.all_motors:
        plc.place(motor.axis, mm)


def test_a_stroke_from_rest_goes_up_and_comes_back_down(homed_model, plc):
    stroke(homed_model)
    rest_at(plc, homed_model)

    homed_model.start(RunMode.SINGLE)

    for motor in homed_model.all_motors:
        assert plc.path(motor.axis) == [BOTTOM, TOP, BOTTOM], (
            "onto the stroke at the bottom, up to the top, and back down"
        )


def test_the_operators_stroke_is_given_back_afterwards(homed_model, plc):
    """Position 1 and both speeds are borrowed; the PLC must end holding the
    operator's own values, or the next run inherits the last leg's."""
    stroke(homed_model, speed=300)
    rest_at(plc, homed_model)

    homed_model.start(RunMode.SINGLE)

    for motor in homed_model.all_motors:
        for name, value in (("Position 1", TOP), ("Speed 1", 300),
                            ("Speed 2", 300)):
            assert plc.read(params.BY_NAME[name].tag(motor.axis)) == value, name


def test_each_leg_runs_at_the_speed_set_for_its_direction(homed_model, plc):
    """Speed 1 heads for Position 2 and Speed 2 comes back to Position 1, as in
    a continuous run. Here Position 1 is the top, so up is Speed 2."""
    stroke(homed_model)
    for motor in homed_model.all_motors:
        motor.set_param("Speed 1", 300)
        motor.set_param("Speed 2", 600)
    rest_at(plc, homed_model)

    homed_model.start(RunMode.SINGLE)

    axis = homed_model.all_motors[0].axis
    _approach, up, down = [legs[axis] for legs in plc.pulses]
    assert up == (TOP, 600, 600)
    assert down == (BOTTOM, 300, 300)


def test_up_is_up_even_when_position_one_is_the_bottom(homed_model, plc):
    """An inverted stroke still rises first, now heading for Position 2."""
    stroke(homed_model, top=BOTTOM, bottom=TOP)   # Position 1 = 150, 2 = 0
    for motor in homed_model.all_motors:
        motor.set_param("Speed 1", 300)
        motor.set_param("Speed 2", 600)
    rest_at(plc, homed_model)

    homed_model.start(RunMode.SINGLE)

    axis = homed_model.all_motors[0].axis
    assert plc.path(axis) == [BOTTOM, TOP, BOTTOM]
    _approach, up, down = [legs[axis] for legs in plc.pulses]
    assert up[1:] == (300, 300)
    assert down[1:] == (600, 600)


def test_getting_onto_the_stroke_is_gentle(homed_model, plc):
    """From rest to the stroke can be most of the travel; it is positioning,
    so it goes at the staging pace, not at the wave's 900 mm/s."""
    stroke(homed_model, speed=900)
    rest_at(plc, homed_model)

    homed_model.start(RunMode.SINGLE)

    axis = homed_model.all_motors[0].axis
    approach = plc.pulses[0][axis]
    gentle = model_module.STAGE_SPEED
    assert approach == (BOTTOM, gentle, gentle)


class TestLegWindow:
    """Each leg is timed from the real distance to go, at that leg's pace."""

    def test_the_long_way_onto_the_stroke_gets_the_time_it_needs(
            self, homed_model, plc):
        rest_at(plc, homed_model, 370)
        axis = homed_model.all_motors[0].axis
        # 370 mm at 200 mm/s is 1.85 s of travel before any headroom.
        assert homed_model._travel_seconds({axis: 0}, {axis: 200}) > 1.85 * 2

    def test_a_piston_already_there_gets_only_the_floor(self, homed_model, plc):
        rest_at(plc, homed_model, BOTTOM)
        axis = homed_model.all_motors[0].axis
        assert homed_model._travel_seconds(
            {axis: BOTTOM}, {axis: 200}) == pytest.approx(3.0)

    def test_a_zero_speed_is_capped_not_endless(self, homed_model, plc):
        rest_at(plc, homed_model, 370)
        axis = homed_model.all_motors[0].axis
        assert homed_model._travel_seconds({axis: 0}, {axis: 0}) == \
            model_module.MAX_STROKE_SECONDS


def test_the_travel_reported_is_the_stroke_not_the_approach(homed_model, plc):
    """From rest at 370, a stroke of 0 to 150 moved 150 mm, not 370."""
    stroke(homed_model)
    rest_at(plc, homed_model)

    homed_model.start(RunMode.SINGLE)

    said = " ".join(homed_model.bridge.messages)
    assert "moved {0} mm".format(BOTTOM - TOP) in said
    assert "moved {0} mm".format(REST - TOP) not in said


def test_the_stroke_pauses_at_the_top_but_not_after_it_ends(
        homed_model, plc, monkeypatch):
    """Time 1 is the dwell at Position 1 (the top here) and Time 2 at
    Position 2 (the bottom). Only the turn at the top is part of the stroke."""
    stroke(homed_model)
    for motor in homed_model.all_motors:
        motor.set_param("Time 1", 700)
        motor.set_param("Time 2", 9000)
    rest_at(plc, homed_model)
    waits = []
    monkeypatch.setattr(homed_model, "_sleep", waits.append)

    homed_model.start(RunMode.SINGLE)

    assert 0.7 in waits, "the pause at the top"
    assert 9.0 not in waits, "no pause once the stroke has ended"


def test_a_piston_already_at_the_bottom_goes_straight_up(homed_model, plc):
    """A second One stroke starts where the first ended: no approach move."""
    stroke(homed_model)
    rest_at(plc, homed_model, BOTTOM)

    homed_model.start(RunMode.SINGLE)

    for motor in homed_model.all_motors:
        assert plc.path(motor.axis) == [TOP, BOTTOM]


def test_two_strokes_in_a_row_are_both_whole(homed_model, plc):
    stroke(homed_model)
    rest_at(plc, homed_model)

    homed_model.start(RunMode.SINGLE)
    homed_model.start(RunMode.SINGLE)

    axis = homed_model.all_motors[0].axis
    assert plc.path(axis) == [BOTTOM, TOP, BOTTOM, TOP, BOTTOM]


def test_stop_during_the_approach_starts_no_stroke(homed_model, plc,
                                                   monkeypatch):
    """Stopped on the way onto the stroke, nothing more is commanded, the
    operator's stroke is given back, and it is not reported as a fault."""
    stroke(homed_model)
    rest_at(plc, homed_model)
    real_write = plc.write

    def write(tag, value):
        real_write(tag, value)
        if tag == tags.RUN_SINGLE and value and len(plc.pulses) == 1:
            homed_model._stop_requested.set()   # Stop lands mid-approach

    monkeypatch.setattr(plc, "write", write)

    homed_model.start(RunMode.SINGLE)

    axis = homed_model.all_motors[0].axis
    assert plc.path(axis) == [BOTTOM], "no leg after the approach"
    assert plc.read(tags.RUN_SINGLE) == 0
    assert plc.read(params.BY_NAME["Position 1"].tag(axis)) == TOP
    assert homed_model.bridge.problems == []
    assert homed_model.bridge.messages[-1] == "Stroke cancelled."


def test_the_moving_mock_goes_up_and_comes_back_down(monkeypatch):
    """End to end in the mock that really moves: from rest at 370 a stroke of
    0 to 150 visits the top and finishes at the bottom, having moved 150 mm.
    The mock is not the machine; this proves what the application commands."""
    import threading
    import time

    import app.simulator as sim_module
    from app.simulator import SimulatedMachine
    from Model import MachineState, Model

    monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
    monkeypatch.setattr(model_module, "HOME_POLL_SECONDS", 0.05)
    monkeypatch.setattr(model_module, "STAGE_SPEED", 3000)
    machine = SimulatedMachine(tick=0.005)
    try:
        model = Model(transport=machine, is_live=False)
        model._spawn = lambda name, work: work()
        model.toggle(0, True)
        model.create_set()
        stroke(model, speed=900)
        assert model.prepare()
        assert model.state is MachineState.HOMED
        machine.place(0, REST)

        seen = []
        done = threading.Event()

        def watch():
            while not done.is_set():
                seen.append(machine.snapshot()[0])
                time.sleep(0.005)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            model.start(RunMode.SINGLE)
        finally:
            done.set()
            watcher.join(timeout=1.0)

        # Visiting the top and then finishing at the bottom is up and back
        # down. The watcher can lag the last few millimetres under load, so
        # where it ended is read from the machine itself.
        assert min(seen) == pytest.approx(TOP, abs=6), "reached the top"
        assert machine.snapshot()[0] == pytest.approx(BOTTOM, abs=6), (
            "came back down to the bottom")
        import re

        moved = re.search(r"moved (\d+) mm", " ".join(model.bridge.messages))
        assert moved, "the stroke's travel was reported"
        assert int(moved.group(1)) == pytest.approx(BOTTOM - TOP, abs=6), (
            "the stroke, not the journey from rest")
    finally:
        machine.close()
