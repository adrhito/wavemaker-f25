"""What the Soliton tab puts on the drives.

Built with ``__new__`` and stub attributes, the way tests/test_row_cascade.py
exercises the Wave tab, so no Tk root is needed and nothing is drawn.

The test that matters most is the direction one. A cascade marching the wrong
way down the tank has already happened once in this repository, and here it
would drive the wave into the end wall the bank is bolted to.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import params, solitons, tags
from Motor import Motor


def column_of(axis: int) -> int:
    return (tags.display_number(axis) - 1) // tags.ROWS_PER_COLUMN


def send_with(depth=250, amplitude=40, columns=5, pitch=150, gain=1.0):
    """Run the tab's send() over real Motor objects and hand them back."""
    from soliton import SolitonDesigner as module

    motors = [Motor(a) for a in range(tags.MOTOR_COUNT)]

    class FakeSet(list):
        pass

    tab = module.SolitonDesigner.__new__(module.SolitonDesigner)
    tab.model = SimpleNamespace(
        sets=[FakeSet(motors)],
        set_selection=lambda axes: None,
        create_set=lambda: None,
        mark_unprepared=lambda: None,
    )
    tab.columns = SimpleNamespace(get=lambda: columns)
    tab.result = SimpleNamespace(configure=lambda **kw: None)
    tab.view = SimpleNamespace(status=lambda m: None, refresh_all=lambda: None)
    tab.logger = SimpleNamespace(info=lambda *a: None)
    tab._design = solitons.design(depth, amplitude, gain=gain, pitch=pitch,
                                  columns=columns)
    tab.send()
    return tab, motors


# -- the direction the wave travels -------------------------------------------

def test_the_column_nearest_the_end_wall_fires_first():
    """Column 1 is pistons 1, 2, 3: deepest, against the wall the bank is on.

    It has to lead. If the stagger ran the other way the disturbance would
    march towards the wall instead of down the tank.
    """
    _tab, motors = send_with()

    first = [m for m in motors if column_of(m.axis) == 0]
    assert all(m.write_params["Curve Offset"] == 0 for m in first)


def test_each_column_fires_later_than_the_one_before_it():
    _tab, motors = send_with()

    by_column = {}
    for motor in motors:
        by_column.setdefault(column_of(motor.axis), set()).add(
            motor.write_params["Curve Offset"]
        )

    used = [by_column[c] for c in range(5)]
    assert all(len(offsets) == 1 for offsets in used), (
        "every piston in a column must share one offset"
    )
    ordered = [next(iter(offsets)) for offsets in used]
    assert ordered == sorted(ordered)
    assert ordered[0] < ordered[-1], "the stagger must actually stagger"


def test_the_offsets_are_not_keyed_by_axis():
    """Axis 0 is piston 30, at the far end of the bank.

    Indexing the stagger by ``axis // 3`` rather than by display position gives
    the front column the largest delay and runs the wave backwards. The Wave
    tab carries a comment about this exact bug; this asserts the soliton tab
    does not repeat it.
    """
    # All ten columns staggered, so every column has a distinct offset and the
    # axis-versus-display confusion cannot hide behind unused columns.
    _tab, motors = send_with(columns=tags.COLUMN_COUNT)

    piston_one = [m for m in motors if tags.display_number(m.axis) == 1][0]
    piston_thirty = [m for m in motors if tags.display_number(m.axis) == 30][0]

    assert piston_one.axis == 29, "piston 1 is axis 29, not axis 1"
    assert piston_thirty.axis == 0, "piston 30 is axis 0"

    # Keyed by display position, piston 1 leads and piston 30 trails. Keyed by
    # axis it would be exactly the other way round, which is the bug.
    assert piston_one.write_params["Curve Offset"] == 0
    assert piston_thirty.write_params["Curve Offset"] == max(
        m.write_params["Curve Offset"] for m in motors
    )
    assert piston_thirty.write_params["Curve Offset"] > 0


# -- which pistons --------------------------------------------------------------

