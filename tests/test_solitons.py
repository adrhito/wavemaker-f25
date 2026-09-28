"""The water target is analytical; no motor travel is inferred from it."""

import math

import pytest

from app.solitons import (
    BOTTOM_MM, MAX_TRIAL_SPEED_MM_S, SolitaryTarget, SolitonTrial,
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
    trial = SolitonTrial(SolitaryTarget(100, 100, 100), 120)
    assert trial.speed_limited
    assert trial.speed_mm_s == MAX_TRIAL_SPEED_MM_S
    assert trial.pulse_parameters()["Speed 1"] == MAX_TRIAL_SPEED_MM_S
    assert trial.pulse_parameters()["Decel 1"] == 4000
    assert trial.pulse_parameters()["Jerk 1"] == 2000


def test_width_slider_changes_the_commanded_floor_speed():
    narrow = SolitonTrial(SolitaryTarget(30, 600, 150), 60)
    broad = SolitonTrial(SolitaryTarget(30, 1800, 150), 60)
    assert narrow.speed_mm_s > broad.speed_mm_s
    assert narrow.pulse_parameters()["Speed 1"] > broad.pulse_parameters()["Speed 1"]


@pytest.mark.parametrize("lift", [0, 121, 1.5, True])
def test_trial_rejects_invalid_lift(lift):
    with pytest.raises(ValueError):
        SolitonTrial(SolitaryTarget(30, 600, 150), lift)
