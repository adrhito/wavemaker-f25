"""The water target is analytical; no motor travel is inferred from it."""

import math

import pytest

from app import params
from app.solitons import (
    BOTTOM_MM, MAX_TRIAL_ACCEL_MM_S2, MAX_TRIAL_JERK_MM_S3, MAX_TRIAL_LIFT_MM,
    MAX_TRIAL_SPEED_MM_S, STAGE_SPEED_MM_S, SolitaryTarget, SolitonTrial,
)


def test_first_order_width_and_half_height():
    height = 30.0
    depth = 150.0
    k = math.sqrt(3 * height / (4 * depth ** 3))
    width = 2 * math.acosh(math.sqrt(2)) / k
    target = SolitaryTarget(height, width, depth)
    assert target.theoretical_width_mm == pytest.approx(width)
    assert target.elevation_mm(0) == pytest.approx(height)
    assert target.elevation_mm(width / 2) == pytest.approx(height / 2)
    assert target.width_matches_depth


def test_height_and_depth_change_the_solitary_width():
    baseline = SolitaryTarget(30, 600, 150)
    taller = SolitaryTarget(60, 600, 150)
    deeper = SolitaryTarget(30, 600, 300)
    assert taller.theoretical_width_mm < baseline.theoretical_width_mm
    assert deeper.theoretical_width_mm > baseline.theoretical_width_mm
    assert taller.phase_speed_mm_s > baseline.phase_speed_mm_s
    assert baseline.half_height_seconds == pytest.approx(
        baseline.width_mm / baseline.phase_speed_mm_s)


@pytest.mark.parametrize("values", [
    (0, 500, 150), (30, -1, 150), (30, 500, float("nan")),
    (float("inf"), 500, 150),
])
def test_invalid_target_is_rejected(values):
    with pytest.raises(ValueError):
        SolitaryTarget(*values)


def test_trial_keeps_water_target_separate_from_motor_lift():
    target = SolitaryTarget(60, 2000, 150)
    trial = SolitonTrial(target, 40)
    assert trial.top_mm == BOTTOM_MM - 40
    assert trial.pulse_parameters()["Position 1"] == BOTTOM_MM - 40
    assert trial.stage_parameters()["Position 1"] == BOTTOM_MM
    assert trial.pulse_parameters()["Position 2"] == trial.top_mm
    assert trial.pulse_parameters()["Move Type"] == 0
    assert trial.pulse_parameters()["Profile"] == 2


def test_narrow_target_is_speed_limited_without_silent_overspeed():
    trial = SolitonTrial(SolitaryTarget(100, 100, 100), MAX_TRIAL_LIFT_MM)
    assert trial.speed_limited
    assert trial.speed_mm_s == MAX_TRIAL_SPEED_MM_S
    assert trial.pulse_parameters()["Speed 1"] == MAX_TRIAL_SPEED_MM_S
    assert trial.pulse_parameters()["Decel 1"] == MAX_TRIAL_ACCEL_MM_S2
    assert trial.pulse_parameters()["Jerk 1"] == MAX_TRIAL_JERK_MM_S3


def test_pulse_can_use_the_full_travel_and_speed_other_tabs_accept():
    # The old 120 mm / 200 mm/s caps made waves too small to judge.
    assert MAX_TRIAL_LIFT_MM == (params.BY_NAME["Position 2"].maximum -
                                 params.BY_NAME["Position 1"].minimum)
    assert MAX_TRIAL_SPEED_MM_S == params.BY_NAME["Speed 1"].maximum
    trial = SolitonTrial(SolitaryTarget(100, 100, 100), MAX_TRIAL_LIFT_MM)
    pulse = trial.pulse_parameters()
    assert pulse["Position 1"] == params.BY_NAME["Position 1"].minimum
    assert not params.validate_all(pulse)
    assert not params.validate_all(trial.stage_parameters())


def test_a_strong_pulse_still_stages_gently():
    trial = SolitonTrial(SolitaryTarget(100, 100, 100), MAX_TRIAL_LIFT_MM)
    stage = trial.stage_parameters()
    assert stage["Speed 1"] == STAGE_SPEED_MM_S
    assert stage["Speed 1"] < trial.pulse_parameters()["Speed 1"]
    assert stage["Jerk 1"] < trial.pulse_parameters()["Jerk 1"]


def test_width_slider_changes_the_commanded_floor_speed():
    narrow = SolitonTrial(SolitaryTarget(30, 600, 150), 60)
    broad = SolitonTrial(SolitaryTarget(30, 1800, 150), 60)
    assert narrow.speed_mm_s > broad.speed_mm_s
    assert narrow.pulse_parameters()["Speed 1"] > broad.pulse_parameters()["Speed 1"]


@pytest.mark.parametrize("lift", [0, MAX_TRIAL_LIFT_MM + 1, 1.5, True])
def test_trial_rejects_invalid_lift(lift):
    with pytest.raises(ValueError):
        SolitonTrial(SolitaryTarget(30, 600, 150), lift)


def theory_target(crest, depth):
    return SolitaryTarget(crest, SolitaryTarget(crest, 1, depth).theoretical_width_mm,
                          depth)


def test_eighty_mm_in_deep_water_gets_the_motion_worked_out_by_hand():
    """240 mm for 80 mm in 394 mm of water: the figures given the lab."""
    trial = SolitonTrial(theory_target(80, 394), 240)
    assert trial.speed_mm_s == pytest.approx(257, abs=3)
    assert trial.accel_mm_s2 == pytest.approx(422, abs=5)
    assert trial.jerk_mm_s3 == pytest.approx(2339, abs=25)
    assert not trial.speed_limited
    pulse = trial.pulse_parameters()
    assert pulse["Decel 1"] == pulse["Accel 1"] == trial.accel_mm_s2
    assert pulse["Jerk 2"] == trial.jerk_mm_s3


def test_a_long_deep_water_soliton_gets_a_gentle_push():
    """A hard knock makes ripples; the ideal push here is slow and smooth."""
    deep = SolitonTrial(theory_target(40, 394), 170)
    shallow = SolitonTrial(theory_target(40, 150), 170)
    assert deep.accel_mm_s2 < shallow.accel_mm_s2
    assert deep.jerk_mm_s3 < shallow.jerk_mm_s3
    assert deep.accel_mm_s2 < 1000


def test_travel_time_includes_the_s_curve_ramps():
    trial = SolitonTrial(theory_target(80, 394), 240)
    assert trial.nominal_travel_seconds > 240 / trial.speed_mm_s
    assert trial.nominal_travel_seconds == pytest.approx(1.72, abs=0.1)
    longer = SolitonTrial(trial.target, 300)
    assert longer.nominal_travel_seconds > trial.nominal_travel_seconds
