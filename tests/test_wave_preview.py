"""Geometry tests for the Operate tab's wave strip.

These exercise the pure functions the drawing loop is built from -- no
Tkinter, no Canvas, no timers -- because what the lab owner asked for is a
correct *shape* (asymmetric legs, dwell flats, amplitude tracking the
stroke), not a pixel-for-pixel picture. Where a test needs the class itself
(``_column_phase`` combining a stagger with the running phase) it builds a
``WavePreview`` with ``__new__`` and sets only the attributes that method
reads, so it never touches the real ``__init__`` and never needs a Tk root.
"""

from __future__ import annotations

import math

import pytest

from modules.wave_preview import (
    WavePreview,
    amplitude_for,
    cycle_sample,
    invert_leg_position,
    leg_fractions,
    leg_position,
)


# -- leg_position / invert_leg_position ---------------------------------------

@pytest.mark.parametrize("smoothing", [0.0, 0.3, 0.6, 0.8, 1.0])
def test_leg_always_starts_at_0_and_ends_at_1(smoothing):
    # Whatever the easing, a leg leaves Position 1 and arrives at Position 2 --
    # a piston that eases in slowly must still get there in the end.
    assert leg_position(0.0, smoothing) == pytest.approx(0.0, abs=1e-9)
    assert leg_position(1.0, smoothing) == pytest.approx(1.0, abs=1e-9)


def test_no_easing_is_a_straight_ramp():
    # Trapezoidal's PROFILE_SMOOTHING is 0: constant speed for the whole leg,
    # so distance covered is just proportional to time.
    for s in (0.1, 0.25, 0.5, 0.75, 0.9):
        assert leg_position(s, 0.0) == pytest.approx(s)


def test_full_easing_is_slower_at_the_ends_than_a_straight_ramp():
    # Sine's PROFILE_SMOOTHING is 1: the whole leg is one eased sweep, so
    # early/late progress lags a straight ramp and the middle overtakes it --
    # that S-shape is the entire reason to pick a gentler profile.
    assert leg_position(0.1, 1.0) < 0.1
    assert leg_position(0.9, 1.0) > 0.9
    assert leg_position(0.5, 1.0) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.parametrize("smoothing", [0.0, 0.6, 1.0])
@pytest.mark.parametrize("distance", [0.0, 0.2, 0.5, 0.8, 1.0])
def test_invert_leg_position_round_trips(smoothing, distance):
    s = invert_leg_position(distance, smoothing)
    assert leg_position(s, smoothing) == pytest.approx(distance, abs=1e-3)


# -- leg_fractions -------------------------------------------------------------

def test_unequal_speeds_split_the_cycle_asymmetrically():
    # Speed 1 (out, toward Position 2) at twice Speed 2 (back, toward
    # Position 1): the return leg must take twice as long as the outbound
    # one, which a symmetric preview could never show.
    out_frac, dwell2, back_frac, dwell1, total = leg_fractions(
        stroke=200.0, speed_1=400.0, speed_2=200.0,
        dwell_1_s=0.0, dwell_2_s=0.0, fallback_period=1.0,
    )
    assert dwell2 == pytest.approx(0.0)
    assert dwell1 == pytest.approx(0.0)
    assert back_frac == pytest.approx(2.0 * out_frac, rel=1e-6)
    assert total == pytest.approx(200.0 / 400.0 + 200.0 / 200.0)


def test_equal_speeds_split_the_cycle_evenly():
    out_frac, dwell2, back_frac, dwell1, _total = leg_fractions(
        stroke=150.0, speed_1=300.0, speed_2=300.0,
        dwell_1_s=0.0, dwell_2_s=0.0, fallback_period=1.0,
    )
    assert out_frac == pytest.approx(back_frac)


