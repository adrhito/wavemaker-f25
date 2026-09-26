"""The solitary-wave maths, and the limits it has to respect.

These check relationships rather than magic numbers wherever they can, because
the absolute values are an uncalibrated recipe -- nothing in this repository has
ever measured water height against piston motion -- while the relationships are
what the physics actually asserts and what a later calibration must not break.
"""

from __future__ import annotations

import math

import pytest

from app import params, solitons, tags


# -- the wave -----------------------------------------------------------------

def test_a_taller_soliton_travels_faster():
    # c = sqrt(g(h+a)), so amplitude raises the celerity. This is why the
    # firing delays cannot be computed once and reused for every amplitude.
    assert solitons.celerity(250, 80) > solitons.celerity(250, 20)


def test_celerity_matches_shallow_water_for_a_tiny_wave():
    # As a -> 0 the soliton speed becomes the linear long-wave speed sqrt(gh).
    shallow = math.sqrt(solitons.GRAVITY * 250)
    assert solitons.celerity(250, solitons.MIN_AMPLITUDE) == pytest.approx(
        shallow, rel=0.02
    )


def test_a_taller_soliton_is_narrower():
    # kappa grows with amplitude, so width = 1/kappa shrinks. Tall solitons are
    # short and fast; long swells are not solitons.
    assert solitons.width(250, 80) < solitons.width(250, 20)


def test_a_deeper_tank_makes_a_wider_wave():
    assert solitons.width(350, 40) > solitons.width(150, 40)


def test_the_push_takes_longer_for_a_gentler_wave():
    # A wide slow wave takes longer to pass a point than a narrow fast one.
    assert solitons.duration(250, 20) > solitons.duration(250, 80)


# -- what the pistons do ------------------------------------------------------

def test_a_taller_wave_needs_a_longer_push():
    assert solitons.stroke(250, 80) > solitons.stroke(250, 20)


def test_gain_scales_the_push_and_nothing_else():
    """The operator's calibration handle.

    The stroke is a recipe, not a prediction, so it has to be correctable. It
    must not quietly change the wave the design claims to be making.
    """
    plain = solitons.design(250, 40, gain=1.0)
    doubled = solitons.design(250, 40, gain=2.0)

    assert doubled.stroke == pytest.approx(plain.stroke * 2.0)
    assert doubled.celerity == pytest.approx(plain.celerity)
    assert doubled.width == pytest.approx(plain.width)
    assert doubled.offsets == plain.offsets


def test_peak_speed_is_twice_the_mean_not_the_mean():
    """Checking the mean would pass designs the drive then clips.

    A sech^2 velocity pulse spends most of its travel in the middle of the
    push. If the peak is above the drive's limit the top is flattened, which
    turns the velocity bell into a plateau and makes something that is not a
    soliton -- while the mean speed looks perfectly legal.
    """
    design = solitons.design(250, 40)
    mean = design.stroke / design.duration

    assert design.peak_speed == pytest.approx(2.0 * mean)
    assert design.peak_speed > mean


# -- firing the columns in sequence -------------------------------------------

def test_each_column_fires_later_than_the_one_in_front():
    delays = solitons.column_delays(250, 40, columns=5, pitch=150)
    ordered = [delays[c] for c in sorted(delays)]
    assert ordered == sorted(ordered)
    assert ordered[0] == 0.0


def test_the_delay_between_columns_is_the_time_the_wave_takes_to_cross_one():
    """The whole idea: the bank pushes where the wave already is.

    Each column sits one pitch further along the tank, so it has to fire one
    pitch-crossing later to be part of the same wave.
    """
    delays = solitons.column_delays(250, 40, columns=3, pitch=150)
    expected = 150.0 / solitons.celerity(250, 40)

    assert delays[1] - delays[0] == pytest.approx(expected)
    assert delays[2] - delays[1] == pytest.approx(expected)


def test_a_faster_wave_needs_tighter_delays():
    slow = solitons.column_delays(250, 20, columns=5, pitch=150)
    fast = solitons.column_delays(250, 150, columns=5, pitch=150)
    assert fast[4] < slow[4]


def test_offsets_are_whole_hundredths_because_that_is_what_the_tag_holds():
    offsets = solitons.curve_offsets(250, 40, columns=5, pitch=150)
    assert all(isinstance(v, int) for v in offsets.values())
    assert offsets[0] == 0
    # Distinct, or the stagger is not a stagger.
    assert len(set(offsets.values())) == len(offsets)


def test_a_stagger_too_fine_for_the_tag_is_called_out():
    """Curve Offset cannot express less than a hundredth of a second.

    With the columns almost touching, every offset rounds to the same value and
    the run becomes a single simultaneous push -- which is a released hump, not
    a soliton. Saying so is the difference between a wrong result and a known
    limitation.
    """
    design = solitons.design(250, 40, pitch=1.0)
    assert any("hundredth" in w for w in design.warnings)


# -- the limits ---------------------------------------------------------------

def test_a_wave_past_breaking_is_refused():
    design = solitons.design(250, int(250 * 0.8))
    assert not design.runnable
    assert any("breaking" in p for p in design.problems)


def test_a_steep_but_legal_wave_warns_rather_than_refuses():
    design = solitons.design(250, int(250 * 0.55))
    assert any("steep" in w for w in design.warnings)


def test_a_gentle_wave_is_neither_refused_nor_flagged():
    design = solitons.design(250, 40)
    assert design.runnable
    assert design.warnings == []


def test_a_push_longer_than_the_travel_is_refused():
    reach = (params.BY_NAME["Position 2"].maximum
             - params.BY_NAME["Position 1"].minimum)
    design = solitons.design(250, 40, gain=20.0)
    assert design.stroke > reach
    assert not design.runnable


def test_a_push_faster_than_the_drives_is_refused():
    design = solitons.design(250, 40, gain=12.0)
    assert design.peak_speed > params.BY_NAME["Speed 1"].maximum
    assert any("mm/s" in p for p in design.problems)


def test_nonsense_input_is_clamped_rather_than_raising():
    # This feeds a Tk variable, so a half-typed number must not take the tab
    # down.
    for bad in (None, "", "abc", -5, 99999):
        design = solitons.design(bad, bad)
        assert design.depth >= solitons.MIN_DEPTH
        assert design.amplitude >= solitons.MIN_AMPLITUDE


# -- which pistons ------------------------------------------------------------

def test_only_the_always_submerged_columns_are_used_by_default():
    """Pistons 16-30 ride clear of a low surface and move no water.

    They still report a healthy stroke while doing it, so including them makes
    a design that looks stronger than it is. See the tank section of the
    wavemaker skill.
    """
    design_columns = solitons.submerged_columns(250)
    axes = solitons.axes_for(design_columns)

    assert len(axes) == 15
    assert sorted(tags.display_number(a) for a in axes) == list(range(1, 16))


def test_the_first_column_is_the_deepest_pistons():
    # Column 1 is pistons 1, 2, 3 -- nearest the end wall, deepest, and the
    # most effective. Getting this backwards marches the wave the wrong way,
    # which has happened in this repository before.
    axes = solitons.axes_for(1)
    assert sorted(tags.display_number(a) for a in axes) == [1, 2, 3]


def test_more_columns_than_the_array_has_are_not_invented():
    design = solitons.design(250, 40, columns=99)
    assert len(design.offsets) <= tags.COLUMN_COUNT
