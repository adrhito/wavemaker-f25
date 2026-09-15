"""End-to-end stress of long and extreme runs, against the mock only.

Nothing here contacts a PLC: every test uses :class:`app.plc.SimulatedPlc` or
:class:`app.simulator.SimulatedMachine`.

Two kinds of test live here.

* Plain tests assert behaviour that already holds. They are the evidence that
  a stressed area came back clean, and they are there to keep it clean.
* ``xfail(strict=True)`` tests assert the behaviour the code *should* have and
  fail today. They are evidence of a defect, not a fix -- remove the marker
  when the defect is repaired, and the test will start guarding it.

The deterministic simulator
---------------------------
:func:`frozen` returns a :class:`~app.simulator.SimulatedMachine` whose stepping
thread has been stopped, so a test can advance simulated time by calling
``_step(dt)`` itself. Wall-clock sleeps make timing-sensitive assertions flaky;
stepping by hand makes them exact.
"""

from __future__ import annotations

import math
import random
import threading
import time

import pytest

import Model as model_module
from app import params, patterns, tags, waves
from app.plc import PlcError, SimulatedPlc
from app.simulator import HOME_POSITION, SimulatedMachine
from Model import MachineState, Model, RunMode
from Motor import Motor

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

#: A full cycle of a piston driven between these two positions takes 1.0 s.
FAST = dict(pos1=0, pos2=150, speed=300)


@pytest.fixture
def frozen():
    """A simulated machine that only moves when the test tells it to."""
    machine = SimulatedMachine(tick=0.01)
    machine.close()          # stop the stepping thread; the tag store remains
    time.sleep(0.05)         # let the thread notice
    yield machine


def arm(machine, axes=range(tags.MOTOR_COUNT), **kwargs):
    """Select some pistons on the mock and give them a stroke."""
    settings = dict(FAST, **kwargs)
    for axis in axes:
        machine.write(tags.live_motor(axis), 1)
        machine.write(tags.motor_field(axis, "Pos_1"), settings["pos1"])
        machine.write(tags.motor_field(axis, "Pos_2"), settings["pos2"])
        machine.write(tags.motor_field(axis, "Spd_1"), settings["speed"])
        machine.write(tags.motor_field(axis, "Spd_2"), settings["speed"])


def advance(machine, seconds, dt=0.01):
    """Run the frozen machine forward by hand."""
    for _ in range(int(round(seconds / dt))):
        machine._step(dt)


def ready_model(plc, axes=(0, 1, 2), **parameters):
    """A model with one homed group, running its commands inline."""
    model = Model(transport=plc, is_live=True)
    model._spawn = lambda name, work: work()
    for axis in axes:
        model.toggle(axis, True)
    model.create_set()
    for motor in model.all_motors:
        for name, value in parameters.items():
            motor.set_param(name, value)
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    assert model.prepare() is True
    assert model.state is MachineState.HOMED
    return model


# ==========================================================================
# 1. Parameter extremes and boundaries
# ==========================================================================


