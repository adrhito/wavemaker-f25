"""From a run (pistons, settings, water depth) to predicted waves in the channel."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import air, linear, motion, tank
from .geometry import Geometry, load, parse_pistons

DT = 0.04              # s, synthesis sample interval
WINDOW = 163.84        # s, FFT window (4096 samples)
SIGMA_DECADES = 6.9    # exponential window: forcing weighted down by e^-6.9 = 1/1000 over the window
DEFAULT_STATIONS = [1.0, 3.0, 5.0, 8.0, 11.0, 14.0]


@dataclass
class Run:
    pistons: List[int]                         # display numbers
    params: motion.Params                      # shared settings
    depth: float                               # still-water depth, m
    cycles: Optional[float] = 5                # full cycles; None runs for `duration`
    duration: float = 30.0                     # s of motion allowed
    delays: Dict[int, float] = field(default_factory=dict)   # per-piston start delay, s
    per_piston: Dict[int, motion.Params] = field(default_factory=dict)  # overrides

    @classmethod
    def simple(cls, pistons="all", stroke_mm=200, speed=500, depth=0.4, accel=10000, jerk=2000,
               profile=motion.BESTEHORN, cycles=5, column_delay=0.0, g: Geometry = None) -> "Run":
        g = g or load()
        ids = parse_pistons(pistons, g) if isinstance(pistons, str) else list(pistons)
        delays = {}
        if column_delay:
            for n in ids:
                delays[n] = ((n - 1) // g.rows) * column_delay
        return cls(ids, motion.Params.simple(stroke_mm, speed, accel, jerk, profile), depth, cycles,
                   delays=delays)


def _flux(run: Run, g: Geometry, n: int, hp: tank.HydroParams):
    """Water flux into the tank from each of the 30 pistons, m^3/s, on the DT grid.

    Each disc's motion goes through the air trapped in its pipe (air.py):
    dry rows move nothing, wet rows move what the air passes on.
    """
    fine = 0.002
    nf = int(round(WINDOW / fine))
    step = int(round(DT / fine))
    Q = np.zeros((g.rows * g.columns, n))
    info = {"peak_speed": 0.0, "cycle_time": float("nan"), "end_time": 0.0, "pipes": {}}
    trajs, done = {}, {}
    zd_park = g.disc_height(g.park_mm)
    for p in run.pistons:
        prm = run.per_piston.get(p, run.params)
        delay = run.delays.get(p, 0.0)
        tkey = (tuple(vars(prm).values()), round(delay, 4))
        if tkey not in trajs:
            tr = motion.run(prm, duration=run.duration + delay, kj=g.bestehorn_jerk_factor, dt=fine,
                            delay=delay, cycles=run.cycles)
            pos = np.full(nf, tr.pos[-1])
            m = min(len(tr.pos), nf)
            pos[:m] = tr.pos[:m]
            moving = np.nonzero(np.abs(tr.vel) > 1e-6)[0]
            trajs[tkey] = (pos, tr, tr.t[moving[-1]] if len(moving) else 0.0)
        pos, tr, end = trajs[tkey]
        col = (p - 1) // g.rows + 1
        zb = g.row_bottom(col)
        fkey = (tkey, round(zb, 5))
        if fkey not in done:
            done[fkey] = air.pipe_flux(g.disc_height(pos), zb, run.depth, g.bore_area, fine, step, n,
                                       zd_park, hp.seal, hp.air_n, tr.legs)
        q, pinfo = done[fkey]
        Q[p - 1] = q
        info["pipes"][p] = pinfo
        info["peak_speed"] = max(info["peak_speed"], tr.peak_speed)
        info["cycle_time"] = tr.cycle_time
        info["end_time"] = max(info["end_time"], end)
    return Q, info


def row_summary(run: Run, info: Dict, g: Geometry) -> List[Dict]:
    """One line per selected row of three: wet or dry, and the water stroke the air passes on."""
    out = []
    for col in sorted({(p - 1) // g.rows + 1 for p in run.pistons}):
        ps = [p for p in run.pistons if (p - 1) // g.rows + 1 == col]
        pi = info["pipes"][ps[0]]
        out.append({"row": col, "pistons": ps, "bottom_mm": 1000 * pi["bottom_m"],
                    "submergence_mm": 1000 * pi["submergence_m"], "wet": pi["wet"],
                    "disc_down_mm": pi["disc_down_mm"], "first_down_mm": pi["first_down_mm"],
                    "last_down_mm": pi["last_down_mm"], "vented": pi["vented"], "leaked": pi["leaked"]})
    return out


def _span(cols: List[int]) -> str:
    """'8-10' or '3, 5' for a list of row numbers."""
    if not cols:
        return "none"
    if cols == list(range(cols[0], cols[-1] + 1)) and len(cols) > 1:
        return "%d-%d" % (cols[0], cols[-1])
    return ", ".join(str(c) for c in cols)


def display_number(axis: int) -> int:
    """The application's display number for PLC axis 0..29 (wavemaker-f25 app/tags.py)."""
    return 30 - 3 * (axis // 3) - (axis % 3)


def from_preset(path, pistons, depth: float, cycles: Optional[float] = 5, g: Geometry = None) -> Run:
    """A run from one of the lab's preset CSVs (wavemaker-f25/Presets), as the application applies it:
    the All row for every selected piston, overridden by a piston's own row unless that row is zeros.
    Curve Offset (hundredths of a second) becomes a start delay."""
    import csv
    g = g or load()
    ids = parse_pistons(pistons, g) if isinstance(pistons, str) else list(pistons)
    rows = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows[r["Motor"].strip()] = r

    def params(r):
        f = lambda k: float(r[k] or 0)
        return motion.Params(f("Position 1"), f("Position 2"), f("Speed 1"), f("Speed 2"), f("Accel 1"),
                             f("Accel 2"), f("Decel 1"), f("Decel 2"), f("Jerk 1"), f("Jerk 2"),
                             f("Time 1"), f("Time 2"), int(f("Profile")))

    base = params(rows["All"])
    per, delays = {}, {}
    for key, r in rows.items():
        if key == "All":
            continue
        vals = [float(r[k] or 0) for k in ("Position 1", "Position 2", "Speed 1", "Speed 2")]
        n = display_number(int(key))
        if any(vals):
            per[n] = params(r)
        off = float(r.get("Curve Offset") or 0)
        if off:
            delays[n] = off / 100.0
    run = Run(ids, base, depth, cycles=cycles, delays={n: d for n, d in delays.items() if n in ids},
              per_piston={n: q for n, q in per.items() if n in ids})
    run.curve = int(float(rows["All"].get("Curve ID") or 0))
    return run


def predict(run: Run, stations: Sequence[float] = DEFAULT_STATIONS, g: Geometry = None,
            hp: tank.HydroParams = None, verbose: bool = False) -> Dict:
    """Surface elevation at channel stations for a run.

    Uses an exponential window: the forcing is weighted by exp(-sigma t), the
    tank is solved at complex frequency 2 pi f + i sigma, and the answer is
    un-weighted. A closed, lightly damped tank rings for minutes; this keeps
    that ringing from wrapping round the FFT window into t = 0.
    """
    g = g or load()
    hp = hp or tank.HydroParams()
    n = int(round(WINDOW / DT))
    t = np.arange(n) * DT
    sigma = SIGMA_DECADES / WINDOW
    freqs = np.fft.rfftfreq(n, DT)
    Q, info = _flux(run, g, n, hp)
    net = Q.sum(axis=0)
    QF = np.fft.rfft(Q * np.exp(-sigma * t), axis=1)            # [piston, f]
    tf = tank.transfer(run.depth, stations, freqs, g, hp, verbose=verbose, sigma=sigma)
    # transfer functions use exp(-i w t); numpy's forward FFT pairs with exp(+i w t)
    EF = np.einsum("fps,pf->sf", np.conj(tf.H), QF)
    eta = np.fft.irfft(EF, n=n, axis=1) * np.exp(sigma * t) * hp.gain     # [station, t], m

    # spectrum check: how much of the forcing lies above the solved band
    P = (np.abs(np.fft.rfft(Q, axis=1)) ** 2).sum(axis=0)
    above = float(P[freqs > tf.meta.get("f_max", hp.f_max)].sum() / max(P.sum(), 1e-30))

    rows = row_summary(run, info, g)
    out = {
        "t": t, "eta": eta, "stations": list(stations),
        "net_flux": net,
        "volume_per_stroke_m3": sum(g.bore_area * r["last_down_mm"] / 1000.0 * len(r["pistons"]) for r in rows),
        "volume_first_stroke_m3": sum(g.bore_area * r["first_down_mm"] / 1000.0 * len(r["pistons"]) for r in rows),
        "volume_disc_m3": g.bore_area * run.params.stroke / 1000.0 * len(run.pistons),
        "rows": rows, "seal": hp.seal,
        "peak_flux_m3s": float(np.abs(net).max()),
        "peak_piston_speed_mm_s": info["peak_speed"],
        "cycle_time_s": info["cycle_time"],
        "motion_end_s": info["end_time"],
        "spectral_energy_above_fmax": above,
        "dx": tf.dx, "cells": tf.meta.get("cells"),
        "warnings": [],
        "metrics": [],
    }
    dry = [r["row"] for r in rows if not r["wet"]]
    if dry:
        out["warnings"].append("row%s %s of the selection stand%s above the %.0f mm water (bottoms %s mm): "
                               "%s only air" % ("s" if len(dry) > 1 else "", _span(dry), "" if len(dry) > 1 else "s",
                                                1000 * run.depth,
                                                ", ".join("%.0f" % (1000 * g.row_bottom(c)) for c in dry),
                                                "they move" if len(dry) > 1 else "it moves"))
    if not [r for r in rows if r["wet"]]:
        out["warnings"].append("no selected row reaches the water: no wave")
    capped = [r for r in rows if r["wet"] and r["vented"] and r["last_down_mm"] < 0.9 * r["disc_down_mm"]]
    if capped:
        out["warnings"].append("air vents from the bottom of row%s %s: the water stroke is %s mm of the %.0f mm "
                               "disc stroke (%s seal)" % ("s" if len(capped) > 1 else "", _span([r["row"] for r in capped]),
                                                     "/".join("%.0f" % r["last_down_mm"] for r in capped),
                                                     run.params.stroke, hp.seal))
    if above > 0.02:
        out["warnings"].append("%.0f%% of the forcing lies above %.1f Hz and is left out" % (100 * above, hp.f_max))
    if getattr(run, "curve", 0):
        out["warnings"].append("this preset runs stored curve %d; the model drives the plain Position 1/2 "
                               "stroke with each piston's Curve Offset as a start delay" % run.curve)
    if run.params.speed2 > info["peak_speed"] * 1.05:
        out["warnings"].append("commanded %.0f mm/s but the jerk/acceleration limits only reach %.0f mm/s"
                               % (run.params.speed2, info["peak_speed"]))
    for si, x in enumerate(stations):
        out["metrics"].append(_metrics(eta[si], t, x, run, info, g))
    return out


def _waves(e, t, t0, t1):
    """Zero up-crossing waves wholly inside [t0, t1]: (start, period, height, crest, trough)."""
    m = (t >= t0) & (t <= t1)
    idx = np.nonzero(m)[0]
    if len(idx) < 3:
        return []
    seg, ts = e[idx], t[idx]
    s = np.sign(seg)
    up = np.nonzero((s[:-1] <= 0) & (s[1:] > 0))[0]
    out = []
    for a, b in zip(up[:-1], up[1:]):
        w = seg[a:b + 1]
        out.append((ts[a], ts[b] - ts[a], w.max() - w.min(), w.max(), w.min()))
    return out


def _lead(e, t, t0, t1, frac=0.05):
    """The leading wave between t0 and t1: (crest, trough, crest-to-trough height).

    The first lobe to pass `frac` of the peak and the next lobe of the other
    sign that also does; smaller wiggles between them are skipped. This is
    the wave someone watching the station sees first, and it arrives before
    any reflection from the head's walls can catch up with it.
    """
    m = (t >= t0) & (t <= t1)
    seg = e[m]
    if len(seg) < 3 or not np.any(seg):
        return 0.0, 0.0, 0.0
    big = frac * np.abs(seg).max()
    i = int(np.argmax(np.abs(seg) > big))
    s = np.sign(seg[i:])
    cut = np.nonzero(np.diff(s))[0] + 1
    runs = np.split(seg[i:], cut)
    s1 = np.sign(runs[0][0])
    first, second = 0.0, 0.0
    for r in runs:
        ext = r.max() if s1 > 0 else r.min()
        if np.sign(r[0]) == s1:
            if second:
                break
            first = max(first, ext) if s1 > 0 else min(first, ext)
        else:
            opp = r.min() if s1 > 0 else r.max()
            if abs(opp) > big:
                second = min(second, opp) if s1 > 0 else max(second, opp)
    crest, trough = (first, second) if s1 > 0 else (second, first)
    return float(crest), float(trough), float(crest - trough)


def _metrics(e, t, x, run: Run, info, g: Geometry) -> Dict:
    h = run.depth
    c0 = math.sqrt(linear.G * h)
    horizon = info["end_time"] + (2 * g.channel_length) / c0 + 10
    keep = t <= horizon
    e, t = e[keep], t[keep]
    peak = np.abs(e).max() if len(e) else 0.0
    first = np.nonzero(np.abs(e) > 0.05 * peak)[0] if peak > 0 else []
    arrival = float(t[first[0]]) if len(first) else float("nan")
    t_gate = (2 * g.channel_length - x) / c0          # earliest return of the gate reflection
    all_w = [w for w in _waves(e, t, 0, t[-1]) if w[2] > 0.1 * (e.max() - e.min())]
    inc = [w for w in _waves(e, t, arrival, t_gate) if w[2] > 0.05 * (e.max() - e.min())] if arrival == arrival else []
    lead_crest, lead_trough, lead_H = _lead(e, t, 0.0, t_gate)
    inc_H = max((w[2] for w in inc), default=float("nan"))
    inc_crest = float(e[(t >= arrival) & (t <= t_gate)].max()) if inc else float("nan")
    heights = np.array([w[2] for w in all_w]) if all_w else np.array([0.0])
    H13 = float(np.mean(np.sort(heights)[-max(1, len(heights) // 3):]))
    T = float(np.median([w[1] for w in all_w])) if all_w else float("nan")
    w = 2 * math.pi / T if T == T and T > 0 else float("nan")
    k = float(linear.wavenumber(w, h)) if w == w else float("nan")
    Hlead = inc_H if inc_H == inc_H else lead_H
    flags = []
    crest2 = Ur = float("nan")
    if k == k and Hlead > 0:
        crest2 = float(linear.stokes2_crest(Hlead / 2, k, h)[0])
        Ur = float(linear.ursell(Hlead, k, h))
        Hb = float(linear.breaking_height(k, h))
        if max(Hlead, H13) > Hb:
            flags.append("breaking: H %.0f mm exceeds the %.0f mm limit, real waves will be smaller"
                         % (1000 * max(Hlead, H13), 1000 * Hb))
        if Ur > 26:
            flags.append("Ursell %.0f: cnoidal regime, crests sharper and higher than linear" % Ur)
    return {
        "x_m": x,
        "arrival_s": arrival,
        "gate_reflection_s": t_gate,
        "first_wave_height_mm": 1000 * lead_H,
        "first_crest_mm": 1000 * lead_crest,
        "first_trough_mm": 1000 * lead_trough,
        "incident_height_mm": 1000 * inc_H,
        "incident_crest_mm": 1000 * inc_crest,
        "max_crest_mm": 1000 * float(e.max()),
        "min_trough_mm": 1000 * float(e.min()),
        "H_one_third_mm": 1000 * H13,
        "period_s": T,
        "wavelength_m": 2 * math.pi / k if k == k and k > 0 else float("nan"),
        "stokes2_crest_mm": 1000 * crest2,
        "ursell": Ur,
        "flags": flags,
    }
