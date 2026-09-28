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
