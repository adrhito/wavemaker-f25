"""How well does the tank model predict the camera's waves?  Cross-validated.

For every usable logged run (``analytics/rig/runs.jsonl``) the physics model
(``tools/camera/vision_physics.py``: piston motion -> air model -> water flux ->
``tank.transfer``) is synthesised once per row of pistons, so that each
row's contribution to the surface at the camera's five stations is known
separately.  Two models are then scored by K-fold cross-validation over runs:

* **plain**: the physics prediction times one global gain, fitted on the
  training runs;
* **corrected**: each row's flux multiplied by its own gain ``g_n`` and
  delayed by its own shift ``tau_n`` (|tau| <= 1.5 s), shared by all runs
  and fitted on the training runs.

Each held-out run is scored over the window from 1 s before firing to 40 s
after it (or the next run's start, if sooner), over all five stations:
error = RMS(pred - meas) / RMS(meas).

Row n is display pistons 3n-2..3n (row 1 at the head end).  The per-row
predictions are cached in ``analytics/rig/eval_cache/`` so that reruns only
synthesise new runs; ``--refresh`` recomputes them.  Nothing here contacts
the PLC.

    python tools/camera/vision_eval.py [--folds 5] [--refresh]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "camera"))
RIGDIR = ROOT / "analytics" / "rig"
CACHE = RIGDIR / "eval_cache"
#: Records before this local time have bad camera data.
FIRST_GOOD = "2026-11-16 20:25"
DEFAULT_LENGTH = 23.37
DEFAULT_DEPTH = 0.406   # the calibration's depth, so its transfer functions are reused
BEFORE, AFTER = 10.0, 60.0   # the window ``prepared`` loads (as the calibration does)
WIN_BEFORE, WIN_AFTER = 1.0, 40.0
MAX_SHIFT = 1.5
N_ROWS = 10
PER_ROW = 3
FIT_ROWS = range(1, 7)  # rows 1-6; the others are dry at this depth
MIDDLE = 2


# --------------------------------------------------------------------------
# Fitting (pure numpy; a "run" here is a dict with
#   tm   (n,)      model time, s from the run's firing
#   rows (R, S, n) each row's predicted surface, mm
#   t    (m,)      record time of the scored samples, s from firing
#   meas (m, S)    measured surface there, mm (no NaN)
# --------------------------------------------------------------------------

def shifted(run, r, tau):
    """Row ``r`` (0-based) delayed by ``tau`` s, at the scored samples: (m, S)."""
    tq = run["t"] - tau
    return np.stack([np.interp(tq, run["tm"], run["rows"][r, s], left=0.0, right=0.0)
                     for s in range(run["rows"].shape[1])], axis=1)


def predict(run, gains, taus):
    """The corrected prediction (m, S) for per-row gains and shifts."""
    out = np.zeros_like(run["meas"], dtype=float)
    for r in range(run["rows"].shape[0]):
        if gains[r] != 0.0 and np.any(run["rows"][r]):
            out += gains[r] * shifted(run, r, taus[r])
    return out


def plain(run):
    """The plain physics prediction (all rows, no shift): (m, S)."""
    return predict(run, np.ones(run["rows"].shape[0]), np.zeros(run["rows"].shape[0]))


def rel_err(pred, meas):
    return float(np.sqrt(np.mean((pred - meas) ** 2)) / np.sqrt(np.mean(meas ** 2)))


def fit_global_gain(runs):
    num = den = 0.0
    for run in runs:
        p = plain(run)
        num += float(np.sum(p * run["meas"]))
        den += float(np.sum(p * p))
    return num / den if den > 0 else 1.0


def _column(runs, r, tau):
    return np.concatenate([shifted(run, r, tau).ravel() for run in runs])


def fit_rows(runs, rows=None, max_shift=MAX_SHIFT, step=0.04, passes=3, fine=0.005):
    """Per-row gains and shifts, least squares over ``runs``.

    Coordinate descent over the shifts (a grid of ``step``, then refined to
    ``fine``); for every candidate the gains are solved exactly (linear
    least squares).  Rows with no motion in ``runs`` keep gain 1, shift 0.
    """
    R = runs[0]["rows"].shape[0]
    gains, taus = np.ones(R), np.zeros(R)
    rows = list(range(R)) if rows is None else list(rows)
    active = [r for r in rows if any(np.any(run["rows"][r]) for run in runs)]
    y = np.concatenate([run["meas"].ravel() for run in runs]).astype(float)
    # Rows not fitted stay as they are (gain 1, no shift): take them off the target.
    for r in range(R):
        if r not in active and any(np.any(run["rows"][r]) for run in runs):
            y = y - _column(runs, r, 0.0)
    if not active:
        return gains, taus
    cols = {r: _column(runs, r, 0.0) for r in active}

    def solve(cmap):
        X = np.stack([cmap[r] for r in active], axis=1)
        g, *_ = np.linalg.lstsq(X, y, rcond=None)
        res = y - X @ g
        return g, float(res @ res)

    g, best = solve(cols)
    grid = np.arange(-max_shift, max_shift + 1e-9, step)

    def sweep(r, cands):
        nonlocal g, best, cols
        changed = False
        for tau in cands:
            if abs(tau) > max_shift + 1e-9 or tau == taus[r]:
                continue
            trial = dict(cols)
            trial[r] = _column(runs, r, tau)
            gt, sse = solve(trial)
            if sse < best - 1e-12:
                best, g, cols, changed = sse, gt, trial, True
                taus[r] = tau
        return changed

    for _ in range(passes):
        if not any([sweep(r, grid) for r in active]):
            break
    for r in active:
        sweep(r, np.arange(taus[r] - step, taus[r] + step + 1e-9, fine))
    for k, r in enumerate(active):
        gains[r] = g[k]
    return gains, taus


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def kind_of(entry):
    k = entry.get("kind")
    if k in ("single", "continuous", "soliton"):
        return k
    if k in ("soliton_down", "pulse_down"):
        return "pulse_down"
    if k is None and entry.get("run", {}).get("groups"):
        return "single"
    return "other"


def first_good_epoch():
    return time.mktime(time.strptime(FIRST_GOOD, "%Y-%m-%d %H:%M"))


def candidate_runs():
    """Usable run entries, in time order, and each one's window end (s after firing)."""
    entries = [json.loads(l) for l in (RIGDIR / "runs.jsonl").read_text().splitlines() if l.strip()]
    ok = [e for e in entries if e.get("reply", {}).get("ok")]
    ok.sort(key=lambda e: e["fire_sent"])
    t0 = first_good_epoch()
    out, skipped = [], []
    for i, e in enumerate(ok):
        name = e["run"]["name"]
        if e["fire_sent"] < t0:
            continue
        if not e.get("reply", {}).get("data", {}).get("samples"):
            skipped.append((name, "no position samples"))
            continue
        end = WIN_AFTER
        for later in ok[i + 1:]:
            start = min(later.get("watch_started", later["fire_sent"]), later["fire_sent"])
            if start > e["fire_sent"]:
                end = min(end, start - e["fire_sent"])
                break
        out.append((e, end))
    return out, skipped


