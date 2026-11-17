"""Record the laser gauge while wavectl fires single strokes, one probe at a time.

For each probe this selects the pistons, records still water, fires one
stroke through ``wavectl/wm run --single --sample``, and keeps recording while
the wave passes the beam.  Every motion goes through ``wavectl``; this file
never contacts the PLC itself.  A ``wavectl`` session must already be running
and prepared.

What a probe leaves in ``analytics/probes/``:

- ``probe-<stamp>.mkv``: a lossless (FFV1) crop of the picture around the beam,
  for measuring.  The camera's own MJPEG compression is the only loss.
- ``probe-<stamp>.mp4``: the whole picture, small, for looking at.
- ``probe-<stamp>.json``: the command, wavectl's replies (with the path of the
  position samples), the crop, and the arrival time of every picture.

Example::

    python tools/vision_probe.py --pistons 1 2 3 --stroke 30 --speed 150
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analytics" / "probes"
WM = ROOT / "wavectl" / "wm"
#: Where the pistons rest after homing; strokes end here unless told otherwise.
REST_MM = 350


def wm(*args: str, timeout: float = 180.0) -> dict:
    """Ask the wavectl session one thing; return its full JSON reply."""
    command = ["sh", str(WM), *args, "--json"]
    done = subprocess.run(command, capture_output=True, text=True,
                          timeout=timeout, cwd=str(ROOT))
    text = done.stdout.strip().splitlines()
    try:
        reply = json.loads(text[-1]) if text else {}
    except json.JSONDecodeError:
        reply = {"ok": False, "text": done.stdout + done.stderr}
    reply.setdefault("ok", done.returncode == 0)
    reply["command"] = " ".join(args)
    return reply


class Camera:
    """One thread that reads the camera continuously and, when asked, records."""

    def __init__(self, index: int, width: int, height: int, fps: int,
                 crop: tuple, exposure: float = None) -> None:
        self.capture = cv2.VideoCapture(index, cv2.CAP_DSHOW, [
            cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"),
            cv2.CAP_PROP_FRAME_WIDTH, width,
            cv2.CAP_PROP_FRAME_HEIGHT, height,
            cv2.CAP_PROP_FPS, fps])
        if not self.capture.isOpened():
            raise RuntimeError("camera {0} did not open".format(index))
        if exposure is not None:
            # A locked exposure keeps the waterline's brightness from wandering
            # as the picture changes; the driver keeps the last value it got.
            self.capture.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            self.capture.set(cv2.CAP_PROP_GAIN, 0)
            self.capture.set(cv2.CAP_PROP_EXPOSURE, exposure)
        self.size = (int(self.capture.get(3)), int(self.capture.get(4)))
        self.crop = crop  # x0, x1, y0, y1
        self.lock = threading.Lock()
        self.writers = None
        self.times = []
        self.latest = None
        self.running = True
        self.failed = None
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        misses = 0
        while self.running:
            ok, frame = self.capture.read()
            now = time.time()
            if not ok:
                misses += 1
                if misses > 50:
                    self.failed = "camera stopped giving pictures"
                    return
                continue
            misses = 0
            self.latest = frame
            with self.lock:
                if self.writers is not None:
                    full, strip = self.writers
                    x0, x1, y0, y1 = self.crop
                    strip.write(frame[y0:y1, x0:x1])
                    full.write(cv2.resize(frame, (960, 540)))
                    self.times.append(now)

    def rate(self, seconds: float = 2.0) -> float:
        count, start = 0, time.time()
        last = None
        while time.time() - start < seconds:
            if self.latest is not last:
                last = self.latest
                count += 1
            time.sleep(0.002)
        return count / seconds

    def begin(self, stem: Path, fps: float) -> None:
        x0, x1, y0, y1 = self.crop
        strip = cv2.VideoWriter(str(stem.with_suffix(".mkv")),
                                cv2.VideoWriter_fourcc(*"FFV1"), fps,
                                (x1 - x0, y1 - y0))
        full = cv2.VideoWriter(str(stem.with_suffix(".mp4")),
                               cv2.VideoWriter_fourcc(*"mp4v"), fps, (960, 540))
        if not strip.isOpened() or not full.isOpened():
            raise RuntimeError("could not open the clip writers")
        with self.lock:
            self.times = []
            self.writers = (full, strip)

    def end(self) -> list:
        with self.lock:
            full, strip = self.writers
            self.writers = None
            times = self.times
        full.release()
        strip.release()
        return times

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=2)
        self.capture.release()


def _settings(group: dict) -> list:
    """``wm set`` arguments for one group of a run."""
    stroke = int(group["stroke"])
    top = int(group.get("top", REST_MM - stroke))
    out = ["p1={0}".format(top), "stroke={0}".format(stroke),
           "speed={0}".format(int(group["speed"]))]
    for key in ("accel", "decel", "jerk"):
        if key in group:
            out.append("{0}={1}".format(key, int(group[key])))
    return out


def probe(camera: Camera, run: dict, args, fps: float) -> Path:
    """One run: select, set each group, film still water, fire, keep filming.

    ``run`` is ``{"name": ..., "groups": [{"pistons": "1-3", "stroke": 60,
    "speed": 300}, ...]}``.  A group may also give ``top`` (Position 1,
    default rest minus stroke so the stroke ends at rest) and ``accel``,
    ``decel`` or ``jerk``.  Settings not given are left as the drives hold
    them, so plans should give every one they rely on.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = OUT / "probe-{0}-{1}".format(stamp, run["name"])
    record = {"run": run,
              "camera": {"index": args.camera, "size": camera.size,
                         "measured_fps": fps, "crop": camera.crop,
                         "exposure": None if args.auto else args.exposure},
              "replies": []}
    everyone = ",".join(g["pistons"] for g in run["groups"])
    commands = [("select", everyone)]
    for group in run["groups"]:
        commands.append(("set", *_settings(group), "--pistons", group["pistons"]))
    for command in commands:
        reply = wm(*command)
        record["replies"].append(reply)
        if not reply.get("ok"):
            raise RuntimeError("wavectl refused {0}: {1}".format(
                " ".join(command), reply.get("text")))
    camera.begin(stem, fps)
    reply = {}
    try:
        time.sleep(args.before)
        record["fire_sent"] = time.time()
        reply = wm("run", "--single", "--sample", "30")
        record["fire_answered"] = time.time()
        record["replies"].append(reply)
        time.sleep(args.after)
    finally:
        record["frame_times"] = camera.end()
        stem.with_suffix(".json").write_text(json.dumps(record, indent=1))
    ok = "ok" if reply.get("ok") else "FAILED"
    print("{0} {1}: {2} | {3} frames | {4}".format(
        ok, run["name"], reply.get("text", "")[:120],
        len(record["frame_times"]), stem.name), flush=True)
    if not reply.get("ok"):
        raise RuntimeError("stroke failed: {0}".format(reply.get("text")))
    return stem


