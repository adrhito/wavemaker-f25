"""Local, inspectable records of one-shot floor trials and water observations."""

from __future__ import annotations

import json
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app import paths, tags
from app.solitons import SolitonTrial, TRIAL_ACCEL_MM_S2, TRIAL_DECEL_MM_S2, TRIAL_JERK_MM_S3


_RECORD_LOCK = threading.RLock()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def save_trial(trial: SolitonTrial, axes: Iterable[int],
               directory: Optional[Path] = None,
               actual_end_positions_mm: Optional[dict] = None,
               source: str = "unknown",
               planned_station_mm: Optional[float] = None) -> Path:
    """Save the planned pulse before motion, including if the pulse later fails."""
    if source not in ("hardware", "simulator", "unknown"):
        raise ValueError("Unknown soliton trial source.")
    if planned_station_mm is not None:
        planned_station_mm = _optional_measurement(
            planned_station_mm, "Planned station")
        if planned_station_mm is None:
            raise ValueError("Planned station must be a number in millimetres.")
    folder = directory or paths.ANALYTICS_DIR / "soliton-trials"
    now = datetime.now(timezone.utc)
    filename = "trial-{0}-{1}.json".format(
        now.strftime("%Y%m%dT%H%M%S%fZ"), uuid.uuid4().hex[:8])
    path = folder / filename
    data = {
        "schema_version": 1,
        "timestamp_utc": now.isoformat(),
        "status": "pulse_pending",
        "pulse_status": "pending",
        "pulse_finished_utc": None,
        "pulse_error": None,
        "source": source,
        "display_pistons": [tags.display_number(axis) for axis in axes],
        "target_crest_rise_mm": trial.target.crest_height_mm,
        "target_fwhm_width_mm": trial.target.width_mm,
        "still_water_depth_mm": trial.target.water_depth_mm,
        "theoretical_fwhm_width_mm": trial.target.theoretical_width_mm,
        "floor_start_mm": trial.stage_parameters()["Position 1"],
        "floor_end_mm": trial.top_mm,
        "floor_lift_mm": trial.floor_lift_mm,
        "actual_end_positions_mm": actual_end_positions_mm or {},
        "command_speed_mm_s": trial.speed_mm_s,
        "command_accel_mm_s2": TRIAL_ACCEL_MM_S2,
        "command_decel_mm_s2": TRIAL_DECEL_MM_S2,
        "command_jerk_mm_s3": TRIAL_JERK_MM_S3,
        "command_profile": "S-curve",
        "observed_crest_rise_mm": None,
        "observed_crest_to_trough_mm": None,
        "observed_fwhm_width_mm": None,
        "measurement_station_mm": None,
        "planned_measurement_station_mm": planned_station_mm,
        "measurement_notes": "",
    }
    _write_json(path, data)
    return path


def finish_trial(path: Path, outcome: str, actual_end_positions_mm: dict,
                 error: Optional[str] = None) -> None:
    """Record how a planned pulse ended, even when it did not reach its target."""
    if outcome not in ("completed", "interrupted", "failed"):
        raise ValueError("Unknown soliton pulse outcome: {0}".format(outcome))
    with _RECORD_LOCK:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("Unknown soliton trial record format.")
        observed = (data.get("observed_crest_rise_mm") is not None or
                    data.get("observed_crest_to_trough_mm") is not None)
        data.update({
            "status": "water_observation_recorded" if observed else (
                "floor_raised_no_water_measurement_yet" if outcome == "completed"
                else "pulse_{0}_no_water_measurement_yet".format(outcome)),
            "pulse_status": outcome,
            "pulse_finished_utc": datetime.now(timezone.utc).isoformat(),
            "pulse_error": str(error) if error is not None else None,
            "actual_end_positions_mm": actual_end_positions_mm,
        })
        _write_json(path, data)


def _optional_measurement(value, label: str):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise ValueError("{0} must be a number in millimetres.".format(label))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("{0} must be a number in millimetres.".format(label)) from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("{0} must be a finite nonnegative number.".format(label))
    return number


def record_observation(path: Path, crest_rise_mm, crest_to_trough_mm,
                       station_mm, notes: str = "", fwhm_width_mm=None) -> None:
    """Add measured wave dimensions without changing commanded trial fields."""
    crest = _optional_measurement(crest_rise_mm, "Crest rise")
    total = _optional_measurement(crest_to_trough_mm, "Crest-to-trough height")
    width = _optional_measurement(fwhm_width_mm, "Observed half-height width")
    if width == 0:
        raise ValueError("Observed half-height width must be positive.")
    station = _optional_measurement(station_mm, "Measurement station")
    if crest is None and total is None:
        raise ValueError("Enter at least one observed water height.")
    with _RECORD_LOCK:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("Unknown soliton trial record format.")
        data.update({
            "status": "water_observation_recorded",
            "observed_crest_rise_mm": crest,
            "observed_crest_to_trough_mm": total,
            "observed_fwhm_width_mm": width,
            "measurement_station_mm": station,
            "measurement_notes": str(notes).strip(),
            "observation_recorded_utc": datetime.now(timezone.utc).isoformat(),
        })
        _write_json(path, data)