def load_settings():
    """(channel_length, channel_damping, head_absorption, source)."""
    cal = RIGDIR / "calibration.json"
    if cal.exists():
        try:
            b = json.loads(cal.read_text())["best"]
            return (b["channel_length"], b["channel_damping"], b["head_absorption"],
                    "calibration.json")
        except Exception as exc:  # noqa: BLE001
            print("calibration.json unreadable (%s)" % exc)
    log = RIGDIR / "calibration.log"
    if log.exists():
        rows = []
        for line in log.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        rows = [r for r in rows if r.get("train_mean") is not None]
        if rows:
            b = min(rows, key=lambda r: r["train_mean"])
            return (b["channel_length"], b["channel_damping"], b["head_absorption"],
                    "calibration.log")
    from vision_physics import tank
    hp = tank.HydroParams()
    return DEFAULT_LENGTH, hp.channel_damping, hp.head_absorption, "defaults"


def synth_rows(prep, hp, channel_length):
    """Each row's predicted surface on the model grid: (time from firing, (R, S, n) mm).

    The same arithmetic as ``vision_physics.synth``, split by row.
    """
    import dataclasses
    from vision_physics import tank, tm_predict, load_geometry
    g = dataclasses.replace(load_geometry(), channel_length=float(channel_length))
    n, dt = prep["n"], prep["dt"]
    t = np.arange(n) * dt
    sigma = tm_predict.SIGMA_DECADES / tm_predict.WINDOW
    freqs = np.fft.rfftfreq(n, dt)
    tfn = tank.transfer(prep["depth"], list(prep["x"]), freqs, g, hp, sigma=sigma)
    QF = np.fft.rfft(prep["Q"] * np.exp(-sigma * t), axis=1)
    per = g.rows  # pistons in a row of the bank
    R = prep["Q"].shape[0] // per
    rows = np.zeros((R, len(prep["x"]), n))
    for r in range(R):
        sl = slice(r * per, (r + 1) * per)
        if not np.any(prep["Q"][sl]):
            continue
        EF = np.einsum("fps,pf->sf", np.conj(tfn.H[:, sl, :]), QF[sl])
        rows[r] = np.fft.irfft(EF, n=n, axis=1) * np.exp(sigma * t) * hp.gain * 1000.0
    tm = t - prep["lead"] - prep["fire_rec"]
    return tm, rows


