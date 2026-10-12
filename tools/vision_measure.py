"""Measure the wave in a recorded clip.

    python tools/vision_measure.py clip.mov --rig vision/rigs/tank.json --depth-mm 406

The clip must start with still water: the first ``--still-seconds`` of it are
taken as the level everything else is measured from.  Prints the measurement
and writes two files beside the clip: ``<clip>.surface.npz`` with every
reading, so the run can be measured again without the video, and
``<clip>.crest.png`` with the detected surface drawn on the picture taken as
the crest passed.  Look at that picture before believing the numbers.

Reads a file.  It does not open a camera or contact the PLC.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from vision import overlay  # noqa: E402
from vision.calibration import Rig, locate  # noqa: E402
from vision.camera import describe, open_clip  # noqa: E402
from vision.metrics import measure_pulse  # noqa: E402
from vision.waterline import WaterlineReader  # noqa: E402


def measure_clip(clip, rig, still_seconds=1.0, depth_mm=None, station_mm=None):
    """Read every picture in ``clip`` and measure the largest wave in it."""
    capture = open_clip(clip)
    try:
        _, _, rate = describe(capture)
        if not rate or rate != rate:
            raise RuntimeError("The clip does not say how many pictures a second it holds.")
        still_count = max(int(round(still_seconds * rate)), 1)
        reader = None
        still_frames, heights = [], []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if reader is None:
                reader = WaterlineReader(rig, locate(frame, rig))
            if reader.still_mm is None:
                still_frames.append(frame)
                if len(still_frames) < still_count:
                    continue
                reader.learn_still(still_frames, near_depth_mm=depth_mm)
                heights.extend(reader.read(kept) for kept in still_frames)
                still_frames = []
                continue
            heights.append(reader.read(frame))
        if reader is None or reader.still_mm is None:
            raise RuntimeError("The clip is shorter than the still water it must start with.")

        heights = np.array(heights)
        times = np.arange(len(heights)) / rate
        rise = heights - reader.still_mm
        measurement = measure_pulse(
            times, reader.x_mm, rise, station_mm=station_mm,
            still_depth_mm=float(np.nanmedian(reader.still_mm)))

        crest_frame = int(np.argmin(np.abs(times - measurement.crest_time_s)))
        capture.set(cv2.CAP_PROP_POS_FRAMES, crest_frame)
        ok, frame = capture.read()
        picture = None
        if ok:
            picture = overlay.draw(
                frame, rig, reader.view, reader, heights[crest_frame],
                reader.view.markers_px,
                ["crest {0:.1f} mm".format(measurement.crest_rise_mm),
                 "marker error {0:.2f} mm".format(reader.view.marker_error_mm)])
    finally:
        capture.release()
    return measurement, reader, times, heights, picture


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip")
    parser.add_argument("--rig", required=True, help="the rig file for this camera position")
    parser.add_argument("--still-seconds", type=float, default=1.0)
    parser.add_argument("--depth-mm", type=float, default=None,
                        help="the depth you measured, to find the water among other level edges")
    parser.add_argument("--station-mm", type=float, default=None,
                        help="where along the tank to report the height (default: mid-view)")
    args = parser.parse_args()

    clip = Path(args.clip)
    measurement, reader, times, heights, picture = measure_clip(
        clip, Rig.load(args.rig), args.still_seconds, args.depth_mm, args.station_mm)
    np.savez_compressed(str(clip.with_suffix(".surface.npz")), times_s=times,
                        x_mm=reader.x_mm, height_mm=heights, still_mm=reader.still_mm)
    if picture is not None:
        cv2.imwrite(str(clip.with_suffix(".crest.png")), picture)
    record = measurement.to_record()
    record["marker_error_mm"] = round(reader.view.marker_error_mm, 3)
    record["mm_per_pixel"] = round(reader.mm_per_row, 3)
    print(json.dumps(record, indent=2))
    if not measurement.crest_seen:
        print("The top of the wave was not seen: it left the strip the rig "
              "searches, or passed behind something. Widen z_range_mm or move "
              "the station; the crest height above is too low.", file=sys.stderr)


if __name__ == "__main__":
    main()
