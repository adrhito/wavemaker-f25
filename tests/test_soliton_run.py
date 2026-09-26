"""Running a soliton without a stored curve.

The fallback route, for when no curve is loaded on the drives. It is exercised
entirely against the simulator, which proves what the application *sends* --
the order of the writes, the bits it raises, and that it drops them again. It
cannot prove how a real drive responds to a Live_Motors bit going high while
Run_1 is already high, which is the one assumption the mechanism rests on and
is flagged as unverified in Model.run_soliton's own docstring.
"""

from __future__ import annotations

import pytest

from app import solitons, tags
from Model import MachineState, RunMode


@pytest.fixture
def design():
    return solitons.design(250, 40, columns=3, pitch=150)


# -- the plan -----------------------------------------------------------------

def test_the_plan_puts_the_deepest_column_first(design):
    plan = solitons.sequence_plan(design)
    assert plan[0].at == 0.0
    assert plan[0].column == 0
    assert sorted(tags.display_number(a) for a in plan[0].axes) == [1, 2, 3]


def test_the_plan_is_in_time_order(design):
    plan = solitons.sequence_plan(design)
    assert [s.at for s in plan] == sorted(s.at for s in plan)


def test_every_column_brings_its_whole_row_of_three(design):
    for step in solitons.sequence_plan(design):
        assert len(step.axes) == tags.ROWS_PER_COLUMN


def test_the_plan_reads_as_something_an_operator_can_check(design):
    text = solitons.describe_plan(design, solitons.sequence_plan(design))
    # Piston numbers, not axes. Axis 0 is piston 30.
    assert "pistons 1, 2 and 3" in text
    assert "column  1" in text


def test_an_empty_plan_says_so_rather_than_indexing_off_the_end():
    empty = solitons.design(250, 40, columns=1)
    empty = empty._replace(offsets={})
    assert "Nothing to run" in solitons.describe_plan(empty, [])


# -- the dry run --------------------------------------------------------------

def test_a_dry_run_moves_nothing(model, plc, design):
    """The default, because this route has never been tried at the machine."""
    for axis in solitons.axes_for(3):
        model.toggle(axis, True)
    model.create_set()
    plc.clear_history()

    assert model.run_soliton(design) is True

    assert plc.history == [], "a dry run must not write a single tag"


def test_running_for_real_has_to_be_asked_for(model, plc, design):
    import inspect

    from Model import Model

    signature = inspect.signature(Model.run_soliton)
    assert signature.parameters["dry_run"].default is True


# -- the real run -------------------------------------------------------------

def prepared(model, plc, monkeypatch, columns=3):
    """A homed model whose pistons report arriving where they are sent.

    SimulatedPlc is an inert tag store: it records writes and never moves a
    piston, so a position read never matches a target and the run correctly
    refuses to push from positions it cannot confirm. That refusal has its own
    test below; these ones are about what happens afterwards, so arrival is
    granted here.
    """
    import Model as model_module

    for axis in solitons.axes_for(columns):
        model.toggle(axis, True)
    model.create_set()
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    model.prepare()
    monkeypatch.setattr(model_module.Model, "_all_within",
                        lambda self, targets, tolerance: True)
    monkeypatch.setattr(model_module, "STAGE_SECONDS", 0.5)
    plc.clear_history()
    return model


def test_the_single_stroke_bit_always_comes_down(model, plc, design, monkeypatch):
    """AGENTS.md: clear asserted bits in finally paths.

    A soliton left with Run_1 high is a machine nobody else can command.
    """
    prepared(model, plc, monkeypatch)
    model.run_soliton(design, dry_run=False)

    assert plc.read(tags.RUN_SINGLE) in (0, False)


def test_position_1_is_given_back_after_being_borrowed(model, plc, design, monkeypatch):
    """Run_1 moves to Position 1, so the push borrows it as a destination.

    Not restoring it is a real fault with a quiet symptom: the next run uses
    the far end of the stroke as its near end. _single_stroke carries the same
    guarantee for the same reason.
    """
    prepared(model, plc, monkeypatch)
    from app import params

    before = {}
    for motor in model.all_motors:
        before[motor.axis] = int(motor.write_params["Position 1"])

    model.run_soliton(design, dry_run=False)

    for axis, original in before.items():
        assert plc.read(params.BY_NAME["Position 1"].tag(axis)) == original


def test_the_columns_are_let_in_one_at_a_time(model, plc, design, monkeypatch):
    """The whole mechanism: Live_Motors is what staggers the push.

    Every column's bit must be written high, and the deepest column's must come
    before the others in the write history.
    """
    prepared(model, plc, monkeypatch)
    model.run_soliton(design, dry_run=False)

    order = []
    live_tags = dict(
        (tags.live_motor(axis), axis) for axis in solitons.axes_for(3)
    )
    # Only the push counts. Settling the pistons at the start marks the whole
    # selection live in one go, in axis order, which is not a stagger and is
    # not what this is about. The push begins after that selection is cleared
    # again, so start reading from the last bit dropped.
    for index, (tag, value) in enumerate(plc.history):
        if tag in live_tags and not value:
            start = index
    for tag, value in plc.history[start:]:
        axis = live_tags.get(tag)
        if axis is None or not value:
            continue
        column = (tags.display_number(axis) - 1) // tags.ROWS_PER_COLUMN
        if column not in order:
            order.append(column)
    assert order == sorted(order), "columns must be let in front to back"
    assert order[0] == 0


def test_a_stop_partway_through_leaves_nothing_asserted(model, plc, design, monkeypatch):
    prepared(model, plc, monkeypatch)
    model._stop_requested.set()

    model.run_soliton(design, dry_run=False)

    assert plc.read(tags.RUN_SINGLE) in (0, False)


def test_a_soliton_never_raises_the_continuous_bit(model, plc, design, monkeypatch):
    """A soliton is one push. Run_2 would repeat it for ever.

    Worth stating because the rest of this application is built around
    continuous motion, and Run_2 is the habitual bit to reach for.
    """
    prepared(model, plc, monkeypatch)
    model.run_soliton(design, dry_run=False)

    assert not any(plc.writes_to(tags.RUN_CONTINUOUS))


def test_a_piston_that_will_not_reach_the_start_stops_the_whole_run(model, plc,
                                                                    design):
    """Refusing beats pushing from wherever the pistons happen to be.

    A soliton is one shot: if a column is not at the start of its push when the
    bit goes high, it contributes the wrong displacement at the wrong moment,
    and there is no second cycle in which to notice. The inert simulator never
    moves anything, so this is the natural state to test it in.
    """
    import Model as model_module

    for axis in solitons.axes_for(3):
        model.toggle(axis, True)
    model.create_set()
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    model.prepare()
    model_module.STAGE_SECONDS = 0.0
    plc.clear_history()

    model.run_soliton(design, dry_run=False)

    # The arming step, and only the arming step, writes the far end of the
    # stroke into Position 1. If that never happened, no push was commanded.
    from app import params

    armed = []
    for axis in solitons.axes_for(3):
        motor = [m for m in model.all_motors if m.axis == axis][0]
        far = int(motor.write_params["Position 2"])
        armed.extend(v for v in plc.writes_to(params.BY_NAME["Position 1"].tag(axis))
                     if v == far)
    assert armed == [], "no push may be commanded if the start was not reached"
    assert any("Could not start the soliton" in str(p)
               for p in model.bridge.problems)
