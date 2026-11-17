"""Reading the water's height from a laser beam shone up through it.

A laser on the tank floor points straight up.  Where it passes through water
the beam glows with the light the water scatters; where it passes through air
it glows far less, but it does not vanish -- dust lights it all the way to the
ceiling.  So the surface is not where the beam ends: it is where the beam's
brightness changes, and often a small bright spot besides, where the light
crosses the surface.  The beam stays where it is, so each picture gives the
height of the water at one spot, and a clip gives that height over time.

The camera looks through the side wall, and the printed markers on the glass
(``calibration``) turn pixels into millimetres, exactly as for ``waterline``.
The beam is behind the glass, not on it, so it looks a little smaller than the
markers do; ``beam_depth_mm`` corrects for that once it is measured.

Two settings say how the surface shows on the beam.  ``"step"`` looks for the
change from the brighter water to the dimmer air (or the reverse; still water
decides which).  ``"spot"`` looks for the brightest point along the beam, for
when the crossing glints more than the water glows.  Look at the check picture
before believing either.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

from vision.calibration import Rig, View


#: How the beam's colour is told from everything else, by its BGR channel.
COLOUR_CHANNELS = {"blue": 0, "green": 1, "red": 2}
#: Water's refractive index: how much nearer the beam looks than it is.
WATER_REFRACTIVE_INDEX = 1.333
#: Rows either side of a row that its above/below brightness is averaged over.
STEP_WINDOW_MM = 6.0
#: A reading fainter than this fraction of the still water's is not believed.
MIN_STRENGTH_FRACTION = 0.3
#: A surface found this close to either end of the searched beam has left it.
EDGE_MM = 4.0


class LaserGauge:
    """The height of the water where the laser crosses it, picture by picture."""

    def __init__(self, rig: Rig, view: View, beam_x_mm: float,
                 z_range_mm: Optional[Tuple[float, float]] = None,
                 half_width_mm: float = 15.0, colour: str = "green",
                 mode: str = "step", beam_depth_mm: float = 0.0,
                 mm_per_row: Optional[float] = None) -> None:
        if colour not in COLOUR_CHANNELS:
            raise ValueError("colour must be one of {0}".format(sorted(COLOUR_CHANNELS)))
        if mode not in ("step", "spot"):
            raise ValueError("mode must be 'step' or 'spot'")
        self.rig = rig
        self.view = view
        self.beam_x_mm = float(beam_x_mm)
        self.z_range_mm = tuple(float(v) for v in (z_range_mm or rig.z_range_mm))
        if not self.z_range_mm[1] > self.z_range_mm[0]:
            raise ValueError("z_range_mm must run from low to high.")
        self.half_width_mm = float(half_width_mm)
        self.colour = colour
        self.mode = mode
        self.beam_depth_mm = float(beam_depth_mm)
        self._channel = COLOUR_CHANNELS[colour]
        self._centre_z = 0.5 * (self.z_range_mm[0] + self.z_range_mm[1])
        self.mm_per_row = float(mm_per_row or view.mm_per_pixel(self.beam_x_mm, self._centre_z))
        self._x_range = (self.beam_x_mm - self.half_width_mm,
                         self.beam_x_mm + self.half_width_mm)
        self._window = max(int(round(STEP_WINDOW_MM / self.mm_per_row)), 2)
        self._edge_rows = max(int(round(EDGE_MM / self.mm_per_row)), 1)
        self._polarity = 0       # +1: brighter below the surface; -1: brighter above
        self._columns: Optional[slice] = None
        self._background: Optional[np.ndarray] = None
        self._typical: Optional[float] = None
        self.still_mm: Optional[float] = None

    # -- geometry ---------------------------------------------------------
    @property
    def scale(self) -> float:
        """How much larger a height on the beam is than it looks against the markers."""
        distance = self.rig.camera_distance_mm
        if distance <= 0:
            return self.rig.wall_scale
        return self.rig.wall_scale + self.beam_depth_mm / (WATER_REFRACTIVE_INDEX * distance)

    def row_height(self, row) -> np.ndarray:
        """Height above the floor of a (fractional) row of the beam strip."""
        seen = self.z_range_mm[1] - (np.asarray(row, dtype=np.float64) + 0.5) * self.mm_per_row
        return self._centre_z + (seen - self._centre_z) * self.scale

    def to_pixels(self, height_mm: float) -> Tuple[float, float]:
        """Where a height on the beam appears in the camera's picture."""
        seen = self._centre_z + (height_mm - self._centre_z) / self.scale
        point = self.view.to_pixels([(self.beam_x_mm, seen)])[0]
        return float(point[0]), float(point[1])

    # -- reading ----------------------------------------------------------
    def strip(self, frame: np.ndarray) -> np.ndarray:
        """The beam's colour standing out from grey, flattened: top row highest."""
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        flat = self.view.rectify(frame, self._x_range, self.z_range_mm,
                                 self.mm_per_row).astype(np.float32)
        others = [flat[:, :, c] for c in range(3) if c != self._channel]
        # How much more of the beam's colour there is than of the others: a
        # white glare or a grey wall scores nothing, a green beam scores high.
        score = flat[:, :, self._channel] - np.maximum(others[0], others[1])
        score = np.maximum(score, 0.0)
        if self._background is not None:
            score = np.maximum(score - self._background, 0.0)
        return score

    def profile(self, frame: np.ndarray) -> np.ndarray:
        """Brightness of the beam along its length, one value per row."""
        score = self.strip(frame)
        if self._columns is not None:
            score = score[:, self._columns]
        # The brightest few columns of each row: the beam itself, whatever
        # its width, without the dark wall either side diluting it.
        keep = min(3, score.shape[1])
        return np.sort(score, axis=1)[:, -keep:].mean(axis=1)

    def read(self, frame: np.ndarray) -> float:
        """Height of the surface above the floor, NaN when it cannot be seen."""
        return self._height(self.profile(frame), self._allowed_rows())

    def rise(self, frame: np.ndarray) -> float:
        if self.still_mm is None:
            raise RuntimeError("Learn the still water before asking for rise.")
        return self.read(frame) - self.still_mm

    # -- learning ---------------------------------------------------------
    def learn_background(self, frames: Sequence[np.ndarray]) -> None:
        """Pictures with the laser off: anything of its colour there is not the beam."""
        self._background = None
        self._background = np.median([self.strip(f) for f in frames], axis=0).astype(np.float32)

    def learn_still(self, frames: Sequence[np.ndarray],
                    near_depth_mm: Optional[float] = None,
                    within_mm: float = 25.0) -> float:
        """Take these pictures as still water: find the beam, the surface and its strength."""
        if not len(frames):
            raise ValueError("Learning still water needs at least one picture.")
        strips = np.array([self.strip(f) for f in frames])
        mean_strip = strips.mean(axis=0)
        # Lock onto the beam's own columns, so a reflection beside it does
        # not count.
        by_column = mean_strip.mean(axis=0)
        if not np.isfinite(by_column).any() or by_column.max() <= 0:
            raise ValueError(
                "No {0} light was found in the strip around x = {1:.0f} mm. "
                "Check the beam is on, the colour is right and beam_x_mm is "
                "where the beam is.".format(self.colour, self.beam_x_mm))
        centre = int(np.argmax(by_column))
        half = max(int(round(3.0 / self.mm_per_row)), 2)
        self._columns = slice(max(centre - half, 0), centre + half + 1)
        self.beam_found_x_mm = self._x_range[0] + (centre + 0.5) * self.mm_per_row

        profile = np.mean([self.profile(f) for f in frames], axis=0)
        allowed = self._allowed_rows(len(profile))
        if near_depth_mm is not None:
            near = np.abs(self.row_height(np.arange(len(profile))) - near_depth_mm) <= within_mm
            if not near.any():
                raise ValueError(
                    "The expected depth of {0:.0f} mm is outside the searched beam "
                    "({1:.0f} to {2:.0f} mm).".format(near_depth_mm, *self.z_range_mm))
            allowed &= near
        if self.mode == "step" and self._polarity == 0:
            self._polarity = max((1, -1), key=lambda sign: np.max(
                np.where(allowed, self._steps(profile, sign), 0.0)))
        row, strength = self._best(profile, allowed)
        if row is None or strength <= 0:
            raise ValueError("The surface could not be found on the beam in still water.")
        self._typical = strength
        readings = [self.read(f) for f in frames]
        self.still_mm = float(np.nanmedian(readings))
        return self.still_mm

    # -- internals --------------------------------------------------------
    def _allowed_rows(self, count: Optional[int] = None) -> np.ndarray:
        if count is None:
            count = int(round((self.z_range_mm[1] - self.z_range_mm[0]) / self.mm_per_row))
            count = max(count, 1)
        allowed = np.ones(count, dtype=bool)
        allowed[:self._edge_rows] = False
        allowed[count - self._edge_rows:] = False
        return allowed

    def _steps(self, profile: np.ndarray, sign: int) -> np.ndarray:
        """How much brighter just below each row is than just above it."""
        window = self._window
        padded = np.concatenate([np.full(window, profile[0]), profile,
                                 np.full(window, profile[-1])])
        sums = np.concatenate([[0.0], np.cumsum(padded)])
        rows = np.arange(len(profile)) + window
        # The boundary sits between row i-1 and row i (i is the first row below).
        above = (sums[rows] - sums[rows - window]) / window
        below = (sums[rows + window] - sums[rows]) / window
        return sign * (below - above)

    def _best(self, profile: np.ndarray, allowed: np.ndarray):
        if self.mode == "spot":
            smooth = cv2.GaussianBlur(profile.reshape(-1, 1).astype(np.float32),
                                      (1, 0), 1.0).ravel()
            score = np.where(allowed, smooth - np.median(smooth), -np.inf)
        else:
            score = np.where(allowed, self._steps(profile, self._polarity), -np.inf)
        if not np.isfinite(score).any():
            return None, 0.0
        row = int(np.argmax(score))
        return row, float(score[row])

    def _height(self, profile: np.ndarray, allowed: np.ndarray) -> float:
        if len(allowed) != len(profile):
            allowed = self._allowed_rows(len(profile))
        row, strength = self._best(profile, allowed)
        if row is None:
            return float("nan")
        if self._typical and strength < MIN_STRENGTH_FRACTION * self._typical:
            return float("nan")
        if self.mode == "spot":
            if 0 < row < len(profile) - 1:
                a, b, c = profile[row - 1], profile[row], profile[row + 1]
                curve = a - 2.0 * b + c
                nudge = 0.5 * (a - c) / curve if curve < 0 else 0.0
            else:
                nudge = 0.0
            return float(self.row_height(row + np.clip(nudge, -0.5, 0.5)))
        # Step: the boundary lies between row-1 and row.  Place it where the
        # profile crosses half way between the air's and the water's level.
        window = self._window
        lo, hi = max(row - window, 0), min(row + window, len(profile))
        air = float(np.mean(profile[lo:row])) if row > lo else float(profile[row])
        water = float(np.mean(profile[row:hi]))
        half = 0.5 * (air + water)
        segment = profile[lo:hi]
        boundary = float(row) - 0.5
        rows = np.arange(lo, hi)
        signed = self._polarity * (segment - half)
        crossings = np.nonzero((signed[:-1] < 0) & (signed[1:] >= 0))[0]
        if len(crossings):
            pick = crossings[np.argmin(np.abs(rows[crossings] + 0.5 - boundary))]
            a, b = signed[pick], signed[pick + 1]
            boundary = rows[pick] + (-a / (b - a) if b != a else 0.5)
        return float(self.row_height(boundary))
