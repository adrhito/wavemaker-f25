"""Reading the water's height along the side wall, from the waterline's edge.

With the room lit and a dark sheet behind the far wall, the camera sees the
room above the water and the dark sheet below it.  The waterline is the
sharpest bright-above, dark-below edge in each column of the picture, and its
row gives the height of the water at that column.  Every picture is therefore
a slice of the wave along about a metre of tank, not a single point.

The edge is found to a fraction of a pixel by fitting a parabola to the
brightness gradient around its peak.  An edge that is too weak (a splash, a
hand, a reflection) gives NaN, never a guess.

Scale comes from the tapes on the glass: strips with their bottom edges a
known distance apart (6 in).  ``tape_rows`` finds them, and ``Scale`` turns
rows into millimetres above the still water.  The tapes and the waterline
are on the same glass, so no refraction correction is needed for the
height at the wall.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

#: The tapes' bottom edges are this far apart.
TAPE_PITCH_MM = 152.4
#: An edge weaker than this (grey levels per pixel) is not believed.
MIN_STEP = 6.0


def edge_rows(gray: np.ndarray, lo: int, hi: int, bin_px: int = 1,
              min_step: float = MIN_STEP) -> np.ndarray:
    """Sub-pixel row of the bright-above, dark-below edge in each column.

    ``gray`` is one picture (rows x columns).  Only rows ``lo..hi`` are
    searched.  Columns are first averaged in groups of ``bin_px``.  Returns
    one row per group, NaN where no believable edge was found.
    """
    g = np.asarray(gray, dtype=np.float32)
    if bin_px > 1:
        width = (g.shape[1] // bin_px) * bin_px
        g = g[:, :width].reshape(g.shape[0], -1, bin_px).mean(axis=2)
    # Central difference, positive where it gets darker going down.
    d = np.zeros_like(g)
    d[1:-1] = (g[:-2] - g[2:]) * 0.5
    band = d[lo:hi]
    peak = np.argmax(band, axis=0)
    cols = np.arange(band.shape[1])
    strength = band[peak, cols]
    inner = (peak > 0) & (peak < band.shape[0] - 1)
    pk = np.clip(peak, 1, band.shape[0] - 2)
    a, b, c = band[pk - 1, cols], band[pk, cols], band[pk + 1, cols]
    denom = a - 2 * b + c
    with np.errstate(divide="ignore", invalid="ignore"):
        shift = np.where(np.abs(denom) > 1e-6, 0.5 * (a - c) / denom, 0.0)
    shift = np.clip(shift, -0.5, 0.5)
    rows = lo + peak + np.where(inner, shift, 0.0)
    rows = rows.astype(np.float64)
    rows[(strength < min_step) | ~inner] = np.nan
    return rows


def edge_rows_near(gray: np.ndarray, lo: int, hi: int, last: np.ndarray,
                   track: float, min_step: float = MIN_STEP,
                   strength_out: Optional[list] = None) -> np.ndarray:
    """``edge_rows`` searching only ``track`` rows either side of ``last``.

    Columns whose ``last`` is NaN search the whole band.  Vectorised, for
    reading every column of a live picture at the camera's rate.
    """
    g = np.asarray(gray, dtype=np.float32)
    d = np.zeros_like(g)
    d[1:-1] = (g[:-2] - g[2:]) * 0.5
    band = d[lo:hi]
    ys = np.arange(lo, hi, dtype=np.float32)[:, None]
    near = np.abs(ys - last[None, :]) <= track
    near |= np.isnan(last)[None, :]
    masked = np.where(near, band, -np.inf)
    peak = np.argmax(masked, axis=0)
    cols = np.arange(band.shape[1])
    strength = masked[peak, cols]
    inner = (peak > 0) & (peak < band.shape[0] - 1)
    pk = np.clip(peak, 1, band.shape[0] - 2)
    a, b, c = band[pk - 1, cols], band[pk, cols], band[pk + 1, cols]
    denom = a - 2 * b + c
    with np.errstate(divide="ignore", invalid="ignore"):
        shift = np.where(np.abs(denom) > 1e-6, 0.5 * (a - c) / denom, 0.0)
    rows = (lo + peak + np.where(inner, np.clip(shift, -0.5, 0.5), 0.0)).astype(np.float64)
    rows[(strength < min_step) | ~inner] = np.nan
    if strength_out is not None:
        strength_out.append(np.where(np.isfinite(strength), strength, 0.0))
    return rows


def weak_columns(strengths: Sequence[np.ndarray], fraction: float = 0.45) -> np.ndarray:
    """Columns whose edge is persistently weak compared with the rest.

    Where something pale stands behind the tank instead of the dark sheet,
    the waterline barely shows and the strongest edge is often something
    else.  Those columns are better bridged by the fitted line than read.
    """
    typical = np.median(np.asarray(strengths), axis=0)
    return typical < fraction * np.median(typical)


def fit_line(rows: np.ndarray, window: int = 61, reject: float = 2.5,
             smooth: float = 12.0, min_fraction: float = 0.2) -> np.ndarray:
    """One smooth waterline through a picture's column edges.

    The surface is smooth across the window (waves here are metres long), so
    a column far from its neighbours is a glint, a post or a dark patch, not
    water.  Columns more than ``reject`` pixels from the running median of
    ``window`` columns are dropped (twice, the second time against the
    cleaned line), and the rest are averaged with a Gaussian ``smooth``
    columns wide, which also bridges the gaps.  Returns NaN everywhere if
    fewer than ``min_fraction`` of the columns survive.
    """
    from scipy.ndimage import gaussian_filter1d, median_filter

    rows = np.asarray(rows, dtype=float)
    good = ~np.isnan(rows)
    if good.mean() < min_fraction:
        return np.full_like(rows, np.nan)
    x = np.arange(len(rows))
    centre = None
    for _ in range(2):
        filled = np.interp(x, x[good], rows[good])
        if centre is not None:
            # Rejected columns take the cleaned line, not their stray value.
            filled = np.where(good, rows, centre)
        med = median_filter(filled, window, mode="nearest")
        good = ~np.isnan(rows) & (np.abs(rows - med) <= reject)
        if good.mean() < min_fraction:
            return np.full_like(rows, np.nan)
        centre = med
    weights = gaussian_filter1d(good.astype(float), smooth, mode="nearest")
    values = gaussian_filter1d(np.where(good, rows, 0.0), smooth, mode="nearest")
    line = values / np.maximum(weights, 1e-9)
    # Far from any surviving column the line is a guess: say so.
    line[weights < 0.02] = np.nan
    return line


def edges(frames: Sequence[np.ndarray], lo: int, hi: int, bin_px: int = 1,
          min_step: float = MIN_STEP, track: Optional[float] = None) -> np.ndarray:
    """``edge_rows`` for every picture: an array of pictures x column groups.

    With ``track`` set, each column's search window follows its last good
    row, ``track`` pixels either way, so a strong edge elsewhere in the band
    cannot capture it once the waterline has been found.
    """
    out = []
    last = None
    for frame in frames:
        if track is None or last is None:
            rows = edge_rows(frame, lo, hi, bin_px, min_step)
        else:
            rows = _tracked(frame, lo, hi, bin_px, min_step, last, track)
        out.append(rows)
        if track is not None:
            last = rows if last is None else np.where(np.isnan(rows), last, rows)
    return np.array(out)


def _tracked(frame, lo, hi, bin_px, min_step, last, track) -> np.ndarray:
    rows = edge_rows(frame, lo, hi, bin_px, min_step)
    stray = np.abs(rows - last) > track
    if not np.any(stray):
        return rows
    # Search again near the last good row for the columns that jumped.
    g = np.asarray(frame, dtype=np.float32)
    if bin_px > 1:
        width = (g.shape[1] // bin_px) * bin_px
        g = g[:, :width].reshape(g.shape[0], -1, bin_px).mean(axis=2)
    for col in np.flatnonzero(stray):
        if np.isnan(last[col]):
            continue
        a = int(max(lo, np.floor(last[col] - track)))
        b = int(min(hi, np.ceil(last[col] + track) + 1))
        if b - a < 3:
            rows[col] = np.nan
            continue
        rows[col] = edge_rows(g[:, col:col + 1], a, b, 1, min_step)[0]
    return rows


def tape_rows(bgr: np.ndarray, x0: int, x1: int, redness: float = 25.0) -> np.ndarray:
    """Rows of the bottom edges of the red tapes between columns x0 and x1."""
    a = np.asarray(bgr, dtype=np.float32)[:, x0:x1]
    red = a[..., 2] - np.maximum(a[..., 0], a[..., 1])
    profile = np.median(red, axis=1)
    mask = profile > redness
    bottoms = []
    for y in range(1, len(mask)):
        if mask[y - 1] and not mask[y]:
            # Sub-pixel: where the profile crosses half its local height.
            top = profile[y - 1]
            frac = (top - redness) / max(top - profile[y], 1e-6)
            bottoms.append(y - 1 + min(max(frac, 0.0), 1.0))
    return np.array(bottoms)


@dataclass
class Scale:
    """Rows to millimetres above the still water."""

    mm_per_px: float
    still_row: np.ndarray  # per column group, or a single value

    @classmethod
    def from_tapes(cls, bottoms: Sequence[float], still_row,
                   pitch_mm: float = TAPE_PITCH_MM) -> "Scale":
        b = np.sort(np.asarray(bottoms, dtype=float))
        if len(b) < 2:
            raise ValueError("need at least two tapes for a scale")
        # Least squares over every tape: row = r0 + k * index.
        k = np.polyfit(np.arange(len(b)), b, 1)[0]
        return cls(pitch_mm / k, np.asarray(still_row, dtype=float))

    def height_mm(self, rows: np.ndarray) -> np.ndarray:
        """Height above the still water, upward positive."""
        return (self.still_row - np.asarray(rows, dtype=float)) * self.mm_per_px