class TestParameterLimits:
    """Every parameter driven to its limits and one step past them."""

    @pytest.mark.parametrize("spec", params.PARAMS, ids=lambda s: s.name)
    def test_the_stated_limits_are_exactly_the_accepted_ones(self, spec):
        motor = Motor(0)
        if spec.minimum is not None:
            motor.set_param(spec.name, spec.minimum)
            with pytest.raises(ValueError):
                motor.set_param(spec.name, spec.minimum - 1)
        if spec.maximum is not None:
            motor.set_param(spec.name, spec.maximum)
            with pytest.raises(ValueError):
                motor.set_param(spec.name, spec.maximum + 1)

    def test_a_zero_and_a_full_travel_stroke_are_both_accepted(self):
        """0, 1 mm and the whole travel are all legal strokes."""
        motor = Motor(0)
        for low, high in ((0, 0), (0, 1), (-20, 370), (370, 370)):
            motor.set_param("Position 1", low)
            motor.set_param("Position 2", high)
            assert motor.validate() == []

    def test_beyond_the_travel_is_rejected_at_both_ends(self):
        motor = Motor(0)
        for name in ("Position 1", "Position 2"):
            with pytest.raises(ValueError):
                motor.set_param(name, -21)
            with pytest.raises(ValueError):
                motor.set_param(name, 371)

    def test_position_one_above_position_two_is_accepted(self):
        """Documented, not endorsed: an inverted stroke passes validation.

        Nothing rejects it and nothing corrects it; the machine is simply told
        to travel downwards first. Recorded here so a future change that starts
        rejecting it is a deliberate one.
        """
        motor = Motor(0)
        motor.set_param("Position 1", 300)
        motor.set_param("Position 2", 10)
        assert motor.validate() == []

    def test_parse_rejects_everything_that_is_not_a_whole_number(self):
        for text in ("", "   ", "3.0", "1e3", "0x10", "true", "371", "-21"):
            with pytest.raises(ValueError):
                params.parse("Position 1", text)

    def test_parse_accepts_the_limits_and_surrounding_whitespace(self):
        assert params.parse("Position 1", " -20 ") == -20
        assert params.parse("Position 2", "370") == 370
        assert params.parse("Speed 1", "0") == 0
        assert params.parse("Speed 1", "900") == 900

    @pytest.mark.xfail(
        strict=True,
        reason="app/params.py:110 compares with < and >, and every comparison "
               "against NaN is False, so a NaN passes validation and would be "
               "written to the drive",
    )
    def test_a_not_a_number_position_is_rejected(self):
        spec = params.BY_NAME["Position 2"]
        assert spec.validate(float("nan")) is not None

    @pytest.mark.xfail(
        strict=True,
        reason="app/params.py:110 never checks the type, so Motor.set_param "
               "stores a float and Motor.write_to sends it to a DINT tag",
    )
    def test_a_fractional_value_is_rejected(self):
        motor = Motor(0)
        with pytest.raises(ValueError):
            motor.set_param("Position 1", 3.7)

    @pytest.mark.xfail(
        strict=True,
        reason="app/params.py:157-173 leaves Jerk, Time, Curve ID, Time Scale, "
               "Amplitude Scale and Curve Offset with maximum=None, so a value "
               "far beyond a ControlLogix DINT is accepted",
    )
    @pytest.mark.parametrize(
        "name",
        ["Jerk 1", "Jerk 2", "Time 1", "Time 2", "Curve ID", "Time Scale",
         "Amplitude Scale", "Curve Offset"],
    )
    def test_the_unbounded_parameters_still_reject_an_impossible_value(self, name):
        spec = params.BY_NAME[name]
        assert spec.validate(2 ** 31) is not None, (
            "{0} has no upper limit at all".format(name)
        )

    @pytest.mark.xfail(
        strict=True,
        reason="app/params.py:151-152 allow Speed 0, and Model._stroke_seconds "
               "then floors the speed at 1 mm/s, so a single stroke holds Run_1 "
               "high for the full MAX_STROKE_SECONDS while nothing moves",
    )
    def test_a_zero_speed_does_not_produce_a_two_minute_stroke(self, plc):
        model = Model(transport=plc, is_live=True)
        model._spawn = lambda name, work: work()
        for axis in (0, 1, 2):
            model.toggle(axis, True)
        model.create_set()
        for motor in model.all_motors:
            motor.set_param("Position 2", 350)
            motor.set_param("Speed 1", 0)
            motor.set_param("Speed 2", 0)
        assert model._stroke_seconds() < model_module.MAX_STROKE_SECONDS

    def test_validate_all_reports_a_missing_parameter_rather_than_ignoring_it(self):
        values = params.defaults()
        del values["Speed 1"]
        assert any("Speed 1" in problem for problem in params.validate_all(values))

    def test_model_rejects_an_out_of_range_pending_parameter(self, model):
        with pytest.raises(ValueError):
            model.set_pending_param("Speed 1", 901)
        with pytest.raises(KeyError):
            model.set_pending_param("Not A Parameter", 1)


# ==========================================================================
# 2. Wave and pattern mathematics
# ==========================================================================


