"""A fast 1-D channel model of the camera's waves, fitted on the logged runs.

Each row of pistons pushes water out of its duct at the channel mouth; the
flux comes from the measured piston motion through the tank model's air
model (``air.pipe_flux``), so rows above the water push nothing.  The waves
then run down the channel at the linear dispersive speed for the depth, are
damped by a frequency-dependent rate, reflect partly at the far end (the
gate is open, so its distance ``L`` is fitted) and partly at the head, and
bounce between the two:

    eta(x, w) = sum_n g_n Q_n(w) e^{-i w tau_n} / (b c(w))
                * [e^{-i k x} + R_e e^{-i k (2L - x)}] / (1 - R_e R_h e^{-i w tau_h} e^{-2 i k L})

with k complex (k - i delta(w)).  Depth h is an input (each run's marked
baseline), x is the station's distance along the channel, so the fitted
model carries to other depths and other windows.  Evaluating it costs an
FFT, so every parameter is fitted at once by least squares, and scored by
leaving runs out.

    python tools/camera/vision_channel.py fit          # fit on all usable runs, K-fold scores
    python tools/camera/vision_channel.py fit --plot   # plus a picture of the worst held-out runs
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "camera"))
sys.path.insert(0, str(ROOT / "tank-model"))

from wavemodel import linear  # noqa: E402

CACHE = ROOT / "analytics" / "rig" / "channel_cache"
RUNS = ROOT / "analytics" / "rig" / "runs.jsonl"
WIDTH = 0.74295          # channel inside width, m (taped)
DT = 0.1                 # model grid, s
NFFT = 8192              # 819 s at 0.1 s: room for 5 minutes of history and the window
ROWS = 6
#: Camera data before this is unusable (the first tracker).
GOOD_FROM = datetime(2026, 11, 16, 20, 25).timestamp()


# ------------------------------------------------------------------ data

def usable_runs():
    out = []
    for line in RUNS.read_text().splitlines():
        e = json.loads(line)
        if e["fire_sent"] < GOOD_FROM or not (e.get("reply") or {}).get("ok"):
            continue
        if not ((e.get("reply") or {}).get("data") or {}).get("samples"):
            continue
        out.append(e)
    return out


def load_case(name, depth=None, after=40.0):
    """Measured surface and per-row flux for one run, cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (name + ".npz")
    if path.exists():
        z = np.load(path)
        return {k: z[k] for k in z.files}
    from vision_physics import prepared  # slow import chain; only when needed
    p = prepared(name, depth, before=8.0, after=after)
    t_rec = p["t_rec"] - p["fire_rec"]                  # s from this run's fire
    tq = np.arange(p["n"]) * p["dt"] - p["lead"] - p["fire_rec"]
    Q = p["Q"]
    rows = np.array([Q[3 * r:3 * r + 3].sum(axis=0) for r in range(ROWS)])
    case = {"t": t_rec, "meas": p["meas"], "x": p["x"], "depth": np.array(p["depth"]),
            "tq": tq, "Qrows": rows}
    np.savez_compressed(path, **case)
    return case


# ------------------------------------------------------------------ flux, once per run

FLUX = ROOT / "analytics" / "rig" / "flux_cache"
FLUX_DT = 0.04


def run_flux(entry, depth=0.406):
    """This run's own per-row water flux on its own stretch of the shared clock.

    Computed once from its position samples (through the air model) and
    cached; any window then sums the runs that overlap it, before and after.
    """
    FLUX.mkdir(parents=True, exist_ok=True)
    name = entry["run"]["name"]
    path = FLUX / ("%s_%03d.npz" % (name, round(depth * 1000)))
    if path.exists():
        z = np.load(path)
        return float(z["t0"]), z["Q"]
    from vision_physics import flux_from_samples, load_geometry
    from vision import wavefit
    times, _, _ = wavefit._read_samples(Path(entry["reply"]["data"]["samples"]["file"]))
    t0 = float(times[0]) - 2.0
    n = int(np.ceil((times[-1] + 4.0 - t0) / FLUX_DT))
    g = load_geometry()
    Q = flux_from_samples(entry, t0, n, FLUX_DT, depth, g)
    rows = np.array([Q[3 * r:3 * r + 3].sum(axis=0) for r in range(ROWS)])
    np.savez_compressed(path, t0=t0, Q=rows)
    return t0, rows


