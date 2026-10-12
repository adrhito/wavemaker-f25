"""Opening whatever the pictures come from: a camera, or a recorded clip."""

from __future__ import annotations

import sys
from typing import List, Tuple

import cv2


_BACKEND = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY


def open_camera(index: int, width: int = 1920, height: int = 1080,
                fps: int = 60) -> cv2.VideoCapture:
    """Open a camera and ask it for a picture size and rate.

    The camera is free to ignore the request; read the capture's properties
    afterwards to see what it actually gave.
    """
    capture = cv2.VideoCapture(index, _BACKEND)
    if not capture.isOpened():
        raise RuntimeError(
            "Camera {0} did not open. On a Mac the first attempt asks for "
            "camera permission and fails; allow it under System Settings > "
            "Privacy & Security > Camera for the program you ran this from, "
            "then try again.".format(index))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    capture.set(cv2.CAP_PROP_FPS, fps)
    return capture


def open_clip(path: str) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("Could not open the clip {0}.".format(path))
    return capture


def describe(capture: cv2.VideoCapture) -> Tuple[int, int, float]:
    """Width, height and pictures per second the source is really giving."""
    return (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            float(capture.get(cv2.CAP_PROP_FPS)))


def probe(limit: int = 6) -> List[Tuple[int, int, int, float]]:
    """Every camera number that opens, with what it gives.

    OpenCV cannot name cameras, only number them, and the numbers change when
    a phone connects.  Turning each one on in turn is the only way to tell.
    """
    found = []
    for index in range(limit):
        capture = cv2.VideoCapture(index, _BACKEND)
        if capture.isOpened():
            found.append((index,) + describe(capture))
        capture.release()
    return found
