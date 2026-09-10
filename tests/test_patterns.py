"""Filling a parameter across the array in a shape."""

from __future__ import annotations

import pytest

from app import params, patterns

ALL = list(range(30))


def by_column(result):
    """One value per column, taking the top row of each."""
    return [result.values[axis] for axis in range(0, 30, 3)]


class TestStagger:
    def test_stagger_advances_once_per_column(self):
        """This is what makes a wave travel along the tank instead of
        arriving everywhere at once."""
        result = patterns.build(ALL, "Curve Offset", patterns.STAGGER, start=0, step=10)
        assert by_column(result) == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]

    def test_pistons_in_the_same_column_match(self):
        """A column is one position along the tank; its three pistons are at
        the same place in the wave."""
        result = patterns.build(ALL, "Curve Offset", patterns.STAGGER, start=0, step=10)
        for column_start in range(0, 30, 3):
            values = [result.values[column_start + row] for row in range(3)]
            assert len(set(values)) == 1

    def test_wrap_cycles_the_offset(self):
        result = patterns.build(
            ALL, "Curve Offset", patterns.STAGGER, start=0, step=30, wrap=90
        )
        assert by_column(result) == [0, 30, 60, 0, 30, 60, 0, 30, 60, 0]

    def test_stagger_down_rows(self):
        result = patterns.build(
            ALL, "Curve Offset", patterns.STAGGER, start=0, step=5,
            across=patterns.ACROSS_ROWS,
        )
        assert [result.values[a] for a in (0, 1, 2)] == [0, 5, 10]
        assert result.values[3] == 0  # next column, back to row 1


class TestRampAndMirror:
    def test_ramp_spans_start_to_end(self):
        result = patterns.build(ALL, "Position 2", patterns.RAMP, start=100, end=350)
        columns = by_column(result)
        assert columns[0] == 100
        assert columns[-1] == 350
        assert columns == sorted(columns)

    def test_mirror_is_symmetric(self):
        result = patterns.build(ALL, "Position 2", patterns.MIRROR, start=350, end=100)
        columns = by_column(result)
        assert columns == columns[::-1]
        assert columns[0] < columns[4]  # edges lower than the centre

    def test_uniform_gives_one_value(self):
        result = patterns.build(ALL, "Speed 1", patterns.UNIFORM, start=400)
        assert result.distinct == 1
        assert set(result.values.values()) == {400}

    def test_ramp_over_a_partial_selection_spans_that_selection(self):
        """A ramp should spread over the pistons chosen, not over the whole
        array, or a six-piston set would only use a fifth of the range."""
        axes = [0, 3, 6, 9, 12]
        result = patterns.build(axes, "Position 2", patterns.RAMP, start=0, end=300)
        assert result.values[0] == 0
        assert result.values[12] == 300


class TestLimits:
    def test_values_are_clamped_to_the_parameter_limits(self):
        """A pattern must not be able to write past a limit the entry boxes
        would have refused."""
        result = patterns.build(ALL, "Speed 1", patterns.STAGGER, start=800, step=50)
        assert max(result.values.values()) == params.BY_NAME["Speed 1"].maximum
        assert result.clamped

    def test_clamping_is_reported_per_motor(self):
        result = patterns.build(ALL, "Speed 1", patterns.STAGGER, start=880, step=100)
        assert any("motor" in entry for entry in result.clamped)

    def test_a_clean_pattern_reports_no_clamping(self):
        result = patterns.build(ALL, "Speed 1", patterns.RAMP, start=100, end=800)
        assert result.clamped == []

    def test_negative_positions_are_allowed_down_to_the_limit(self):
        result = patterns.build(ALL, "Position 1", patterns.RAMP, start=-20, end=100)
        assert min(result.values.values()) == -20
        assert result.clamped == []

    def test_unknown_parameter_is_refused(self):
        with pytest.raises(KeyError):
            patterns.build(ALL, "Nonsense", patterns.UNIFORM, start=0)

    def test_unknown_pattern_is_refused(self):
        with pytest.raises(ValueError):
            patterns.build(ALL, "Speed 1", "spiral", start=0)

    def test_no_axes_gives_an_empty_result(self):
        result = patterns.build([], "Speed 1", patterns.UNIFORM, start=0)
        assert result.values == {}


class TestAppliedToASet:
    def test_a_pattern_only_touches_its_own_set(self, model, make_group):
        first = make_group(model, range(0, 6))
        second = make_group(model, range(6, 12))

        result = patterns.build(
            first.axes, "Curve Offset", patterns.STAGGER, start=0, step=10
        )
        for axis, value in result.values.items():
            first.motors[axis].set_param("Curve Offset", value)

        assert first.common_value("Curve Offset") is None  # it varies now
        assert second.common_value("Curve Offset") == 0    # untouched

    def test_patterned_values_reach_the_right_drives(self, model, plc):
        from app import tags

        for axis in range(0, 9):
            model.toggle(axis, True)
        motor_set = model.create_set()
        result = patterns.build(
            motor_set.axes, "Curve Offset", patterns.STAGGER, start=0, step=10
        )
        for axis, value in result.values.items():
            motor_set.motors[axis].set_param("Curve Offset", value)

        for axis in range(30):
            plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
        assert model.prepare()

        # Axis 0 is Curve_1, axis 3 is Curve_4, axis 6 is Curve_7.
        assert plc.read("Program:Wave_Control.Curve_1.CurveOffset") == 0
        assert plc.read("Program:Wave_Control.Curve_4.CurveOffset") == 10
        assert plc.read("Program:Wave_Control.Curve_7.CurveOffset") == 20
