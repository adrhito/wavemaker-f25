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
    reply = record["replies"][-1]
    path = Path(reply["data"]["samples"]["file"])
    times, pistons, pos = _read_samples(path)
    settings = {}
    for group in record["run"]["groups"]:
        for p in _expand(group["pistons"]):
            settings[p] = group
    disp, notes = {}, {}
    for col, p in enumerate(pistons):
        g = settings.get(p)
        if g is None:
            continue
        stroke = float(g["stroke"])
        top = float(g.get("top", REST_MM - stroke))
        speed, accel = float(g["speed"]), float(g.get("accel", 20000))
        decel = float(g.get("decel", 20000))
        found = _fit_legs(times - fire, pos[:, col], top, speed, accel, decel)
        if found is None:
            continue
        legs, a_eff, smooth = found[:2], found[2], found[3]
        length = REST_MM - top
        disp[p] = _stroke(grid, legs, length, speed, a_eff, smooth)
        model = _stroke(times - fire, legs, length, speed, a_eff, smooth)
        resid = (REST_MM - pos[:, col]) - model
        notes[p] = {"legs": [float(v) for v in legs], "accel": float(a_eff),
                    "smooth": float(smooth),
                    "rms_mm": float(np.sqrt(np.mean(resid ** 2)))}
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
        stations_px: Optional[np.ndarray] = None) -> Model:
    """Least-squares kernels for every station at once.

    ``ridge`` and ``smooth`` penalise the kernels' size and their second
    difference, which keeps the long tails from fitting noise.
    """
    px = _common_stations(probes) if stations_px is None else stations_px
    names = list(groups)
    blocks, targets = [], []
    for probe in probes:
        u = inputs(probe, groups)
        X = np.hstack([_lagged(u[n], taps) for n in names])
        Y = probe.eta[:, _station_cols(probe, px)]
        ok = ~np.any(np.isnan(Y), axis=1)
        blocks.append(X[ok])
        targets.append(Y[ok])
    X = np.vstack(blocks)
    Y = np.vstack(targets)
    n = X.shape[1]
    D = np.zeros((taps - 2, taps))
    for k in range(taps - 2):
        D[k, k:k + 3] = (1, -2, 1)
    P = np.kron(np.eye(len(names)), D.T @ D) * smooth + np.eye(n) * ridge
    scale = np.mean(np.sum(X ** 2, axis=0)) + 1e-12
    A = X.T @ X + P * scale * 1e-4
    W = np.linalg.solve(A, X.T @ Y)
    kernels = W.reshape(len(names), taps, -1)
    return Model({k: list(v) for k, v in groups.items()}, taps, kernels, px)


def score(model: Model, probe: Probe, t0: float = 0.0,
          t1: Optional[float] = None) -> dict:
    """Relative RMS error of the prediction over the stations and window."""
    pred = model.predict(probe)
    Y = probe.eta[:, _station_cols(probe, model.stations_px)]
    mask = (probe.t >= t0) & (probe.t <= (t1 if t1 is not None else probe.t[-1]))
    mask &= ~np.any(np.isnan(Y), axis=1)
    err = pred[mask] - Y[mask]
    rms = float(np.sqrt(np.mean(Y[mask] ** 2)))
    return {"rel_rms": float(np.sqrt(np.mean(err ** 2))) / max(rms, 1e-9),
            "rms_mm": rms, "err_mm": float(np.sqrt(np.mean(err ** 2))),
            "peak_mm": float(np.max(np.abs(Y[mask])))}


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
