"""Places that still disagree about which end of the chamber is the front.

``app/tags.py`` is the ground truth: ``display_number`` places axis 29 at the
top left of the picture as piston 1, and the drawing in ``modules/tank_view.py``
labels the left-hand side "FRONT - nearest you". Front-to-back therefore runs
*display* column 1 -> 10, i.e. axes 27..29 first and axes 0..2 last.

Each test below asserts the behaviour that convention requires, and is marked
xfail because the code still does the opposite. They are evidence, not fixes --
remove the marker when the corresponding defect is repaired.
"""

from __future__ import annotations

import csv
import os
from types import SimpleNamespace

import pytest

from app import diagnostics, patterns, tags, waves
from Motor import Motor

from tests.test_diagnostics import HEALTHY, drive, headlines

PRESET_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Presets"
)


def preset_rows(name):
    """``{axis: {parameter: value}}`` for one of the shipped preset files."""
    rows = {}
    with open(os.path.join(PRESET_DIR, name), "r", encoding="utf-8-sig",
              newline="") as handle:
        for row in csv.DictReader(handle):
            label = (row.get("Motor") or "").strip()
            if not label.isdigit():
                continue
            rows[int(label)] = row
    return rows


def front_axes(columns=1):
    """The axes in the first ``columns`` columns counting from the front."""
    return [a for a in range(tags.MOTOR_COUNT)
            if (tags.display_number(a) - 1) // tags.ROWS_PER_COLUMN < columns]


def back_axes(columns=1):
    return [a for a in range(tags.MOTOR_COUNT)
            if (tags.display_number(a) - 1) // tags.ROWS_PER_COLUMN
            >= tags.COLUMN_COUNT - columns]


# --- the coordinate system itself, which is sound ----------------------------


def test_display_numbering_is_a_bijection_onto_the_picture():
    """Ground truth, asserted so the tests below have something to stand on."""
    assert sorted(tags.display_number(a) for a in range(30)) == list(range(1, 31))
    assert all(tags.axis_from_display(tags.display_number(a)) == a
               for a in range(30))
    # Top left is piston 1 and drives axis 29; bottom right is piston 30, axis 0.
    assert tags.display_number(29) == 1
    assert tags.display_number(0) == 30
    # Each column of the picture is one whole axis triple, so grouping by
    # ``axis // 3`` and by ``(display_number - 1) // 3`` partitions identically.
    by_axis = {}
    by_display = {}
    for axis in range(30):
        by_axis.setdefault(axis // 3, set()).add(axis)
        by_display.setdefault(
            (tags.display_number(axis) - 1) // 3, set()).add(axis)
    assert sorted(map(sorted, by_axis.values())) == sorted(
        map(sorted, by_display.values()))


# --- the wave designer -------------------------------------------------------


def test_wave_designer_travels_the_same_way_as_the_pattern_tool():
    from wave import WaveDesigner as designer_module

    class FakeSet(list):
        pass

    motors = [Motor(a) for a in range(30)]
    designer = designer_module.WaveDesigner.__new__(designer_module.WaveDesigner)
    designer.model = SimpleNamespace(
        sets=[FakeSet(motors)], mark_unprepared=lambda: None
    )
    designer.shape = waves.SHAPES[0]
    designer.height = SimpleNamespace(get=lambda: 200)
    designer.period = SimpleNamespace(get=lambda: 2.0)
    designer.direction = SimpleNamespace(get=lambda: waves.TRAVELLING)
    designer.result = SimpleNamespace(configure=lambda **kw: None)
    designer.view = SimpleNamespace(status=lambda m: None, refresh_all=lambda: None)
    designer.logger = SimpleNamespace(info=lambda *a: None)

    designer.send()

    applied = dict((m.axis, m.write_params["Curve Offset"]) for m in motors)
    # The Pattern tool's front-to-back stagger for the same period.
    wanted = patterns.build(
        list(range(30)), "Curve Offset", patterns.STAGGER,
        start=0, step=20, across=patterns.ACROSS_COLUMNS,
    ).values
    assert [applied[a] for a in front_axes()] == [wanted[a] for a in front_axes()]
    assert applied[tags.axis_from_display(1)] < applied[tags.axis_from_display(30)]


# --- the shipped presets, generated before the array was renumbered ----------


def test_shallow_water_preset_covers_the_pistons_select_front_four_picks():
    rows = preset_rows("Shallow water - front four columns.csv")
    for axis in front_axes(4):
        row = rows[axis]
        stroke = abs(int(row["Position 2"]) - int(row["Position 1"]))
        assert stroke > 0, (
            "piston {0} (axis {1}) is in the front four columns but the "
            "'front four columns' preset gives it no stroke".format(
                tags.display_number(axis), axis)
        )


def test_travelling_wave_preset_starts_at_the_front():
    rows = preset_rows("Travelling wave - front to back.csv")
    first = int(rows[tags.axis_from_display(1)]["Curve Offset"])
    last = int(rows[tags.axis_from_display(30)]["Curve Offset"])
    assert first < last, (
        "piston 1 (front) has Curve Offset {0} and piston 30 (back) has {1}, "
        "so the wave starts at the back".format(first, last)
    )


def test_tapered_preset_is_largest_at_the_back():
    rows = preset_rows("Tapered - largest at the back.csv")

    def stroke(axis):
        row = rows[axis]
        return abs(int(row["Position 2"]) - int(row["Position 1"]))

    assert stroke(tags.axis_from_display(30)) > stroke(tags.axis_from_display(1))


# --- the last raw axis arithmetic left in the layout -------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Motor.py:103-104 derives row and column from the raw axis, so the "
           "repr is mirrored against the picture",
)
def test_motor_row_and_column_match_where_the_piston_is_drawn():
    for axis in range(30):
        column, row = divmod(tags.display_number(axis) - 1,
                             tags.ROWS_PER_COLUMN)
        motor = Motor(axis)
        assert (motor.row, motor.column) == (row + 1, column + 1)


# --- diagnostics: findings the operator asked for and did not get ------------


def test_a_movement_test_that_found_no_movement_is_always_reported():
    """The operator ran the probe; a routine software note must not hide it."""
    motor = Motor(14)
    motor.set_param("Position 2", 300)   # differs from what the drive holds
    report = diagnostics.diagnose(
        motor, drive(status=HEALTHY, pos1=0, pos2=350), probe=lambda: 0.0
    )
    moved = next(c for c in report.checks if c.name == "Movement when commanded")
    assert moved.verdict == "bad"
    assert "did not move" in headlines(report), headlines(report)


def test_a_large_following_error_is_always_reported():
    motor = Motor(14)
    motor.set_param("Position 2", 300)
    report = diagnostics.diagnose(
        motor, drive(status=HEALTHY, warn=1 << 4, actual=0, demand=200,
                     pos1=0, pos2=350)
    )
    assert "behind its commanded position" in headlines(report), headlines(report)


def test_a_drive_holding_no_stroke_is_never_called_ok():
    motor = Motor(14)
    motor.set_param("Position 1", 0)
    motor.set_param("Position 2", 350)
    report = diagnostics.diagnose(
        motor, drive(status=HEALTHY, pos1=200, pos2=200), expected_live=True
    )
    stroke = next(c for c in report.checks if c.name == "Stroke held on the drive")
    assert stroke.reading == "0 mm"
    assert stroke.verdict != "ok"


def test_an_active_quick_stop_is_reported():
    """``STATUS_BITS[5]`` reads "quick stop is *not* active" when the bit is
    set, so a clear bit 5 means the drive is held in quick stop."""
    from app import drive_status

    held = (1 << 0) | (1 << 11)          # enabled and homed, bit 5 clear
    assert drive_status.summary(0, held) != "homed, no faults"
    assert drive_status.problems(0, held)


@pytest.mark.xfail(
    strict=True,
    reason="diagnostics/Diagnostics.py:163 and 173 iterate axes, so the report "
           "comes out piston 30 first and piston 1 last",
)
def test_diagnose_all_reports_in_display_order():
    from diagnostics.Diagnostics import Diagnostics

    tab = Diagnostics.__new__(Diagnostics)
    asked = []
    tab._start = lambda axes, probe: asked.append(list(axes))
    tab.diagnose_all()
    numbers = [tags.display_number(a) for a in asked[0]]
    assert numbers == sorted(numbers), numbers[:5]
