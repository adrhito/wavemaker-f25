"""The air trapped between each piston's sealed disc and the water in its pipe.

The pipes stand on the duct block, stepped from row 1 (lowest) to row 10
(highest), tops level; each opens at its bottom into its own duct, which
runs to an outlet in the block's face at the channel mouth. A disc with a
rubber seal runs in the top of each pipe, and between the disc and
the water there is air. So:

* A row whose pipe bottom (its duct's ceiling) is above the still water moves
  only air: its outlet is not under water, and the air leaves by it.
* In a wet row the air is a stiff spring between disc and water. The water
  inside follows the disc a few per cent short of it, because the air
  squeezes: n p / L is 15-30 times the rho g of the water pushing back.
* If a downstroke drives the inner water down to the pipe bottom, the rest of
  it blows air along the duct and out of the outlet, and moves no water
  (venting).
* An upstroke draws water up the pipe, above the outside level if it must
  (a slight vacuum in the air), unless air gets past the seal.

Which of two seal behaviours the real discs have is not known yet:

'tight'  No air passes the disc either way. The air in a pipe can only shrink
         (by venting). The pistons park at 370 mm, the bottom of their
         travel, and a run lifts them to Position 1 before stroking, so the
         inner water never reaches the pipe bottom: nothing vents and every
         stroke of a wet row moves its full volume, less the air's give.
'leaky'  Air from above leaks past the seal whenever the trapped air drops
         below atmospheric. The inner water can then never rise above the
         outside level, so a downstroke moves at most the pipe bottom's
         submergence (plus the air's squeeze) before it vents. This is the
         "useful stroke capped at the submergence" behaviour.

Seeing bubbles come out of the pipe bottoms on every downstroke would mean
'leaky'; none after the first stroke would mean 'tight'. A measured run
with a stroke longer than the submergence also tells them apart.

`pipe_flux` is quasi-static: the air pressure balances the water at the
inner surface, p = p0 + rho g (h - z_in), and p L^n stays fixed between
venting and leak events (n = 1 while parked and while Prepare lifts the
pistons, the polytropic index during the run). `pipes_dynamic` adds the
water's inertia: with the drawn ducts it is large (drive.py), and air and
water ring at 0.9-3 Hz, inside the band the strokes drive. (Without the
ducts, as this model was first written, they rang at 3-5 Hz.)
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from .linear import G

P0 = 101325.0      # Pa, atmospheric
RHO = 1000.0       # kg/m^3
SEALS = ("tight", "leaky")


def air_length(zd, C, h, n=1.0):
    """Length L of trapped air under a disc at height zd holding p L^n = C, m.

    Solves (p0 + rho g (h - zd + L)) L^n = C, increasing and convex in L, by
    Newton from above.
    """
    zd = np.asarray(zd, float)
    C = np.broadcast_to(np.asarray(C, float), zd.shape)
    L = np.maximum((C / P0) ** (1.0 / n), 1e-9) * 1.2 + 1e-6
    for _ in range(40):
        p = P0 + RHO * G * (h - zd + L)
        f = p * L ** n - C
        df = RHO * G * L ** n + n * p * L ** (n - 1)
        step = f / df
        L = np.maximum(L - step, 1e-9)
        if np.all(np.abs(step) < 1e-10):
            break
    return L


def _staged(zd_park: float, zd_to: float, zb: float, h: float, seal: str) -> float:
    """p L (isothermal) after filling with the disc parked, then a slow move to zd_to."""
    C = P0 * (zd_park - zb)                     # trapped at atmospheric as the water passed the bottom
    if zd_to < zd_park:                          # moved down: may vent at the bottom
        C = min(C, (P0 + RHO * G * (h - zb)) * (zd_to - zb))
    elif seal == "leaky":                        # moved up: air drawn in past the seal
        C = max(C, P0 * max(zd_to - h, 0.0))
    return C


def rest_state(zd_park: float, zd: float, zb: float, h: float, seal: str = "tight",
               n_run: float = 1.4) -> Dict:
    """Air and water in one pipe at rest with the disc at zd (after parking at zd_park)."""
    if zb >= h:
        return {"water_column_m": 0.0, "inner_level_m": None, "air_length_m": zd - zb,
                "air_gauge_kpa": 0.0, "follow": 0.0}
    C = _staged(zd_park, zd, zb, h, seal)
    L = float(air_length(zd, C, h, 1.0))
    z_in = zd - L
    p = P0 + RHO * G * (h - z_in)
    follow = n_run * p / (n_run * p + RHO * G * L)    # d(z_in)/d(zd) for a small, quick move
    return {"water_column_m": max(0.0, z_in - zb), "inner_level_m": z_in, "air_length_m": L,
            "air_gauge_kpa": (p - P0) / 1000.0, "follow": follow}


def pipe_flux(zd: np.ndarray, zb: float, h: float, area: float, dt: float, step: int, ncoarse: int,
              zd_park: float, seal: str = "tight", n: float = 1.4,
              legs: Optional[List[Dict]] = None):
    """Water pushed out of one pipe, m^3/s, averaged onto a grid `step` fine samples apart.

    `zd` is the disc height on the fine grid (dt); the run starts there at
    rest, the pistons having been lifted from the park position before t = 0.
    Returns (q[ncoarse], info).
    """
    if seal not in SEALS:
        raise ValueError("seal must be one of %s" % (SEALS,))
    info = {"wet": zb < h, "bottom_m": zb, "submergence_m": h - zb}
    if zb >= h:
        info.update(first_down_mm=0.0, last_down_mm=0.0, disc_down_mm=float("nan"), vented=False,
                    leaked=False, follow=0.0)
        return np.zeros(ncoarse), info

    # state at the start position, then switch from isothermal to the run's polytropic index
    zd0 = float(zd[0])
    L0 = float(air_length(zd0, _staged(zd_park, zd0, zb, h, seal), h, 1.0))
    p0 = P0 + RHO * G * (h - zd0 + L0)
    c = p0 * L0 ** n

    pb = P0 + RHO * G * (h - zb)
    vent = pb * np.maximum(zd - zb, 1e-9) ** n           # most air the pipe can hold at each instant
    leak = P0 * np.maximum(zd - h, 0.0) ** n if seal == "leaky" else np.zeros_like(zd)
    C = np.empty_like(zd)
    moving = np.nonzero(np.diff(zd))[0]
    i0, i1 = (int(moving[0]), int(moving[-1]) + 2) if len(moving) else (len(zd), len(zd))
    C[:i0] = c
    vented = leaked = False
    vl, ll = vent[i0:i1].tolist(), leak[i0:i1].tolist()
    out = []
    for v, lk in zip(vl, ll):
        if c < lk:
            c, leaked = lk, True
        if c > v:
            c, vented = v, True
        out.append(c)
    C[i0:i1] = out
    C[i1:] = c

    z_in = zd - air_length(zd, C, h, n)
    V = area * (z_in[0] - z_in)                           # pushed out so far, m^3
    idx = np.minimum(np.arange(ncoarse + 1) * step, len(V) - 1)
    q = np.diff(V[idx]) / (step * dt)

    downs = []
    for lg in legs or []:
        a = min(int(round(lg["start"] / dt)), len(zd) - 1)
        b = min(int(round((lg["start"] + lg["time"]) / dt)), len(zd) - 1)
        if zd[b] < zd[a] - 1e-6:
            downs.append((float(1000 * (V[b] - V[a]) / area), float(1000 * (zd[a] - zd[b]))))
    info.update(first_down_mm=downs[0][0] if downs else 0.0, last_down_mm=downs[-1][0] if downs else 0.0,
                disc_down_mm=downs[0][1] if downs else 0.0, vented=vented, leaked=leaked,
                follow=n * p0 / (n * p0 + RHO * G * L0), inner_start_m=float(z_in[0]),
                inner_min_m=float(z_in.min()), inner_max_m=float(z_in.max()),
                air_start_m=L0, p_start_pa=p0, pLn=c, n=n)
    return q, info


def pipes_dynamic(ZD: np.ndarray, zb: np.ndarray, h: float, area: float, mass: np.ndarray, c_quad: np.ndarray,
                  c_lin: np.ndarray, C0: np.ndarray, z0: np.ndarray, n: float, seal: str, dt: float):
    """Air and water in several wet pipes at once, with the water's inertia.

    ZD[u, k]: disc height of pipe group u at step k (dt apart). Per group: pipe
    bottom zb, water mass M (pipe + duct, referred to the inner surface), quadratic
    loss c_quad (N per (m/s)^2) and linear damping c_lin (N s/m), and the start
    state: p L^n = C0, inner surface z0, at rest.

        M dw/dt = -A (p - p0 - rho g (h - z_in)) - c_lin w - c_quad w |w|
        p = C / (z_disc - z_in)^n

    Venting: the inner water cannot go below the pipe bottom; air leaves along
    the duct instead (C drops) and the water there stops. 'leaky': air comes in
    past the seal whenever p < p0 (C rises). Semi-implicit Euler.
    Returns flux out (m^3/s) and air pressure (Pa), both [u, k], and per-group
    flags (vented, leaked).
    """
    U, N = ZD.shape
    z = z0.astype(float).copy()
    w = np.zeros(U)
    C = C0.astype(float).copy()
    q = np.zeros((U, N))
    P = np.zeros((U, N))
    vented = np.zeros(U, bool)
    leaked = np.zeros(U, bool)
    pb = P0 + RHO * G * (h - zb)
    rg = RHO * G
    leaky = seal == "leaky"
    for k in range(N):
        zd = ZD[:, k]
        L = np.maximum(zd - z, 1e-4)
        p = C / L ** n
        if leaky:
            low = p < P0
            if low.any():
                C = np.where(low, P0 * L ** n, C)
                p = np.where(low, P0, p)
                leaked |= low
        acc = (-area * (p - P0 - rg * (h - z)) - c_lin * w - c_quad * w * np.abs(w)) / mass
        w = w + dt * acc
        z = z + dt * w
        under = z < zb
        if under.any():
            z = np.where(under, zb, z)
            w = np.where(under, np.maximum(w, 0.0), w)
            Lb = np.maximum(zd - zb, 1e-4)
            C = np.where(under, np.minimum(C, pb * Lb ** n), C)
            p = np.where(under, C / Lb ** n, p)
            vented |= under
        q[:, k] = -area * w
        P[:, k] = p
    return q, P, vented, leaked
