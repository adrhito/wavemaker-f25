"""Turning a run of surface readings into the numbers a trial is recorded with.

A solitary wave in this tank is longer than the camera can see at once: thirty
millimetres of crest on 150 mm of water is nearly 700 mm wide at half height,
and it gets longer as the water gets deeper.  So nothing here measures a wave
from one picture.  Height comes from watching one place on the wall while the
wave goes by, speed from how much later the crest reaches the far side of the
view than the near side, and width from the two together: how long the surface
stays above half height, times how fast the wave is moving.

Everything takes plain arrays, so a saved run can be measured again later
without the video or OpenCV.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

import numpy as np

from app.solitons import SolitaryTarget


_HALF_HEIGHT_ARGUMENT = math.acosh(math.sqrt(2.0))

# A column's arrival time is only trusted when the wave there is at least
# this fraction of the height seen at the station.
MIN_ARRIVAL_FRACTION = 0.5

# Speed needs the crest timed over a real distance; a short baseline turns a
# frame of timing error into a large error in speed.
MIN_SPEED_BASELINE_MM = 200.0
MIN_SPEED_COLUMNS = 8

# The station is a short stretch of wall, not a point, so that one noisy
# column cannot set the crest height.  Kept short because the wave is not
# level across it: a wide station reads the crest as the average of its
# top and its shoulders, which is always too low.
STATION_HALFWIDTH_MM = 15.0


@dataclass(frozen=True)
class PulseMeasurement:
    """One wave as the camera saw it.  Lengths in mm, times in seconds."""

    station_mm: float
    crest_rise_mm: float
    #: False when the top of the wave was hidden or outside the strip the rig
    #: searches, in which case the crest height is too low and not to be used.
    crest_seen: bool
    trough_mm: float
    crest_to_trough_mm: float
    crest_time_s: float
    #: None when the surface never came back below half height in the record.
    half_height_seconds: Optional[float]
    #: None when the crest could not be timed across enough of the view.
    speed_mm_s: Optional[float]
    #: +1 when the wave travels towards larger x, -1 the other way.
    direction: Optional[int]
    fwhm_mm: Optional[float]
    #: Typical gap between the wave and a sech² of the same height and width,
    #: as a fraction of the crest.  Small means it looks like a soliton.
    shape_misfit: Optional[float]
    still_depth_mm: Optional[float]
    #: What first-order theory gives for a soliton of this height and depth.
    theory_fwhm_mm: Optional[float]
    theory_speed_mm_s: Optional[float]

    def to_record(self) -> dict:
        return {name: (round(value, 4) if isinstance(value, float) else value)
                for name, value in asdict(self).items()}


def fill_gaps(profile: np.ndarray) -> np.ndarray:
    """Bridge hidden columns from their neighbours; all-hidden stays hidden."""
    profile = np.asarray(profile, dtype=np.float64)
    known = np.isfinite(profile)
    if known.all() or not known.any():
        return profile.copy()
    places = np.arange(profile.size)
    return np.interp(places, places[known], profile[known])


def station_series(x_mm: np.ndarray, rise_mm: np.ndarray, station_mm: float,
                   halfwidth_mm: float = STATION_HALFWIDTH_MM) -> np.ndarray:
    """Surface rise against time at one place, steadied over nearby columns.

    NaN for any picture in which the surface was not seen there at all.
    """
    near = np.abs(np.asarray(x_mm) - station_mm) <= halfwidth_mm
    if not near.any():
        near = np.zeros(len(x_mm), dtype=bool)
        near[int(np.argmin(np.abs(np.asarray(x_mm) - station_mm)))] = True
    window = rise_mm[:, near]
    usable = np.isfinite(window).any(axis=1)
    series = np.full(rise_mm.shape[0], np.nan)
    series[usable] = np.nanmedian(window[usable], axis=1)
    return series


def _upper_half(series: np.ndarray) -> Optional[Tuple[int, int, float]]:
    """The unbroken run of samples above half the peak, and the peak itself.

    None when the run reaches either end of the record, because then the wave
    was still arriving or still leaving and its timing cannot be trusted.
    """
    if not np.isfinite(series).any():
        return None
    peak = int(np.nanargmax(series))
    height = float(series[peak])
    if height <= 0:
        return None
    above = series > 0.5 * height
    start = peak
    while start > 0 and above[start - 1]:
        start -= 1
    stop = peak
    while stop < series.size - 1 and above[stop + 1]:
        stop += 1
    if start == 0 or stop == series.size - 1:
        return None
    return start, stop, height


def _crossing(times_s: np.ndarray, series: np.ndarray, below: int, above: int,
              level: float) -> float:
    """When the surface passed ``level`` between two neighbouring samples."""
    span = series[above] - series[below]
    part = 0.5 if span == 0 else (level - series[below]) / span
    return float(times_s[below] + part * (times_s[above] - times_s[below]))


def arrival_times(times_s: np.ndarray, rise_mm: np.ndarray,
                  min_height_mm: float) -> np.ndarray:
    """When the wave passed each column: the middle of its upper half.

    The single highest sample would do for a sharp peak, but a crest is flat
    on top and a frame either side of the true peak is almost as high, so the
    highest sample wanders.  The balance point of everything above half
    height uses the whole top of the wave and does not.
    """
    arrivals = np.full(rise_mm.shape[1], np.nan)
    for column in range(rise_mm.shape[1]):
        series = fill_gaps(rise_mm[:, column])
        run = _upper_half(series)
        if run is None or run[2] < min_height_mm:
            continue
        start, stop, height = run
        weight = series[start:stop + 1] - 0.5 * height
        arrivals[column] = float(np.sum(times_s[start:stop + 1] * weight) / np.sum(weight))
    return arrivals


def pulse_speed(x_mm: np.ndarray, arrivals_s: np.ndarray) -> Optional[float]:
    """Signed speed along the tank from arrival time against position.

    Pairs each column with the one half a view away and takes the middle
    value of all those speeds, so a few mistimed columns beside a post do not
    drag the answer the way they would in a straight-line fit.
    """
    timed = np.isfinite(arrivals_s)
    places, arrivals = np.asarray(x_mm)[timed], arrivals_s[timed]
    if places.size < MIN_SPEED_COLUMNS:
        return None
    half = places.size // 2
    run = places[half:] - places[:places.size - half]
    if np.median(np.abs(run)) < MIN_SPEED_BASELINE_MM:
        return None
    delay = np.median((arrivals[half:] - arrivals[:places.size - half]) / run)
    if not np.isfinite(delay) or abs(delay) < 1e-9:
        return None
    return float(1.0 / delay)


def measure_pulse(times_s: np.ndarray, x_mm: np.ndarray, rise_mm: np.ndarray,
                  station_mm: Optional[float] = None,
                  still_depth_mm: Optional[float] = None) -> PulseMeasurement:
    """Measure the largest wave in a run of readings.

    ``rise_mm`` has one row per picture and one column per place along the
    wall, and is height above still water.  ``station_mm`` is the place the
    height is reported at; the middle of the view when not given.
    """
    times_s = np.asarray(times_s, dtype=np.float64)
    x_mm = np.asarray(x_mm, dtype=np.float64)
    rise_mm = np.asarray(rise_mm, dtype=np.float64)
    if rise_mm.shape != (times_s.size, x_mm.size):
        raise ValueError("Readings must be one row per time and one column per place.")
    if station_mm is None:
        station_mm = float((x_mm[0] + x_mm[-1]) / 2.0)

    seen = station_series(x_mm, rise_mm, station_mm)
    if not np.isfinite(seen).any():
        raise ValueError("The surface was never seen at the station.")
    series = fill_gaps(seen)
    peak = int(np.nanargmax(series))
    # Bridging a gap is fine on the flanks and wrong at the top: a crest
    # that left the searched strip, or passed behind something, would be
    # reported as the height of the last place it was still visible.
    crest_seen = bool(np.isfinite(seen[max(peak - 1, 0):peak + 2]).all())
    crest = float(series[peak])
    crest_time = float(times_s[peak])
    if 0 < peak < series.size - 1:
        # The crest falls between two pictures; a parabola through the three
        # highest samples recovers the top it would otherwise clip.
        before, after = series[peak - 1], series[peak + 1]
        curve = before - 2.0 * crest + after
        if curve < 0:
            offset = 0.5 * (before - after) / curve
            crest = float(crest - 0.25 * (before - after) * offset)
            crest_time += offset * float(times_s[peak + 1] - times_s[peak - 1]) / 2.0
    trough = float(np.nanmin(series))

    half_seconds = None
    run = _upper_half(series)
    if run is not None:
        start, stop, height = run
        rising = _crossing(times_s, series, start - 1, start, 0.5 * height)
        falling = _crossing(times_s, series, stop + 1, stop, 0.5 * height)
        half_seconds = falling - rising
        crest_time = float(np.sum(times_s[start:stop + 1] * (series[start:stop + 1] - 0.5 * height))
                           / np.sum(series[start:stop + 1] - 0.5 * height))

    signed = pulse_speed(x_mm, arrival_times(
        times_s, rise_mm, MIN_ARRIVAL_FRACTION * crest))
    speed = abs(signed) if signed is not None else None
    direction = None if signed is None else (1 if signed > 0 else -1)
    fwhm = half_seconds * speed if half_seconds and speed else None

    misfit = None
    if half_seconds:
        scale = half_seconds / (2.0 * _HALF_HEIGHT_ARGUMENT)
        near = np.abs(times_s - crest_time) <= 1.5 * half_seconds
        ideal = crest / np.cosh(np.minimum(np.abs(times_s[near] - crest_time) / scale, 350.0)) ** 2
        misfit = float(np.sqrt(np.mean((series[near] - ideal) ** 2)) / crest)

    theory_fwhm = theory_speed = None
    if still_depth_mm and crest > 0:
        theory = SolitaryTarget(crest, 1.0, still_depth_mm)
        theory_fwhm, theory_speed = theory.theoretical_width_mm, theory.phase_speed_mm_s

    return PulseMeasurement(
        station_mm=float(station_mm), crest_rise_mm=crest, crest_seen=crest_seen,
        trough_mm=trough,
        crest_to_trough_mm=crest - trough, crest_time_s=crest_time,
        half_height_seconds=half_seconds, speed_mm_s=speed, direction=direction,
        fwhm_mm=fwhm, shape_misfit=misfit, still_depth_mm=still_depth_mm,
        theory_fwhm_mm=theory_fwhm, theory_speed_mm_s=theory_speed)


def restlessness_mm(heights_mm: np.ndarray) -> float:
    """How much the surface is still moving, from a few seconds of readings.

    Each column's wobble over the window, with the worst tenth of columns set
    aside so one flickering reflection cannot keep the tank "moving" for
    ever.  The next trial should wait until this is down near what the camera
    reads on water known to be still.
    """
    heights_mm = np.asarray(heights_mm, dtype=np.float64)
    seen = np.isfinite(heights_mm).sum(axis=0) >= 2
    if not seen.any():
        return float("inf")
    return float(np.percentile(np.nanstd(heights_mm[:, seen], axis=0), 90))