def settings_key(L, d, h, depth):
    return "L%.3f_d%.3f_h%.4f_z%.3f" % (L, d, h, depth)


def load_run(entry, end, L, d, h, depth, refresh=False, scratch=None):
    """A run's cached or freshly synthesised per-row prediction and measurement."""
    from vision_physics import prepared, tank
    name = entry["run"]["name"]
    key = settings_key(L, d, h, depth)
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / ("%s_%d_%s.npz" % (name, int(entry["fire_sent"]), key))
    z = None
    if path.exists() and not refresh:
        z = dict(np.load(path, allow_pickle=False))
        # A record still being written when cached: redo once more has arrived.
        if float(z["t_rec"].max()) < min(end, AFTER) - 0.5 and time.time() - entry["fire_sent"] > AFTER + 30:
            z = None
    if z is None:
        prep = prepared(name, depth, before=BEFORE, after=AFTER, one_run_log=scratch)
        hp = tank.HydroParams(channel_damping=float(d), head_absorption=float(h))
        tm, rows = synth_rows(prep, hp, L)
        z = {"tm": tm, "rows": rows.astype(np.float32),
             "t_rec": prep["t_rec"] - prep["fire_rec"], "meas": prep["meas"],
             "used": np.array(prep["used"]), "key": np.array(key)}
        np.savez_compressed(path, **z)
    t_rec, meas = z["t_rec"], z["meas"]
    win = (t_rec >= -WIN_BEFORE) & (t_rec <= end)
    ok = win & ~np.any(np.isnan(meas), axis=1)
    if ok.sum() < 10.0 / max(np.median(np.diff(t_rec)), 1e-3):
        raise ValueError("fewer than 10 s of measured surface in the window")
    return {"name": name, "kind": kind_of(entry), "fire": entry["fire_sent"], "end": end,
            "tm": z["tm"].astype(float), "rows": z["rows"].astype(float),
            "t": t_rec[ok], "meas": meas[ok],
            "t_win": t_rec[win], "meas_win": meas[win]}


# --------------------------------------------------------------------------

def crossval(runs, folds, fit_rows_idx):
    k = min(folds, len(runs))
    fold_of = [i % k for i in range(len(runs))]
    res = [None] * len(runs)
    fits = []
    for f in range(k):
        train = [r for r, fo in zip(runs, fold_of) if fo != f]
        test = [i for i, fo in enumerate(fold_of) if fo == f]
        G = fit_global_gain(train)
        gains, taus = fit_rows(train, rows=fit_rows_idx)
        fits.append({"fold": f, "global_gain": G, "gains": gains.tolist(), "taus": taus.tolist(),
                     "test": [runs[i]["name"] for i in test]})
        for i in test:
            run = runs[i]
            pp = plain(run) * G
            pc = predict(run, gains, taus)
            res[i] = {"fold": f, "plain": rel_err(pp, run["meas"]), "corrected": rel_err(pc, run["meas"]),
                      "global_gain": G, "gains": gains, "taus": taus}
    return res, fits


def summarise(runs, res):
    out = {}
    for kind in ("single", "continuous", "pulse_down", "soliton", "other", "all"):
        idx = [i for i, r in enumerate(runs) if kind == "all" or r["kind"] == kind]
        if not idx:
            continue
        p = np.array([res[i]["plain"] for i in idx])
        c = np.array([res[i]["corrected"] for i in idx])
        out[kind] = {"n": len(idx), "plain_median": float(np.median(p)), "plain_mean": float(np.mean(p)),
                     "corrected_median": float(np.median(c)), "corrected_mean": float(np.mean(c))}
    return out


