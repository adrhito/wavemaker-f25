"""Write validation.json: the camera's measurements the simulator is scored against.

Needs the rig's records (``analytics/rig/runs.jsonl`` and ``channel_cache/``,
which stay on the rig laptop).  For each logged run it keeps:

- its commands, and when its pistons actually started: the simulated flux
  is slid against the flux measured from the drives' position samples until
  they line up best;
- the runs sent in the five minutes before it, with their start times, since
  their waves were still sloshing when this one was measured;
- the surface at the window's centre station, with readings over +40 mm
  dropped before 16 Nov 23:40 (the line could stick to the tape there).

Then ``wm.py flow-check`` / ``flow-calibrate`` and the tests can score the
simulator without the raw footage.

    python calibration/build_validation.py      (from tank-model/)
"""

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

from wavemodel import flow  # noqa: E402
from wavemodel.geometry import load  # noqa: E402

RIG = ROOT / "analytics" / "rig"
STATION = 2                      # the centre of the five stations
HISTORY_S = 300.0
TAPE_SAFE_MM = 40.0
TAPE_FIXED = datetime(2026, 11, 16, 23, 40).timestamp()
GOOD_FROM = datetime(2026, 11, 16, 20, 25).timestamp()


def spec(cmds):
    return [{k: v for k, v in asdict(c).items() if k != "start"} for c in cmds]


def aligned_start(run, tq, Qmeas, depth, g):
    """When the pistons began, s after sending: best match of simulated to measured flux."""
    tot = Qmeas.sum(axis=0)
    act = np.abs(Qmeas).sum(axis=0)
    on = np.nonzero((act > 0.01 * act.max()) & (tq > -5))[0]
    if not len(on):
        return None                     # the drives' samples missed this run
    rough = float(tq[on[0]])
    dur = float(tq[-1]) + 5
    nt = int(round(dur / flow.DT)) + 1
    _, _, Q = flow.piston_flux(flow.command_from_run(run, 0.0), depth, nt, g)
    sim = Q.sum(axis=0)
    ts = np.arange(nt) * flow.DT
    best, best_s = -np.inf, rough
    for s in np.arange(rough - 1.5, rough + 1.5, 0.02):
        m = np.interp(tq, ts + s, sim, left=0.0, right=0.0)
        sel = (tq > rough - 3) & (tq < rough + 30)
        score = -np.sum((m[sel] - tot[sel]) ** 2)
        if score > best:
            best, best_s = score, s
    return round(float(best_s), 3)


def main():
    g = load()
    runs = [json.loads(line) for line in (RIG / "runs.jsonl").read_text().splitlines()]
    for r in runs:
        r["run"].setdefault("kind", r.get("kind"))
    by_name = {r["run"]["name"]: r for r in runs}
    cases, latency = [], {}
    for path in sorted((RIG / "channel_cache").glob("*.npz")):
        e = by_name.get(path.stem)
        if e is None or e["fire_sent"] < GOOD_FROM or e["run"].get("kind") == "soliton":
            continue                      # soliton: the upward fire, staged differently; not modelled
        z = np.load(path)
        depth = float(z["depth"])
        start = aligned_start(e["run"], z["tq"], z["Qrows"], depth, g)
        if start is None:
            continue
        cmds = flow.command_from_run(e["run"], start)
        latency.setdefault(cmds[0].kind, []).append(start)
        t = z["t"]
        meas = z["meas"][:, STATION].astype(float)
        base = np.nanmean(meas[(t >= -1.0) & (t < 0)])
        meas = meas - base
        if e["fire_sent"] < TAPE_FIXED:
            meas[meas > TAPE_SAFE_MM] = np.nan
        later = [r["fire_sent"] for r in runs if r["fire_sent"] > e["fire_sent"] + 0.5]
        end = min(40.0, min(later) - e["fire_sent"] + 4.0) if later else 40.0
        cases.append({"name": path.stem, "fire": e["fire_sent"], "kind": cmds[0].kind, "depth_m": depth,
                      "x_m": float(z["x"][STATION]), "start_s": start, "end_s": round(end, 2),
                      "commands": spec(cmds), "t": [round(float(v), 3) for v in t],
                      "eta_mm": [None if np.isnan(v) else round(float(v), 2) for v in meas]})
    lat = {k: float(np.median(v)) for k, v in latency.items()}
    for c in cases:
        hist = []
        for r in runs:
            dt = r["fire_sent"] - c["fire"]
            if not (-HISTORY_S < dt < -0.5) or r["run"].get("kind") == "soliton":
                continue
            try:
                cm = flow.command_from_run(r["run"], 0.0)
            except (KeyError, ValueError):
                continue
            own = next((x for x in cases if x["name"] == r["run"]["name"] and abs(x["fire"] - r["fire_sent"]) < 1), None)
            st = own["start_s"] if own else lat.get(cm[0].kind, 1.5)
            hist.append({"name": r["run"]["name"], "start_s": round(dt + st, 3), "commands": spec(cm)})
        c["history"] = hist
    for c in cases:
        del c["fire"]
    out = {"what": "Camera measurements at the window's centre station, 16 Nov 2026, for scoring wavemodel/flow.py",
           "time_zero": "the moment the run was sent; start_s is when its pistons began to move",
           "latency_s": lat, "cases": cases}
    (HERE / "validation.json").write_text(json.dumps(out, separators=(",", ":")))
    print("wrote %d cases; start after sending, median by kind: %s" % (len(cases), lat))


if __name__ == "__main__":
    main()