def stations_only(fire, before=8.0, after=40.0, half=5):
    """The surface at the five stations around one run, reading little memory.

    Reads only the record pieces that overlap the window and, from each, only
    ``half`` columns either side of each station, so a window costs a few
    megabytes instead of hundreds.
    """
    from vision import wavefit
    from vision_physics import STATION_PX, station_x_m
    folder = ROOT / "analytics" / "rig"
    paths = sorted(folder.glob("edges-*.npz"))
    stamps = [datetime.strptime(p.stem[6:], "%Y%m%d-%H%M%S").timestamp() for p in paths]
    ts, cols = [], []
    for path, st in zip(paths, stamps):
        if not (fire - before - 120 <= st <= fire + after):
            continue
        z = np.load(path)
        key = "line50" if "line50" in z.files else "rows50"
        raw = z[key]
        x0 = int(z["x0"])
        t = z["t"]
        keep = (t >= fire - before) & (t <= fire + after)
        if not keep.any():
            continue
        block = []
        for px in STATION_PX:
            c = int(round(px)) - x0
            a, b = max(c - half, 0), min(c + half + 1, raw.shape[1])
            v = raw[keep, a:b].astype(np.float64) / 50.0
            v[raw[keep, a:b] < 0] = np.nan
            with np.errstate(all="ignore"):
                block.append(np.nanmedian(v, axis=1) if b > a else np.full(keep.sum(), np.nan))
        ts.append(t[keep])
        cols.append(np.array(block).T)
    if not ts:
        raise ValueError("camera did not record this window")
    t = np.concatenate(ts)
    rows = np.vstack(cols)
    base = wavefit.baseline_at(folder, t[0])
    still = base["row"] if base else np.nanmedian(rows, axis=0)
    eta = (still - rows) * wavefit.MM_PER_PX
    grid = np.arange(-before, after, DT) + DT / 2
    out = np.full((len(grid), eta.shape[1]), np.nan)
    idx = np.floor((t - fire + before) / DT).astype(int)
    for k in range(len(grid)):
        sel = idx == k
        if sel.any():
            with np.errstate(all="ignore"):
                out[k] = np.nanmean(eta[sel], axis=0)
    return grid, out, station_x_m(np.array(STATION_PX))


def load_case2(name, entries, depth=0.406, before=8.0, after=40.0, history=300.0):
    """Measured surface for one run's window, with every run's flux around it."""
    from vision import wavefit
    e = [x for x in entries if x["run"]["name"] == name][-1]
    fire = e["fire_sent"]
    meas_path = CACHE / (name + ".npz")
    if meas_path.exists():
        z = np.load(meas_path)
        t, meas, x = z["t"], z["meas"], z["x"]
    else:
        t, meas, x = stations_only(fire, before, after)
    grid_t = np.arange(fire - history, fire + after + 1.0, FLUX_DT)
    Q = np.zeros((ROWS, len(grid_t)))
    for other in entries:
        if not (fire - history - 60 < other["fire_sent"] < fire + after):
            continue
        t0, rows = run_flux(other, depth)
        tt = t0 + np.arange(rows.shape[1]) * FLUX_DT
        for r in range(ROWS):
            Q[r] += np.interp(grid_t, tt, rows[r], left=0.0, right=0.0)
    return {"t": t, "meas": meas, "x": x, "depth": np.array(depth),
            "tq": grid_t - fire, "Qrows": Q}


# ------------------------------------------------------------------ model

def wavenumbers(w, h):
    """Linear dispersion w^2 = g k tanh(k h), solved for every w at once."""
    w = np.asarray(w, float)
    k = np.maximum(w / np.sqrt(linear.G * h), 1e-12)          # shallow-water start
    k = np.maximum(k, w ** 2 / linear.G)                      # deep-water bound
    for _ in range(30):
        th = np.tanh(k * h)
        f = linear.G * k * th - w ** 2
        df = linear.G * th + linear.G * k * h * (1 - th ** 2)
        k = np.maximum(k - f / df, 1e-12)
    return np.where(w > 0, k, 0.0)


NAMES = (["g%d" % (r + 1) for r in range(ROWS)] + ["tau%d" % (r + 1) for r in range(ROWS)]
         + ["L", "Re", "Rh", "tau_h", "log_d0", "log_d1", "log_d2", "h_scale"])


