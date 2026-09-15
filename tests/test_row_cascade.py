"""Rows moving out of step with each other.

The array can already be staggered front to back, which makes a wave travel
along the chamber. This is the same idea turned ninety degrees: the three rows
of every column are started at different points in the cycle, so one row sits
near the top of its stroke, one near the middle and one near the bottom, while
all thirty pistons run the identical stroke at the identical speed.

Curve Offset is how that intent is written down, and the Pattern tool can
already set it "Top to bottom (rows 1 - 3)". The part that did not work is that
continuous motion ignores Curve Offset entirely -- the controller only consults
it during a curve run -- so the intent has to be converted into a different
starting position per piston, which is what ``Model.cascade_targets`` does.
It used to collapse every column to a single offset and throw the row
difference away.
"""

from __future__ import annotations

import pytest

from app import patterns, tags, waves


def row_of(axis: int) -> int:
    """0 for the top row of a column, 2 for the bottom."""
    return (tags.display_number(axis) - 1) % tags.ROWS_PER_COLUMN


def column_of(axis: int) -> int:
    return (tags.display_number(axis) - 1) // tags.ROWS_PER_COLUMN


class TestRowOffsets:
    def test_rows_are_spread_evenly_around_the_cycle(self):
        offsets = waves.row_offsets(period=3.0, rows=3)
        assert set(offsets) == {0, 1, 2}
        assert offsets[0] == 0
        # A third and two thirds of a 3 s period, in hundredths of a second.
        assert offsets[1] == 100
        assert offsets[2] == 200

    def test_a_longer_period_spreads_the_rows_further(self):
        short = waves.row_offsets(period=1.0)
        long = waves.row_offsets(period=4.0)
        assert long[1] > short[1]

    def test_the_top_row_always_leads(self):
        offsets = waves.row_offsets(period=2.0)
        assert offsets[0] < offsets[1] < offsets[2]


class TestStagingAcrossRows:
    """The conversion continuous motion actually honours.

    Curve Offset is an integer in hundredths of a second, so a third of a cycle
    rarely lands exactly: a 0.5 s period gives offsets 0, 17 and 33, and 17/33
    is 0.515 rather than 0.5. That is a 1.5% error on one leg of the stroke --
    a couple of millimetres out of hundreds -- so the tolerances below are the
    real requirement, not a weakened one. The ends must be exact; the middle
    row only has to be near the middle.
    """

    def test_three_rows_land_at_the_bottom_middle_and_top(self):
        fractions = waves.cascade_fractions(waves.row_offsets(period=2.0))
        assert fractions[0] == 0.0
        assert fractions[1] == pytest.approx(0.5, abs=0.02)
        assert fractions[2] == 1.0

    def test_the_starting_positions_span_the_whole_stroke(self):
        starts = waves.cascade_starts(
            waves.row_offsets(period=2.0), position_1=0, position_2=300
        )
        assert starts[0] == 0                              # bottom
        assert starts[1] == pytest.approx(150, abs=6)      # half way up
        assert starts[2] == 300                            # top

    def test_the_spread_is_the_same_whatever_the_period(self):
        """The period sets the offsets, but staging normalises to one leg.

        Worth pinning: it means an operator cannot accidentally ask for a row
        stagger wider than the machine can produce in continuous motion. A
        six-second period and a half-second one stage identically.
        """
        for period in (0.5, 2.0, 6.0):
            starts = waves.cascade_starts(
                waves.row_offsets(period=period), position_1=0, position_2=200
            )
            assert starts[0] == 0, period
            assert starts[1] == pytest.approx(100, abs=4), period
            assert starts[2] == 200, period


class TestThePatternToolCanExpressIt:
    """The operator-facing route: Pattern -> Curve Offset -> Top to bottom."""

    def test_a_row_stagger_gives_each_row_its_own_offset(self):
        every = list(range(tags.MOTOR_COUNT))
        result = patterns.build(
            every, "Curve Offset", patterns.STAGGER,
            start=0, step=100, across=patterns.ACROSS_ROWS,
        )
        by_row = {}
        for axis, value in result.values.items():
            by_row.setdefault(row_of(axis), set()).add(value)

        # Every piston in a row gets the same offset...
        assert all(len(values) == 1 for values in by_row.values())
        # ...and the three rows differ.
        assert len({next(iter(v)) for v in by_row.values()}) == 3

    def test_pistons_in_the_same_column_are_no_longer_identical(self):
        """The case that used to be lost: a stagger within one column."""
        every = list(range(tags.MOTOR_COUNT))
        result = patterns.build(
            every, "Curve Offset", patterns.STAGGER,
            start=0, step=100, across=patterns.ACROSS_ROWS,
        )
        front_column = [a for a in every if column_of(a) == 0]
        assert len(front_column) == tags.ROWS_PER_COLUMN
        offsets = {result.values[a] for a in front_column}
        assert len(offsets) == tags.ROWS_PER_COLUMN, (
            "pistons 1, 2 and 3 share a column and must still differ"
        )


