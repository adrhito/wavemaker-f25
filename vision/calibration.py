"""Turning camera pixels into millimetres on the tank's side wall.

Printed markers are taped to the outside of the glass and somebody measures
where the centre of each one is: how far along the tank, and how high above
the inside floor.  Finding those markers in a picture then says where the
wall is in that picture, wherever the tripod happens to stand.  Only the
centres are used, so a marker printed at the wrong scale or taped a little
crooked does no harm.

Four markers fix the wall exactly and leave nothing to check the tape
measurements against.  Six is the intended number: the two spare ones show up
as a disagreement in millimetres when a measurement or the lens is off.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np


MARKER_DICTIONARY = cv2.aruco.DICT_4X4_50
MIN_MARKERS = 4

# Close enough for glass and acrylic alike; the correction it feeds is under
# one percent to begin with.
WALL_REFRACTIVE_INDEX = 1.5


class CalibrationError(RuntimeError):
    """The wall could not be located in a picture."""


@dataclass(frozen=True)
class Rig:
    """One camera position: where the markers are and which strip to read.

    ``x`` runs along the tank and ``z`` is height above the inside floor, both
    in millimetres.  The strip between ``z_range_mm`` is where the surface is
    searched for, so it should clear the marker rows and cover the highest
    crest and the lowest trough expected.
    """

    markers_mm: Dict[int, Tuple[float, float]]
    x_range_mm: Tuple[float, float]
    z_range_mm: Tuple[float, float]
    glass_thickness_mm: float = 0.0
    camera_distance_mm: float = 0.0
    #: True or False once known; None lets the still water decide.
    water_is_darker: Optional[bool] = None

    def __post_init__(self) -> None:
        if len(self.markers_mm) < MIN_MARKERS:
            raise ValueError("A rig needs at least {0} markers.".format(MIN_MARKERS))
        for low, high in (self.x_range_mm, self.z_range_mm):
            if not high > low:
                raise ValueError("Each range must run from low to high.")

    @property
    def wall_scale(self) -> float:
        """How much larger things are at the water than they look at the markers.

        The markers are on the outside of the glass and the water touches the
        inside, so the surface is slightly further from the lens and looks
        slightly smaller.  Without both measurements this is 1 and the
        readings are a fraction of a percent small.
        """
        if self.glass_thickness_mm <= 0 or self.camera_distance_mm <= 0:
            return 1.0
        return 1.0 + (self.glass_thickness_mm /
                      (WALL_REFRACTIVE_INDEX * self.camera_distance_mm))

    def to_record(self) -> dict:
        return {
            "markers_mm": {str(marker): [x, z]
                           for marker, (x, z) in sorted(self.markers_mm.items())},
            "x_range_mm": list(self.x_range_mm),
            "z_range_mm": list(self.z_range_mm),
            "glass_thickness_mm": self.glass_thickness_mm,
            "camera_distance_mm": self.camera_distance_mm,
            "water_is_darker": self.water_is_darker,
        }

    @classmethod
    def from_record(cls, record: dict) -> "Rig":
        return cls(
            markers_mm={int(marker): (float(x), float(z))
                        for marker, (x, z) in record["markers_mm"].items()},
            x_range_mm=tuple(float(v) for v in record["x_range_mm"]),
            z_range_mm=tuple(float(v) for v in record["z_range_mm"]),
            glass_thickness_mm=float(record.get("glass_thickness_mm", 0.0)),
            camera_distance_mm=float(record.get("camera_distance_mm", 0.0)),
            water_is_darker=record.get("water_is_darker"),
        )

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_record(), indent=2) + "\n",
                              encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Rig":
        return cls.from_record(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class View:
    """Where the wall sits in one camera picture."""

    wall_to_pixel: np.ndarray
    pixel_to_wall: np.ndarray
    markers_px: Dict[int, Tuple[float, float]]
    #: Typical distance between where a marker was measured and where the fit
    #: puts it.  NaN with exactly four markers, which always fit perfectly.
    marker_error_mm: float

    def to_pixels(self, points_mm) -> np.ndarray:
        return _apply(self.wall_to_pixel, points_mm)

    def to_wall(self, points_px) -> np.ndarray:
        return _apply(self.pixel_to_wall, points_px)

    def mm_per_pixel(self, x_mm: float, z_mm: float) -> float:
        """Height on the wall covered by one pixel at this spot."""
        here, above = self.to_pixels([(x_mm, z_mm), (x_mm, z_mm + 1.0)])
        return 1.0 / float(np.hypot(*(above - here)))

    def rectify(self, frame: np.ndarray, x_range_mm: Tuple[float, float],
                z_range_mm: Tuple[float, float], mm_per_px: float) -> np.ndarray:
        """Cut the strip out as a flat picture: columns along the tank, top row highest."""
        width = max(int(round((x_range_mm[1] - x_range_mm[0]) / mm_per_px)), 1)
        height = max(int(round((z_range_mm[1] - z_range_mm[0]) / mm_per_px)), 1)
        strip_to_wall = np.array([
            [mm_per_px, 0.0, x_range_mm[0] + 0.5 * mm_per_px],
            [0.0, -mm_per_px, z_range_mm[1] - 0.5 * mm_per_px],
            [0.0, 0.0, 1.0],
        ])
        return cv2.warpPerspective(
            frame, self.wall_to_pixel @ strip_to_wall, (width, height),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REPLICATE)

    def shift_px(self, other: "View") -> float:
        """Furthest any shared marker moved between two pictures, in pixels.

        A bumped tripod shows up here before it shows up as a wrong wave.
        """
        shared = set(self.markers_px) & set(other.markers_px)
        if not shared:
            return float("inf")
        return max(float(np.hypot(self.markers_px[m][0] - other.markers_px[m][0],
                                  self.markers_px[m][1] - other.markers_px[m][1]))
                   for m in shared)


def _apply(matrix: np.ndarray, points) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    lifted = np.hstack([points, np.ones((len(points), 1))]) @ matrix.T
    return lifted[:, :2] / lifted[:, 2:3]


def as_grey(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _detector() -> "cv2.aruco.ArucoDetector":
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(MARKER_DICTIONARY), parameters)


def _diagonal_crossing(corners: np.ndarray) -> Tuple[float, float]:
    """Centre of a marker seen at an angle.

    The average of the four corners drifts towards the nearer side when the
    marker is foreshortened; the point where the diagonals cross does not.
    """
    a, b, c, d = corners
    across, down = c - a, d - b
    denominator = across[0] * down[1] - across[1] * down[0]
    along = ((b[0] - a[0]) * down[1] - (b[1] - a[1]) * down[0]) / denominator
    centre = a + along * across
    return float(centre[0]), float(centre[1])


def find_markers(frame: np.ndarray) -> Dict[int, Tuple[float, float]]:
    """Pixel position of the centre of every marker in the picture."""
    corners, ids, _ = _detector().detectMarkers(as_grey(frame))
    if ids is None:
        return {}
    return {int(marker): _diagonal_crossing(quad.reshape(4, 2).astype(np.float64))
            for quad, marker in zip(corners, ids.ravel())}


def locate(frame: np.ndarray, rig: Rig) -> View:
    """Work out where the wall is in ``frame`` from the rig's markers."""
    seen = {marker: centre for marker, centre in find_markers(frame).items()
            if marker in rig.markers_mm}
    if len(seen) < MIN_MARKERS:
        raise CalibrationError(
            "Only {0} of the rig's markers are visible ({1}); at least {2} are "
            "needed. Check the lighting, the focus, and that nothing is in "
            "front of them.".format(
                len(seen), sorted(seen) or "none", MIN_MARKERS))
    order = sorted(seen)
    wall = np.array([rig.markers_mm[m] for m in order], dtype=np.float64)
    pixels = np.array([seen[m] for m in order], dtype=np.float64)
    wall_to_pixel, _ = cv2.findHomography(wall, pixels, 0)
    if wall_to_pixel is None:
        raise CalibrationError(
            "The markers do not describe a flat wall. Check that no two "
            "share a measured position and that they are not all in one line.")
    pixel_to_wall = np.linalg.inv(wall_to_pixel)
    if len(order) > MIN_MARKERS:
        miss = _apply(pixel_to_wall, pixels) - wall
        error = float(np.sqrt(np.mean(np.sum(miss ** 2, axis=1))))
    else:
        error = float("nan")
    return View(wall_to_pixel, pixel_to_wall, seen, error)
