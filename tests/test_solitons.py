"""The water target is analytical; no motor travel is inferred from it."""

import math

import pytest

from app.solitons import SolitaryTarget


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