def test_dwell_takes_its_correct_share_of_the_cycle():
    # Half a second of travel each way, one second dwelling at Position 2:
    # that dwell should be exactly half of the two-second cycle.
    out_frac, dwell2, back_frac, dwell1, total = leg_fractions(
        stroke=100.0, speed_1=200.0, speed_2=200.0,
        dwell_1_s=0.0, dwell_2_s=1.0, fallback_period=1.0,
    )
    assert total == pytest.approx(2.0)
    assert dwell2 == pytest.approx(0.5)
    assert dwell1 == pytest.approx(0.0)
    assert out_frac == pytest.approx(back_frac)


def test_missing_speeds_fall_back_to_the_old_symmetric_split():
    # A caller that has not been updated to pass speed_1/speed_2 (today's
    # Operate.py) must keep getting the previous behaviour: half the period
    # out, half back, no dwell.
    out_frac, dwell2, back_frac, dwell1, total = leg_fractions(
        stroke=100.0, speed_1=None, speed_2=None,
        dwell_1_s=5.0, dwell_2_s=5.0, fallback_period=3.0,
    )
    assert (out_frac, dwell2, back_frac, dwell1) == (0.5, 0.0, 0.5, 0.0)
    assert total == pytest.approx(3.0)


# -- cycle_sample ---------------------------------------------------------

def test_cycle_sample_reaches_the_full_peak_and_trough():
    # Regardless of the split, the piston does reach both ends -- the drawn
    # surface's peak-to-trough must not fall short of the full stroke.
    out_frac, dwell2, back_frac, dwell1, _total = leg_fractions(
        stroke=100.0, speed_1=500.0, speed_2=150.0,
        dwell_1_s=0.2, dwell_2_s=0.0, fallback_period=1.0,
    )
    samples = [
        cycle_sample(p / 200.0, out_frac, dwell2, back_frac, dwell1, 0.6)
        for p in range(201)
    ]
    assert max(samples) == pytest.approx(1.0, abs=1e-2)
    assert min(samples) == pytest.approx(-1.0, abs=1e-2)


def test_dwell_at_position_2_holds_the_sample_flat():
    out_frac, dwell2, back_frac, dwell1, _total = leg_fractions(
        stroke=100.0, speed_1=200.0, speed_2=200.0,
        dwell_1_s=0.0, dwell_2_s=1.0, fallback_period=1.0,
    )
    # dwell2 is half the cycle here (see the fractions test above); sampling
    # anywhere in the middle of it must read exactly the Position 2 extreme,
    # not some point along a curve that ignores the pause.
    mid_of_dwell = out_frac + dwell2 / 2.0
    assert cycle_sample(mid_of_dwell, out_frac, dwell2, back_frac, dwell1, 0.6) == 1.0


def test_dwell_at_position_1_holds_the_sample_flat():
    out_frac, dwell2, back_frac, dwell1, _total = leg_fractions(
        stroke=100.0, speed_1=200.0, speed_2=200.0,
        dwell_1_s=1.0, dwell_2_s=0.0, fallback_period=1.0,
    )
    mid_of_dwell = out_frac + dwell2 + back_frac + dwell1 / 2.0
    assert cycle_sample(mid_of_dwell, out_frac, dwell2, back_frac, dwell1, 0.6) == -1.0


def test_back_leg_occupies_its_full_share_of_the_cycle_in_cycle_sample():
    # Speed 2 at half of Speed 1 means the return leg should take up twice
    # the phase-space of the outbound one (see the leg_fractions test above)
    # -- and cycle_sample must actually honour that span, rising from -1 to
    # +1 across out_frac and only then spending the (longer) back_frac
    # falling back from +1 to -1, rather than distributing the fall evenly
    # across the whole cycle regardless of speed.
    out_frac, dwell2, back_frac, dwell1, _total = leg_fractions(
        stroke=100.0, speed_1=400.0, speed_2=200.0,
        dwell_1_s=0.0, dwell_2_s=0.0, fallback_period=1.0,
    )
    assert back_frac == pytest.approx(2.0 * out_frac, rel=1e-6)

    just_before_turn = cycle_sample(out_frac - 1e-6, out_frac, dwell2, back_frac, dwell1, 0.0)
    just_after_turn = cycle_sample(out_frac + 1e-6, out_frac, dwell2, back_frac, dwell1, 0.0)
    just_before_home = cycle_sample(out_frac + back_frac - 1e-6, out_frac, dwell2, back_frac, dwell1, 0.0)
    assert just_before_turn == pytest.approx(1.0, abs=1e-3)
    assert just_after_turn == pytest.approx(1.0, abs=1e-3)
    assert just_before_home == pytest.approx(-1.0, abs=1e-3)