class TestWaveMaths:
    """The period/phase arithmetic has to agree with itself."""

    @pytest.mark.parametrize("height", [20, 80, 200, 350, 390])
    @pytest.mark.parametrize("period", [0.4, 1.0, 2.0, 6.0])
    def test_the_period_a_design_asks_for_is_the_period_the_machine_runs(
        self, height, period, plc
    ):
        """speed_for, achievable_period and Model._stroke_seconds must agree."""
        shape = waves.BY_KEY["swell"]
        values = waves.to_parameters(shape, height, period)
        assert params.validate_all(values) == []

        model = Model(transport=plc, is_live=True)
        model._spawn = lambda name, work: work()
        model.toggle(0, True)
        model.create_set()
        model.all_motors[0].update_params(values)

        # A cycle is out and back, which is what _stroke_seconds measures
        # before it applies its floor and its safety margin.
        cycle = 2.0 * waves.clamp_height(height) / float(values["Speed 1"])
        assert cycle == pytest.approx(waves.achievable_period(height, period), rel=0.02)
        assert model._stroke_seconds(floor=0.0) == pytest.approx(cycle * 1.5, rel=0.02)

    def test_a_design_that_cannot_run_as_fast_as_asked_says_so(self):
        assert waves.is_limited(390, 0.4) is True
        assert waves.is_limited(80, 2.0) is False

    def test_column_offsets_rise_steadily_across_the_chamber(self):
        offsets = waves.column_offsets(1.6)
        ordered = [offsets[c] for c in range(10)]
        assert ordered == sorted(ordered)
        assert ordered[0] == 0
        assert len(set(ordered)) == 10

    @pytest.mark.xfail(
        strict=True,
        reason="app/waves.py:229 builds the offset as period * 100 while "
               "operate/Operate.py:863 and modules/wave_preview.py:121 read it "
               "as milliseconds -- the preview shows a tenth of the stagger",
    )
    def test_a_column_offset_means_the_same_number_of_seconds_everywhere(self):
        period = 1.6
        offsets = waves.column_offsets(period)
        # The back column is meant to lag the front by nine tenths of a cycle.
        wanted = 0.9 * period
        assert offsets[9] / 1000.0 == pytest.approx(wanted, rel=0.01)

    @pytest.mark.xfail(
        strict=True,
        reason="app/waves.py:233-258 maps the LARGEST Curve Offset to the "
               "LARGEST fraction of the stroke. A larger offset is a longer "
               "delay (modules/wave_preview.py:119), so the column that should "
               "arrive last is staged furthest ahead and the wave runs backwards",
    )
    def test_a_longer_delay_is_staged_further_back_in_the_cycle(self):
        fractions = waves.cascade_fractions({0: 0, 1: 50, 2: 100})
        assert fractions[0] > fractions[2], (
            "column 2 has the longest delay, so it must start least far along "
            "its stroke, not furthest: {0}".format(fractions)
        )

    @pytest.mark.xfail(
        strict=True,
        reason="app/waves.py:251-257 normalises by the spread, so the size of "
               "the stagger is discarded: a one-unit offset and a huge one "
               "produce exactly the same staging",
    )
    def test_the_size_of_the_stagger_changes_the_size_of_the_cascade(self):
        small = waves.cascade_fractions({0: 0, 9: 1})
        large = waves.cascade_fractions({0: 0, 9: 100000})
        assert small != large

    def test_cascade_starts_stay_inside_the_stroke_however_it_is_written(self):
        offsets = waves.column_offsets(1.0)
        for low, high in ((0, 350), (350, 0), (-20, 370), (100, 100)):
            starts = waves.cascade_starts(offsets, low, high)
            for column, where in starts.items():
                assert min(low, high) <= where <= max(low, high), (
                    "column {0} staged at {1}, outside {2}..{3}".format(
                        column, where, low, high)
                )

    def test_no_stagger_means_no_cascade(self):
        assert waves.cascade_fractions({}) == {}
        assert waves.cascade_fractions({0: 5, 1: 5}) == {}
        assert waves.cascade_starts({0: 5, 1: 5}, 0, 350) == {}

    @pytest.mark.parametrize("height", [-100, 0, 19, 20, 390, 391, 10 ** 6])
    def test_every_height_produces_a_stroke_inside_the_travel(self, height):
        low, high = waves.stroke_for(height)
        assert params.BY_NAME["Position 1"].validate(low) is None
        assert params.BY_NAME["Position 2"].validate(high) is None
        assert low <= high

    @pytest.mark.parametrize("period", [-5.0, 0.0, 0.39, 0.4, 6.0, 6.1, 10 ** 6])
    def test_every_period_produces_a_legal_speed(self, period):
        speed = waves.speed_for(200, period)
        assert params.BY_NAME["Speed 1"].validate(speed) is None

    @pytest.mark.xfail(
        strict=True,
        reason="app/waves.py:228 divides by wavelength without checking it, so "
               "a zero wavelength raises ZeroDivisionError instead of being "
               "rejected",
    )
    def test_a_zero_wavelength_is_rejected_rather_than_crashing(self):
        with pytest.raises(ValueError):
            waves.column_offsets(1.0, wavelength=0)


class TestPatternsAcrossTheArray:
    """A pattern has to land on the pistons the operator pointed at."""

    def test_every_selected_piston_gets_exactly_one_value(self):
        axes = list(range(tags.MOTOR_COUNT))
        result = patterns.build(axes, "Speed 1", patterns.RAMP, 100, end=800)
        assert sorted(result.values) == axes

    def test_a_column_pattern_gives_the_three_pistons_of_a_column_one_value(self):
        result = patterns.build(
            range(tags.MOTOR_COUNT), "Curve Offset", patterns.STAGGER,
            start=0, step=40, across=patterns.ACROSS_COLUMNS,
        )
        for column in range(tags.COLUMN_COUNT):
            numbers = range(column * 3 + 1, column * 3 + 4)
            axes = [tags.axis_from_display(n) for n in numbers]
            assert len(set(result.values[a] for a in axes)) == 1, (
                "display column {0} disagrees".format(column + 1)
            )

    def test_a_row_pattern_gives_each_row_one_value_across_the_chamber(self):
        result = patterns.build(
            range(tags.MOTOR_COUNT), "Speed 1", patterns.RAMP, 100, end=700,
            across=patterns.ACROSS_ROWS,
        )
        for row in range(tags.ROWS_PER_COLUMN):
            axes = [tags.axis_from_display(n)
                    for n in range(row + 1, 31, tags.ROWS_PER_COLUMN)]
            assert len(set(result.values[a] for a in axes)) == 1

    def test_a_ramp_hits_both_ends_exactly(self):
        result = patterns.build(
            range(tags.MOTOR_COUNT), "Speed 1", patterns.RAMP, 100, end=800,
            across=patterns.ACROSS_COLUMNS,
        )
        assert result.values[tags.axis_from_display(1)] == 100
        assert result.values[tags.axis_from_display(30)] == 800

    def test_a_mirror_is_symmetric_about_the_middle(self):
        result = patterns.build(
            range(tags.MOTOR_COUNT), "Speed 1", patterns.MIRROR, 100, end=500,
            across=patterns.ACROSS_COLUMNS,
        )
        for column in range(tags.COLUMN_COUNT):
            near = result.values[tags.axis_from_display(column * 3 + 1)]
            far = result.values[tags.axis_from_display((9 - column) * 3 + 1)]
            assert near == far

    def test_a_stagger_that_would_leave_the_limits_is_clamped_and_named(self):
        result = patterns.build(
            range(tags.MOTOR_COUNT), "Speed 1", patterns.STAGGER,
            start=800, step=200, across=patterns.ACROSS_COLUMNS,
        )
        assert max(result.values.values()) == params.BY_NAME["Speed 1"].maximum
        assert result.clamped, "clamping must be reported, not silent"

    def test_a_wrapped_stagger_cycles_instead_of_growing(self):
        result = patterns.build(
            range(tags.MOTOR_COUNT), "Curve Offset", patterns.STAGGER,
            start=0, step=40, wrap=100, across=patterns.ACROSS_COLUMNS,
        )
        assert max(result.values.values()) < 100
        assert not result.clamped

    def test_a_pattern_over_one_piston_is_just_that_value(self):
        for pattern in (patterns.UNIFORM, patterns.RAMP, patterns.STAGGER,
                        patterns.MIRROR):
            result = patterns.build([5], "Speed 1", pattern, 123, end=456, step=7)
            assert result.values == {5: 123}

    def test_an_unknown_parameter_or_pattern_is_refused(self):
        with pytest.raises(KeyError):
            patterns.build([0], "Nope", patterns.UNIFORM, 1)
        with pytest.raises(ValueError):
            patterns.build([0], "Speed 1", "nope", 1)

    @pytest.mark.xfail(
        strict=True,
        reason="app/patterns.py:75-81 falls through to selection order for any "
               "unrecognised direction, so a typo silently produces a different "
               "pattern instead of being refused",
    )
    def test_an_unknown_direction_is_refused(self):
        with pytest.raises(ValueError):
            patterns.build([0, 1, 2], "Speed 1", patterns.RAMP, 1, end=9,
                           across="sideways")


