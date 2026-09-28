"""Conservative lift suggestions from measured solitary-wave trials.

The first-order water profile does not determine floor travel for this
vertically moving machine. A suggestion is available only between repeated,
consistent physical observations made with the same setup. It remains a
proposal for the operator to review, never a guarantee of water height.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Optional, Sequence

from app import paths
from app.solitons import (
    BOTTOM_MM, MAX_TRIAL_LIFT_MM, MAX_TRIAL_SPEED_MM_S,
    TRIAL_ACCEL_MM_S2, TRIAL_DECEL_MM_S2, TRIAL_JERK_MM_S3,
    SolitaryTarget,
)


MIN_REPEATS_PER_LIFT = 3
MAX_WIDTH_ERROR_FRACTION = 0.15
MAX_PULSE_TIME_ERROR_FRACTION = 0.10
MAX_HEIGHT_SPREAD_FRACTION = 0.15
MAX_HEIGHT_SPREAD_MM = 5.0
POSITION_TOLERANCE_MM = 2.0


@dataclass(frozen=True)
class LiftSuggestion:
    floor_lift_mm: int
    lower_lift_mm: int
    upper_lift_mm: int
    lower_observed_crest_mm: float
    upper_observed_crest_mm: float
    trials_used: int


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _same(value, wanted: float) -> bool:
    parsed = _number(value)
    return parsed is not None and abs(parsed - wanted) < 1e-6


def _matching_observation(record: dict, target: SolitaryTarget,
                          pistons: Sequence[int], station_mm: float):
    """Return (lift, crest) only for a comparable, completed physical pulse."""
    if (record.get("schema_version") != 1 or
            record.get("source") != "hardware" or
            record.get("pulse_status") != "completed"):
        return None
    if record.get("command_profile") != "S-curve":
        return None
    if any(not _same(record.get(name), value) for name, value in (
            ("command_accel_mm_s2", TRIAL_ACCEL_MM_S2),
            ("command_decel_mm_s2", TRIAL_DECEL_MM_S2),
            ("command_jerk_mm_s3", TRIAL_JERK_MM_S3),
            ("target_fwhm_width_mm", target.width_mm),
            ("still_water_depth_mm", target.water_depth_mm),
            ("measurement_station_mm", station_mm))):
        return None
    saved_pistons = record.get("display_pistons")
    if (not isinstance(saved_pistons, list) or
            any(type(piston) is not int for piston in saved_pistons) or
            sorted(saved_pistons) != sorted(pistons)):
        return None

    lift = _number(record.get("floor_lift_mm"))
    speed = _number(record.get("command_speed_mm_s"))
    crest = _number(record.get("observed_crest_rise_mm"))
    width = _number(record.get("observed_fwhm_width_mm"))
    if (lift is None or lift != int(lift) or not 1 <= lift <= MAX_TRIAL_LIFT_MM or
            speed is None or not 1 <= speed <= MAX_TRIAL_SPEED_MM_S or
            crest is None or crest <= 0 or width is None or width <= 0):
        return None
    if not _same(record.get("floor_end_mm"), BOTTOM_MM - lift):
        return None
    if abs(width / target.width_mm - 1.0) > MAX_WIDTH_ERROR_FRACTION:
        return None
    pulse_time = lift / speed
    if (abs(pulse_time / target.half_height_seconds - 1.0) >
            MAX_PULSE_TIME_ERROR_FRACTION):
        return None

    positions = record.get("actual_end_positions_mm")
    if not isinstance(positions, dict):
        return None
    for piston in pistons:
        actual = _number(positions.get(str(piston)))
        if actual is None or abs(actual - (BOTTOM_MM - lift)) > POSITION_TOLERANCE_MM:
            return None
    return int(lift), crest


def suggest_lift(target: SolitaryTarget, display_pistons: Sequence[int],
                 station_mm: float, directory: Optional[Path] = None
                 ) -> Optional[LiftSuggestion]:
    """Interpolate only inside a repeatable, matched physical data range.

    At least three observations are required at each of two distinct lifts.
    Target width, water depth, piston selection, station, controller profile,
    pulse duration, measured width, and endpoint readback must match. This
    prevents simulator data, incomplete runs, and unrelated tank setups from
    silently becoming a motor command.
    """
    station = _number(station_mm)
    if station is None or station < 0 or not display_pistons:
        return None
    if not target.width_matches_depth:
        return None
    folder = directory if directory is not None else paths.ANALYTICS_DIR / "soliton-trials"
    if not folder.is_dir():
        return None

    by_lift = {}
    for path in folder.glob("trial-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue  # One damaged record cannot hide valid measured runs.
        if not isinstance(record, dict):
            continue
        sample = _matching_observation(record, target, display_pistons, station)
        if sample is not None:
            lift, crest = sample
            by_lift.setdefault(lift, []).append(crest)

    levels = []
    for lift, crests in sorted(by_lift.items()):
        if len(crests) < MIN_REPEATS_PER_LIFT:
            continue
        middle = float(median(crests))
        if (max(crests) - min(crests) >
                max(MAX_HEIGHT_SPREAD_MM, MAX_HEIGHT_SPREAD_FRACTION * middle)):
            continue
        levels.append((lift, middle, len(crests)))
    if len(levels) < 2:
        return None
    if any(right[1] <= left[1] for left, right in zip(levels, levels[1:])):
        return None

    desired = target.crest_height_mm
    if desired < levels[0][1] or desired > levels[-1][1]:
        return None  # Never extrapolate beyond observed water heights.
    for lift, crest, count in levels:
        if desired == crest:
            return LiftSuggestion(lift, lift, lift, crest, crest, count)
    for lower, upper in zip(levels, levels[1:]):
        if lower[1] < desired < upper[1]:
            fraction = (desired - lower[1]) / (upper[1] - lower[1])
            estimate = int(round(lower[0] + fraction * (upper[0] - lower[0])))
            return LiftSuggestion(
                estimate, lower[0], upper[0], lower[1], upper[1],
                lower[2] + upper[2])
    return None