def plot_worst(runs, res, path, n=4):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    order = sorted(range(len(runs)), key=lambda i: -res[i]["corrected"])[:n]
    fig, axes = plt.subplots(len(order), 1, figsize=(12, 2.6 * len(order)), squeeze=False)
    for ax, i in zip(axes[:, 0], order):
        run, r = runs[i], res[i]
        full = dict(run, t=run["t_win"], meas=np.nan_to_num(run["meas_win"]))
        pp = plain(full)[:, MIDDLE] * r["global_gain"]
        pc = predict(full, r["gains"], r["taus"])[:, MIDDLE]
        ax.plot(run["t_win"], run["meas_win"][:, MIDDLE], color="#222222", lw=1.2, label="measured")
        ax.plot(run["t_win"], pp, color="#8a8a8a", lw=1, ls="--", label="physics x global gain")
        ax.plot(run["t_win"], pc, color="#c0392b", lw=1, label="corrected (row gains + shifts)")
        ax.set_title("%s (%s): error plain %.0f%%, corrected %.0f%%"
                     % (run["name"], run["kind"], 100 * r["plain"], 100 * r["corrected"]),
                     fontsize=10, loc="left")
        ax.set_ylabel("mm, middle station")
        ax.grid(alpha=0.3)
    axes[0, 0].legend(loc="upper right", fontsize=8, frameon=False)
    axes[-1, 0].set_xlabel("s from firing")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--refresh", action="store_true", help="recompute the cached per-row predictions")
    ap.add_argument("--depth", type=float, default=DEFAULT_DEPTH, help="still water, m")
    ap.add_argument("--out", default=str(RIGDIR / "eval.json"))
    ap.add_argument("--plot", default=str(RIGDIR / "eval_worst.png"))
    a = ap.parse_args()

    L, d, h, src = load_settings()
    print("tank settings (%s): channel_length %.2f m, damping %g, head absorption %g, depth %.3f m"
          % (src, L, d, h, a.depth))
    cands, skipped = candidate_runs()
    runs = []
    scratch = Path(tempfile.gettempdir()) / "vision_eval_one_run.jsonl"
    for entry, end in cands:
        name = entry["run"]["name"]
        try:
            t0 = time.time()
            runs.append(load_run(entry, end, L, d, h, a.depth, a.refresh, scratch))
            print("  loaded %-28s %-11s window -1..%.0f s  (%.0f s)"
                  % (name, runs[-1]["kind"], end, time.time() - t0), flush=True)
        except Exception as exc:  # noqa: BLE001
            skipped.append((name, "%s: %s" % (type(exc).__name__, exc)))
            print("  skipped %s: %s" % (name, exc), flush=True)
    if len(runs) < 2:
        raise SystemExit("need at least two usable runs; have %d" % len(runs))

    fit_idx = [r - 1 for r in FIT_ROWS]
    res, fits = crossval(runs, a.folds, fit_idx)
    g_all, tau_all = fit_rows(runs, rows=fit_idx)
    G_all = fit_global_gain(runs)
    summary = summarise(runs, res)

    print()
    print("%-28s %-11s %8s %8s %9s %9s" % ("run", "kind", "rms mm", "peak mm", "plain %", "corr %"))
    rows_out = []
    for run, r in zip(runs, res):
        rms = float(np.sqrt(np.mean(run["meas"] ** 2)))
        peak = float(np.max(np.abs(run["meas"])))
        print("%-28s %-11s %8.1f %8.1f %9.0f %9.0f"
              % (run["name"], run["kind"], rms, peak, 100 * r["plain"], 100 * r["corrected"]))
        rows_out.append({"name": run["name"], "kind": run["kind"], "fold": r["fold"],
                         "window_s": [-WIN_BEFORE, run["end"]], "wave_rms_mm": rms, "peak_mm": peak,
                         "error_plain": r["plain"], "error_corrected": r["corrected"]})
    print()
    print("%-11s %3s %13s %12s %13s %12s" % ("kind", "n", "plain median", "plain mean",
                                          "corr median", "corr mean"))
    for kind, s in summary.items():
        print("%-11s %3d %12.0f%% %11.0f%% %12.0f%% %11.0f%%"
              % (kind, s["n"], 100 * s["plain_median"], 100 * s["plain_mean"],
                 100 * s["corrected_median"], 100 * s["corrected_mean"]))
    print()
    print("fitted on all runs: global gain %.3f" % G_all)
    for r in FIT_ROWS:
        print("  row %d (pistons %d-%d): gain %6.3f  shift %+.3f s"
              % (r, 3 * r - 2, 3 * r, g_all[r - 1], tau_all[r - 1]))
    if skipped:
        print("\nskipped:")
        for name, why in skipped:
            print("  %s: %s" % (name, why))

    plot_worst(runs, res, a.plot)
    Path(a.out).write_text(json.dumps({
        "settings": {"channel_length": L, "channel_damping": d, "head_absorption": h,
                     "depth": a.depth, "source": src},
        "window": {"before_s": WIN_BEFORE, "after_s": WIN_AFTER, "stations": 5},
        "folds": len(fits), "fold_fits": fits,
        "fit_all": {"global_gain": G_all,
                    "rows": [{"row": r, "pistons": [3 * r - 2, 3 * r], "gain": float(g_all[r - 1]),
                              "shift_s": float(tau_all[r - 1])} for r in FIT_ROWS]},
        "runs": rows_out, "summary": summary,
        "skipped": [{"name": n, "why": w} for n, w in skipped],
        "written": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=1))
    print("\nwrote %s and %s" % (a.out, a.plot))


if __name__ == "__main__":
    main()