# -- amplitude_for --------------------------------------------------------

def test_amplitude_grows_with_stroke():
    small = amplitude_for(50.0, 74.0)
    large = amplitude_for(200.0, 74.0)
    assert 0.0 < small < large


def test_amplitude_is_capped_at_full_stroke():
    from modules.wave_preview import FULL_STROKE_MM

    at_cap = amplitude_for(FULL_STROKE_MM, 74.0)
    past_cap = amplitude_for(FULL_STROKE_MM * 2.0, 74.0)
    assert at_cap == pytest.approx(past_cap)


def test_zero_stroke_has_no_amplitude():
    assert amplitude_for(0.0, 74.0) == 0.0


# -- WavePreview._column_phase -------------------------------------------

def _bare_preview(**attrs) -> WavePreview:
    """A WavePreview with no Tk widget behind it -- just the plain attributes
    _column_phase and _sample read. Building one with __new__ mirrors the
    pattern already used for TankView in tests/test_orientation.py."""
    preview = WavePreview.__new__(WavePreview)
    preview._phase = 0.0
    preview._period = 1.0
    preview._out_frac = 0.5
    preview._dwell_2_frac = 0.0
    preview._back_frac = 0.5
    preview._dwell_1_frac = 0.0
    preview._smoothing = 0.6
    preview._offsets = {}
    preview._column_leads = {}
    for name, value in attrs.items():
        setattr(preview, name, value)
    return preview


def test_column_with_no_stagger_reads_the_plain_phase():
    preview = _bare_preview()
    preview._phase = 0.37
    assert preview._column_phase(3) == pytest.approx(0.37)


def test_start_fraction_zero_matches_a_column_parked_at_position_1():
    preview = _bare_preview(_column_leads={0: 0.0})
    preview._phase = 0.0
    # Parked exactly at Position 1 is the same as an unstaggered column at
    # the very start of its outbound leg.
    assert preview._sample(preview._column_phase(0)) == pytest.approx(-1.0, abs=1e-2)


def test_start_fraction_gives_a_column_a_head_start():
    preview = _bare_preview(_column_leads={0: 0.25}, _offsets={0: 999.0})
    preview._phase = 0.0
    # The column lead takes priority over a legacy Curve Offset entry for the
    # same column -- start_fractions describes what a continuous run
    # actually does; the offsets dict is the old, curve-only semantics.
    assert preview._column_phase(0) == pytest.approx(0.25)


def test_legacy_offset_still_works_as_a_timing_delay():
    preview = _bare_preview(_offsets={2: 0.25}, _period=1.0)
    preview._phase = 0.1
    # Wrapped into [0, 1): a column "delayed" past the start of the cycle
    # is really showing where the previous cycle left off.
    assert preview._column_phase(2) == pytest.approx((0.1 - 0.25) % 1.0)


def test_travelling_wave_orders_columns_by_their_stagger():
    # Two columns started from different points along the stroke must show
    # different water heights at the same instant -- that visible lead/lag
    # is what makes a design read as travelling rather than a wobble.
    preview = _bare_preview(_column_leads={0: 0.0, 1: 0.5})
    preview._phase = 0.0
    front = preview._sample(preview._column_phase(0))
    back = preview._sample(preview._column_phase(1))
    assert front != pytest.approx(back)
