"""The camera's part of a trial: watching the water, and recording the pulse.

Everything here leans on ``vision/``, which does the measuring, and so needs
NumPy and OpenCV (``requirements-vision.txt``). None of it is imported until a
trial asks for a camera, so a session without those installed drives the
machine exactly as before.

A :class:`Recorder` owns the camera for the length of one trial. One thread
reads it the whole time, for two reasons. A camera left unread hands back
stale pictures when it is next asked, and the moment that matters -- the
pulse -- is exactly when there is no time to flush them. And while the floor
is staged and the water is settling, the same pictures say how much the
surface is still moving.

On timing
---------
``vision`` works out a clip's times from the frame rate written in its
header. A camera's real rate is not the one it was asked for, so the rate is
measured from the pictures as they arrive before the clip is opened, and the
arrival time of every recorded picture is kept beside the clip so the two can
be compared afterwards. Arrival is not exposure: a phone used as a camera
delivers each picture a little late, by an amount this cannot know.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app import paths

#: Seconds of arrivals the measured frame rate is taken over.
RATE_WINDOW_SECONDS = 2.0
#: How often the surface is read while waiting for the water to settle.
WOBBLE_INTERVAL_SECONDS = 0.2
#: How many of those readings a "how much is it moving" figure is taken from.
WOBBLE_READINGS = 15
#: Pictures read and thrown away when a camera opens, while its exposure settles.
WARMUP_FRAMES = 15
#: The drawn tank is paced at this rate, as if it were a camera.
SYNTHETIC_RATE = 20.0


class Unavailable(RuntimeError):
    """The camera, or what is needed to use it, is not there."""


def _vision():
    """Import the measuring code, or say what is missing."""
    try:
        import cv2
        import numpy
    except ImportError as exc:
        raise Unavailable(
            "Using a camera needs NumPy and OpenCV, which this Python does "
            "not have ({0}). Install them with: python -m pip install -r "
            "requirements-vision.txt".format(exc)) from exc
    from vision import calibration, camera, metrics, waterline

    return cv2, numpy, calibration, camera, metrics, waterline


def _measure_clip_tool():
    """``tools/vision_measure.py``, which holds the clip-measuring function.

    Loaded by path because ``tools/`` is a folder of scripts, not a package.
    Measuring is kept there, in one place, rather than copied here.
    """
    path = paths.APP_DIR / "tools" / "vision_measure.py"
    spec = importlib.util.spec_from_file_location("wavectl_vision_measure", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DrawnTank:
    """``vision.synthetic``'s drawn tank, played as if it were a camera.

    Still water until :meth:`launch`, then one wave of known height crossing
    the view. For rehearsing a whole trial with no camera and no tank; what it
    shows has nothing to do with the pulse that was commanded.
    """

    def __init__(self) -> None:
        from vision.synthetic import SyntheticTank

        self.tank = SyntheticTank()
        self.rig = self.tank.rig
        self.size = self.tank.image_size
        self._launched: Optional[float] = None
        self._due = time.monotonic()

    def launch(self) -> None:
        self._launched = time.monotonic()

    def read(self):
        self._due = max(self._due + 1.0 / SYNTHETIC_RATE, time.monotonic())
        time.sleep(max(self._due - time.monotonic(), 0.0))
        elapsed = 0.0 if self._launched is None else time.monotonic() - self._launched
        return True, self.tank.frame(elapsed)

    def release(self) -> None:
        pass


class Recorder:
    """One camera, read continuously for the length of one trial."""

    def __init__(self, source: str, rig_path: Optional[str] = None,
                 depth_mm: Optional[float] = None, width: int = 1920,
                 height: int = 1080, fps: int = 60) -> None:
        self.source = str(source)
        self.rig_path = rig_path
        self.depth_mm = depth_mm
        self.asked = (int(width), int(height), int(fps))
        self.rig = None
        self.reader = None
        self.size = (0, 0)
        self.reported_fps = 0.0
        self._capture = None
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._arrivals = deque()
        self._wobble = deque(maxlen=WOBBLE_READINGS)
        self._writer = None
        self._clip: Optional[Path] = None
        self._header_fps = 0.0
        self._stamps = []
        self._failed: Optional[str] = None

    @property
    def synthetic(self) -> bool:
        return self.source == "synthetic"

    # -- opening -------------------------------------------------------------------

    def open(self) -> dict:
        """Open the camera and prove it can be used, before anything moves.

        Raises :class:`Unavailable` if it will not open, gives no picture, or
        the rig's markers cannot be found in what it sees.
        """
        cv2, _numpy, calibration, camera, _metrics, waterline = _vision()
        if self.synthetic:
            self._capture = _DrawnTank()
            self.rig = self._capture.rig
            self.depth_mm = self._capture.tank.depth_mm
            self.size = self._capture.size
            self.reported_fps = SYNTHETIC_RATE
        else:
            try:
                index = int(self.source)
            except ValueError:
                raise Unavailable(
                    '--camera takes a camera number, or "synthetic" for the '
                    "drawn tank; not {0!r}. tools/vision_preview.py --list "
                    "names the numbers.".format(self.source)) from None
            if self.rig_path:
                try:
                    self.rig = calibration.Rig.load(Path(self.rig_path))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise Unavailable("The rig file {0} could not be used: {1}".format(
                        self.rig_path, exc)) from exc
            try:
                self._capture = camera.open_camera(index, *self.asked)
            except RuntimeError as exc:
                raise Unavailable(str(exc)) from exc
            width, height, rate = camera.describe(self._capture)
            self.size, self.reported_fps = (width, height), rate

        frame = None
        for _ in range(1 if self.synthetic else WARMUP_FRAMES):
            ok, frame = self._capture.read()
            if not ok or frame is None:
                self.close()
                raise Unavailable("Camera {0} opened but gave no picture.".format(
                    self.source))
        self.size = (int(frame.shape[1]), int(frame.shape[0]))

        info = {"source": self.source, "width": self.size[0],
                "height": self.size[1], "reported_fps": round(self.reported_fps, 2)}
        if self.rig is not None:
            try:
                view = calibration.locate(frame, self.rig)
            except calibration.CalibrationError as exc:
                self.close()
                raise Unavailable("The camera cannot find the rig: {0}".format(exc)) from exc
            self.reader = waterline.WaterlineReader(self.rig, view)
            error = float(view.marker_error_mm)
            info["marker_error_mm"] = round(error, 3) if error == error else None
            info["mm_per_pixel"] = round(float(self.reader.mm_per_row), 3)

        self._thread = threading.Thread(target=self._loop, name="wavectl camera",
                                        daemon=True)
        self._thread.start()
        return info

    def _loop(self) -> None:
        last_read = 0.0
        while not self._halt.is_set():
            ok, frame = self._capture.read()
            now = time.time()
            if not ok or frame is None:
                self._failed = "the camera stopped giving pictures"
                return
            with self._lock:
                self._arrivals.append(now)
                while self._arrivals and now - self._arrivals[0] > RATE_WINDOW_SECONDS:
                    self._arrivals.popleft()
                recording = self._writer is not None
                if recording:
                    self._writer.write(frame)
                    self._stamps.append(now)
            # Reading the surface costs a few milliseconds a picture, so it
            # is not done while recording, when a late read is a lost frame.
            if (self.reader is not None and not recording
                    and now - last_read >= WOBBLE_INTERVAL_SECONDS):
                last_read = now
                try:
                    self._wobble.append(self.reader.read(frame))
                except Exception:  # noqa: BLE001 - one unreadable picture is not a failure
                    pass

    # -- while waiting -------------------------------------------------------------

    def rate(self) -> float:
        """Pictures a second, measured from how they have been arriving."""
        with self._lock:
            arrivals = list(self._arrivals)
        if len(arrivals) < 3 or arrivals[-1] <= arrivals[0]:
            return 0.0
        return (len(arrivals) - 1) / (arrivals[-1] - arrivals[0])

    def restlessness_mm(self) -> Optional[float]:
        """How much the surface has moved over the last few seconds, in mm.

        None until there are enough readings, or without a rig to read by.
        """
        if self.reader is None or len(self._wobble) < WOBBLE_READINGS * 2 // 3:
            return None
        _cv2, numpy, _calibration, _camera, metrics, _waterline = _vision()
        value = metrics.restlessness_mm(numpy.array(list(self._wobble)))
        return float(value) if value == value and value != float("inf") else None

    def failure(self) -> Optional[str]:
        return self._failed

    def launch(self) -> None:
        """Tell a drawn tank the pulse has gone. A real camera needs no telling."""
        if self.synthetic and self._capture is not None:
            self._capture.launch()

    # -- recording -----------------------------------------------------------------

    def begin(self, clip: Path) -> None:
        """Start writing pictures to ``clip``."""
        cv2, _numpy, _calibration, _camera, _metrics, _waterline = _vision()
        measured = self.rate()
        # The header rate is what the clip's times are later worked out from,
        # so it is the rate the pictures really arrive at, not the one asked for.
        self._header_fps = round(measured if measured > 1.0 else
                                 (self.reported_fps or float(self.asked[2])), 3)
        clip.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"),
                                 self._header_fps, self.size)
        if not writer.isOpened():
            raise Unavailable("This OpenCV cannot write an mp4 at {0}.".format(clip))
        with self._lock:
            self._stamps = []
            self._clip = clip
            self._writer = writer

    def end(self) -> dict:
        """Stop writing, and say what was written and how evenly."""
        with self._lock:
            writer, self._writer = self._writer, None
            stamps, clip = list(self._stamps), self._clip
        if writer is None:
            return {}
        writer.release()
        gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
        achieved = (len(stamps) - 1) / (stamps[-1] - stamps[0]) if len(stamps) > 1 else 0.0
        return {
            "clip": str(clip),
            "frames": len(stamps),
            "seconds": round(stamps[-1] - stamps[0], 3) if len(stamps) > 1 else 0.0,
            "header_fps": self._header_fps,
            "achieved_fps": round(achieved, 3),
            "worst_gap_s": round(max(gaps), 4) if gaps else None,
            "first_frame_utc": datetime.fromtimestamp(
                stamps[0], timezone.utc).isoformat() if stamps else None,
            "frame_arrival_s": [round(stamp - stamps[0], 4) for stamp in stamps],
            "first_frame_epoch": stamps[0] if stamps else None,
        }

    def close(self) -> None:
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.end()
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:  # noqa: BLE001
                pass
            self._capture = None


def timing_note(clip: dict) -> Optional[str]:
    """A warning if the clip's pictures did not arrive as its header says."""
    header, achieved = clip.get("header_fps") or 0.0, clip.get("achieved_fps") or 0.0
    if not header or not achieved:
        return "the clip is too short to check its timing"
    notes = []
    if abs(achieved - header) / header > 0.01:
        notes.append("pictures arrived at {0:.2f} a second but the clip says "
                     "{1:.2f}, so its speeds and widths are off by that much".format(
                         achieved, header))
    worst = clip.get("worst_gap_s") or 0.0
    if worst > 2.5 / header:
        notes.append("a gap of {0:.0f} ms means pictures were dropped".format(
            worst * 1000.0))
    return "; ".join(notes) or None


