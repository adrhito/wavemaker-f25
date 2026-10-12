"""Drawing what was measured back onto the camera's picture.

The numbers are only as good as the line they came from, and the quickest way
to know whether the line is on the water is to look at it.
"""

from __future__ import annotations

from typing import Iterable, Optional

import cv2
import numpy as np

from vision.calibration import Rig, View
from vision.waterline import WaterlineReader


SURFACE = (0, 220, 255)
STILL = (255, 170, 0)
STRIP = (120, 120, 120)
MARKER = (0, 200, 0)


def draw(frame: np.ndarray, rig: Optional[Rig] = None, view: Optional[View] = None,
         reader: Optional[WaterlineReader] = None,
         heights_mm: Optional[np.ndarray] = None,
         markers_px: Optional[dict] = None,
         lines: Iterable[str] = ()) -> np.ndarray:
    """A copy of ``frame`` with the markers, strip, surface and notes drawn on."""
    picture = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    weight = max(int(round(picture.shape[1] / 960)), 1)
    if view is not None and rig is not None:
        (x_low, x_high), (z_low, z_high) = rig.x_range_mm, rig.z_range_mm
        corners = view.to_pixels([(x_low, z_low), (x_high, z_low),
                                  (x_high, z_high), (x_low, z_high)])
        cv2.polylines(picture, [np.round(corners).astype(np.int32)], True, STRIP, weight)
    for marker, (column, row) in (markers_px or {}).items():
        spot = (int(round(column)), int(round(row)))
        cv2.circle(picture, spot, 6 * weight, MARKER, weight)
        cv2.putText(picture, str(marker), (spot[0] + 8 * weight, spot[1] - 8 * weight),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * weight, MARKER, weight, cv2.LINE_AA)
    if reader is not None:
        if reader.still_mm is not None:
            _line(picture, reader.outline_px(reader.still_mm), STILL, weight)
        if heights_mm is not None:
            _line(picture, reader.outline_px(heights_mm), SURFACE, weight)
    for number, text in enumerate(lines):
        origin = (12 * weight, (28 + 26 * number) * weight)
        # A dark plate behind each line, so it reads over bright and dark alike.
        (wide, tall), below = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6 * weight, weight)
        cv2.rectangle(picture, (origin[0] - 4 * weight, origin[1] - tall - 4 * weight),
                      (origin[0] + wide + 4 * weight, origin[1] + below + 2 * weight),
                      (0, 0, 0), -1)
        cv2.putText(picture, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.6 * weight,
                    (255, 255, 255), weight, cv2.LINE_AA)
    return picture


def _line(picture: np.ndarray, points_px: np.ndarray, colour, weight: int) -> None:
    if len(points_px) >= 2:
        cv2.polylines(picture, [np.round(points_px).astype(np.int32)], False,
                      colour, weight, cv2.LINE_AA)
