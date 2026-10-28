"""Fit the water model to waves you have measured.

Observations go in a CSV (`wm.py template` writes observations_template.csv).
Each row is one run seen at one station:

    run_id, pistons, position1_mm, position2_mm, speed_mm_s, accel_mm_s2,
    jerk_mm_s3, profile, cycles, column_delay_s, depth_m, station_x_m,
    observed_height_mm, observed_crest_mm, observed_period_s, notes

`pistons` uses the application's display numbers: "all", "1-30", "row8-10"
(William's rows 8 to 10), "lane2", "1,4,7". `station_x_m` is measured along
the channel from its mouth. `observed_height_mm` is the crest-to-trough
height of the first full wave at the station; leave a cell blank if you did
not measure it. `depth_m` is the still-water depth of that run: it decides
which rows reach the water, so enter it every time.

The fit adjusts, by least squares on log wave height, any of:
  gain             overall efficiency of the pistons (leaks, compliance); the default
  channel_damping  multiple of laminar boundary-layer damping (needs one run seen at
                   two or more stations)
  bottom_offset    a shift of every pipe bottom, m (William's 3 in steps were 'roughly';
                   needs runs at several depths)
  disc_zero        disc face height at 0 mm, m (sets the length of trapped air; weak)
  head_absorption  how much the backward wave is lost in the head. The first wave
                   reaches the stations before anything comes back from the head,
                   so first-wave heights cannot fit this; leave it out unless you
                   add later-wave data.
and reports how well the model then explains each run, so systematic
misfits (one depth, one piston set) stand out. `compare` repeats the fit for
both disc seals and ranks them. The bank's layout is not fitted: William
confirmed which end the longest pipes are at (geometry.json).
"""

from __future__ import annotations

import csv
import math
from dataclasses import replace
from typing import Dict, List, Optional

import numpy as np
from scipy.optimize import least_squares

from . import motion, predict, tank
from .geometry import load, parse_pistons

FIELDS = ["run_id", "pistons", "position1_mm", "position2_mm", "speed_mm_s", "accel_mm_s2",
          "jerk_mm_s3", "profile", "cycles", "column_delay_s", "depth_m", "station_x_m",
          "observed_height_mm", "observed_crest_mm", "observed_period_s", "notes"]

BOUNDS = {"gain": (0.3, 2.0), "head_absorption": (0.0, 0.3), "channel_damping": (0.5, 6.0),
          "bottom_offset": (-0.06, 0.06), "disc_zero": (1.2, 1.4)}


def _f(row, k, default=None):
    v = (row.get(k) or "").strip()
    return float(v) if v else default


def read(path) -> List[Dict]:
    with open(path, newline="") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("run_id") or "").strip()]


