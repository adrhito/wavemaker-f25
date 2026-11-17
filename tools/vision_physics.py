"""Predict the camera's waves from the tank model, driven by the measured piston motion.

The bridge between ``vision/`` (what the camera saw, what the pistons did)
and ``tank-model/`` (the physics: air in each pipe, which rows reach the
water at this depth, the ducts, the head and the channel).  For one logged
run it:

1. rebuilds every piston's disc position from wavectl's samples
   (``wavefit.legs_from_samples``) on a fine grid;
2. turns it into water flux with ``air.pipe_flux`` -- a dry row moves none;
3. applies ``tank.transfer`` at the camera's stations, placed along the
   channel by the operator's marks (``vision/rigs/channel_window_185in.json``);
4. compares with the measured surface.

The depth is a variable: the still-water baseline marked in the window, or
``--depth``.  Nothing here contacts the PLC.

    python tools/vision_physics.py E_bigwave_1 --plot out.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tank-model"))

from vision import wavefit  # noqa: E402
from wavemodel import air, tank  # noqa: E402
from wavemodel.geometry import load as load_geometry  # noqa: E402
from wavemodel import predict as tm_predict  # noqa: E402

RIG = json.loads((ROOT / "vision" / "rigs" / "channel_window_185in.json").read_text())
FINE = 0.002
#: The picture columns compared with the model: across the window, clear of
#: the ignored columns (548-622, 1301-1311).
STATION_PX = [1435.0, 1195.0, 975.0, 755.0, 515.0]


def station_x_m(px):
    """Distance along the channel, m, of a picture column (operator's marks)."""
    a = RIG["along_inches"]
    return (a["at_col0"] + a["per_col"] * np.asarray(px, float)) * 0.0254


def flux_from_samples(entry, origin, n, dt, depth, g, seal="tight"):
    """Water flux of every piston (m^3/s) on the coarse grid, from its samples."""
    times, pistons, pos = wavefit._read_samples(Path(entry["reply"]["data"]["samples"]["file"]))
    t_rel = times - origin
    nf = int(round(n * dt / FINE))
    step = int(round(dt / FINE))
    tf = np.arange(nf) * FINE
    Q = np.zeros((g.rows * g.columns, n))
    zd_park = g.disc_height(g.park_mm)
    for col, p in enumerate(pistons):
        legs = wavefit.legs_from_samples(t_rel, pos[:, col])
        start = wavefit.REST_MM - pos[0, col]
        lift = wavefit.lift_series(tf, legs, start)
        mm = wavefit.REST_MM - lift
        row = (p - 1) // g.rows + 1
        q, _ = air.pipe_flux(g.disc_height(mm), g.row_bottom(row), depth, g.bore_area,
                             FINE, step, n, zd_park, seal)
        Q[p - 1] = q
    return Q


def predict(entry, origin, stations_m, depth, gain=1.0, seal="tight", hp=None):
    g = load_geometry()
    hp = hp or tank.HydroParams()
    dt = tm_predict.DT
    n = int(round(tm_predict.WINDOW / dt))
    t = np.arange(n) * dt
    sigma = tm_predict.SIGMA_DECADES / tm_predict.WINDOW
    freqs = np.fft.rfftfreq(n, dt)
    # Every run whose motion is in, or shortly before, the window: the tank
    # still rings with the earlier ones' waves.
    entries = entry if isinstance(entry, list) else [entry]
    Q = sum(flux_from_samples(e, origin, n, dt, depth, g, seal) for e in entries)
    QF = np.fft.rfft(Q * np.exp(-sigma * t), axis=1)
    tfn = tank.transfer(depth, list(stations_m), freqs, g, hp, sigma=sigma, verbose=True)
    EF = np.einsum("fps,pf->sf", np.conj(tfn.H), QF)
    eta = np.fft.irfft(EF, n=n, axis=1) * np.exp(sigma * t) * hp.gain * gain
    return t, eta * 1000.0, Q


def compare(run_name, depth=None, hp=None, seal="tight", before=15.0, after=85.0, n_stations=5):
    """Measured and predicted heights (record steps x stations) for one run."""
    entries = [json.loads(l) for l in (ROOT / "analytics/rig/runs.jsonl").read_text().splitlines()]
    entry = [e for e in entries if e["run"]["name"] == run_name and e["reply"].get("ok")][-1]
    fire = entry["fire_sent"]
    one = ROOT / "analytics" / "rig" / "_one_run.jsonl"
    one.write_text(json.dumps(entry) + "\n")
    probe, runs = wavefit.load_rig(ROOT / "analytics/rig", runs_log=one,
                                   t_start=fire - before, t_end=fire + after)
    if not runs:
        raise ValueError("the record does not cover %s's start" % run_name)
    origin = fire - runs[0]["fire"]
    used = [e for e in entries if e["reply"].get("ok")
            and e.get("reply", {}).get("data", {}).get("samples")
            and origin - 120 < e["fire_sent"] <= fire]
    if depth is None:
        base = wavefit.baseline_at(ROOT / "analytics/rig", fire)
        depth = (base or {}).get("depth_mm", RIG["still_water_depth_mm"]) / 1000.0
    # Fixed picture columns, the same for every run, so the tank's transfer
    # functions are solved once per setting and cached.
    want = np.array(STATION_PX[:n_stations] if n_stations <= len(STATION_PX) else STATION_PX)
    cols = np.array([int(np.argmin(np.abs(probe.stations_px - w))) for w in want])
    xs = station_x_m(want)
    lead = max(0.0, origin - min(e.get("watch_started", e["fire_sent"]) for e in used) + 5.0)
    t, eta, Q = predict(used, origin - lead, xs, depth, seal=seal, hp=hp)
    t = t - lead
    meas = probe.eta[:, cols]
    pred = np.array([np.interp(probe.t, t, e) for e in eta]).T
    return {"t": probe.t - runs[0]["fire"], "meas": meas, "pred": pred, "x": xs,
            "depth": depth, "used": [e["run"]["name"] for e in used]}


_PREP = {}


def prepared(run_name, depth=None, seal="tight", before=15.0, after=85.0, n_stations=5):
    """Everything about a run that does not depend on the tank's settings.

    The measured surface and each piston's flux (motion through the air
    model) are worked out once; ``synth`` then applies any tank settings.
    """
    key = (run_name, depth, seal, before, after, n_stations)
    if key in _PREP:
        return _PREP[key]
    entries = [json.loads(l) for l in (ROOT / "analytics/rig/runs.jsonl").read_text().splitlines()]
    entry = [e for e in entries if e["run"]["name"] == run_name and e["reply"].get("ok")][-1]
    fire = entry["fire_sent"]
    one = ROOT / "analytics" / "rig" / "_one_run.jsonl"
    one.write_text(json.dumps(entry) + chr(10))
    probe, runs = wavefit.load_rig(ROOT / "analytics/rig", runs_log=one,
                                   t_start=fire - before, t_end=fire + after)
    if not runs:
        raise ValueError("the record does not cover %s's start" % run_name)
    origin = fire - runs[0]["fire"]
    used = [e for e in entries if e["reply"].get("ok")
            and e.get("reply", {}).get("data", {}).get("samples")
            and origin - 120 < e["fire_sent"] <= fire]
    if depth is None:
        base = wavefit.baseline_at(ROOT / "analytics/rig", fire)
        depth = (base or {}).get("depth_mm", RIG["still_water_depth_mm"]) / 1000.0
    want = np.array(STATION_PX[:n_stations])
    cols = np.array([int(np.argmin(np.abs(probe.stations_px - w))) for w in want])
    lead = max(0.0, origin - min(e.get("watch_started", e["fire_sent"]) for e in used) + 5.0)
    g = load_geometry()
    dt = tm_predict.DT
    n = int(round(tm_predict.WINDOW / dt))
    Q = sum(flux_from_samples(e, origin - lead, n, dt, depth, g, seal) for e in used)
    out = {"t_rec": probe.t, "fire_rec": runs[0]["fire"], "meas": probe.eta[:, cols],
           "x": station_x_m(want), "depth": depth, "Q": Q, "lead": lead, "n": n, "dt": dt,
           "used": [e["run"]["name"] for e in used]}
    _PREP[key] = out
    return out


def synth(prep, hp=None, channel_length=None):
    """The model's surface at the stations for prepared flux and tank settings.

    ``channel_length`` overrides the channel's length: with the gate open the
    waves run on into the reservoir before they reflect (16 Nov 2026).
    """
    import dataclasses
    g = load_geometry()
    if channel_length is not None:
        g = dataclasses.replace(g, channel_length=float(channel_length))
    hp = hp or tank.HydroParams()
    n, dt = prep["n"], prep["dt"]
    t = np.arange(n) * dt
    sigma = tm_predict.SIGMA_DECADES / tm_predict.WINDOW
    freqs = np.fft.rfftfreq(n, dt)
    QF = np.fft.rfft(prep["Q"] * np.exp(-sigma * t), axis=1)
    tfn = tank.transfer(prep["depth"], list(prep["x"]), freqs, g, hp, sigma=sigma)
    EF = np.einsum("fps,pf->sf", np.conj(tfn.H), QF)
    eta = np.fft.irfft(EF, n=n, axis=1) * np.exp(sigma * t) * hp.gain * 1000.0
    t = t - prep["lead"]
    pred = np.array([np.interp(prep["t_rec"], t, e) for e in eta]).T
    return {"t": prep["t_rec"] - prep["fire_rec"], "meas": prep["meas"], "pred": pred,
            "x": prep["x"], "depth": prep["depth"], "used": prep["used"]}


def rel_error(c, gain=1.0):
    ok = ~np.any(np.isnan(c["meas"]), axis=1)
    m, p = c["meas"][ok], c["pred"][ok] * gain
    return float(np.sqrt(np.mean((p - m) ** 2)) / np.sqrt(np.mean(m ** 2)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="run name in analytics/rig/runs.jsonl")
    parser.add_argument("--depth", type=float, help="still water, m (default: the marked baseline)")
    parser.add_argument("--before", type=float, default=15.0)
    parser.add_argument("--after", type=float, default=85.0)
    parser.add_argument("--stations", type=int, default=5)
    parser.add_argument("--seal", default="tight", choices=["tight", "leaky"])
    parser.add_argument("--plot")
    args = parser.parse_args()

    entries = [json.loads(l) for l in (ROOT / "analytics/rig/runs.jsonl").read_text().splitlines()]
    entry = [e for e in entries if e["run"]["name"] == args.run and e["reply"].get("ok")][-1]
    fire = entry["fire_sent"]
    one = ROOT / "analytics" / "rig" / "_one_run.jsonl"
    one.write_text(json.dumps(entry) + "\n")
    probe, runs = wavefit.load_rig(ROOT / "analytics/rig", runs_log=one,
                                   t_start=fire - args.before, t_end=fire + args.after)
    if not runs:
        raise SystemExit("the record does not cover %s's start (the window was not recording)" % args.run)
    origin = fire - runs[0]["fire"]  # epoch of the record's t = 0
    # Earlier runs within the tank's memory (two minutes) also drive the water.
    entries_used = [e for e in entries if e["reply"].get("ok")
                    and e.get("reply", {}).get("data", {}).get("samples")
                    and origin - 120 < e["fire_sent"] <= fire]
    depth = args.depth
    if depth is None:
        base = wavefit.baseline_at(ROOT / "analytics/rig", fire)
        depth = (base or {}).get("depth_mm", RIG["still_water_depth_mm"]) / 1000.0
    cols = np.linspace(0, len(probe.stations_px) - 1, args.stations).round().astype(int)
    px = probe.stations_px[cols]
    xs = station_x_m(px)
    print("depth %.3f m; stations %s m" % (depth, np.round(xs, 2)))
    # Earlier runs start before t = 0: shift the clock so they fit the window.
    lead = max(0.0, origin - min(e["fire_sent"] for e in entries_used) + 5.0)
    t, eta, Q = predict(entries_used, origin - lead, xs, depth, seal=args.seal)
    t = t - lead
    print("runs driving the window: %s" % [e["run"]["name"] for e in entries_used])
    meas = probe.eta[:, cols]
    pred = np.array([np.interp(probe.t, t, e) for e in eta]).T
    ok = ~np.any(np.isnan(meas), axis=1)
    m, pr = meas[ok], pred[ok]
    rel = np.sqrt(np.mean((pr - m) ** 2)) / np.sqrt(np.mean(m ** 2))
    best = float(np.sum(pr * m) / np.sum(pr * pr))
    rel_g = np.sqrt(np.mean((best * pr - m) ** 2)) / np.sqrt(np.mean(m ** 2))
    print("wet rows moved %.1f L peak flux; measured rms %.1f mm, predicted rms %.1f mm"
          % (1000 * np.abs(Q).sum(axis=0).max() * tm_predict.DT, np.sqrt(np.mean(m ** 2)),
             np.sqrt(np.mean(pr ** 2))))
    print("uncalibrated error %.1f%%; with a best single gain (%.2f) %.1f%%" % (100 * rel, best, 100 * rel_g))
    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(len(cols), 1, figsize=(14, 2.2 * len(cols)), sharex=True)
        rt = probe.t - runs[0]["fire"]
        for ax, k, x in zip(axes, range(len(cols)), xs):
            ax.plot(rt, meas[:, k], "k", lw=1, label="measured")
            ax.plot(rt, pred[:, k], "r", lw=1, label="tank model (uncalibrated)")
            ax.set_ylabel("x=%.2f m\nmm" % x)
        axes[0].legend(loc="upper right")
        axes[0].set_title("%s at depth %.0f mm: camera vs physics" % (args.run, depth * 1000))
        axes[-1].set_xlabel("s from the run command")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=100)
        print("plot:", args.plot)


if __name__ == "__main__":
    main()
