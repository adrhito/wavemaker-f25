"""A drawn tank with a known wave on it.

The development Mac cannot see the real tank, so this is what the measuring
code is tested against: a side wall with markers on it, water of a chosen
depth, and one solitary wave of a chosen height travelling along it, seen by
a camera standing slightly off square.  Because the wave is drawn from a
formula, the right answer for its height, width and speed is known exactly.

It is a drawing, not a tank.  It has no meniscus, no reflections off the
underside of the surface, no glare on the glass and no dirt at the waterline.
Passing here says the arithmetic is right; only footage of the real tank says
whether the surface can be found in it.
"""

from __future__ import annotations

import math
from typing import Tuple

import cv2
import numpy as np

from app.solitons import SolitaryTarget
from vision.calibration import MARKER_DICTIONARY, Rig


MARKER_SIDE_MM = 60.0
WALL_X_MM = (0.0, 1400.0)


class SyntheticTank:
    """Pictures of one solitary wave passing a fixed camera."""

    def __init__(self, depth_mm: float = 150.0, crest_mm: float = 30.0,
                 image_size: Tuple[int, int] = (960, 540),
                 water_is_darker: bool = True, black_backdrop: bool = False,
                 clutter: bool = True,
                 launch_x_mm: float = -2000.0, keystone: float = 0.05,
                 noise_grey: float = 2.0, seed: int = 0) -> None:
        self.depth_mm = float(depth_mm)
        self.crest_mm = float(crest_mm)
        self.image_size = image_size
        self.clutter = clutter
        self.launch_x_mm = float(launch_x_mm)
        self.noise_grey = float(noise_grey)
        self._random = np.random.default_rng(seed)
        self._air, self._water = (190.0, 70.0) if water_is_darker else (70.0, 190.0)
        self._meniscus_mm = 0.0
        self._glare = 0.0
        if black_backdrop:
            # Black cloth behind the tank and bright lights overhead: the air
            # is black, the water glows faintly with scattered light, the line
            # where it meets the glass catches the light, and the white room
            # behind the camera lies over all of it as a reflection.
            self._air, self._water = 12.0, 38.0
            self._meniscus_mm = 1.5
            self._glare = 30.0

        wave = SolitaryTarget(self.crest_mm, 1.0, self.depth_mm)
        self.fwhm_mm = wave.theoretical_width_mm
        self.speed_mm_s = wave.phase_speed_mm_s
        self._wavenumber = math.sqrt(3.0 * self.crest_mm / (4.0 * self.depth_mm ** 3))

        # The wall is drawn from below the floor to well above the highest
        # crest, with a marker row in the water and another in the air.
        headroom = max(110.0, self.crest_mm + 50.0)
        self._z_mm = (-30.0, self.depth_mm + headroom + 120.0)
        self._rail_z_mm = self.depth_mm + 70.0
        self._post_x_mm = 480.0

        # Drawn at about the camera's own resolution: a finer drawing would be
        # point-sampled on the way into the picture, which snaps the surface
        # to whole pixels in a way no real sensor does.
        width_px, height_px = image_size
        self._mm_per_px = (WALL_X_MM[1] - WALL_X_MM[0]) / (0.94 * width_px)
        self._columns = int(round((WALL_X_MM[1] - WALL_X_MM[0]) / self._mm_per_px))
        self._rows = int(round((self._z_mm[1] - self._z_mm[0]) / self._mm_per_px))
        self._x_centres = WALL_X_MM[0] + (np.arange(self._columns) + 0.5) * self._mm_per_px
        self._row_tops = self._z_mm[1] - np.arange(self._rows) * self._mm_per_px

        low_row = 40.0
        high_row = self.depth_mm + headroom + 70.0
        self._fixed, markers_mm = self._draw_fixed({
            0: (100.0, high_row), 1: (700.0, high_row), 2: (1300.0, high_row),
            3: (100.0, low_row), 4: (700.0, low_row), 5: (1300.0, low_row)})
        self.rig = Rig(
            markers_mm=markers_mm,
            x_range_mm=(40.0, 1360.0),
            z_range_mm=(self.depth_mm - 60.0, self.depth_mm + headroom),
        )
        self._drawing_to_picture = self._camera(keystone)

    # -- the wave ---------------------------------------------------------
    def rise_mm(self, x_mm, time_s: float):
        """Height of the surface above still water."""
        crest_x = self.launch_x_mm + self.speed_mm_s * time_s
        reach = np.minimum(np.abs(self._wavenumber * (np.asarray(x_mm) - crest_x)), 350.0)
        return self.crest_mm / np.cosh(reach) ** 2

    def crest_time_s(self, x_mm: float) -> float:
        return (x_mm - self.launch_x_mm) / self.speed_mm_s

    # -- the picture ------------------------------------------------------
    def frame(self, time_s: float) -> np.ndarray:
        surface = self.depth_mm + self.rise_mm(self._x_centres, time_s)
        # Fraction of each drawn pixel that is under water, so the surface
        # lands between pixel rows exactly where the formula puts it.
        wet = np.clip((surface[None, :] - (self._row_tops[:, None] - self._mm_per_px))
                      / self._mm_per_px, 0.0, 1.0)
        lighting = 1.0 + 0.15 * (self._x_centres - 700.0) / 700.0
        drawing = self._air * (1.0 - wet) + self._water * wet
        if self._meniscus_mm:
            under = np.clip((surface[None, :] - self._meniscus_mm
                             - (self._row_tops[:, None] - self._mm_per_px))
                            / self._mm_per_px, 0.0, 1.0)
            drawing += (200.0 - self._water) * (wet - under)
        drawing = drawing * lighting[None, :]
        if self._glare:
            # A soft bright patch that does not move, wider than it is tall.
            across = (self._x_centres[None, :] - 900.0) / 350.0
            up = (self._row_tops[:, None] - (self.depth_mm + 20.0)) / 60.0
            drawing += self._glare * np.exp(-0.5 * (across ** 2 + up ** 2))
        mask, paint = self._fixed
        drawing = np.where(mask, paint, drawing).astype(np.float32)

        picture = cv2.warpPerspective(
            drawing, self._drawing_to_picture, self.image_size,
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=110)
        picture = cv2.GaussianBlur(picture, (0, 0), 1.0)
        picture += self._random.normal(0.0, self.noise_grey, picture.shape).astype(np.float32)
        grey = np.clip(picture, 0, 255).astype(np.uint8)
        return cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)

    def _to_drawing(self, x_mm: float, z_mm: float) -> Tuple[float, float]:
        return ((x_mm - WALL_X_MM[0]) / self._mm_per_px - 0.5,
                (self._z_mm[1] - z_mm) / self._mm_per_px - 0.5)

    def to_pixels(self, x_mm: float, z_mm: float) -> Tuple[float, float]:
        """Where a point on the wall really is in the picture."""
        spot = self._drawing_to_picture @ np.array([*self._to_drawing(x_mm, z_mm), 1.0])
        return float(spot[0] / spot[2]), float(spot[1] / spot[2])

    def _draw_fixed(self, wanted_mm):
        """Markers and clutter: everything on the wall that never moves.

        Returns the paint and where each marker's centre actually landed,
        which differs from where it was wanted by the rounding to whole
        pixels -- the same way a taped marker differs from the plan and has
        to be measured.
        """
        mask = np.zeros((self._rows, self._columns), dtype=bool)
        paint = np.zeros((self._rows, self._columns), dtype=np.float32)
        dictionary = cv2.aruco.getPredefinedDictionary(MARKER_DICTIONARY)
        side = int(round(MARKER_SIDE_MM / self._mm_per_px))
        quiet = int(round(0.25 * side))
        sheet = side + 2 * quiet
        landed = {}
        for marker, (x_mm, z_mm) in wanted_mm.items():
            patch = np.full((sheet, sheet), 255, dtype=np.uint8)
            patch[quiet:quiet + side, quiet:quiet + side] = cv2.resize(
                cv2.aruco.generateImageMarker(dictionary, marker, 240),
                (side, side), interpolation=cv2.INTER_AREA)
            column, row = self._to_drawing(x_mm, z_mm)
            left = int(round(column - (sheet - 1) / 2.0))
            top = int(round(row - (sheet - 1) / 2.0))
            mask[top:top + sheet, left:left + sheet] = True
            paint[top:top + sheet, left:left + sheet] = patch
            landed[marker] = (
                WALL_X_MM[0] + (left + (sheet - 1) / 2.0 + 0.5) * self._mm_per_px,
                self._z_mm[1] - (top + (sheet - 1) / 2.0 + 0.5) * self._mm_per_px)
        if self.clutter:
            # A dark rail above the water: a sharper horizontal edge than the
            # surface itself, in the strip the surface is searched for in.
            _, rail_top = self._to_drawing(0.0, self._rail_z_mm + 4.0)
            _, rail_bottom = self._to_drawing(0.0, self._rail_z_mm - 4.0)
            mask[int(rail_top):int(rail_bottom), :] = True
            paint[int(rail_top):int(rail_bottom), :] = 30.0
            # An upright post that hides the surface altogether for a stretch.
            post_left, _ = self._to_drawing(self._post_x_mm - 12.0, 0.0)
            post_right, _ = self._to_drawing(self._post_x_mm + 12.0, 0.0)
            mask[:, int(post_left):int(post_right)] = True
            paint[:, int(post_left):int(post_right)] = 40.0
        return (mask, paint), landed

    def _camera(self, keystone: float) -> np.ndarray:
        """A camera a little to one side, so the far end of the wall looks smaller."""
        width_px, height_px = self.image_size
        margin = 0.03 * width_px
        wall_height_px = self._rows * (width_px - 2 * margin) / self._columns
        top = (height_px - wall_height_px) / 2.0
        pinch = keystone * wall_height_px
        drawing = np.float32([[0, 0], [self._columns, 0],
                              [self._columns, self._rows], [0, self._rows]])
        picture = np.float32([[margin, top], [width_px - margin, top + pinch],
                              [width_px - margin, top + wall_height_px - pinch],
                              [margin, top + wall_height_px]])
        return cv2.getPerspectiveTransform(drawing, picture)