def _done(name: str) -> bool:
    return any(OUT.glob("probe-*-{0}.json".format(name)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pistons", nargs="+",
                        help="one probe per entry; an entry may be a set, e.g. 1-3")
    parser.add_argument("--plan", help="a JSON list of runs (see probe()); "
                        "runs whose name already has a record are skipped")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--stroke", type=int, default=30)
    parser.add_argument("--speed", type=int, default=150)
    parser.add_argument("--top", type=int, default=320,
                        help="Position 1, the top of the stroke in mm (smaller is higher)")
    parser.add_argument("--before", type=float, default=3.0)
    parser.add_argument("--after", type=float, default=25.0)
    parser.add_argument("--settle", type=float, default=20.0,
                        help="seconds of quiet between probes")
    parser.add_argument("--camera", type=int, default=1)
    parser.add_argument("--crop", type=int, nargs=4, default=(440, 1490, 460, 780),
                        metavar=("X0", "X1", "Y0", "Y1"),
                        help="the band kept losslessly, in full-picture pixels")
    parser.add_argument("--exposure", type=float, default=-7,
                        help="DirectShow exposure step (2**step s); omit-able with --auto")
    parser.add_argument("--auto", action="store_true", help="leave exposure automatic")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    camera = Camera(args.camera, 1920, 1080, 30, tuple(args.crop),
                    None if args.auto else args.exposure)
    try:
        time.sleep(1.0)
        fps = camera.rate()
        print("camera {0} at {1}x{2}, {3:.1f} pictures/s".format(
            args.camera, camera.size[0], camera.size[1], fps), flush=True)
        if args.plan:
            runs = json.loads(Path(args.plan).read_text())
        else:
            runs = [{"name": "p" + p.replace(",", "_"),
                     "groups": [{"pistons": p, "stroke": args.stroke,
                                 "speed": args.speed, "top": args.top}]}
                    for p in args.pistons] * args.repeat
        for index, run in enumerate(runs):
            if args.plan and _done(run["name"]):
                continue
            probe(camera, run, args, fps)
            if camera.failed:
                raise RuntimeError(camera.failed)
            if index < len(runs) - 1:
                time.sleep(args.settle)
    finally:
        camera.close()


if __name__ == "__main__":
    main()