# ==========================================================================
# 3. The mock itself
# ==========================================================================


class TestTheMockUnderLoad:
    def test_a_long_run_does_not_let_the_pistons_drift_apart(self, frozen):
        """Hundreds of cycles, and identical pistons must stay identical."""
        arm(frozen)
        frozen.write(tags.RUN_CONTINUOUS, 1)
        advance(frozen, 300.0, dt=0.01)      # 300 cycles of a 1 s wave
        snapshot = frozen.snapshot()
        assert max(snapshot.values()) - min(snapshot.values()) == 0.0
        for where in snapshot.values():
            assert FAST["pos1"] <= where <= FAST["pos2"]

    def test_a_staggered_array_holds_its_phase_over_hundreds_of_cycles(self, frozen):
        """Staggered pistons must keep the phase relationship they started with.

        Asserted through cycle time rather than through position. Comparing
        ``late[axis] - early[axis]`` only works when the elapsed time happens
        to be a whole number of periods -- otherwise each piston is caught at a
        different point of its cycle and the positions differ for a reason that
        has nothing to do with drift. That assertion passed by coincidence
        while the mock moved at a constant speed, and broke the moment the
        simulator started modelling acceleration and the period changed.

        Pistons on the same period cannot drift apart; pistons on different
        periods must. So measure the period.
        """
        arm(frozen)
        for axis in range(tags.MOTOR_COUNT):
            frozen.place(axis, (axis // 3) * 15.0)
        frozen.write(tags.RUN_CONTINUOUS, 1)

        tracks = {axis: [] for axis in range(tags.MOTOR_COUNT)}
        for _ in range(30000):                     # 300 s at 10 ms
            advance(frozen, 0.01, dt=0.01)
            shot = frozen.snapshot()
            for axis in tracks:
                tracks[axis].append(shot[axis])

        def period_samples(series):
            """Samples between direction changes, averaged over the run."""
            marks, rising, anchor = [], None, series[0]
            for index, value in enumerate(series):
                if abs(value - anchor) < 1.0:
                    continue
                now = value > anchor
                if rising is not None and now != rising:
                    marks.append(index)
                rising, anchor = now, value
            if len(marks) < 3:
                return None
            return (marks[-1] - marks[0]) / ((len(marks) - 1) / 2.0)

        periods = {a: period_samples(t) for a, t in tracks.items()}
        measured = [p for p in periods.values() if p]
        assert len(measured) == tags.MOTOR_COUNT, "some pistons never cycled"
        assert max(measured) - min(measured) <= 1.0, (
            "columns are on different periods, so they must drift apart: "
            "{0}".format(sorted(set(round(p, 2) for p in measured)))
        )

    def test_a_piston_outside_the_selection_never_moves(self, frozen):
        arm(frozen, axes=[0])
        arm(frozen, axes=[1])
        frozen.write(tags.live_motor(1), 0)
        frozen.write(tags.RUN_CONTINUOUS, 1)
        moved = False
        for _ in range(5000):
            frozen._step(0.01)
            moved = moved or frozen.snapshot()[0] != HOME_POSITION
            assert frozen.snapshot()[1] == HOME_POSITION
        assert moved, "the selected piston never moved"

    def test_a_single_stroke_finishes_back_at_position_one(self, frozen):
        arm(frozen, axes=[0])
        frozen.write(tags.RUN_SINGLE, 1)
        advance(frozen, 5.0)
        assert frozen.snapshot()[0] == pytest.approx(FAST["pos1"], abs=0.01)

    def test_a_zero_length_stroke_neither_moves_nor_hangs(self, frozen):
        arm(frozen, axes=[0], pos1=200, pos2=200)
        frozen.place(0, 200)
        frozen.write(tags.RUN_CONTINUOUS, 1)
        advance(frozen, 20.0)
        assert frozen.snapshot()[0] == 200

    def test_a_zero_speed_is_floored_rather_than_stopping_the_mock(self, frozen):
        """Recorded because it hides the zero-speed defect from the mock.

        The mock floors Speed at 1 mm/s, so a piston told to move at zero still
        creeps. On the machine it would simply sit there.
        """
        arm(frozen, axes=[0], speed=0)
        frozen.write(tags.RUN_CONTINUOUS, 1)
        advance(frozen, 10.0)
        assert frozen.snapshot()[0] == pytest.approx(10.0, abs=0.2)

    def test_an_inverted_stroke_still_runs_between_the_two_positions(self, frozen):
        arm(frozen, axes=[0], pos1=300, pos2=50)
        frozen.place(0, 300)
        frozen.write(tags.RUN_CONTINUOUS, 1)
        advance(frozen, 40.0)
        assert 50 <= frozen.snapshot()[0] <= 300

    def test_positions_are_published_in_drive_counts(self, frozen):
        arm(frozen, axes=[0])
        frozen.write(tags.RUN_CONTINUOUS, 1)
        advance(frozen, 0.5)
        published = frozen.read(tags.axis_field(0, tags.ACTUAL_POSITION))
        assert published == pytest.approx(
            params.to_counts(frozen.snapshot()[0]), abs=1
        )

    def test_clearing_faults_forgets_that_the_pistons_were_homed(self, frozen):
        arm(frozen, axes=[0])
        frozen.write(tags.HOME_BUTTON, 1)
        advance(frozen, 5.0)
        assert frozen.read(tags.axis_field(0, tags.STATUS_WORD)) == 1 << 11
        frozen.write(tags.CLEAR_MOTOR_ERROR, 1)
        frozen._publish()
        assert frozen.read(tags.axis_field(0, tags.STATUS_WORD)) == 0

    @pytest.mark.xfail(
        strict=True,
        reason="app/simulator.py:115-118 clears _thread without joining it and "
               "start() then clears the same _stop event the old thread is "
               "watching, so every close/start pair adds another thread "
               "stepping the same pistons -- motion runs at a multiple of the "
               "commanded speed",
    )
    def test_restarting_the_mock_does_not_leave_two_machines_running(self):
        machine = SimulatedMachine(tick=0.01)
        try:
            for _ in range(4):
                machine.close()
                machine.start()
            time.sleep(0.1)
            alive = [t for t in threading.enumerate()
                     if t.name == "SimulatedMachine" and t.is_alive()]
            assert len(alive) == 1, "{0} stepping threads".format(len(alive))
        finally:
            machine.close()
            time.sleep(0.1)


# ==========================================================================
# 4. The travelling wave, end to end
# ==========================================================================


class TestTravellingWaveBehaviour:
    def test_a_column_stagger_spreads_the_array_along_the_stroke(self, model):
        for axis in range(tags.MOTOR_COUNT):
            model.toggle(axis, True)
        model.create_set()
        built = patterns.build(
            range(tags.MOTOR_COUNT), "Curve Offset", patterns.STAGGER,
            start=0, step=40, across=patterns.ACROSS_COLUMNS,
        )
        for motor in model.all_motors:
            motor.set_param("Position 1", 0)
            motor.set_param("Position 2", 300)
            motor.set_param("Curve Offset", built.values[motor.axis])

        targets = model.cascade_targets()
        assert len(targets) == tags.MOTOR_COUNT
        assert min(targets.values()) == 0
        assert max(targets.values()) == 300
        # One value per column, and the three pistons of a column agree.
        by_column = {}
        for axis, target in targets.items():
            by_column.setdefault(axis // tags.ROWS_PER_COLUMN, set()).add(target)
        assert all(len(v) == 1 for v in by_column.values())
        assert len(by_column) == tags.COLUMN_COUNT

    @pytest.mark.xfail(
        strict=True,
        reason="Model.cascade_targets (Model.py:1560-1573) inherits the "
               "inversion in waves.cascade_fractions: piston 1, which the "
               "pattern gives the shortest delay, is staged at the START of "
               "the stroke and so lags the whole array instead of leading it",
    )
    def test_the_front_of_the_chamber_leads_a_front_to_back_wave(self, model):
        for axis in range(tags.MOTOR_COUNT):
            model.toggle(axis, True)
        model.create_set()
        built = patterns.build(
            range(tags.MOTOR_COUNT), "Curve Offset", patterns.STAGGER,
            start=0, step=40, across=patterns.ACROSS_COLUMNS,
        )
        for motor in model.all_motors:
            motor.set_param("Position 1", 0)
            motor.set_param("Position 2", 300)
            motor.set_param("Curve Offset", built.values[motor.axis])

        targets = model.cascade_targets()
        front = targets[tags.axis_from_display(1)]
        back = targets[tags.axis_from_display(30)]
        # Everything sets off towards Position 2 together, so the piston
        # staged furthest along gets there first. The front must be that one.
        assert front > back, (
            "front staged at {0}, back at {1}: the back leads".format(front, back)
        )

    @pytest.mark.xfail(
        strict=True,
        reason="app/simulator.py:140-158 only resets a piston's direction on a "
               "tick with every run bit low. Model._stage_cascade drops Run_1 "
               "and raises Run_2 with no tick in between, so whether the array "
               "sets off up or down -- and therefore which way the wave travels "
               "-- depends on scheduling",
    )
    def test_the_direction_of_travel_does_not_depend_on_scheduling(self, frozen):
        """Stage, stop the staging move, start the run: which way do they go?"""
        arm(frozen, axes=[0], pos1=0, pos2=300, speed=300)

        def first_direction(tick_between):
            frozen.place(0, 150)
            frozen.write(tags.RUN_SINGLE, 1)
            advance(frozen, 3.0)            # the staging move, as Model runs it
            frozen.write(tags.RUN_SINGLE, 0)
            if tick_between:
                frozen._step(0.01)          # a tick with nothing running
            frozen.place(0, 150)
            frozen.write(tags.RUN_CONTINUOUS, 1)
            frozen._step(0.01)
            where = frozen.snapshot()[0]
            frozen.write(tags.RUN_CONTINUOUS, 0)
            return "up" if where > 150 else "down"

        assert first_direction(True) == first_direction(False)

    def test_an_unstaggered_array_needs_no_staging_at_all(self, model):
        for axis in range(tags.MOTOR_COUNT):
            model.toggle(axis, True)
        model.create_set()
        assert model.cascade_targets() == {}


# ==========================================================================
# 5. Operator abuse
# ==========================================================================


class TestRapidOperatorAbuse:
    def test_forty_start_stop_pairs_leave_a_runnable_machine(self, plc):
        model = ready_model(plc)
        for _ in range(40):
            model.run(RunMode.CONTINUOUS)
            model.stop(immediate=True, park=False)
        assert model.state is MachineState.HOMED
        assert model.busy is False
        for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE,
                    tags.HOME_BUTTON):
            assert plc.read(tag) == 0
        assert model.run(RunMode.CONTINUOUS) is True
        model.stop(immediate=True, park=False)

    def test_stop_pressed_twice_is_harmless(self, plc):
        model = ready_model(plc)
        model.run(RunMode.CONTINUOUS)
        assert model.stop() is True
        assert model.stop() is True
        assert model.busy is False
        assert model.run(RunMode.CONTINUOUS) is True
        model.stop(immediate=True, park=False)

    def test_stop_during_preparing_leaves_the_machine_usable(self, plc):
        model = Model(transport=plc, is_live=True)
        model._spawn = lambda name, work: work()
        for axis in (0, 1, 2):
            model.toggle(axis, True)
        model.create_set()

        stopped = []
        original = model._write_all_parameters

        def stop_halfway():
            if not stopped:
                stopped.append(True)
                model.stop(immediate=True, park=False)
            original()

        model._write_all_parameters = stop_halfway
        model.prepare()
        assert stopped, "the interruption point must be exercised"
        assert model.state is MachineState.READY
        assert model.busy is False
        assert plc.read(tags.HOME_BUTTON) == 0

        # And the machine is still preparable afterwards.
        model._write_all_parameters = original
        for axis in range(tags.MOTOR_COUNT):
            plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
        assert model.prepare() is True
        assert model.state is MachineState.HOMED

    def test_stop_during_homing_drops_the_home_bit(self, plc):
        model = Model(transport=plc, is_live=True)
        model._spawn = lambda name, work: work()
        for axis in (0, 1, 2):
            model.toggle(axis, True)
        model.create_set()

        original = model.plc.write
        fired = []

        def watch(tag, value):
            original(tag, value)
            if tag == tags.HOME_BUTTON and value == 1 and not fired:
                fired.append(True)
                model.stop(immediate=True, park=False)

        model.plc.write = watch
        model.prepare()
        model.plc.write = original
        assert fired, "homing must actually have started"
        assert plc.read(tags.HOME_BUTTON) == 0
        assert model.state is MachineState.READY
        assert model.busy is False

    def test_stop_during_the_parking_move_leaves_the_pistons_alone(self, plc):
        model = ready_model(plc)
        model.run(RunMode.CONTINUOUS)
        interrupted = []
        original = model._park_moves

        def park():
            interrupted.append(True)
            model.stop(immediate=True)      # the second press
            return original()

        model._park_moves = park
        model.stop(immediate=True, park=True)
        assert interrupted, "the parking move must have been reached"
        assert model.busy is False
        assert plc.read(tags.RUN_SINGLE) == 0
        assert model.run(RunMode.CONTINUOUS) is True
        model.stop(immediate=True, park=False)

    def test_calibrate_is_refused_while_running_rather_than_interleaved(self, plc):
        model = ready_model(plc)
        model.run(RunMode.CONTINUOUS)
        assert model.calibrate_all() is False
        assert model.state is MachineState.RUNNING
        assert plc.read(tags.RUN_CONTINUOUS) == 1
        model.stop(immediate=True, park=False)

    def test_a_second_run_mode_cannot_be_started_on_top_of_a_run(self, plc):
        model = ready_model(plc)
        model.run(RunMode.CONTINUOUS)
        for mode in RunMode:
            assert model.start(mode) is False
            assert model.run(mode) is False
        assert plc.read(tags.RUN_SINGLE) == 0
        assert plc.read(tags.RUN_CURVE) == 0
        assert plc.read(tags.RUN_CONTINUOUS) == 1
        model.stop(immediate=True, park=False)

    def test_a_live_speed_change_is_refused_when_not_running(self, plc):
        model = ready_model(plc)
        with pytest.raises(ValueError):
            model.change_speed_live(400)
        model.run(RunMode.CONTINUOUS)
        assert model.change_speed_live(400) is True
        for bad in (0, -1, 901):
            with pytest.raises(ValueError):
                model.change_speed_live(bad)
        model.stop(immediate=True, park=False)

    @pytest.mark.xfail(
        strict=True,
        reason="Model.toggle/_sync_implicit_group rebuild the group during a "
               "run without touching Live_Motors, so a piston removed from the "
               "group is still live on the PLC. Model._park_moves then raises "
               "Run_1 and that piston strokes on its old parameters while the "
               "rest park",
    )
    def test_deselecting_a_piston_mid_run_stops_commanding_it(self, plc):
        model = ready_model(plc, axes=(0, 1, 2))
        model.run(RunMode.CONTINUOUS)
        model.toggle(0, False)
        assert [motor.axis for motor in model.all_motors] == [1, 2]
        assert plc.read(tags.live_motor(0)) == 0, (
            "piston 30 is no longer in the group but the machine still has it "
            "selected; the resting move will stroke it"
        )
        model.stop(immediate=True, park=False)

    def test_selecting_a_piston_mid_run_does_not_disturb_the_run(self, plc):
        model = ready_model(plc, axes=(0, 1, 2))
        model.run(RunMode.CONTINUOUS)
        model.toggle(5, True)
        assert model.state is MachineState.RUNNING
        assert plc.read(tags.RUN_CONTINUOUS) == 1
        model.stop(immediate=True, park=False)

    def test_reset_always_returns_a_usable_machine(self, plc):
        model = ready_model(plc)
        model.run(RunMode.CONTINUOUS)
        model.stop(immediate=True, park=False)
        assert model.reset() is True
        assert model.state is MachineState.IDLE
        assert model.sets == []
        assert model.selected_axes() == []
        assert model.pending_params == params.defaults()
        for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE,
                    tags.HOME_BUTTON):
            assert plc.read(tag) == 0


# ==========================================================================
# 6. Long soak through the model
# ==========================================================================


class TestSoak:
    def test_monitoring_does_not_grow_without_limit(self, plc, monkeypatch):
        model = ready_model(plc, axes=range(tags.MOTOR_COUNT))
        model.run(RunMode.CONTINUOUS)

        # Four hundred polls at the real monitoring interval, on a clock the
        # test drives, so the result does not depend on how fast the machine
        # running the test happens to be.
        class Clock:
            def __init__(self, start):
                self.now = start

            def time(self):
                self.now += model_module.MONITOR_INTERVAL
                return self.now

            def sleep(self, _seconds):
                pass

        monkeypatch.setattr(model_module, "time", Clock(time.time()))
        for _ in range(400):
            model._poll_positions()

        expected = model_module.MOVEMENT_WINDOW / model_module.MONITOR_INTERVAL
        for axis, history in model._history.items():
            assert len(history) <= expected + 2, (
                "piston {0} kept {1} samples for a {2}s window".format(
                    axis, len(history), model_module.MOVEMENT_WINDOW)
            )
        assert len(model._history) <= tags.MOTOR_COUNT
        assert len(model._lag_reported) <= tags.MOTOR_COUNT
        assert len(model.lagging_axes) <= tags.MOTOR_COUNT
        assert len(model.unreadable_axes) <= tags.MOTOR_COUNT
        model.stop(immediate=True, park=False)
        assert model._history == {}
        assert model._lag_reported == set()

    def test_two_hundred_prepare_run_stop_cycles_leave_no_residue(self, plc):
        model = ready_model(plc, axes=(0, 1, 2))
        for _ in range(200):
            assert model.run(RunMode.CONTINUOUS) is True
            model.stop(immediate=True, park=False)
        assert model.state is MachineState.HOMED
        assert len(model.sets) == 1
        assert len(model.all_motors) == 3
        assert model._homed_axes == {0, 1, 2}
        assert model.unhomed_axes == []
        assert model.lagging_axes == []

    @pytest.mark.xfail(
        strict=True,
        reason="Model.stop_monitoring (Model.py:2078-2080) drops the thread "
               "reference without joining, and start_monitoring then clears "
               "the same _monitor_stop event the old loop is watching, so "
               "every stop/start pair adds another polling thread",
    )
    def test_restarting_monitoring_does_not_add_a_polling_thread(self, plc):
        model = Model(transport=plc, is_live=True)
        try:
            for _ in range(10):
                model.start_monitoring()
                model.stop_monitoring()
            model.start_monitoring()
            time.sleep(0.2)
            alive = [t for t in threading.enumerate()
                     if t.name == "Monitor" and t.is_alive()]
            assert len(alive) == 1, "{0} monitor threads".format(len(alive))
        finally:
            model.stop_monitoring()
            time.sleep(0.4)

    def test_a_long_mock_run_keeps_every_piston_inside_its_stroke(self, frozen):
        """Three hundred cycles of the whole array, watched throughout."""
        arm(frozen, pos1=-20, pos2=370, speed=780)
        frozen.write(tags.RUN_CONTINUOUS, 1)
        for _ in range(3000):
            frozen._step(0.01)
            for where in frozen.snapshot().values():
                assert -20.001 <= where <= 370.001
                assert math.isfinite(where)


# ==========================================================================
# 7. Fuzzing
# ==========================================================================


#: Exceptions the public API is documented to raise. Anything else is a defect.
EXPECTED_EXCEPTIONS = (ValueError, KeyError, TypeError, PlcError)

#: Values chosen to sit on, just inside and just outside every limit.
FUZZ_VALUES = [-(2 ** 31), -100, -21, -20, -1, 0, 1, 2, 3, 370, 371, 900, 901,
               20000, 20001, 2 ** 31]


def _operations(model, plc, rnd):
    """Every operation an operator can reach, valid arguments and invalid."""
    return [
        ("toggle", lambda: model.toggle(rnd.randrange(tags.MOTOR_COUNT),
                                        rnd.choice([True, False]))),
        ("toggle_bad", lambda: model.toggle(rnd.choice([-1, 30, 999]), True)),
        ("select", lambda: model.set_selection(
            rnd.sample(range(tags.MOTOR_COUNT), rnd.randrange(0, 8)))),
        ("create_set", lambda: model.create_set()),
        ("add_group", lambda: model.add_group()),
        ("remove_set", lambda: model.remove_set(rnd.choice(model.sets))
            if model.sets else None),
        ("pending_param", lambda: model.set_pending_param(
            rnd.choice(params.PARAM_NAMES), rnd.choice(FUZZ_VALUES))),
        ("group_param", lambda: rnd.choice(model.sets).set_param(
            rnd.choice(params.PARAM_NAMES), rnd.choice(FUZZ_VALUES))
            if model.sets else None),
        ("prepare", lambda: model.prepare()),
        ("run", lambda: model.run(rnd.choice(list(RunMode)))),
        ("start", lambda: model.start(rnd.choice(list(RunMode)))),
        ("stop", lambda: model.stop(immediate=rnd.choice([True, False]),
                                    park=rnd.choice([True, False, None]))),
        ("emergency_stop", lambda: model.emergency_stop()),
        ("calibrate", lambda: model.calibrate_all()),
        ("drop_unhomed", lambda: model.drop_unhomed()),
        ("speed_live", lambda: model.change_speed_live(rnd.choice(FUZZ_VALUES))),
        ("stroke_live", lambda: model.change_stroke_live(rnd.choice(FUZZ_VALUES))),
        ("check_drives", lambda: model.check_drives()),
        ("drive_report", lambda: model.drive_report()),
        ("cascade", lambda: model.cascade_targets()),
        ("needs_homing", lambda: model.needs_homing),
        ("mark_unprepared", lambda: model.mark_unprepared()),
        ("clear_lag", lambda: model.clear_lag_warnings()),
        ("poll", lambda: model._poll_positions()),
        ("reset", lambda: model.reset()),
    ]


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_random_operation_sequences_never_wedge_the_model(plc, seed, monkeypatch):
    """A few thousand random operations, valid and invalid.

    Two things are asserted after every single one: nothing escaped that the
    caller was not told to expect, and the model is still in a state some
    operation can be issued from.
    """
    for name in ("PARK_SECONDS", "GRACEFUL_STOP_SECONDS", "STAGE_SECONDS",
                 "PROBE_SECONDS", "MAX_STROKE_SECONDS"):
        monkeypatch.setattr(model_module, name, 0.0)

    model = Model(transport=plc, is_live=True)
    model._spawn = lambda name, work: work()
    for axis in range(tags.MOTOR_COUNT):
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)

    rnd = random.Random(seed)
    operations = _operations(model, plc, rnd)
    recent = []

    for _ in range(1000):
        name, operation = rnd.choice(operations)
        recent.append(name)
        del recent[:-15]
        try:
            operation()
        except EXPECTED_EXCEPTIONS:
            pass
        except Exception as exc:                      # noqa: BLE001
            raise AssertionError(
                "{0} raised {1}: {2}\nlast operations: {3}".format(
                    name, type(exc).__name__, exc, recent)
            )

        assert model.state in MachineState, recent
        assert not model.busy, (
            "{0} left the worker locked; last operations: {1}".format(name, recent)
        )

    # Whatever it has been put through, the machine can always be reset and
    # driven again from there.
    assert model.reset() is True
    assert model.state is MachineState.IDLE
    model.toggle(0, True)
    model.create_set()
    assert model.prepare() is True
    assert model.run(RunMode.CONTINUOUS) is True
    model.stop(immediate=True, park=False)
    assert model.state is MachineState.HOMED


@pytest.mark.parametrize("seed", [11, 12])
def test_random_parameter_storms_never_reach_the_machine_out_of_range(plc, seed):
    """Whatever is thrown at the parameters, what is written stays legal."""
    rnd = random.Random(seed)
    model = Model(transport=plc, is_live=True)
    model._spawn = lambda name, work: work()
    for axis in range(tags.MOTOR_COUNT):
        model.toggle(axis, True)
        plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
    model.create_set()

    for _ in range(600):
        name = rnd.choice(params.PARAM_NAMES)
        value = rnd.choice(FUZZ_VALUES)
        try:
            rnd.choice(model.sets).set_param(name, value)
        except (ValueError, KeyError):
            continue
    plc.clear_history()
    model.prepare()

    by_field = dict((spec.tag(axis), spec)
                    for spec in params.PARAMS
                    for axis in range(tags.MOTOR_COUNT))
    for tag, value in plc.history:
        spec = by_field.get(tag)
        if spec is None:
            continue
        assert spec.validate(value) is None, (
            "{0} = {1} was written to the machine".format(tag, value)
        )