def default_params():
    p = dict.fromkeys(NAMES, 0.0)
    for r in range(ROWS):
        p["g%d" % (r + 1)] = 0.5
    p.update(L=23.4, Re=0.6, Rh=0.5, tau_h=0.0, log_d0=np.log(0.01),
             log_d1=np.log(0.005), log_d2=np.log(0.002), h_scale=1.0)
    return p


def predict(case, p, x=None):
    """Model surface (mm) at the case's stations (or ``x``) on the case's times."""
    # The effective depth for wave speed: the floor under the window may
    # differ from the marked one, so a single scale is fitted for all runs.
    h = float(case["depth"]) * p.get("h_scale", 1.0)
    xs = case["x"] if x is None else np.atleast_1d(x)
    tq, Qr = case["tq"], case["Qrows"]
    t0 = tq[0]
    grid = t0 + np.arange(NFFT) * DT
    q = np.array([np.interp(grid, tq, row, left=0.0, right=0.0) for row in Qr])
    w = 2 * np.pi * np.fft.rfftfreq(NFFT, DT)
    k = wavenumbers(w, h)
    c = np.where(w > 0, w / np.maximum(k, 1e-9), np.sqrt(linear.G * h))
    delta = np.exp(p["log_d0"]) + np.exp(p["log_d1"]) * w + np.exp(p["log_d2"]) * w ** 2
    kc = k - 1j * delta
    QF = np.fft.rfft(q, axis=1)
    src = np.zeros(len(w), complex)
    for r in range(ROWS):
        src += p["g%d" % (r + 1)] * QF[r] * np.exp(-1j * w * p["tau%d" % (r + 1)])
    src /= WIDTH * c
    L, Re, Rh = p["L"], p["Re"], p["Rh"]
    loop = 1.0 - Re * Rh * np.exp(-1j * w * p["tau_h"]) * np.exp(-2j * kc * L)
    out = []
    for xi in xs:
        H = (np.exp(-1j * kc * xi) + Re * np.exp(-1j * kc * (2 * L - xi))) / loop
        H[0] = 0.0
        eta = np.fft.irfft(src * H, n=NFFT)
        out.append(np.interp(case["t"], grid, eta) * 1000.0)
    return np.array(out).T


#: Readings above this (mm over still water) are not trusted in records made
#: before 16 Nov 23:40: the "18 in" tape sits about 51 mm up and the line
#: stuck to its edge.  Later records mask the tapes properly.
TAPE_SAFE_MM = 40.0


def valid(case):
    """Per-reading trust: measured, and not where the line could have stuck."""
    m = case["meas"]
    ok = ~np.isnan(m)
    base = np.nanmean(m[(case["t"] >= -1.0) & (case["t"] < 0)], axis=0)
    with np.errstate(invalid="ignore"):
        ok &= (m - base) < TAPE_SAFE_MM
    return ok


def window(case, after=40.0):
    """From a second before firing until the next test's waves could arrive.

    The model knows only the runs fired up to this one, so the window ends
    4 s after the next run fired (waves need at least 5 s to reach the
    camera), or ``after`` seconds, whichever comes first.
    """
    t = case["t"]
    end = min(after, float(case.get("next_fire", np.inf)) + 4.0)
    ok = (t >= -1.0) & (t <= end)
    return ok


def attach_next_fire(cases, names):
    """Seconds from each run's fire to the next logged run's fire."""
    fires = sorted(json.loads(l)["fire_sent"] for l in RUNS.read_text().splitlines())
    by_name = {json.loads(l)["run"]["name"]: json.loads(l)["fire_sent"]
               for l in RUNS.read_text().splitlines()}
    for c, n in zip(cases, names):
        f = by_name[n]
        later = [x for x in fires if x > f + 0.5]
        c["next_fire"] = (later[0] - f) if later else np.inf


def score(case, p):
    pred = predict(case, p)
    ok = window(case)
    m, e = case["meas"][ok], pred[ok]
    # still level: the measured mean in the second before firing
    base = np.nanmean(case["meas"][(case["t"] >= -1.0) & (case["t"] < 0)], axis=0)
    m = m - base
    return float(np.sqrt(np.mean((e - m) ** 2)) / np.sqrt(np.mean(m ** 2))), \
        float(np.sqrt(np.mean(m ** 2))), float(np.max(np.abs(m)))