def _run_from_row(r, g) -> predict.Run:
    p1, p2 = _f(r, "position1_mm", 0.0), _f(r, "position2_mm", 350.0)
    sp, ac, jk = _f(r, "speed_mm_s", 500.0), _f(r, "accel_mm_s2", 10000.0), _f(r, "jerk_mm_s3", 2000.0)
    prof = int(_f(r, "profile", 1))
    prm = motion.Params(p1, p2, sp, sp, ac, ac, ac, ac, jk, jk, 0, 0, prof)
    ids = parse_pistons(r.get("pistons") or "all", g)
    delay = _f(r, "column_delay_s", 0.0)
    delays = {n: ((n - 1) // g.rows) * delay for n in ids} if delay else {}
    depth = _f(r, "depth_m")
    if depth is None:
        raise SystemExit("run %s has no depth_m: the still-water depth decides which rows move water" % r["run_id"])
    return predict.Run(ids, prm, depth, cycles=_f(r, "cycles", 5), delays=delays)


def _predict_rows(rows, g, hp):
    """Predict every observed row. All runs share one station list, so each depth needs
    one tank solution (cached) whatever the run."""
    out = []
    groups = {}
    for r in rows:
        groups.setdefault(r["run_id"], []).append(r)
    stations = sorted({float(r["station_x_m"]) for r in rows})
    for rid, rs in groups.items():
        run = _run_from_row(rs[0], g)
        res = predict.predict(run, stations, g, hp)
        by_x = dict(zip(stations, res["metrics"]))
        for r in rs:
            out.append((r, by_x[float(r["station_x_m"])]))
    return out


def fit(path, fit_params=("gain",), seal: str = "tight", row_10_end: Optional[str] = None,
        verbose=True):
    rows = read(path)
    rows = [r for r in rows if _f(r, "observed_height_mm") is not None]
    if not rows:
        raise SystemExit("no rows with observed_height_mm in %s" % path)
    base = {"row_10_end": row_10_end} if row_10_end else {}
    g0 = load(**base)
    hp0 = tank.HydroParams(seal=seal)
    names = list(fit_params)
    for n in names:
        if n not in BOUNDS:
            raise SystemExit("cannot fit %r; choose from %s" % (n, ", ".join(BOUNDS)))
    x0 = [getattr(hp0, n) if hasattr(hp0, n) else getattr(g0, n) for n in names]
    lo = [BOUNDS[n][0] for n in names]
    hi = [BOUNDS[n][1] for n in names]

    def setup(x):
        g, hp = load(**base), replace(hp0)
        for n, v in zip(names, x):
            if hasattr(hp, n):
                setattr(hp, n, float(v))
            else:
                setattr(g, n, float(v))
        return g, hp

    def resid(x):
        g, hp = setup(x)
        pr = _predict_rows(rows, g, hp)
        return np.array([math.log(max(m["first_wave_height_mm"], 1e-3) / _f(r, "observed_height_mm"))
                         for r, m in pr])

    sol = least_squares(resid, x0, bounds=(lo, hi), diff_step=0.05, max_nfev=40, verbose=0)
    g, hp = setup(sol.x)
    pr = _predict_rows(rows, g, hp)
    table = []
    for r, m in pr:
        obs = _f(r, "observed_height_mm")
        table.append({"run_id": r["run_id"], "station_x_m": r["station_x_m"], "depth_m": r["depth_m"],
                      "pistons": r["pistons"], "observed_mm": obs,
                      "predicted_mm": round(m["first_wave_height_mm"], 1),
                      "ratio": round(m["first_wave_height_mm"] / obs, 3)})
    rms = float(np.sqrt(np.mean(sol.fun ** 2)))
    fitted = dict(zip(names, [float(v) for v in sol.x]))
    if verbose:
        print("seal %s, row 10 at the %s end" % (seal, g.row_10_end))
        print("fitted:", {k: round(v, 4) for k, v in fitted.items()})
        print("rms log error %.3f  (about %.0f%% typical miss)" % (rms, 100 * (math.exp(rms) - 1)))
    return {"seal": seal, "row_10_end": g.row_10_end, "fitted": fitted, "rms_log": rms, "table": table}


def compare(path, fit_params=("gain",)):
    """Fit both disc seals and rank them by rms error."""
    results = []
    for seal in ("tight", "leaky"):
        results.append(fit(path, fit_params, seal=seal))
        print()
    results.sort(key=lambda r: r["rms_log"])
    print("ranked (best first):")
    for r in results:
        print("  seal %-5s  row 10 at the %-7s  rms log %.3f  %s" % (r["seal"], r["row_10_end"], r["rms_log"],
                                                                   {k: round(v, 3) for k, v in r["fitted"].items()}))
    return {"best": results[0], "all": results}


def write_template(path):
    fast = [20000, 7500, 1]
    rows = [
        ["shallow-150", "row1-3", 220, 370, 900] + fast + [3, 0, 0.30, 1.5, "", "", "",
         "one row per run per station; rows 1-3 are the ones in the water at 0.30 m"],
        ["shallow-150", "row1-3", 220, 370, 900] + fast + [3, 0, 0.30, 5.0, "", "", "", ""],
        ["shallow-300", "row1-3", 70, 370, 900] + fast + [3, 0, 0.30, 5.0, "", "", "",
         "stroke longer than the 173 mm row-1 submergence: tells the two seal behaviours apart"],
        ["row1-300", "row1", 70, 370, 900] + fast + [3, 0, 0.30, 5.0, "", "", "",
         "one row alone"],
        ["all-200", "all", 170, 370, 900] + fast + [3, 0, 0.40, 5.0, "", "", "",
         "at 0.40 m rows 1-4 are wet"],
    ]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        w.writerows(rows)
