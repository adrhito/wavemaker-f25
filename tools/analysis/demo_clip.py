"""Render docs/media/waterline-demo.mp4: the 16 Nov 21:47 push-down crest, with the waterline drawn.

    python tools/analysis/demo_clip.py . out.mp4   (needs analytics/rig from the rig laptop)
"""
import glob
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
VIDEO = ROOT / "analytics/rig/review-20261116-214233.mp4"
START = datetime(2026, 11, 16, 21, 42, 33)
FIRE = 1794883653.87
T0, T1 = FIRE - 1.0, FIRE + 36.0
PX_PER_IN = 1 / 0.9845  # px per mm, from the tapes (vision/rigs)

files = sorted(glob.glob(str(ROOT / "analytics/rig/edges-20261116-21[45]*.npz")))
ts, rows = [], []
x0 = None
for f in files:
    d = np.load(f)
    ts.append(d["t"]); rows.append(d["rows50"]); x0 = int(d["x0"])
t = np.concatenate(ts)
r = np.concatenate(rows).astype(float) / 50.0
r[r < 0] = np.nan
cols = x0 + np.arange(r.shape[1])
# Columns that misread on still water (fixed objects behind the glass) are dropped.
still = np.nanmedian(r[(t > FIRE - 30) & (t < FIRE)], axis=0)
smooth_still = np.nanmedian(np.lib.stride_tricks.sliding_window_view(
    np.pad(still, 100, mode="edge"), 201), axis=1)
bad = ~(np.abs(still - smooth_still) < 3)
bad = np.convolve(bad.astype(float), np.ones(21), mode="same") > 0
r[:, bad] = np.nan
centre = np.nanmedian(r[:, 300:800], axis=1)
base = np.nanmedian(centre[(t > FIRE - 6) & (t < FIRE)])
height_in = (base - centre) / PX_PER_IN

cap = cv2.VideoCapture(str(VIDEO))
# Video frame i was written with the i-th stamp of the record from START on.
first = int(np.searchsorted(t, START.timestamp() - 0.5))
i0 = int(np.searchsorted(t, T0)) - first
i1 = int(np.searchsorted(t, T1)) - first
cap.set(cv2.CAP_PROP_POS_FRAMES, i0)

W, H = 960, 540
fourcc = cv2.VideoWriter_fourcc(*"mp4v")
writer = cv2.VideoWriter(str(OUT), fourcc, 60, (W, H))
GREEN = (60, 230, 60)
for i in range(i0, i1):
    ok, frame = cap.read()
    if not ok:
        break
    k = first + i
    raw = r[k]
    pad = np.pad(raw, 40, mode="edge")
    med = np.nanmedian(np.lib.stride_tricks.sliding_window_view(pad, 81), axis=1)
    line = np.where(np.abs(raw - med) < 6, raw, np.nan)
    ok_c = ~np.isnan(line)
    if ok_c.sum() > 10:
        line = np.interp(np.arange(len(line)), np.flatnonzero(ok_c), line[ok_c])
        sm = np.convolve(np.pad(line, 7, mode="edge"), np.ones(15) / 15, mode="valid")
        line = sm
    good = ~np.isnan(line)
    pts = np.stack([cols[good] * 0.5, line[good] * 0.5], axis=1).astype(np.int32)
    if len(pts) > 2:
        cv2.polylines(frame, [pts.reshape(-1, 1, 2)], False, GREEN, 2, cv2.LINE_AA)
    # caption
    cv2.rectangle(frame, (0, 0), (W, 34), (25, 25, 25), -1)
    cv2.putText(frame, "Waterline found by the camera (green). Pistons 1-12 push down 180 mm. 2x speed",
                (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA)
    # trace inset
    px0, py0, pw, ph = 600, 440, 345, 90
    cv2.rectangle(frame, (px0, py0), (px0 + pw, py0 + ph), (25, 25, 25), -1)
    sel = (t >= T0) & (t <= T1)
    tt, hh = t[sel], height_in[sel]
    lo, hi = -8.0, 14.0
    xs = px0 + 8 + (tt - T0) / (T1 - T0) * (pw - 16)
    ys = py0 + ph - 8 - (np.clip(hh, lo, hi) - lo) / (hi - lo) * (ph - 26)
    zero_y = int(py0 + ph - 8 - (0 - lo) / (hi - lo) * (ph - 26))
    cv2.line(frame, (px0 + 8, zero_y), (px0 + pw - 8, zero_y), (90, 90, 90), 1)
    now = t[k]
    done = (tt <= now) & ~np.isnan(hh)
    p = np.stack([xs[done], ys[done]], axis=1).astype(np.int32)
    if len(p) > 1:
        cv2.polylines(frame, [p.reshape(-1, 1, 2)], False, GREEN, 2, cv2.LINE_AA)
    cur = height_in[k]
    label = "water height: {0:+.0f} mm".format(cur) if not np.isnan(cur) else "height at centre"
    cv2.putText(frame, label, (px0 + 8, py0 + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (240, 240, 240), 1, cv2.LINE_AA)
    writer.write(frame)
writer.release()
print("frames", i1 - i0, "peak in", np.nanmax(height_in[(t > T0) & (t < T1)]))
