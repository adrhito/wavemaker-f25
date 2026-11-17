"""Watch the waterline continuously, record it, and let the operator annotate.

One process owns the camera (Windows lets only one program use it).  It:

- finds the waterline in every column of every picture, live, and saves the
  rows with each picture's arrival time to ``analytics/rig/edges-*.npz``
  (one file a minute);
- saves a small review video, ``analytics/rig/review-*.mp4`` (one every ten
  minutes);
- shows the live picture with the detected waterline drawn in green, and the
  operator's annotations.

Annotating, in the window:

- left click: put a mark exactly where you click;
- then type its height in inches (digits, ``.``, ``-``) and press Enter.
  Backspace edits; Esc abandons the mark;
- ``z``: toggle a 3x magnifier around the mouse, for precise clicks;
- ``u``: remove the last mark;
- ``r``: take the waterline as it is now as zero on the readout.

Marks are saved at once to ``analytics/rig/annotations.json`` with the pixel
and the height, and the readout uses them as soon as two have different
heights.  Nothing here contacts the PLC.

    python tools/camera/vision_rig.py              # until the window is closed with Q
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vision import glassline  # noqa: E402

OUT = ROOT / "analytics" / "rig"
WINDOW = "Wavemaker waterline - click a point, type its height in inches, Enter"
DEFAULT_MM_PER_PX = 0.9845
#: The model's stations are every third 10-pixel group: one tick per 30 px.
STATION_STEP = 30


class Grabber:
    def __init__(self, index: int, exposure: float) -> None:
        self.capture = cv2.VideoCapture(index, cv2.CAP_DSHOW, [
            cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"),
            cv2.CAP_PROP_FRAME_WIDTH, 1920, cv2.CAP_PROP_FRAME_HEIGHT, 1080,
            cv2.CAP_PROP_FPS, 30])
        if not self.capture.isOpened():
            raise RuntimeError("camera {0} did not open".format(index))
        self.capture.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        self.capture.set(cv2.CAP_PROP_GAIN, 0)
        self.capture.set(cv2.CAP_PROP_EXPOSURE, exposure)
        self.cond = threading.Condition()
        self.queue = []
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while self.running:
            ok, frame = self.capture.read()
            now = time.time()
            if ok:
                with self.cond:
                    self.queue.append((now, frame))
                    if len(self.queue) > 120:  # never fall minutes behind
                        self.queue.pop(0)
                    self.cond.notify()

    def take(self, timeout: float = 1.0):
        with self.cond:
            if not self.queue:
                self.cond.wait(timeout)
            if not self.queue:
                return None
            items, self.queue = self.queue, []
            return items

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=2)
        self.capture.release()


class Annotations:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.marks = []
        self.baselines = []   # every baseline ever set, newest last
        if path.exists():
            saved = json.loads(path.read_text())
            self.marks = saved.get("marks", [])
            self.baselines = saved.get("baselines", [])
        self.pending = None   # (x, y) waiting for a value
        self.typed = ""
        self.mode = "height"  # "height", "along" (inches along the chamber) or "baseline"

    @property
    def baseline(self):
        return self.baselines[-1] if self.baselines else None

    def set_baseline(self, row: float, how: str) -> dict:
        """Record the still-water level; the depth comes from the height marks."""
        entry = {"row": float(row), "how": how, "at": time.time(),
                 "when": datetime.now().isoformat(timespec="seconds")}
        fit = self.fit()
        if fit is not None:
            entry["depth_in"] = fit[0] + fit[1] * row
            entry["depth_mm"] = 25.4 * entry["depth_in"]
        self.baselines.append(entry)
        self.save()
        return entry

    def mm_per_row(self) -> float:
        fit = self.fit()
        return -25.4 * fit[1] if fit is not None else DEFAULT_MM_PER_PX

    def floor_mm(self, row: float):
        """Height above the tank floor, by the operator's marks."""
        fit = self.fit()
        return None if fit is None else 25.4 * (fit[0] + fit[1] * row)

    def save(self) -> None:
        fit = self.fit()
        along = [m for m in self.marks if m.get("axis") == "along"]
        along_fit = None
        if len({m["inches"] for m in along}) >= 2:
            b, a = np.polyfit([m["x"] for m in along], [m["inches"] for m in along], 1)
            along_fit = {"inches_at_col0": float(a), "inches_per_col": float(b)}
        self.path.write_text(json.dumps({
            "marks": self.marks, "along_fit": along_fit,
            "baselines": self.baselines, "baseline": self.baseline,
            "fit": None if fit is None else {"inches_at_row0": fit[0],
                                             "inches_per_row": fit[1]},
            "saved": datetime.now().isoformat(timespec="seconds")}, indent=1))

    def fit(self):
        """Height in inches as a + b * row, from the marks; None if too few."""
        # The 36 in tape stands off the glass (it sits 159-163 px above the
        # 30 in one where the others are 155 apart), so it is left out.
        marks = [m for m in self.marks if m.get("axis", "height") == "height"
                 and m["inches"] < 33]
        if len({m["inches"] for m in marks}) < 2:
            return None
        rows = np.array([m["y"] for m in marks])
        inches = np.array([m["inches"] for m in marks])
        b, a = np.polyfit(rows, inches, 1)
        return float(a), float(b)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=1)
    parser.add_argument("--exposure", type=float, default=-7)
    parser.add_argument("--x", type=int, nargs=2, default=(480, 1460))
    parser.add_argument("--band", type=int, nargs=2, default=(330, 900),
                        help="rows searched for the waterline (full picture)")
    parser.add_argument("--track", type=float, default=14.0)
    parser.add_argument("--weak-file", default=str(OUT / "weak_fixed.npy"),
                        help="columns to ignore, learnt on calm water; '' to learn now")
    parser.add_argument("--auto-baseline", action="store_true",
                        help="set the baseline by itself once the water is still")
    parser.add_argument("--start-row", type=float, default=605.0,
                        help="the still waterline's row, where tracking begins")
    parser.add_argument("--hblur", type=int, default=15,
                        help="columns averaged along each row before finding the edge")
    parser.add_argument("--min-step", type=float, default=3.0)
    parser.add_argument("--no-window", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "STOP").unlink(missing_ok=True)
    notes = Annotations(OUT / "annotations.json")
    grab = Grabber(args.camera, args.exposure)
    x0, x1 = args.x
    lo, hi = args.band
    last = np.full(x1 - x0, np.nan)
    # Start on the still waterline, never on the strongest edge in the band
    # (a tape or the frame can be stronger).
    line = np.full(x1 - x0, float(args.start_row))
    lost = 0
    weak, learning = None, []
    tapes = None  # where the opaque measuring tapes hide the water
    if args.weak_file:
        weak = np.load(args.weak_file)
        print("ignoring columns from {0}: {1}".format(
            args.weak_file, _spans(np.flatnonzero(weak) + x0)), flush=True)
    zero_centre = None
    recent = []  # the fitted line's median row over the last two seconds
    motion_log = []  # (time, line at ten stations) for the stillness signal
    snapped, snap_n = 0.0, 0
    auto_done = False
    published = 0.0
    zero_row = None
    chunk_t, chunk_rows, chunk_line, chunk_start = [], [], [], time.time()
    video, video_start = None, 0.0
    mouse = {"x": 960, "y": 600}
    zoom = False
    shown = 0.0
    scale_view = 2 / 3  # 1920x1080 shown as 1280x720

    def on_mouse(event, x, y, flags, param):
        mouse["x"], mouse["y"] = int(x / scale_view), int(y / scale_view)
        if event == cv2.EVENT_LBUTTONDOWN:
            box = magnifier.get("box") if zoom else None
            mx, my = x / scale_view, y / scale_view
            if box and box[0] <= mx < box[0] + 960 and box[1] <= my < box[1] + 540:
                # Clicks in the magnifier map back to the picture.
                mouse["x"] = box[2] + (mx - box[0]) / 3
                mouse["y"] = box[3] + (my - box[1]) / 3
            if notes.mode == "baseline":
                notes.set_baseline(float(mouse["y"]), "clicked")
                return
            notes.pending = (float(mouse["x"]), float(mouse["y"]))
            notes.typed = ""

    magnifier = {}
    if not args.no_window:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, on_mouse)
    print("recording to {0}".format(OUT), flush=True)
    try:
        while not (OUT / "STOP").exists():
            items = grab.take()
            if not items:
                continue
            for stamp, frame in items:
                if tapes is None:
                    # Found once, on the first picture: the tapes do not move.
                    tapes = glassline.tape_mask(frame[lo - 2:hi + 2, x0:x1])
                    print("tape pixels ignored: {0}".format(int(tapes.sum())), flush=True)
                gray = cv2.cvtColor(frame[lo - 2:hi + 2, x0:x1], cv2.COLOR_BGR2GRAY)
                # Averaging along each row first steadies weak columns
                # without blurring the waterline's height.
                gray = cv2.blur(gray.astype(np.float32), (args.hblur, 1))
                strengths = []
                # Lost for a while: widen the search around the last line,
                # a little at a time, rather than searching the whole band.
                reach = args.track * (1 + min(lost, 60) / 15.0)
                rows = glassline.edge_rows_near(gray, 2, hi - lo + 2, line - (lo - 2),
                                                reach, args.min_step,
                                                strengths, forbid=tapes) + (lo - 2)
                if weak is None:
                    learning.append(strengths[0])
                    if len(learning) >= 60:  # two seconds of pictures
                        weak = glassline.weak_columns(learning)
                        print("ignoring {0} weak columns: {1}".format(
                            int(weak.sum()), _spans(np.flatnonzero(weak) + x0)), flush=True)
                        np.save(OUT / "weak_columns.npy", weak)
                if weak is not None:
                    rows[weak] = np.nan
                fitted = glassline.fit_line(rows, window=151)
                if not np.all(np.isnan(fitted)):
                    # Bridge any gap so every column keeps a nearby search.
                    ok = ~np.isnan(fitted)
                    cols = np.arange(len(fitted))
                    line = np.interp(cols, cols[ok], fitted[ok])
                    lost = 0
                else:
                    lost += 1
                last = rows
                if not np.all(np.isnan(line)):
                    recent.append(float(np.nanmedian(line)))
                    del recent[:-60]
                    stations = line[STILL_COLS]
                    motion_log.append((stamp, stations))
                    del motion_log[:-200]
                chunk_t.append(stamp)
                chunk_rows.append(np.where(np.isnan(rows), -1,
                                           np.round(rows * 50)).astype(np.int16))
                chunk_line.append(np.where(np.isnan(fitted), -1,
                                           np.round(fitted * 50)).astype(np.int16))
                if video is None or stamp - video_start > 600:
                    if video is not None:
                        video.release()
                    name = "review-{0}.mp4".format(
                        datetime.fromtimestamp(stamp).strftime("%Y%m%d-%H%M%S"))
                    video = cv2.VideoWriter(str(OUT / name),
                                            cv2.VideoWriter_fourcc(*"mp4v"), 30,
                                            (960, 540))
                    video_start = stamp
                small = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)
                video.write(small)
                if stamp - chunk_start >= 60:
                    _save_chunk(chunk_t, chunk_rows, chunk_line, x0)
                    chunk_t, chunk_rows, chunk_line, chunk_start = [], [], [], stamp
            if time.time() - published > 0.5 and len(motion_log) > 20:
                published = time.time()
                state = _publish(motion_log, notes, line, k_mm=notes.mm_per_row())
                if (args.auto_baseline and not auto_done and state
                        and state["moving_mm"] < 1.0 and len(recent) >= 60):
                    entry = notes.set_baseline(float(np.median(recent)),
                                               "automatic: first 3 s of still water after start")
                    auto_done = True
                    print("baseline set automatically: {0}".format(entry), flush=True)
            if args.no_window or time.time() - shown < 1 / 15:
                continue
            shown = time.time()
            frame = items[-1][1]
            view = frame.copy()
            for i in range(0, len(last), 3):
                if not np.isnan(last[i]):
                    view[int(round(last[i])), x0 + i] = (0, 0, 255)
            pts = [(x0 + i, r) for i, r in enumerate(line) if not np.isnan(r)]
            if pts:
                arr = np.ascontiguousarray(np.array(pts)[::2].round().astype(np.int32))
                cv2.polylines(view, [arr.reshape(-1, 1, 2)], False, (0, 255, 0), 1)
            # Where the measuring happens: the searched band, the columns
            # left out, a tick at every model station, and the main point.
            cv2.line(view, (x0, lo), (x1, lo), (255, 200, 0), 1)
            cv2.line(view, (x0, hi), (x1, hi), (255, 200, 0), 1)
            cv2.putText(view, "search band", (x1 + 6, lo + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 0), 1)
            if weak is not None:
                for i in np.flatnonzero(weak)[::4]:
                    cv2.line(view, (x0 + i, lo), (x0 + i, hi), (90, 90, 90), 1)
            for i in range(STATION_STEP // 2, len(line), STATION_STEP):
                if not np.isnan(line[i]):
                    y = int(round(line[i]))
                    cv2.line(view, (x0 + i, y - 8), (x0 + i, y + 8), (0, 255, 255), 1)
            centre = (x1 - x0) // 2
            if not np.isnan(line[centre]):
                cy = int(round(line[centre]))
                cv2.drawMarker(view, (x0 + centre, cy), (255, 0, 255),
                               cv2.MARKER_CROSS, 40, 2)
                ref = notes.baseline["row"] if notes.baseline else (
                    zero_centre if zero_centre is not None else line[centre])
                height = (ref - line[centre]) * notes.mm_per_row()
                if zero_centre is None:
                    zero_centre = line[centre]
                cv2.putText(view, "measuring point x={0}: {1:+.1f} mm".format(
                    x0 + centre, height), (x0 + centre + 25, cy - 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            for m in notes.marks:
                if m.get("axis") == "along":
                    cv2.line(view, (int(m["x"]), 0), (int(m["x"]), 1079), (255, 128, 0), 1)
                    cv2.putText(view, '{0:g}" along'.format(m["inches"]),
                                (int(m["x"]) + 6, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                (255, 128, 0), 2)
                    continue
                cv2.drawMarker(view, (int(m["x"]), int(m["y"])), (0, 0, 255),
                               cv2.MARKER_CROSS, 24, 1)
                cv2.putText(view, '{0:g}"'.format(m["inches"]),
                            (int(m["x"]) + 8, int(m["y"]) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)
            if notes.pending:
                px, py = notes.pending
                cv2.drawMarker(view, (int(px), int(py)), (0, 255, 255),
                               cv2.MARKER_CROSS, 30, 1)
                cv2.putText(view, '{0} (in): {1}_  Enter to keep, Esc to drop'.format(
                    "height" if notes.mode == "height" else "distance along chamber",
                    notes.typed), (int(px) + 10, int(py) + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            mean_row = np.nanmedian(line) if pts else np.nan
            if zero_row is None and pts:
                zero_row = mean_row
            base = notes.baseline
            k = notes.mm_per_row()
            if base is not None:
                brow = base["row"]
                depth = base.get("depth_mm")
                cv2.line(view, (x0 - 40, int(round(brow))), (x1 + 40, int(round(brow))),
                         (255, 255, 0), 1)
                label = "baseline (still water)"
                if depth is not None:
                    label += ": {0:.1f} mm deep ({1:.2f} in)".format(depth, depth / 25.4)
                cv2.putText(view, label, (x1 + 45, int(brow) + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1)
                if depth is not None:
                    lim = limits(depth, brow, lo, hi, k)
                    for name, mm, colour in (("max crest", lim["crest_mm"], (0, 0, 255)),
                                             ("lowest trough seen", -lim["trough_mm"], (0, 128, 255))):
                        y = int(round(brow - mm / k))
                        cv2.line(view, (x0, y), (x1, y), colour, 1, cv2.LINE_AA)
                        cv2.putText(view, "{0} {1:+.0f} mm ({2})".format(
                            name, mm, lim["crest_by"] if mm > 0 else "camera"),
                            (x0 + 5, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 1)
                    cv2.putText(view, "breaking: wave height (crest to trough) up to {0:.0f} mm "
                                "= 0.78 x depth".format(lim["height_mm"]),
                                (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                now = (brow - mean_row) * k if pts else float("nan")
                level = "{0:+.1f} mm above baseline".format(now)
            else:
                level = "{0:+.1f} mm from start - press b on still water to set the baseline".format(
                    (zero_row - mean_row) * k if pts else float("nan"))
            cv2.putText(view, "waterline: " + level, (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            cv2.putText(view, "green: fitted waterline  yellow ticks: measuring stations  "
                        "magenta: main point  red dots: raw edges  grey: ignored",
                        (20, 1025), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.putText(view, "MODE: {0}   (h cycles height / along / baseline;  b = baseline from water now)".format(
                {"height": "HEIGHT marks", "along": "ALONG-CHAMBER marks",
                 "baseline": "BASELINE - click the still waterline"}[notes.mode]),
                (20, 990), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 128, 0), 2)
            cv2.putText(view, "click=mark  type inches+Enter  z=magnify  u=undo  r=zero  Q=quit",
                        (20, 1060), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            if zoom:
                # A 320x180 patch around the mouse, shown 3x in a box that
                # always fits on the picture.
                px0 = int(np.clip(mouse["x"] - 160, 0, 1600))
                py0 = int(np.clip(mouse["y"] - 90, 0, 900))
                bx0 = int(np.clip(px0 + 160 - 480, 0, 960))
                by0 = int(np.clip(py0 + 90 - 270, 0, 540))
                patch = view[py0:py0 + 180, px0:px0 + 320].copy()
                view[by0:by0 + 540, bx0:bx0 + 960] = cv2.resize(
                    patch, (960, 540), interpolation=cv2.INTER_NEAREST)
                cv2.rectangle(view, (bx0, by0), (bx0 + 959, by0 + 539), (0, 255, 255), 2)
                magnifier["box"] = (bx0, by0, px0, py0)
            if time.time() - snapped > 5.0:
                # What the operator sees, for checking the line from afar.
                snapped = time.time()
                snap_n = (snap_n + 1) % 20
                cv2.imwrite(str(OUT / "snapshot.png"), view)
                cv2.imwrite(str(OUT / "snapshot_{0:02d}.png".format(snap_n)), view)
            cv2.imshow(WINDOW, cv2.resize(view, (1280, 720), interpolation=cv2.INTER_AREA))
            key = cv2.waitKey(1) & 0xFF
            if key == 255:
                continue
            if key == ord("Q"):
                break
            if notes.pending is not None:
                if key in (13, 10):
                    try:
                        inches = float(notes.typed)
                    except ValueError:
                        continue
                    notes.marks.append({"x": notes.pending[0], "y": notes.pending[1],
                                        "inches": inches, "axis": notes.mode,
                                        "at": time.time()})
                    notes.pending, notes.typed = None, ""
                    notes.save()
                elif key == 27:
                    notes.pending, notes.typed = None, ""
                elif key == 8:
                    notes.typed = notes.typed[:-1]
                elif chr(key) in "0123456789.-":
                    notes.typed += chr(key)
                continue
            if key == ord("z"):
                zoom = not zoom
            elif key == ord("h"):
                notes.mode = {"height": "along", "along": "baseline",
                              "baseline": "height"}[notes.mode]
            elif key == ord("b") and recent:
                entry = notes.set_baseline(float(np.median(recent)), "fitted line, 2 s median")
                print("baseline set: {0}".format(entry), flush=True)
            elif key == ord("u") and notes.marks:
                notes.marks.pop()
                notes.save()
            elif key == ord("r"):
                zero_row = mean_row
                zero_centre = line[(x1 - x0) // 2]
    finally:
        if chunk_t:
            _save_chunk(chunk_t, chunk_rows, chunk_line, x0)
        if video is not None:
            video.release()
        grab.close()
        cv2.destroyAllWindows()


#: Rim of the tank above its floor (IIHR sheet 4) and the margin kept below it.
RIM_MM = 1152.5
RIM_MARGIN_MM = 50.0
#: McCowan's limit: a wave breaks when its height reaches 0.78 of the depth.
BREAKING = 0.78


def limits(depth_mm: float, base_row: float, lo: int, hi: int, mm_per_row: float) -> dict:
    """What the water can do at this baseline, and what the camera can see.

    The highest crest is the least of: the breaking limit (a solitary wave
    of 0.78 x depth), the rim less a margin, and the top of the band the
    camera searches.  The lowest trough shown is the bottom of the band.
    """
    candidates = {"breaking": BREAKING * depth_mm,
                  "rim": RIM_MM - depth_mm - RIM_MARGIN_MM,
                  "camera": (base_row - lo) * mm_per_row}
    by = min(candidates, key=candidates.get)
    return {"crest_mm": candidates[by], "crest_by": by,
            "trough_mm": min((hi - base_row) * mm_per_row, depth_mm),
            "height_mm": BREAKING * depth_mm}


#: Ten stations across the window for the stillness signal (window columns).
STILL_COLS = np.linspace(20, 960, 10).astype(int)
#: How long the surface must have been quiet to count as still, s.
STILL_WINDOW = 3.0


def _publish(motion_log, notes, line, k_mm) -> None:
    """Write ``live.json``: how much the surface moved in the last seconds.

    ``moving_mm`` is the largest, over ten stations, of the peak-to-peak
    height in the last ``STILL_WINDOW`` seconds; a campaign waits for it to
    fall below its threshold before firing the next test.
    """
    now = motion_log[-1][0]
    recent = np.array([v for t, v in motion_log if t >= now - STILL_WINDOW])
    if len(recent) < 10:
        return None
    with np.errstate(all="ignore"):
        # Robust: each station's 5-95 % spread, then the median station, so
        # a glitch at one station or one picture cannot hold the test back.
        span = np.nanpercentile(recent, 95, axis=0) - np.nanpercentile(recent, 5, axis=0)
        std = np.nanstd(recent, axis=0)
        span = np.array([np.nanmedian(span)])
        std = np.array([np.nanmedian(std)])
    base = notes.baseline
    level = None
    if base is not None:
        level = float((base["row"] - np.nanmedian(line)) * k_mm)
    state = {"t": now, "moving_mm": float(np.nanmax(span) * k_mm),
             "std_mm": float(np.nanmax(std) * k_mm), "window_s": STILL_WINDOW,
             "level_mm_above_baseline": level,
             "baseline_row": None if base is None else base["row"]}
    tmp = OUT / "live.json.tmp"
    tmp.write_text(json.dumps(state))
    try:
        tmp.replace(OUT / "live.json")
    except OSError:
        pass
    return state


def _spans(cols) -> str:
    """'553-660, 700-702' from a sorted list of columns."""
    if len(cols) == 0:
        return "none"
    out, start, prev = [], cols[0], cols[0]
    for c in list(cols[1:]) + [None]:
        if c is None or c != prev + 1:
            out.append("{0}-{1}".format(start, prev) if prev != start else str(start))
            start = c
        prev = c if c is not None else prev
    return ", ".join(out)


def _save_chunk(times, rows, lines, x0) -> None:
    """Raw column edges and the fitted line, both in fiftieths of a pixel."""
    name = "edges-{0}.npz".format(datetime.fromtimestamp(times[0]).strftime("%Y%m%d-%H%M%S"))
    np.savez_compressed(OUT / name, t=np.array(times), rows50=np.array(rows),
                        line50=np.array(lines), x0=x0)


if __name__ == "__main__":
    main()