class TestTheWaveTabCanSendIt:
    """The other operator-facing route: Wave -> MOVES -> Rows out of step."""

    def _designer(self, motors, direction):
        from types import SimpleNamespace

        from wave import WaveDesigner as designer_module

        class FakeSet(list):
            pass

        designer = designer_module.WaveDesigner.__new__(
            designer_module.WaveDesigner)
        designer.model = SimpleNamespace(
            sets=[FakeSet(motors)], mark_unprepared=lambda: None
        )
        designer.shape = waves.SHAPES[0]
        designer.height = SimpleNamespace(get=lambda: 200)
        designer.period = SimpleNamespace(get=lambda: 2.0)
        designer.direction = SimpleNamespace(get=lambda: direction)
        designer.result = SimpleNamespace(configure=lambda **kw: None)
        designer.view = SimpleNamespace(status=lambda m: None,
                                        refresh_all=lambda: None)
        designer.logger = SimpleNamespace(info=lambda *a: None)
        designer.send()
        return designer

    def test_a_cascade_gives_each_row_its_own_offset(self):
        from Motor import Motor

        motors = [Motor(a) for a in range(tags.MOTOR_COUNT)]
        self._designer(motors, waves.CASCADING)

        by_row = {}
        for motor in motors:
            by_row.setdefault(
                row_of(motor.axis), set()
            ).add(motor.write_params["Curve Offset"])

        assert all(len(v) == 1 for v in by_row.values()), (
            "every piston in a row must share one offset"
        )
        assert len({next(iter(v)) for v in by_row.values()}) == 3, (
            "the three rows must differ, or nothing is out of step"
        )

    def test_a_cascade_is_not_turned_into_a_curve(self):
        """Continuous motion stages it; only a travelling wave needs a curve.

        Setting Curve ID here would send the operator to Start Curve for a
        design that an ordinary Start handles perfectly well.
        """
        from Motor import Motor

        motors = [Motor(a) for a in range(tags.MOTOR_COUNT)]
        before = {m.axis: m.write_params["Curve ID"] for m in motors}
        self._designer(motors, waves.CASCADING)
        assert all(m.write_params["Curve ID"] == before[m.axis]
                   for m in motors)

    def test_a_travelling_wave_still_staggers_by_column(self):
        """The mode that already worked must not have been disturbed."""
        from Motor import Motor

        motors = [Motor(a) for a in range(tags.MOTOR_COUNT)]
        self._designer(motors, waves.TRAVELLING)

        front = [m for m in motors if column_of(m.axis) == 0]
        assert len({m.write_params["Curve Offset"] for m in front}) == 1, (
            "a front-to-back wave must not stagger within a column"
        )
        assert all(m.write_params["Curve ID"] == 1 for m in motors)


class TestTheStagingHonoursIt:
    """Model.cascade_targets: the part continuous motion actually reads."""

    def _model(self, across, step=100):
        from Model import Model

        model = Model(simulate=True)
        model.set_selection(list(range(tags.MOTOR_COUNT)))
        model.create_set("cascade")
        result = patterns.build(
            list(range(tags.MOTOR_COUNT)), "Curve Offset", patterns.STAGGER,
            start=0, step=step, across=across,
        )
        for motor in model.all_motors:
            motor.set_param("Position 1", 0)
            motor.set_param("Position 2", 300)
            motor.set_param("Curve Offset", result.values[motor.axis])
        return model

    def test_a_row_stagger_starts_the_three_rows_at_different_heights(self):
        targets = self._model(patterns.ACROSS_ROWS).cascade_targets()
        by_row = {}
        for axis, target in targets.items():
            by_row.setdefault(row_of(axis), set()).add(target)
        assert by_row[0] == {0}, "the top row should start at Position 1"
        assert by_row[1] == {150}, "the middle row should start half way up"
        assert by_row[2] == {300}, "the bottom row should start at Position 2"

    def test_pistons_sharing_a_column_no_longer_stage_identically(self):
        """The defect: a column was collapsed to one offset and rows lost.

        Pistons 1, 2 and 3 are one column, so this is the case that used to
        come out as three pistons starting in exactly the same place.
        """
        targets = self._model(patterns.ACROSS_ROWS).cascade_targets()
        front = [targets[a] for a in range(tags.MOTOR_COUNT)
                 if column_of(a) == 0]
        assert sorted(front) == [0, 150, 300]

    def test_a_front_to_back_stagger_still_stages_by_column(self):
        """The mode that already worked must be unchanged by keying per axis.

        A front-to-back stagger gives every piston in a column the same Curve
        Offset, so reading each piston's own value lands on the same answer.
        """
        targets = self._model(patterns.ACROSS_COLUMNS, step=20).cascade_targets()
        for column in range(tags.COLUMN_COUNT):
            shared = {targets[a] for a in range(tags.MOTOR_COUNT)
                      if column_of(a) == column}
            assert len(shared) == 1, (
                "column {0} staged unevenly: {1}".format(column, shared)
            )
        front = next(targets[a] for a in range(tags.MOTOR_COUNT)
                     if column_of(a) == 0)
        back = next(targets[a] for a in range(tags.MOTOR_COUNT)
                    if column_of(a) == tags.COLUMN_COUNT - 1)
        assert front < back, "the wave must still lead from the front"

    def test_no_stagger_means_no_staging_at_all(self):
        """The common case, and it must stay free."""
        model = self._model(patterns.ACROSS_ROWS, step=0)
        assert model.cascade_targets() == {}