def fit(cases, start=None, free=None):
    from scipy.optimize import least_squares
    p0 = dict(start or default_params())
    free = free or [n for n in NAMES if not n.startswith(("g5", "g6", "tau5", "tau6"))
                    or any(np.abs(c["Qrows"][int(n[-1]) - 1]).max() > 0 for c in cases)]
    lo = {"L": 17.0, "Re": -1.0, "Rh": -1.0, "tau_h": -5.0, "h_scale": 0.7}
    hi = {"L": 40.0, "Re": 1.0, "Rh": 1.0, "tau_h": 20.0, "h_scale": 1.4}
    for n in NAMES:
        if n.startswith("tau") and n != "tau_h":
            lo[n], hi[n] = -2.0, 2.0
        if n.startswith("g"):
            lo[n], hi[n] = -5.0, 5.0
        if n.startswith("log_d"):
            lo[n], hi[n] = -12.0, 1.0

    bases, oks = [], []
    for c in cases:
        b = np.nanmean(c["meas"][(c["t"] >= -1.0) & (c["t"] < 0)], axis=0)
        bases.append(b)
        oks.append(window(c)[:, None] & valid(c))

    def resid(v):
        p = dict(p0)
        p.update(zip(free, v))
        out = []
        for c, b, ok in zip(cases, bases, oks):
            m = (c["meas"] - b)[ok]
            scale = np.sqrt(np.mean(m ** 2)) + 1.0
            out.append((predict(c, p)[ok] - m) / scale)
        return np.concatenate(out)

    v0 = [p0[n] for n in free]
    res = least_squares(resid, v0, bounds=([lo.get(n, -np.inf) for n in free],
                                            [hi.get(n, np.inf) for n in free]),
                        x_scale="jac", max_nfev=200)
    p = dict(p0)
    p.update(zip(free, res.x))
    return p


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["prepare", "fit"])
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--depth", type=float, default=0.406)
    a = ap.parse_args()
    entries = usable_runs()
    cases, names, kinds = [], [], []
    for e in entries:
        name = e["run"]["name"]
        try:
            cases.append(load_case(name, a.depth))
            names.append(name)
            kinds.append(e.get("kind", "single"))
        except Exception as ex:  # a run whose window the camera missed
            print("skip %s: %s" % (name, ex))
    print("%d runs" % len(cases))
    if a.action == "prepare":
        return
    full = fit(cases)
    (ROOT / "analytics" / "rig" / "channel_params.json").write_text(json.dumps(full, indent=1))
    print("fitted on all:", {k: round(v, 3) for k, v in full.items()})
    order = np.arange(len(cases))
    rows = []
    for f in range(a.folds):
        held = order[f::a.folds]
        train = [cases[i] for i in order if i not in held]
        p = fit(train, start=full)
        for i in held:
            err, rms, peak = score(cases[i], p)
            rows.append((names[i], kinds[i], err, rms, peak))
    rows.sort(key=lambda r: r[2])
    for n, k, err, rms, peak in rows:
        print("  %-28s %-12s %6.1f%%   wave rms %5.1f mm  peak %5.1f mm" % (n, k, 100 * err, rms, peak))
    errs = np.array([r[2] for r in rows])
    print("held-out: median %.1f%%  mean %.1f%%  worst %.1f%%" % (100 * np.median(errs),
                                                                  100 * errs.mean(), 100 * errs.max()))
    for k in sorted(set(kinds)):
        e = [r[2] for r in rows if r[1] == k]
        print("  %-12s n=%2d  median %.1f%%" % (k, len(e), 100 * np.median(e)))
    json.dump({"rows": rows, "params": full}, open(ROOT / "analytics" / "rig" / "channel_eval.json", "w"),
              indent=1, default=float)
    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        worst = rows[-4:]
        fig, ax = plt.subplots(len(worst), 1, figsize=(13, 3 * len(worst)))
        for axi, (n, k, err, _, _) in zip(np.atleast_1d(ax), worst):
            c = cases[names.index(n)]
            pr = predict(c, full)
            base = np.nanmean(c["meas"][(c["t"] >= -1) & (c["t"] < 0)], axis=0)
            axi.plot(c["t"], c["meas"][:, 2] - base[2], "k", lw=1, label="measured")
            axi.plot(c["t"], pr[:, 2], "r", lw=1, label="channel model")
            axi.set_xlim(-8, 40)
            axi.set_title("%s (%s): held-out error %.0f%%" % (n, k, 100 * err))
            axi.legend()
        fig.tight_layout()
        fig.savefig(ROOT / "analytics" / "rig" / "channel_worst.png", dpi=90)


if __name__ == "__main__":
    main()
