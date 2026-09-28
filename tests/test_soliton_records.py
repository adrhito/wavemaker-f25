"""Trial files keep targets, commands, and observed water heights distinct."""

import json

import pytest

from app.soliton_records import record_observation, save_trial
from app.solitons import SolitaryTarget, SolitonTrial


def test_trial_record_preserves_request_and_actual_observation(tmp_path):
    trial = SolitonTrial(SolitaryTarget(30, 1200, 150), 45)
    path = save_trial(trial, (29, 28, 27), directory=tmp_path,
                      actual_end_positions_mm={"1": 324.9})
    before = json.loads(path.read_text(encoding="utf-8"))
    assert before["display_pistons"] == [1, 2, 3]
    assert before["target_crest_rise_mm"] == 30
    assert before["floor_lift_mm"] == 45
    assert before["actual_end_positions_mm"] == {"1": 324.9}
    assert before["observed_crest_rise_mm"] is None
    assert before["observed_fwhm_width_mm"] is None

    record_observation(path, "24.5", "28.0", "2000", "side camera A",
                       fwhm_width_mm="1100")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["target_crest_rise_mm"] == before["target_crest_rise_mm"]
    assert after["floor_lift_mm"] == before["floor_lift_mm"]
    assert after["observed_crest_rise_mm"] == 24.5
    assert after["observed_crest_to_trough_mm"] == 28.0
    assert after["observed_fwhm_width_mm"] == 1100.0
    assert after["measurement_station_mm"] == 2000.0
    assert after["measurement_notes"] == "side camera A"


@pytest.mark.parametrize("rise,total,station", [
    ("", "", ""), ("nan", "", ""), ("-1", "", ""),
    ("1", "", "in the middle"),
])
def test_invalid_observation_does_not_alter_record(
        tmp_path, rise, total, station):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        record_observation(path, rise, total, station)
    assert path.read_bytes() == original


@pytest.mark.parametrize("width", ["nan", "0"])
def test_invalid_observed_width_does_not_alter_record(tmp_path, width):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="Observed half-height width"):
        record_observation(path, "12", "", "2000", fwhm_width_mm=width)
    assert path.read_bytes() == original
