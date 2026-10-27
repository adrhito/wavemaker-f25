"""Frequency-domain linear wave model of the whole T-shaped tank.

Plan view on a uniform grid: the head (x < 0), the channel (0 < x < L) and,
inside the head at the channel mouth, the piston bank. For each frequency
the depth-averaged equation

    div(D grad eta) + (omega^2 / g) S eta = i omega s / g        (e^{-i omega t})

is solved, where in open water D = tanh(kh)/k and S = 1 (exact propagation
for linear waves of any depth). Under a row of pipes whose bottoms z_b are
below the still water, the water flows in a layer roofed by the pipe bottoms:
D = z_b, and only the gaps between the round pipes keep a free surface
(S = the void fraction, 0.25 on a square pitch). Rows whose bottoms are above
the water leave open water under them. The bottoms step from row to row, so
the roof does too. s is each piston's volume flux spread over its footprint.
Walls reflect. Damping enters as a complex wavenumber: Hunt's boundary-layer decay
in the channel, plus an adjustable absorption in the head that stands in for
whatever else spreads and dissipates the backward wave there.

The 5-point Laplacian's own dispersion is cancelled along the axis by using
4 sin^2(k dx/2)/dx^2 in place of k^2, so the channel carries waves at the
right speed even at 8-10 cells per wavelength.

The result is a transfer function per piston per station, cached on disk:
eta(station, f) = sum_i H_i(f) Q_i(f). The far-field coupling factor for a
near-bed source (linear.source_coupling) is applied per piston, for the roof
height under which its water leaves the bank toward the channel.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from . import linear
from .geometry import Geometry, load

CACHE = Path(__file__).resolve().parent.parent / "cache"


@dataclass
class HydroParams:
    """Knobs of the water model. Defaults are physics-based first guesses;
    `calibrate` fits the ones marked (fit) to measured waves."""

    gain: float = 1.0                 # (fit) overall efficiency: leaks round pistons, compliance
    channel_damping: float = 1.0      # (fit) multiple of Hunt's laminar damping in the channel
    head_absorption: float = 0.01     # (fit) extra decay in the head, as a fraction of k per metre
    end: str = "gate"                 # 'gate' (closed, reflecting) or 'absorbing' (incident wave only)
    head_walls: str = "reflect"       # 'reflect' or 'absorb'
    f_max: float = 2.0                # Hz, highest frequency solved
    cells_per_wavelength: float = 9.0
    dx_max: float = 0.06              # m
    seal: str = "tight"               # disc seal: 'tight' or 'leaky' (air passes it under vacuum); see air.py
    air_n: float = 1.4                # polytropic index of the trapped air during a run


# Fields that do not change the cached transfer functions (they act on the flux or scale the result).
_FLUX_ONLY_HP = {"seal", "air_n", "gain"}       # gain multiplies the result in predict
_FLUX_ONLY_G = {"raw", "published", "disc_zero", "park_mm", "stroke_min_mm", "stroke_max_mm", "speed_max",
                "accel_max", "bestehorn_jerk_factor", "cylinder_bore", "pipe_top", "floor_above_lab",
                "reservoir_length", "reservoir_width", "reservoir_height"}


@dataclass
class Transfer:
    freqs: np.ndarray                 # Hz
    H: np.ndarray                     # [freq, piston(1..30 -> 0..29), station] complex, m per (m^3/s)
    stations: List[float]             # x of each station, m
    depth: float
    dx: float
    meta: Dict = field(default_factory=dict)


# ---------------------------------------------------------------------------

def _grid(g: Geometry, dx: float):
    x0, x1 = -g.head_length, g.channel_length
    y0, y1 = -g.head_width / 2, g.head_width / 2
    nx = int(round((x1 - x0) / dx))
    ny = int(round((y1 - y0) / dx))
    xs = x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx
    ys = y0 + (np.arange(ny) + 0.5) * (y1 - y0) / ny
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    head = (X < 0)
    chan = (X >= 0) & (np.abs(Y) <= g.channel_width / 2)
    wet = head | chan
    bx0, bx1 = g.bank_x_range
    bank = (X >= bx0) & (X < bx1) & (np.abs(Y) <= g.bank_width / 2) & wet
    # height of the pipe bottoms over each bank cell (inf elsewhere)
    roof = np.full(X.shape, np.inf)
    for c in range(1, g.columns + 1):
        xc = g.column_x(c)
        roof[bank & (X >= xc - g.pitch_along / 2) & (X < xc + g.pitch_along / 2)] = g.row_bottom(c)
    return xs, ys, X, Y, wet, head, chan, bank, roof


def _assemble(g: Geometry, hp: HydroParams, omega: float, h: float, grid):
    xs, ys, X, Y, wet, head, chan, bank, roof = grid
    nx, ny = X.shape
    dx = xs[1] - xs[0]
    dy = ys[1] - ys[0]
    w_re = float(np.real(omega))
    kr = float(linear.wavenumber(w_re, h)) if w_re > 0 else float(abs(np.imag(omega)) / math.sqrt(linear.G * h))
    k = linear.wavenumber_complex(omega, h) if np.iscomplexobj(omega) and np.imag(omega) != 0 else kr
    covered = bank & (roof < h)                      # pipes reach below the surface here
    D_open = np.tanh(k * h) / k
    D = np.where(covered, np.where(np.isfinite(roof), roof, 0.0), D_open) + 0j
    # discrete-dispersion-corrected k^2, with decay folded in as k -> k + i alpha
    kappa2 = 4 * np.sin(k * dx / 2) ** 2 / dx ** 2 if kr * dx < math.pi else k ** 2
    alpha = np.zeros_like(X)
    a_ch = float(linear.hunt_damping(w_re, h, g.channel_width)) * hp.channel_damping
    a_hd = float(linear.hunt_damping(w_re, h, g.head_width)) * hp.channel_damping + hp.head_absorption * kr
    alpha[chan] = a_ch
    alpha[head] = a_hd
    # sponge layers: a gentle quadratic ramp at least one wavelength long reflects little
    lam = 2 * math.pi / max(kr, 1e-9)
    if hp.end == "absorbing":
        Lsp = min(max(4.0, 1.5 * lam), 0.45 * g.channel_length)
        s = np.clip((X - (g.channel_length - Lsp)) / Lsp, 0, 1)
        alpha = np.where(chan, alpha + 8.0 / Lsp * s ** 2, alpha)
    if hp.head_walls == "absorb":
        Lsp = min(max(1.0, 1.5 * lam), 0.45 * (g.head_length - g.bank_length))
        dist = X + g.head_length
        if g.head_width > 4 * Lsp:      # side walls too, where there is room
            dist = np.minimum(dist, g.head_width / 2 - np.abs(Y))
        s = np.clip(1 - dist / Lsp, 0, 1)
        alpha = np.where(head & ~bank, alpha + 8.0 / Lsp * s ** 2, alpha)
    S = np.where(covered, g.void_fraction, 1.0)
    # storage omega^2 S / g, written as D_open kappa^2 S so the discrete dispersion stays matched
    diag_term = (D_open * kappa2) * (1 + 2j * alpha / max(kr, 1e-9)) * S * dx * dy

    idx = -np.ones(X.shape, int)
    idx[wet] = np.arange(wet.sum())
    rows, cols, vals = [], [], []
    diag = np.zeros(wet.sum(), complex)
    diag += diag_term[wet]

    def link(a_mask, b_mask, shift_axis, open_mask=None):
        # faces between cell (i) and its +1 neighbour along shift_axis
        sl_a = [slice(None), slice(None)]
        sl_b = [slice(None), slice(None)]
        sl_a[shift_axis] = slice(0, -1)
        sl_b[shift_axis] = slice(1, None)
        A = wet[tuple(sl_a)] & wet[tuple(sl_b)]
        if open_mask is not None:
            A &= open_mask
        ia = idx[tuple(sl_a)][A]
        ib = idx[tuple(sl_b)][A]
        Da = D[tuple(sl_a)][A]
        Db = D[tuple(sl_b)][A]
        T = 2 * Da * Db / (Da + Db)
        T = T * (dy / dx if shift_axis == 0 else dx / dy)
        return ia, ib, T

    # duct side walls: no flow between the bank and the head across the bank's long sides
    if g.duct_side_walls:
        side_block = ~(bank[:, :-1] ^ bank[:, 1:])
    else:
        side_block = np.ones((nx, ny - 1), bool)
    for axis, om in ((0, None), (1, side_block)):
        ia, ib, T = link(wet, wet, axis, om)
        rows += [ia, ib]
        cols += [ib, ia]
        vals += [T, T]
        np.subtract.at(diag, ia, T)
        np.subtract.at(diag, ib, T)
    rows.append(np.arange(len(diag)))
    cols.append(np.arange(len(diag)))
    vals.append(diag)
    A = sp.csc_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(len(diag), len(diag)))
    return A, idx, k


def _footprint(X, Y, wet, cx, cy, lx, ly, dx):
    """Share of each cell covered by a piston's footprint (exact source length on a coarse grid)."""
    ox = np.clip(np.minimum(X + dx / 2, cx + lx / 2) - np.maximum(X - dx / 2, cx - lx / 2), 0, None) / dx
    oy = np.clip(np.minimum(Y + dx / 2, cy + ly / 2) - np.maximum(Y - dx / 2, cy - ly / 2), 0, None) / dx
    return np.where(wet, ox * oy, 0.0)


