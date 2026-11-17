"""From probe recordings to a model of the waves the pistons make.

A probe (``tools/vision_probe.py``) leaves a lossless clip of the waterline
band, the arrival time of every picture, the settings of every piston, and
the piston positions ``wavectl`` sampled.  This module turns those into:

- ``surface``: the water's height at each station along the glass over time,
  in millimetres above the still water (``glassline``);
- ``motion``: each piston's upward displacement over time, rebuilt as the
  drive's trapezoidal move with each leg's start fitted to the samples, since
  the samples come only 14 times a second when 29 pistons are read;
- a linear model, ``eta_s(t) = sum_i (h_{i,s} * v_i)(t)``, fitted by
  regularised least squares, where ``v_i`` is piston i's upward speed and
  ``h_{i,s}`` its response at station s.  Pistons can share kernels by group
  (``groups``), which is how the physics is tested: one kernel for all, one
  per column, one per piston.

Nothing here contacts the machine.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import cv2
import numpy as np

from vision import glassline

#: Millimetres per pixel at the glass, from the five tapes 6 in apart
#: (154.80 px per pitch, residuals under 1 px; measured 16 Nov 2026).
MM_PER_PX = 0.9845
#: The searched band and column grouping inside the recorded crop.
BAND = (40, 300)
BIN_PX = 10
REST_MM = 350.0
#: Analysis grid.
DT = 0.1
T_BEFORE = 5.0


@dataclass
class Probe:
    stem: Path
    run: dict
    t: np.ndarray                 # analysis grid, s from the fire command
    eta: np.ndarray               # grid x stations, mm
    stations_px: np.ndarray       # column centre of each station, full picture
    disp: Dict[int, np.ndarray]   # piston -> upward displacement on the grid, mm
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------- surface

def _read_gray(path: Path) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    capture.release()
    return np.stack(frames)


def _hampel(series: np.ndarray, half: int = 3, k: float = 4.0) -> np.ndarray:
    """Replace lone spikes in time (per station) by the local median."""
    out = series.copy()
    n = len(series)
    pad = np.pad(series, ((half, half), (0, 0)), mode="edge")
    windows = np.stack([pad[i:i + n] for i in range(2 * half + 1)])
    med = np.nanmedian(windows, axis=0)
    mad = np.nanmedian(np.abs(windows - med), axis=0) * 1.4826
    bad = np.abs(series - med) > k * np.maximum(mad, 0.15)
    bad |= np.isnan(series)
    out[bad] = med[bad]
    return out


def surface(stem: Path, fire: float, frame_times: Sequence[float],
            crop: Sequence[int]) -> tuple:
    """Heights (frames x stations, mm), frame times from fire, station px."""
    cache = stem.with_suffix(".surface.npz")
    if cache.exists():
        z = np.load(cache)
        return z["eta"], z["t"], z["px"]
    gray = _read_gray(stem.with_suffix(".mkv"))
    rows = glassline.edges(gray, BAND[0], BAND[1], BIN_PX, track=12)
    t = np.asarray(frame_times[:len(rows)]) - fire
    rows = rows[:len(t)]
    still = np.nanmedian(rows[t < -0.5], axis=0)
    eta = (still - rows) * MM_PER_PX
    # Stations that lose the edge often (frame edges, posts) are dropped.
    keep = np.mean(np.isnan(eta), axis=0) < 0.05
    eta = _hampel(eta[:, keep])
    px = crop[0] + (np.arange(rows.shape[1]) + 0.5) * BIN_PX
    px = px[keep]
    np.savez_compressed(cache, eta=eta, t=t, px=px)
    return eta, t, px


# ---------------------------------------------------------------- motion

def _read_samples(path: Path) -> tuple:
    began = None
    with open(path) as handle:
        lines = handle.readlines()
    for line in lines:
        if line.startswith("# began_utc"):
            began = datetime.fromisoformat(line.split()[2]).timestamp()
    body = [l for l in lines if not l.startswith("#")]
    reader = csv.reader(body)
    header = next(reader)
    data = np.array([[float(v) for v in row] for row in reader])
    pistons = [int(h[1:]) for h in header[1:]]
    return began + data[:, 0], pistons, data[:, 1:]


def trapezoid(t: np.ndarray, start: float, length: float, speed: float,
              accel: float, decel: float) -> np.ndarray:
    """Distance covered by one move at times t (0 before, length after)."""
    t = np.asarray(t, dtype=float) - start
    length = abs(length)
    ta, td = speed / accel, speed / decel
    da, dd = 0.5 * speed * ta, 0.5 * speed * td
    if da + dd > length:  # never reaches speed: triangular profile
        peak = np.sqrt(2 * length * accel * decel / (accel + decel))
        ta, td = peak / accel, peak / decel
        da, dd = 0.5 * peak * ta, 0.5 * peak * td
        speed, cruise = peak, 0.0
    else:
        cruise = (length - da - dd) / speed
    out = np.zeros_like(t)
    a = (t > 0) & (t <= ta)
    out[a] = 0.5 * accel * t[a] ** 2
    c = (t > ta) & (t <= ta + cruise)
    out[c] = da + speed * (t[c] - ta)
    e = (t > ta + cruise) & (t <= ta + cruise + td)
    u = t[e] - ta - cruise
    out[e] = da + speed * cruise + speed * u - 0.5 * decel * u ** 2
    out[t > ta + cruise + td] = length
    return out


def scurve(t: np.ndarray, start: float, length: float, speed: float,
           accel: float, smooth: float) -> np.ndarray:
    """A trapezoidal move rounded by a box filter ``smooth`` seconds long.

    The drives limit jerk well below their acceleration setting: on 16 Nov
    2026 a 150 mm leg at 300 mm/s took about 0.2 s to reach speed although
    20,000 mm/s^2 was set.  A trapezoid at an effective acceleration,
    averaged over a short window, follows the samples to a fraction of a
    millimetre.
    """
    if smooth <= 1e-4:
        return trapezoid(t, start, length, speed, accel, accel)
    offsets = np.linspace(-smooth / 2, smooth / 2, 9)
    return np.mean([trapezoid(t + o, start, length, speed, accel, accel)
                    for o in offsets], axis=0)


def _stroke(t, legs, length, speed, accel, smooth):
    return (scurve(t, legs[0], length, speed, accel, smooth)
            - scurve(t, legs[1], length, speed, accel, smooth))


def _fit_legs(ts: np.ndarray, pos: np.ndarray, top: float, speed: float,
              accel: float, decel: float) -> Optional[tuple]:
    """Up-leg start, down-leg start, effective acceleration and smoothing."""
    from scipy.optimize import least_squares

    lift = REST_MM - pos
    length = REST_MM - top
    if length <= 0 or np.nanmax(lift) < 0.3 * length:
        return None
    up_i = np.flatnonzero(lift > 0.05 * length)
    t_up = ts[up_i[0]] - 0.05
    hi_i = np.flatnonzero(lift > 0.9 * length)
    t_dn = ts[hi_i[-1]] - 0.05 if len(hi_i) else t_up + length / speed

    def resid(x):
        return _stroke(ts, x[:2], length, speed, x[2], x[3]) - lift

    best = None
    for a0 in (1500.0, 4000.0):
        x0 = [t_up, t_dn, a0, 0.05]
        fit = least_squares(resid, x0, bounds=([t_up - 0.3, t_dn - 0.3, 200, 0],
                                               [t_up + 0.3, t_dn + 0.3, 50000, 0.4]))
        if best is None or fit.cost < best.cost:
            best = fit
    return tuple(best.x)


def motion(record: dict, grid: np.ndarray, fire: float) -> tuple:
    """Upward displacement of every moved piston on the grid, and fit notes."""
    return motion_from(record["replies"][-1], record["run"], grid, fire)


def legs_from_samples(ts: np.ndarray, pos: np.ndarray, still: float = 0.4,
                      prior_accel: float = 2000.0, prior_smooth: float = 0.3) -> list:
    """Every move one piston made, found and fitted from its samples.

    The pistons do not always make only the move planned: on 16 Nov 2026 a
    single stroke first went to the previous run's top and back.  So the
    samples are taken as the truth.  They are split into plateaus (still to
    ``still`` mm between samples) and the legs between them, and each leg is
    fitted with :func:`scurve` -- start, speed, effective acceleration and
    smoothing -- gently held towards the values typical of these drives,
    since a fast leg may have only three or four samples.
    """
    from scipy.optimize import least_squares

    lift = REST_MM - np.asarray(pos, dtype=float)
    n = len(lift)
    moving = np.zeros(n, bool)
    moving[1:] |= np.abs(np.diff(lift)) > still
    moving[:-1] |= np.abs(np.diff(lift)) > still
    # Moving stretches, each split where the piston reverses: in a
    # continuous run it turns round without ever standing still.
    spans = []
    k = 0
    while k < n:
        if not moving[k]:
            k += 1
            continue
        j = k
        while j < n and moving[j]:
            j += 1
        a_i, b_i = max(k - 1, 0), min(j, n - 1)
        step = np.sign(np.diff(lift[a_i:b_i + 1]))
        step[np.abs(np.diff(lift[a_i:b_i + 1])) <= still * 0.25] = 0
        cuts = [a_i]
        last = 0
        for i, sgn in enumerate(step):
            if sgn == 0:
                continue
            if last and sgn != last:
                cuts.append(a_i + i)  # the extreme sample, where it turned
            last = sgn
        cuts.append(b_i)
        spans.extend(zip(cuts[:-1], cuts[1:]))
        k = j
    legs = []
    for a_i, b_i in spans:
        level_a, level_b = lift[a_i], lift[b_i]
        length = level_b - level_a
        if abs(length) < 1.0 or b_i - a_i < 1:
            continue
        t_win, y_win = ts[a_i:b_i + 1], lift[a_i:b_i + 1]
        span = max(t_win[-1] - t_win[0], 1e-3)
        sign = np.sign(length)

        def resid(x):
            model = level_a + sign * scurve(t_win, x[0], abs(length), x[1], x[2], x[3])
            prior = [0.3 * np.log(x[2] / prior_accel), 0.3 * (x[3] - prior_smooth) / 0.3]
            return np.concatenate([model - y_win, np.array(prior) * max(abs(length), 1) * 0.05])

        v0 = abs(length) / span
        x0 = [t_win[0], max(v0 * 1.5, 5.0), prior_accel, prior_smooth]
        fit = least_squares(resid, x0, bounds=([t_win[0] - 0.3, 2.0, 10.0, 0.0],
                                               [t_win[-1], 1500.0, 50000.0, 1.5]))
        legs.append({"from": float(level_a), "to": float(level_b),
                     "start": float(fit.x[0]), "speed": float(fit.x[1]),
                     "accel": float(fit.x[2]), "smooth": float(fit.x[3])})
    return legs


def lift_series(t: np.ndarray, legs: list, start_level: float) -> np.ndarray:
    """Upward displacement on ``t`` from the fitted legs."""
    out = np.full(len(t), float(start_level))
    for leg in legs:
        out += np.sign(leg["to"] - leg["from"]) * scurve(
            t, leg["start"], abs(leg["to"] - leg["from"]), leg["speed"],
            leg["accel"], leg["smooth"])
    return out


def motion_from(reply: dict, run: dict, grid: np.ndarray, fire: float) -> tuple:
    """Upward displacement of every piston that moved, on the grid.

    ``grid`` and the samples are compared in the samples' clock minus
    ``fire``.  Displacement is counted from rest (``REST_MM``), so a piston
    that ends where it began contributes nothing outside its moves.
    """
    path = Path(reply["data"]["samples"]["file"])
    times, pistons, pos = _read_samples(path)
    t = times - fire
    disp, notes = {}, {}
    for col, p in enumerate(pistons):
        legs = legs_from_samples(t, pos[:, col])
        if not legs:
            continue
        start = REST_MM - pos[0, col]
        series = lift_series(grid, legs, start) - start
        model = lift_series(t, legs, start)
        resid = (REST_MM - pos[:, col]) - model
        disp[p] = series
        notes[p] = {"legs": legs, "rms_mm": float(np.sqrt(np.mean(resid ** 2)))}
    return disp, notes


def _expand(text: str) -> List[int]:
    out = []
    for piece in str(text).split(","):
        if "-" in piece:
            a, b = piece.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif piece:
            out.append(int(piece))
    return out


# ---------------------------------------------------------------- loading

def load(stem, span: float = 50.0) -> Probe:
    stem = Path(stem)
    record = json.loads(stem.with_suffix(".json").read_text())
    fire = record["fire_sent"]
    eta_f, t_f, px = surface(stem, fire, record["frame_times"],
                             record["camera"]["crop"])
    grid = np.arange(-T_BEFORE, span, DT)
    # Average the pictures falling in each grid step (box filter, no aliasing).
    eta = np.full((len(grid), eta_f.shape[1]), np.nan)
    idx = np.floor((t_f + T_BEFORE) / DT).astype(int)
    for k in range(len(grid)):
        sel = idx == k
        if np.any(sel):
            eta[k] = eta_f[sel].mean(axis=0)
    # Box averaging delays by half a step; shift the grid to the bin centres.
    disp, notes = motion(record, grid + DT / 2, fire)
    return Probe(stem, record["run"], grid + DT / 2, eta, px, disp,
                 {"motion": notes})


# ---------------------------------------------------------------- the model

def velocity(d: np.ndarray) -> np.ndarray:
    """Displacement per grid step: the kernel input (mm per step)."""
    v = np.zeros_like(d)
    v[1:] = np.diff(d)
    return v


def _lagged(u: np.ndarray, taps: int) -> np.ndarray:
    """Toeplitz design: column k is u delayed by k steps."""
    n = len(u)
    out = np.zeros((n, taps))
    for k in range(taps):
        out[k:, k] = u[:n - k]
    return out


def inputs(probe: Probe, groups: Dict[str, Sequence[int]],
           gain: Optional[Dict[int, float]] = None) -> Dict[str, np.ndarray]:
    """Summed piston speed per kernel group for one probe."""
    out = {}
    for name, members in groups.items():
        u = np.zeros(len(probe.t))
        for p in members:
            if p in probe.disp:
                u += velocity(probe.disp[p]) * (gain or {}).get(p, 1.0)
        out[name] = u
    return out


@dataclass
class Model:
    groups: Dict[str, List[int]]
    taps: int
    kernels: np.ndarray  # groups x taps x stations
    stations_px: np.ndarray

    def predict(self, probe: Probe) -> np.ndarray:
        u = inputs(probe, self.groups)
        out = np.zeros((len(probe.t), self.kernels.shape[2]))
        for j, name in enumerate(self.groups):
            out += _lagged(u[name], self.taps) @ self.kernels[j]
        return out


def _common_stations(probes: Sequence[Probe]) -> np.ndarray:
    common = probes[0].stations_px
    for p in probes[1:]:
        common = np.intersect1d(common, p.stations_px)
    return common


def _station_cols(probe: Probe, px: np.ndarray) -> np.ndarray:
    return np.searchsorted(probe.stations_px, px)


def fit(probes: Sequence[Probe], groups: Dict[str, List[int]],
        taps: int = 450, ridge: float = 1.0, smooth: float = 10.0,
        stations_px: Optional[np.ndarray] = None,
        masks: Optional[Sequence[np.ndarray]] = None) -> Model:
    """Least-squares kernels for every station at once.

    ``ridge`` and ``smooth`` penalise the kernels' size and their second
    difference, which keeps the long tails from fitting noise.  ``masks``
    choose which grid steps of each probe are fitted (held-out steps are
    left out of the fit but their inputs still drive later steps).  Each
    probe gets its own constant offset per station, for the still level.
    """
    px = _common_stations(probes) if stations_px is None else stations_px
    names = list(groups)
    n = len(names) * taps
    m = len(probes)
    XtX = np.zeros((n + m, n + m))
    XtY = np.zeros((n + m, len(px)))
    for j, probe in enumerate(probes):
        u = inputs(probe, groups)
        X = np.hstack([_lagged(u[k], taps) for k in names]
                      + [np.zeros((len(probe.t), m))])
        X[:, n + j] = 1.0
        Y = probe.eta[:, _station_cols(probe, px)]
        ok = ~np.any(np.isnan(Y), axis=1)
        if masks is not None:
            ok &= masks[j]
        XtX += X[ok].T @ X[ok]
        XtY += X[ok].T @ Y[ok]
    D = np.zeros((taps - 2, taps))
    for k in range(taps - 2):
        D[k, k:k + 3] = (1, -2, 1)
    P = np.kron(np.eye(len(names)), D.T @ D) * smooth + np.eye(n) * ridge
    scale = np.mean(np.diag(XtX)[:n]) + 1e-12
    A = XtX.copy()
    A[:n, :n] += P * scale * 1e-4
    W = np.linalg.solve(A + np.eye(n + m) * 1e-9, XtY)
    kernels = W[:n].reshape(len(names), taps, -1)
    model = Model({k: list(v) for k, v in groups.items()}, taps, kernels, px)
    model.offsets = {id(p): W[n + j] for j, p in enumerate(probes)}
    return model


def score(model: Model, probe: Probe, t0: float = 0.0,
          t1: Optional[float] = None, mask: Optional[np.ndarray] = None,
          offset: Optional[np.ndarray] = None) -> dict:
    """Relative RMS error of the prediction over the stations and window.

    The still level is taken from the fit when the probe was part of it, or
    else from ``offset``, or else from the probe's own steps before firing.
    """
    pred = model.predict(probe)
    Y = probe.eta[:, _station_cols(probe, model.stations_px)]
    if mask is None:
        mask = (probe.t >= t0) & (probe.t <= (t1 if t1 is not None else probe.t[-1]))
    mask = mask & ~np.any(np.isnan(Y), axis=1)
    if offset is None:
        offset = getattr(model, "offsets", {}).get(id(probe))
    if offset is None:
        offset = np.nanmedian(Y[probe.t < 0] - pred[probe.t < 0], axis=0)
    pred = pred + offset
    err = pred[mask] - Y[mask]
    centred = Y[mask] - offset
    rms = float(np.sqrt(np.mean(centred ** 2)))
    return {"rel_rms": float(np.sqrt(np.mean(err ** 2))) / max(rms, 1e-9),
            "rms_mm": rms, "err_mm": float(np.sqrt(np.mean(err ** 2))),
            "peak_mm": float(np.max(np.abs(centred)))}


def baseline_at(folder, when: float) -> Optional[dict]:
    """The still-water baseline the operator set last before ``when``.

    Baselines are marked in ``tools/vision_rig.py`` and kept, with their
    times and depths, in ``annotations.json``.  The water level changes
    over days, so each record is measured from the baseline in force then.
    """
    path = Path(folder) / "annotations.json"
    if not path.exists():
        return None
    marks = json.loads(path.read_text()).get("baselines", [])
    before = [b for b in marks if b["at"] <= when]
    if before:
        return before[-1]
    return marks[0] if marks else None


def load_rig(folder, runs_log=None, t_start=None, t_end=None,
             bin_px: int = BIN_PX) -> tuple:
    """The continuous record from ``tools/vision_rig.py`` as one Probe.

    Returns the probe (its ``t`` in seconds since the first picture used)
    and the list of runs, each with ``fire`` on the same clock.
    """
    folder = Path(folder)
    ts, rows = [], []
    # Pieces may cover different columns; place each on picture columns.
    X0, X1 = 400, 1500
    paths = sorted(folder.glob("edges-*.npz"))
    if t_start is not None or t_end is not None:
        # Each piece is named by its first picture's local time and holds
        # about a minute: read only the pieces that can overlap the window.
        stamps = [datetime.strptime(p.stem[6:], "%Y%m%d-%H%M%S").timestamp() for p in paths]
        lo = (t_start if t_start is not None else -np.inf) - 120
        hi = t_end if t_end is not None else np.inf
        paths = [p for p, s in zip(paths, stamps) if lo <= s <= hi]
    for path in paths:
        z = np.load(path)
        # The fitted line where the recorder saved one, else raw edges.
        key = "line50" if "line50" in z.files else "rows50"
        r = z[key].astype(np.float64) / 50.0
        r[z[key] < 0] = np.nan
        full = np.full((len(r), X1 - X0), np.nan)
        a = int(z["x0"]) - X0
        full[:, a:a + r.shape[1]] = r
        ts.append(z["t"])
        rows.append(full)
    x0 = X0
    t = np.concatenate(ts)
    r = np.vstack(rows)
    keep = np.ones(len(t), bool)
    if t_start is not None:
        keep &= t >= t_start
    if t_end is not None:
        keep &= t <= t_end
    t, r = t[keep], r[keep]
    width = (r.shape[1] // bin_px) * bin_px
    with np.errstate(all="ignore"):
        rb = np.nanmedian(r[:, :width].reshape(len(r), -1, bin_px), axis=2)
    px = x0 + (np.arange(rb.shape[1]) + 0.5) * bin_px
    good = np.mean(np.isnan(rb), axis=0) < 0.2
    rb, px = rb[:, good], px[good]
    still = baseline_at(folder, t[0])
    if still is None:
        still = np.nanmedian(rb, axis=0)  # no baseline marked: the record's own median
    else:
        still = still["row"]
    eta_f = (still - rb) * MM_PER_PX
    eta_f = _hampel(eta_f)
    origin = t[0]
    grid = np.arange(0, t[-1] - origin, DT)
    eta = np.full((len(grid), eta_f.shape[1]), np.nan)
    idx = np.floor((t - origin) / DT).astype(int)
    counts = np.bincount(idx, minlength=len(grid))[:len(grid)]
    for s in range(eta_f.shape[1]):
        sums = np.bincount(idx, weights=eta_f[:, s], minlength=len(grid))[:len(grid)]
        with np.errstate(all="ignore"):
            eta[:, s] = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    centres = grid + DT / 2
    runs = []
    log = Path(runs_log) if runs_log else folder / "runs.jsonl"
    disp: Dict[int, np.ndarray] = {}
    notes = {}
    for line in log.read_text().splitlines():
        entry = json.loads(line)
        samples = entry.get("reply", {}).get("data", {}).get("samples")
        if not entry.get("reply", {}).get("ok") or not samples:
            continue  # refused or unrecorded: nothing moved, or nothing known
        fire = entry["fire_sent"] - origin
        if fire < 0 or fire > centres[-1]:
            continue
        # The samples are in epoch seconds; the grid counts from origin.
        d, n = motion_from(entry["reply"], entry["run"], centres + origin, 0.0)
        for p, series in d.items():
            disp[p] = disp.get(p, 0) + series
        runs.append({"name": entry["run"]["name"], "fire": fire, "run": entry["run"],
                     "motion": n})
        notes[entry["run"]["name"]] = n
    probe = Probe(folder, {"name": "rig"}, centres, eta, px, disp, {"motion": notes})
    return probe, runs


def all_probes(folder, names: Optional[Sequence[str]] = None) -> List[Probe]:
    folder = Path(folder)
    out = []
    for path in sorted(folder.glob("probe-*.json")):
        stem = path.with_suffix("")
        record = json.loads(path.read_text())
        if "run" not in record or not stem.with_suffix(".mkv").exists():
            continue
        if names is not None and record["run"]["name"] not in names:
            continue
        out.append(load(stem))
    return out