def test_only_the_submerged_columns_get_a_stagger():
    """Pistons 16-30 ride clear of a low surface and move no water.

    They are left at offset 0 rather than given a delay, because a delay
    implies they are part of the wave.
    """
    _tab, motors = send_with(columns=5)

    unused = [m for m in motors if column_of(m.axis) >= 5]
    assert all(m.write_params["Curve Offset"] == 0 for m in unused)


def test_asking_for_more_columns_staggers_more_of_them():
    _tab, motors = send_with(columns=8)

    staggered = set()
    for motor in motors:
        if motor.write_params["Curve Offset"] != 0:
            staggered.add(column_of(motor.axis))
    assert max(staggered) == 7


# -- a soliton is a curve run --------------------------------------------------

def test_curve_id_is_set_because_a_soliton_needs_a_curve_run():
    """The inverse of the Wave tab's rule, and deliberately so.

    A row cascade must NOT set Curve ID: its stagger is staged as a starting
    position, which an ordinary continuous run honours, so turning it into a
    curve run would only send the operator to the wrong button.

    A soliton is the opposite. It is a single monotone push with real
    per-piston timing, and Curve Offset is read by the controller during a
    curve run only. Without a Curve ID there is nothing for Start Curve to
    play. Anyone applying the Wave tab's rule here would break this.
    """
    _tab, motors = send_with()

    assert all(m.write_params["Curve ID"] != 0 for m in motors)
    assert all(m.write_params["Amplitude Scale"] == 100 for m in motors)
    assert all(m.write_params["Time Scale"] > 0 for m in motors)


def test_the_push_is_eased_at_both_ends():
    # S-curve. A trapezoidal push steps the acceleration at the start and the
    # end, putting a transient in the water that is not part of the wave asked
    # for.
    _tab, motors = send_with()
    assert all(m.write_params["Profile"] == 2 for m in motors)


# -- the machine's limits are respected ---------------------------------------

def test_the_written_speed_never_exceeds_the_drive_limit():
    _tab, motors = send_with(depth=250, amplitude=120)
    ceiling = params.BY_NAME["Speed 1"].maximum
    assert all(m.write_params["Speed 1"] <= ceiling for m in motors)
    assert all(m.write_params["Speed 2"] <= ceiling for m in motors)


def test_the_positions_stay_inside_the_travel():
    _tab, motors = send_with(depth=250, amplitude=120)
    low = params.BY_NAME["Position 1"].minimum
    high = params.BY_NAME["Position 2"].maximum
    for motor in motors:
        assert low <= motor.write_params["Position 1"] <= high
        assert low <= motor.write_params["Position 2"] <= high


def test_the_stroke_written_is_the_stroke_designed():
    tab, motors = send_with(depth=250, amplitude=40)
    one = motors[0]
    written = (one.write_params["Position 2"] - one.write_params["Position 1"])
    assert written == pytest.approx(tab._design.stroke, abs=1.0)


def test_a_design_with_a_problem_sends_nothing():
    """Refusing beats sending something the drive will distort.

    A push past the drive's speed limit comes out with its top flattened, which
    is not a smaller soliton -- it is a different wave. Sending it anyway would
    produce a result nobody could interpret.
    """
    from soliton import SolitonDesigner as module

    motors = [Motor(a) for a in range(tags.MOTOR_COUNT)]

    class FakeSet(list):
        pass

    tab = module.SolitonDesigner.__new__(module.SolitonDesigner)
    tab.model = SimpleNamespace(
        sets=[FakeSet(motors)], set_selection=lambda axes: None,
        create_set=lambda: None, mark_unprepared=lambda: None,
    )
    tab.columns = SimpleNamespace(get=lambda: 5)
    tab.result = SimpleNamespace(configure=lambda **kw: None)
    tab.view = SimpleNamespace(status=lambda m: None, refresh_all=lambda: None)
    tab.logger = SimpleNamespace(info=lambda *a: None)
    tab._design = solitons.design(250, 240)          # past breaking

    assert not tab._design.runnable
    before = dict(motors[0].write_params)
    tab.send()
    assert motors[0].write_params == before, "nothing may reach the drives"
