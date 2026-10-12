"""Reading the height of the water surface along the camera's view.

Seen through the side wall, the surface is the line where the picture changes
from what is above the water to what is below it.  In every column of the
view the surface is somewhere on that brightness change; the reader follows
it from one end of the view to the other and reports its height in
millimetres above the tank floor.

Two things in a real tank look like a surface and are not: anything on the
glass with a level edge (a rail, tape, an old tide mark), and anything in
front of the surface (a post, a clamp).  The first is handled by learning what
the wall looks like while the water is still and discounting every edge that
was already there, away from the water.  The second leaves a gap, which is
reported as NaN rather than guessed at.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import cv2
import numpy as np

from vision.calibration import Rig, View, as_grey


# Steepest surface followed, as rise over run.  A wave much past 0.5 is
# breaking, and a breaking wave has no single surface height to report.
MAX_SLOPE = 1.5

# What one row of wandering costs the trace, as a fraction of a typical
# surface edge.  Low enough to follow a steep face, high enough that a short
# stretch of something brighter does not pull the line off the water.
STIFFNESS = 0.15

# A column whose edge is fainter than this fraction of the typical one is
# hidden or washed out, and is left as a gap.
MIN_EDGE_FRACTION = 0.3

# How far either side of the still surface an edge is taken to be the water
# itself rather than something on the glass.
STILL_BAND_MM = 4.0
STILL_BAND_ROWS = 5

# Rows at the top and bottom of the strip where a reading is not believed.
EDGE_ROWS = 3


class WaterlineReader:
    """Reads the surface from pictures taken through one fixed view."""

    def __init__(self, rig: Rig, view: View, column_mm: float = 2.0) -> None:
        self.rig = rig
        self.view = view
        x_low, x_high = rig.x_range_mm
        z_low, z_high = rig.z_range_mm
        self._centre = ((x_low + x_high) / 2.0, (z_low + z_high) / 2.0)
        self.mm_per_row = view.mm_per_pixel(*self._centre)
        self.column_mm = max(float(column_mm), self.mm_per_row)
        self.columns = max(int(round((x_high - x_low) / self.column_mm)), 2)
        self.column_mm = (x_high - x_low) / self.columns
        self._max_step = max(int(math.ceil(
            MAX_SLOPE * self.column_mm / self.mm_per_row)), 1)
        self._polarity = {None: 0, True: -1, False: 1}[rig.water_is_darker]
        self._clutter: Optional[np.ndarray] = None
        self._still_edge: Optional[float] = None
        self.still_mm: Optional[np.ndarray] = None

        seen_x = x_low + (np.arange(self.columns) + 0.5) * self.column_mm
        #: Distance along the tank of each column, corrected for the glass.
        self.x_mm = self._centre[0] + (seen_x - self._centre[0]) * rig.wall_scale

    # -- reading ----------------------------------------------------------
    def strip(self, frame: np.ndarray) -> np.ndarray:
        """The searched part of the wall, flattened, one column per reading."""
        flat = self.view.rectify(as_grey(frame), self.rig.x_range_mm,
                                 self.rig.z_range_mm, self.mm_per_row)
        # Averaging neighbouring columns rather than skipping them keeps all
        # the light the camera collected, which is what the edge is found in.
        return cv2.resize(flat, (self.columns, flat.shape[0]),
                          interpolation=cv2.INTER_AREA).astype(np.float32)

    def read(self, frame: np.ndarray) -> np.ndarray:
        """Surface height above the floor at each column, NaN where hidden."""
        strength = _edge_strength(self.strip(frame), self._polarity)
        if self._clutter is not None:
            strength = np.maximum(strength - self._clutter, 0.0)
        return self._heights(strength)

    def rise(self, frame: np.ndarray) -> np.ndarray:
        """Surface height above the still water learned earlier."""
        if self.still_mm is None:
            raise RuntimeError("Learn the still water before asking for rise.")
        return self.read(frame) - self.still_mm

    def learn_still(self, frames: Sequence[np.ndarray],
                    near_depth_mm: Optional[float] = None,
                    within_mm: float = 25.0) -> np.ndarray:
        """Take these pictures as still water and remember what they show.

        ``near_depth_mm`` is the operator's own depth measurement.  It only
        has to be roughly right: it stops a rail or a tide mark elsewhere in
        the strip being taken for the water when it happens to be the
        sharpest level edge in view.
        """
        if not len(frames):
            raise ValueError("Learning still water needs at least one picture.")
        average = np.mean([self.strip(frame) for frame in frames], axis=0)
        rows = np.arange(average.shape[0])
        allowed = np.ones(average.shape[0], dtype=bool)
        if near_depth_mm is not None:
            allowed = np.abs(self._row_height(rows) - near_depth_mm) <= within_mm
            if not allowed.any():
                raise ValueError(
                    "The expected depth of {0:.0f} mm is outside the strip the "
                    "rig searches ({1:.0f} to {2:.0f} mm).".format(
                        near_depth_mm, *self.rig.z_range_mm))

        if self._polarity == 0:
            # Whichever way round the brightness changes at the water, the
            # right guess is the one that finds the stronger level line.
            self._polarity = max((-1, 1), key=lambda sign: _trace_score(
                _edge_strength(average, sign) * allowed[:, None], self._max_step))

        strength = _edge_strength(average, self._polarity)
        path, found = _trace(strength * allowed[:, None], self._max_step)
        self._still_edge = float(np.median(found))
        # Never narrower than the blur of the edge itself, which is a few
        # rows wide however coarse the picture: clipping its tails would
        # skew every reading taken a few millimetres from still.
        band = max(int(round(STILL_BAND_MM / self.mm_per_row)), STILL_BAND_ROWS)
        away = np.abs(rows[:, None] - path[None, :]) > band
        self._clutter = np.where(away, strength, 0.0).astype(np.float32)
        readings = np.array([self.read(frame) for frame in frames])
        # A column hidden behind a post is NaN in every picture and stays so.
        hidden = np.all(np.isnan(readings), axis=0)
        self.still_mm = np.where(
            hidden, np.nan, np.nanmedian(np.where(hidden, 0.0, readings), axis=0))
        return self.still_mm

    def outline_px(self, heights_mm: np.ndarray) -> np.ndarray:
        """Where a set of readings falls in the camera's picture, for drawing.

        One row per visible column.  Undoes the glass correction first,
        because the picture shows the surface where it appears, not where it is.
        """
        shown = np.isfinite(heights_mm)
        seen_x = self._centre[0] + (self.x_mm[shown] - self._centre[0]) / self.rig.wall_scale
        seen_z = self._centre[1] + (heights_mm[shown] - self._centre[1]) / self.rig.wall_scale
        return self.view.to_pixels(np.column_stack([seen_x, seen_z]))

    # -- internals --------------------------------------------------------
    def _row_height(self, row) -> np.ndarray:
        seen = self.rig.z_range_mm[1] - (np.asarray(row, dtype=np.float64) + 0.5) * self.mm_per_row
        return self._centre[1] + (seen - self._centre[1]) * self.rig.wall_scale

    def _heights(self, strength: np.ndarray) -> np.ndarray:
        path, found = _trace(strength, self._max_step)
        columns = np.arange(strength.shape[1])
        last = strength.shape[0] - 1
        # The trace pays for every row it moves, so on a gentle slope it
        # steps a column or two late.  Its row is a neighbourhood, not an
        # answer: the sharpest of the three rows around it is the surface.
        nearby = np.stack([np.clip(path + offset, 0, last) for offset in (-1, 0, 1)])
        path = nearby[np.argmax(strength[nearby, columns], axis=0), columns]
        inside = np.clip(path, 1, last - 1)
        above = strength[inside - 1, columns]
        here = strength[inside, columns]
        below = strength[inside + 1, columns]
        # The edge is sharpest somewhere between pixel rows; a parabola
        # through the three rows around the peak says where.
        curve = above - 2.0 * here + below
        nudge = np.where(curve < 0, 0.5 * (above - below) / np.where(curve < 0, curve, 1.0), 0.0)
        height = self._row_height(inside + np.clip(nudge, -0.5, 0.5))
        # Judged against the still water once that is known.  The picture's
        # own typical edge is no yardstick when the wave has left the strip
        # along most of the view: then faint is typical.
        typical = self._still_edge if self._still_edge else float(np.median(found))
        height[strength[path, columns] < MIN_EDGE_FRACTION * typical] = np.nan
        # A line at the very top or bottom of the strip means the surface
        # has left it; the edge of the strip is not a reading of the water.
        height[(path < EDGE_ROWS) | (path > last - EDGE_ROWS)] = np.nan
        return height


def _edge_strength(strip: np.ndarray, polarity: int) -> np.ndarray:
    """How much each pixel looks like the surface, in grey levels per row.

    ``polarity`` is -1 when the picture is darker below the surface, +1 when
    it is brighter, and 0 when that is not known yet.
    """
    smooth = cv2.GaussianBlur(strip, (0, 0), sigmaX=1.5, sigmaY=1.0)
    change = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    if polarity == 0:
        return np.abs(change)
    return np.maximum(polarity * change, 0.0)


def _trace(strength: np.ndarray, max_step: int):
    """The best line from the first column to the last.

    Each column's sharpest edge taken alone would jump to a reflection or a
    scratch whenever one outshone the water.  The line chosen here is instead
    the one that collects the most edge over the whole view while paying for
    every row it moves between neighbouring columns, so it stays on the water
    through a stretch where something else is brighter.  Returns the row in
    each column and the edge strength found there.
    """
    rows, columns = strength.shape
    steps = np.arange(-max_step, max_step + 1)
    typical = float(np.median(strength.max(axis=0)))
    cost = (STIFFNESS * typical * np.abs(steps))[:, None]
    reach = np.arange(rows)[None, :] + steps[:, None] + max_step
    padded = np.full(rows + 2 * max_step, -np.inf)
    came_from = np.zeros((columns, rows), dtype=np.int16)
    best = strength[:, 0].astype(np.float64)
    every_row = np.arange(rows)
    for column in range(1, columns):
        padded[max_step:max_step + rows] = best
        options = padded[reach] - cost
        choice = np.argmax(options, axis=0)
        came_from[column] = choice
        best = options[choice, every_row] + strength[:, column]
    path = np.empty(columns, dtype=np.int64)
    path[-1] = int(np.argmax(best))
    for column in range(columns - 1, 0, -1):
        path[column - 1] = path[column] + steps[came_from[column, path[column]]]
    return path, strength[path, np.arange(columns)]


def _trace_score(strength: np.ndarray, max_step: int) -> float:
    return float(np.sum(_trace(strength, max_step)[1]))
