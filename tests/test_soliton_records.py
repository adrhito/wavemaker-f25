"""Trial files keep targets, commands, and observed water heights distinct."""

import json

import pytest

from app.soliton_records import finish_trial, record_observation, save_trial
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
    assert before["observed_width_method"] is None
    assert before["pulse_status"] == "pending"
    assert all(value is None for value in before["motion_timing"].values())
    assert before["stage_command_parameters"] == trial.stage_parameters()
    assert before["pulse_command_parameters"] == trial.pulse_parameters()

    finish_trial(path, "completed", {"1": 325.0})

    record_observation(path, "24.5", "28.0", "2000", "side camera A",
                       fwhm_width_mm="1100")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["target_crest_rise_mm"] == before["target_crest_rise_mm"]
    assert after["floor_lift_mm"] == before["floor_lift_mm"]
    assert after["observed_crest_rise_mm"] == 24.5
    assert after["observed_crest_to_trough_mm"] == 28.0
    assert after["observed_fwhm_width_mm"] == 1100.0
    assert after["observed_width_method"] == "direct"
    assert after["pulse_status"] == "completed"
    assert after["actual_end_positions_mm"] == {"1": 325.0}
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


def test_failed_trial_keeps_command_and_reason(tmp_path):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    finish_trial(path, "failed", {"1": None}, "connection lost")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pulse_status"] == "failed"
    assert data["pulse_finished_utc"]
    assert data["pulse_error"] == "connection lost"
    assert data["floor_lift_mm"] == 20
    assert data["actual_end_positions_mm"] == {"1": None}


def test_trial_records_planned_station_and_source(tmp_path):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path, source="hardware",
                      planned_station_mm="2500",
                      actual_start_positions_mm={"1": 369.8})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["source"] == "hardware"
    assert data["planned_measurement_station_mm"] == 2500.0
    assert data["actual_start_positions_mm"] == {"1": 369.8}


def test_outcome_written_after_observation_preserves_both(tmp_path):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    record_observation(path, "15", "", "2000", fwhm_width_mm="900")
    finish_trial(path, "interrupted", {"1": 360.0})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "water_observation_recorded"
    assert data["pulse_status"] == "interrupted"
    assert data["observed_crest_rise_mm"] == 15
    assert data["observed_fwhm_width_mm"] == 900


@pytest.mark.parametrize("width", ["nan", "0"])
def test_invalid_observed_width_does_not_alter_record(tmp_path, width):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="Observed half-height width"):
        record_observation(path, "12", "", "2000", fwhm_width_mm=width)
    assert path.read_bytes() == original


def test_video_times_calculate_and_preserve_observed_width(tmp_path):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    saved_width = record_observation(
        path, "15", "18", "2000", "side camera frames 100-160",
        video_station_spacing_mm="500",
        video_crest_transit_s="0.4",
        video_half_height_duration_s="0.8")
    assert saved_width == pytest.approx(1000.0)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["observed_fwhm_width_mm"] == pytest.approx(1000.0)
    assert data["observed_width_method"] == "video_timing"
    assert data["video_station_spacing_mm"] == 500.0
    assert data["video_crest_transit_s"] == 0.4
    assert data["video_half_height_duration_s"] == 0.8
    assert data["measurement_station_mm"] == 2000.0


@pytest.mark.parametrize("timings,direct,station", [
    (("500", "", "0.8"), "", "2000"),
    (("500", "0", "0.8"), "", "2000"),
    (("500", "0.4", "0.8"), "1000", "2000"),
    (("500", "0.4", "0.8"), "", ""),
    (("500", "inf", "0.8"), "", "2000"),
])
def test_invalid_video_measurement_does_not_alter_record(
        tmp_path, timings, direct, station):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        record_observation(
            path, "15", "", station, fwhm_width_mm=direct,
            video_station_spacing_mm=timings[0],
            video_crest_transit_s=timings[1],
            video_half_height_duration_s=timings[2])
    assert path.read_bytes() == original


def test_half_height_width_requires_measured_crest(tmp_path):
    path = save_trial(SolitonTrial(SolitaryTarget(20, 1000, 100), 20),
                      (29,), directory=tmp_path)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="positive crest rise"):
        record_observation(path, "", "18", "2000", fwhm_width_mm="1000")
    assert path.read_bytes() == original