def measure(clip: Path, rig, depth_mm: Optional[float],
            station_mm: Optional[float], still_seconds: float) -> dict:
    """Measure the wave in a clip and save the evidence beside it.

    The same call, and the same two files, as ``tools/vision_measure.py``:
    every surface reading in ``.surface.npz`` and the crest picture in
    ``.crest.png``. Look at the picture before believing the numbers.
    """
    cv2, numpy, _calibration, _camera, _metrics, _waterline = _vision()
    tool = _measure_clip_tool()
    measurement, reader, times, heights, picture = tool.measure_clip(
        clip, rig, still_seconds, depth_mm, station_mm)
    numpy.savez_compressed(str(clip.with_suffix(".surface.npz")), times_s=times,
                           x_mm=reader.x_mm, height_mm=heights,
                           still_mm=reader.still_mm)
    if picture is not None:
        cv2.imwrite(str(clip.with_suffix(".crest.png")), picture)
    # NumPy's own numbers are not JSON's; ask each for the plain Python one.
    record = json.loads(json.dumps(
        measurement.to_record(),
        default=lambda value: value.item() if hasattr(value, "item") else str(value)))
    error = float(reader.view.marker_error_mm)
    record["marker_error_mm"] = round(error, 3) if error == error else None
    record["mm_per_pixel"] = round(float(reader.mm_per_row), 3)
    record["crest_picture"] = (str(clip.with_suffix(".crest.png"))
                               if picture is not None else None)
    return record
