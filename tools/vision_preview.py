"""Live view from the camera with the measurement drawn on it.

    python tools/vision_preview.py --list                    which camera is which
    python tools/vision_preview.py --camera 1                aim: shows every marker it can see
    python tools/vision_preview.py --camera 1 --rig vision/rigs/tank.json --depth-mm 406
    python tools/vision_preview.py --synthetic               rehearse with a drawn tank

For aiming the tripod and checking a rig before a session.  With a rig, press
R once the water is still: the still level is drawn in blue and the live
surface in yellow, and the readout shows how far the surface is from still.

    R   take the last second as still water
    S   save the picture to logs/vision/
    Q   quit

Reads the camera only.  It never contacts the PLC and cannot move a piston.
"""

import argparse
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from vision import overlay  # noqa: E402
from vision.calibration import CalibrationError, Rig, find_markers, locate  # noqa: E402
from vision.camera import describe, open_camera, open_clip, probe  # noqa: E402
from vision.metrics import restlessness_mm  # noqa: E402
from vision.waterline import WaterlineReader  # noqa: E402


# A bumped tripod moves every marker; a pixel or two is only focus breathing.
BUMP_PIXELS = 3.0
RELOCATE_EVERY = 30


class _Drawn:
    """The drawn tank, played on a loop as if it were a camera."""

    def __init__(self):
        from vision.synthetic import SyntheticTank
        self.tank = SyntheticTank(image_size=(1280, 720))
        self.rig = self.tank.rig
        self._started = time.monotonic()
        self._loop = self.tank.crest_time_s(1400.0) + 1.5

    def read(self):
        time.sleep(1.0 / 30)
        return True, self.tank.frame((time.monotonic() - self._started) % self._loop)

    def release(self):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--video", help="play a recorded clip instead of a camera")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--list", action="store_true", help="list cameras and exit")
    parser.add_argument("--rig")
    parser.add_argument("--depth-mm", type=float, default=None)
    args = parser.parse_args()

    if args.list:
        for index, width, height, rate in probe():
            print("camera {0}: {1} x {2} at {3:.0f} a second".format(index, width, height, rate))
        return

    rig = Rig.load(args.rig) if args.rig else None
    if args.synthetic:
        source = _Drawn()
        rig = source.rig
        depth = source.tank.depth_mm
    else:
        source = open_clip(args.video) if args.video else open_camera(args.camera)
        print("Source gives {0} x {1} at {2:.0f} a second".format(*describe(source)))
        depth = args.depth_mm

    view = reader = first_view = None
    recent = deque(maxlen=30)
    wobble = deque(maxlen=90)
    count = 0
    try:
        while True:
            ok, frame = source.read()
            if not ok:
                break
            count += 1
            recent.append(frame)
            lines, heights, markers = [], None, find_markers(frame)
            if rig is None:
                lines.append("{0} markers in view: {1}".format(len(markers), sorted(markers)))
            else:
                if view is None or count % RELOCATE_EVERY == 0:
                    try:
                        view = locate(frame, rig)
                        first_view = first_view or view
                        if reader is None:
                            reader = WaterlineReader(rig, view)
                    except CalibrationError as problem:
                        view = None
                        lines.append(str(problem)[:90])
                if view is not None:
                    lines.append("marker error {0:.2f} mm   {1:.2f} mm per pixel".format(
                        view.marker_error_mm, reader.mm_per_row))
                    if first_view.shift_px(view) > BUMP_PIXELS:
                        lines.append("CAMERA MOVED {0:.0f} px since start: restart and re-take still"
                                     .format(first_view.shift_px(view)))
                    heights = reader.read(frame)
                    wobble.append(heights)
                    if reader.still_mm is None:
                        lines.append("press R when the water is still")
                    else:
                        rise = heights - reader.still_mm
                        lines.append("still depth {0:.1f} mm".format(np.nanmedian(reader.still_mm)))
                        if np.isfinite(rise).any():
                            lines.append("highest now {0:+.1f} mm   lowest {1:+.1f} mm".format(
                                np.nanmax(rise), np.nanmin(rise)))
                    if len(wobble) >= 10:
                        lines.append("movement {0:.2f} mm".format(restlessness_mm(np.array(wobble))))

            picture = overlay.draw(frame, rig, view, reader, heights, markers, lines)
            cv2.imshow("wavemaker camera", picture)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r") and reader is not None:
                try:
                    reader.learn_still(list(recent), near_depth_mm=depth)
                except ValueError as problem:
                    print(problem, flush=True)
            if key == ord("s"):
                folder = Path(__file__).resolve().parent.parent / "logs" / "vision"
                folder.mkdir(parents=True, exist_ok=True)
                saved = folder / ("preview-" + time.strftime("%Y%m%d-%H%M%S") + ".png")
                cv2.imwrite(str(saved), picture)
                print("Saved " + str(saved), flush=True)
    finally:
        source.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
