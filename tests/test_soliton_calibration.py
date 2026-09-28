"""Measured floor-to-wave suggestions never use mock or unmatched trials."""

import pytest

from app.soliton_calibration import suggest_lift
from app.soliton_records import finish_trial, record_observation, save_trial
from app.solitons import BOTTOM_MM, SolitaryTarget, SolitonTrial


@pytest.fixture
def target():
    width = SolitaryTarget(30, 1, 150).theoretical_width_mm
    return SolitaryTarget(30, width, 150)


def add_sample(folder, target, lift, observed_crest, *, source="hardware",
               station=2000, observed_width=None, actual_offset=0.0,
               outcome="completed"):
    trial = SolitonTrial(target, lift)
    path = save_trial(trial, (29, 28, 27), directory=folder,
                      source=source, planned_station_mm=station)
    finish_trial(path, outcome, dict((str(piston), BOTTOM_MM - lift + actual_offset)
                                     for piston in (1, 2, 3)))
    record_observation(path, str(observed_crest), "", str(station),
                       fwhm_width_mm=(target.width_mm if observed_width is None
                                      else observed_width))
    return path


def add_two_levels(folder, target, low=(18, 18.5, 17.5),
                   high=(42, 42.5, 41.5), **kwargs):
    for crest in low:
        add_sample(folder, target, 20, crest, **kwargs)
    for crest in high:
        add_sample(folder, target, 80, crest, **kwargs)


def test_interpolates_only_between_repeated_matching_heights(tmp_path, target):
    add_two_levels(tmp_path, target)
    (tmp_path / "trial-corrupt.json").write_text("{broken", encoding="utf-8")
    result = suggest_lift(target, (1, 2, 3), 2000, directory=tmp_path)
    assert result is not None
    assert result.floor_lift_mm == 50
    assert (result.lower_lift_mm, result.upper_lift_mm) == (20, 80)
    assert result.trials_used == 6

    assert suggest_lift(target, (1, 2, 3), 2100, directory=tmp_path) is None
    assert suggest_lift(target, (1, 2), 2000, directory=tmp_path) is None
    deeper = SolitaryTarget(30, target.width_mm, 160)
    assert suggest_lift(deeper, (1, 2, 3), 2000, directory=tmp_path) is None


def test_does_not_extrapolate_beyond_observed_heights(tmp_path, target):
    add_two_levels(tmp_path, target, low=(28, 28.5, 27.5),
                   high=(32, 32.5, 31.5))
    outside = SolitaryTarget(26, target.width_mm, target.water_depth_mm)
    assert outside.width_matches_depth
    assert suggest_lift(outside, (1, 2, 3), 2000, directory=tmp_path) is None


def test_requires_repeats_at_both_lifts(tmp_path, target):
    add_two_levels(tmp_path, target, low=(18, 18.5), high=(42, 42.5))
    assert suggest_lift(target, (1, 2, 3), 2000, directory=tmp_path) is None
    add_sample(tmp_path, target, 20, 17.5)
    add_sample(tmp_path, target, 80, 41.5)
    assert suggest_lift(target, (1, 2, 3), 2000, directory=tmp_path) is not None


@pytest.mark.parametrize("variant", [
    {"source": "simulator"},
    {"station": 2100},
    {"observed_width": 2000},
    {"actual_offset": 8},
    {"outcome": "interrupted"},
])
def test_rejects_ineligible_trials(tmp_path, target, variant):
    add_two_levels(tmp_path, target, **variant)
    assert suggest_lift(target, (1, 2, 3), 2000, directory=tmp_path) is None


def test_rejects_nonmonotone_or_unstable_height_response(tmp_path, target):
    add_two_levels(tmp_path, target, low=(42, 42.5, 41.5),
                   high=(18, 18.5, 17.5))
    assert suggest_lift(target, (1, 2, 3), 2000, directory=tmp_path) is None

    other = tmp_path / "unstable"
    add_two_levels(other, target, low=(10, 20, 30))
    assert suggest_lift(target, (1, 2, 3), 2000, directory=other) is None
