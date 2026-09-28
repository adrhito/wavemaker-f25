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


@dataclass(frozen=True)
class CalibrationAssessment:
    suggestion: Optional[LiftSuggestion]
    message: str
    matching_runs: int
    eligible_runs: int


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


def _core_matches(record: dict, target: SolitaryTarget,
                  pistons: Sequence[int], station_mm: float) -> bool:
    """Match the requested setup before judging a run's measured quality."""
    if (record.get("schema_version") != 1 or
            record.get("source") != "hardware" or
            record.get("pulse_status") != "completed"):
        return False
    if record.get("command_profile") != "S-curve":
        return False
    if any(not _same(record.get(name), value) for name, value in (
            ("command_accel_mm_s2", TRIAL_ACCEL_MM_S2),
            ("command_decel_mm_s2", TRIAL_DECEL_MM_S2),
            ("command_jerk_mm_s3", TRIAL_JERK_MM_S3),
            ("target_fwhm_width_mm", target.width_mm),
            ("still_water_depth_mm", target.water_depth_mm),
            ("measurement_station_mm", station_mm))):
        return False
    saved_pistons = record.get("display_pistons")
    if (not isinstance(saved_pistons, list) or
            any(type(piston) is not int for piston in saved_pistons) or
            sorted(saved_pistons) != sorted(pistons)):
        return False
    return True


def _matching_observation(record: dict, target: SolitaryTarget,
                          pistons: Sequence[int], station_mm: float):
    """Return (lift, crest) only for a comparable, measured physical pulse."""
    if not _core_matches(record, target, pistons, station_mm):
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
    """Return a matched suggestion, or None when the data cannot support one."""
    return assess_lift(target, display_pistons, station_mm, directory).suggestion


def assess_lift(target: SolitaryTarget, display_pistons: Sequence[int],
                station_mm: float, directory: Optional[Path] = None
                ) -> CalibrationAssessment:
    """Interpolate only inside a repeatable, matched physical data range.

    At least three observations are required at each of two distinct lifts.
    Target width, water depth, piston selection, station, controller profile,
    pulse duration, measured width, and endpoint readback must match. This
    prevents simulator data, incomplete runs, and unrelated tank setups from
    silently becoming a motor command.
    """
    station = _number(station_mm)
    if station is None or station < 0 or not display_pistons:
        return CalibrationAssessment(
            None, "Enter a fixed measurement station and select floor sections.",
            0, 0)
    if not target.width_matches_depth:
        return CalibrationAssessment(
            None, "The chosen height and width differ from first-order solitary-wave "
                  "theory at this depth. Adjust the target or use a manual trial.",
            0, 0)
    folder = directory if directory is not None else paths.ANALYTICS_DIR / "soliton-trials"
    if not folder.is_dir():
        return CalibrationAssessment(
            None, "No measured soliton trial files are available yet.", 0, 0)

    by_lift = {}
    matching_runs = 0
    eligible_runs = 0
    for path in folder.glob("trial-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue  # One damaged record cannot hide valid measured runs.
        if not isinstance(record, dict):
            continue
        if _core_matches(record, target, display_pistons, station):
            matching_runs += 1
        sample = _matching_observation(record, target, display_pistons, station)
        if sample is not None:
            eligible_runs += 1
            lift, crest = sample
            by_lift.setdefault(lift, []).append(crest)

    if matching_runs == 0:
        return CalibrationAssessment(
            None, "No completed hardware trials match this piston selection, "
                  "water depth, target width, motion profile, and station.",
            matching_runs, eligible_runs)
    if eligible_runs == 0:
        return CalibrationAssessment(
            None, "Matching trials need a positive observed crest, a measured "
                  "width near the target, and complete motor endpoint readbacks.",
            matching_runs, eligible_runs)

    levels = []
    unstable_lifts = []
    for lift, crests in sorted(by_lift.items()):
        if len(crests) < MIN_REPEATS_PER_LIFT:
            continue
        middle = float(median(crests))
        if (max(crests) - min(crests) >
                max(MAX_HEIGHT_SPREAD_MM, MAX_HEIGHT_SPREAD_FRACTION * middle)):
            unstable_lifts.append(lift)
            continue
        levels.append((lift, middle, len(crests)))
    if unstable_lifts:
        return CalibrationAssessment(
            None, "Crest measurements vary too much at floor lift(s) {0} mm. "
                  "Review the videos and repeat those settings.".format(
                      ", ".join(str(lift) for lift in unstable_lifts)),
            matching_runs, eligible_runs)
    if len(levels) < 2:
        counts = ", ".join("{0} mm: {1} run(s)".format(lift, len(crests))
                           for lift, crests in sorted(by_lift.items()))
        return CalibrationAssessment(
            None, "Need at least three consistent measured runs at each of two "
                  "floor lifts. Eligible runs: {0}.".format(counts),
            matching_runs, eligible_runs)
    if any(right[1] <= left[1] for left, right in zip(levels, levels[1:])):
        return CalibrationAssessment(
            None, "Observed crest height did not increase with floor lift. "
                  "Review the runs before using a lift suggestion.",
            matching_runs, eligible_runs)

    desired = target.crest_height_mm
    if desired < levels[0][1] or desired > levels[-1][1]:
        return CalibrationAssessment(
            None, "Requested crest is outside the measured {0:.1f}–{1:.1f} mm "
                  "range. Choose a manual trial; lift is never extrapolated.".format(
                      levels[0][1], levels[-1][1]),
            matching_runs, eligible_runs)
    for lift, crest, count in levels:
        if desired == crest:
            suggestion = LiftSuggestion(lift, lift, lift, crest, crest, count)
            return _ready(suggestion, matching_runs, eligible_runs)
    for lower, upper in zip(levels, levels[1:]):
        if lower[1] < desired < upper[1]:
            fraction = (desired - lower[1]) / (upper[1] - lower[1])
            estimate = int(round(lower[0] + fraction * (upper[0] - lower[0])))
            suggestion = LiftSuggestion(
                estimate, lower[0], upper[0], lower[1], upper[1],
                lower[2] + upper[2])
            return _ready(suggestion, matching_runs, eligible_runs)
    return CalibrationAssessment(None, "No matching lift bracket was found.",
                                 matching_runs, eligible_runs)


def _ready(suggestion: LiftSuggestion, matching_runs: int,
           eligible_runs: int) -> CalibrationAssessment:
    return CalibrationAssessment(
        suggestion,
        "Measured trials suggest {0} mm floor lift for the requested crest. "
        "Interpolated between {1} and {2} mm lifts using {3} matching runs. "
        "This is experimental, not a guaranteed wave height.".format(
            suggestion.floor_lift_mm, suggestion.lower_lift_mm,
            suggestion.upper_lift_mm, suggestion.trials_used),
        matching_runs, eligible_runs)