def _key(g: Geometry, hp: HydroParams, h: float, stations, freqs) -> str:
    blob = json.dumps({
        "g": {k: v for k, v in asdict(g).items() if k not in _FLUX_ONLY_G},
        "hp": {k: v for k, v in asdict(hp).items() if k not in _FLUX_ONLY_HP},
        "h": round(h, 5), "st": [round(s, 4) for s in stations],
        "f": [round(float(freqs[0]), 6), round(float(freqs[-1]), 6), len(freqs)],
    }, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def transfer(depth: float, stations: Sequence[float], freqs: np.ndarray, g: Optional[Geometry] = None,
             hp: Optional[HydroParams] = None, use_cache: bool = True, verbose: bool = False,
             sigma: float = 0.0) -> Transfer:
    """Transfer functions from every piston to channel stations (x, m), at
    complex frequency 2 pi f + i sigma (sigma > 0 for the exponential window)."""
    g = g or load()
    hp = hp or HydroParams()
    h = float(depth)
    stations = [float(s) for s in stations]
    freqs = np.asarray(freqs, float)
    key = _key(g, hp, h, stations, freqs) + ("_s%.4f" % sigma if sigma else "")
    path = CACHE / ("tf_%s.npz" % key)
    if use_cache and path.exists():
        z = np.load(path, allow_pickle=True)
        return Transfer(z["freqs"], z["H"], list(z["stations"]), float(z["depth"]), float(z["dx"]),
                        json.loads(str(z["meta"])))

    fmax = min(hp.f_max, float(freqs.max()))
    kmax = float(linear.wavenumber(2 * math.pi * fmax, h))
    dx = min(hp.dx_max, 2 * math.pi / kmax / hp.cells_per_wavelength)
    grid = _grid(g, dx)
    xs, ys, X, Y, wet, head, chan, bank, roof = grid
    pist = g.pistons()
    lips = [g.lip_height(p.column, h) for p in pist]
    # each piston's footprint cells (the duct under it)
    src_w = [_footprint(X, Y, wet, p.x, p.y, g.pitch_along, g.pitch_across, dx) for p in pist]
    # station sampling: mean over the channel cells in the column nearest x
    st_cols = []
    for s in stations:
        i = int(np.argmin(np.abs(xs - s)))
        st_cols.append((i, chan[i, :]))

    H = np.zeros((len(freqs), len(pist), len(stations)), complex)
    idx = None
    for fi, f in enumerate(freqs):
        if f > fmax or (f <= 0 and not sigma):
            continue
        omega = 2 * math.pi * f + 1j * sigma if sigma else 2 * math.pi * f
        A, idx, k = _assemble(g, hp, omega, h, grid)
        nw = A.shape[0]
        B = np.zeros((nw, len(pist)), complex)
        for j, w in enumerate(src_w):
            m = w > 0
            # total right-hand side i omega Q / g for unit Q, spread over the footprint
            B[idx[m], j] = 1j * omega / linear.G * w[m] / w.sum()
        lu = spla.splu(A)
        E = lu.solve(B)
        coup = np.array([complex(linear.source_coupling(k, h, z)) if z is not None else 1.0 for z in lips])
        for si, (i, colmask) in enumerate(st_cols):
            cells = idx[i, colmask]
            H[fi, :, si] = E[cells, :].mean(axis=0) * coup
        if verbose and fi % 25 == 0:
            print("  f=%.3f Hz  (%d/%d)  cells=%d" % (f, fi, len(freqs), nw), flush=True)

    meta = {"cells": int(wet.sum()), "dx": dx, "f_max": fmax, "key": key}
    CACHE.mkdir(exist_ok=True)
    np.savez_compressed(path, freqs=freqs, H=H, stations=np.array(stations), depth=h, dx=dx,
                        meta=json.dumps(meta))
    return Transfer(freqs, H, stations, h, dx, meta)


def field_at(depth: float, freq: float, pistons: Sequence[int], g: Optional[Geometry] = None,
             hp: Optional[HydroParams] = None, dx: Optional[float] = None):
    """Complex surface field over the whole plan for unit flux from `pistons` (for pictures)."""
    g = g or load()
    hp = hp or HydroParams()
    h = float(depth)
    omega = 2 * math.pi * freq
    k = float(linear.wavenumber(omega, h))
    dx = dx or min(hp.dx_max, 2 * math.pi / k / hp.cells_per_wavelength)
    grid = _grid(g, dx)
    xs, ys, X, Y, wet, head, chan, bank, roof = grid
    A, idx, k = _assemble(g, hp, omega, h, grid)
    b = np.zeros(A.shape[0], complex)
    for p in g.pistons():
        z = g.lip_height(p.column, h)
        if p.number in pistons and z is not None:
            w = _footprint(X, Y, wet, p.x, p.y, g.pitch_along, g.pitch_across, dx)
            m = w > 0
            b[idx[m]] += 1j * omega / linear.G * w[m] / w.sum() * complex(linear.source_coupling(k, h, z))
    e = spla.spsolve(A, b)
    out = np.full(X.shape, np.nan, complex)
    out[wet] = e * hp.gain
    return xs, ys, out, wet, bank
