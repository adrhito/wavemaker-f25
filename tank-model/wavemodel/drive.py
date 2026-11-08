"""The pistons as a machine: what each row moves, how the water in its duct
answers, and what the LinMot motors can push.

The water a wet row moves is a long, narrow column: the water in the pipe
under the trapped air, then the whole duct to the outlet at the face (0.37 m
for row 10, 2.61 m for row 1), where it runs 1.9 times faster than the disc
because the duct is half the bore's area. Referred to the inner water
surface, that duct is a large mass,

    M = rho A (water in the pipe) + rho A^2 (duct run + end) / A_duct,

about 190 kg for row 1. The trapped air is the spring between disc and water,

    k = A (n p / L + rho g),

so a row rings at f_n = sqrt(k / M) / 2 pi: about 0.9 Hz for row 1 at 0.30 m
of water, inside the band the strokes drive. The water's motion is the
quasi-static one (air.py) passed through

    H(s) = k / (k + c s + M s^2),

with c from the duct's losses (exit, the elbow's turn, friction), the waves
the outlets radiate, and a damping ratio for everything else (wall friction,
the air's heat exchange; `duct_zeta`, 0.05 est., fittable). H(0) = 1, so the
volume a stroke moves is unchanged; what changes is when it arrives, and the
ringing after the disc stops. predict.py integrates this in time
(air.pipes_dynamic), so venting and leaks act on the swinging water.

The motor holds the disc against gravity and pushes on the air:

    F = m a - m g + A (p - p0)        (positive pushes the disc down)

which the LinMot P01-48x360F gives up to 1020 N (79% of that at the ends of
its extended stroke), and 200 N continuously without a fan (340 N with one).

Numbers marked 'est.' are engineering estimates, not measurements: the end
correction, the loss coefficients and the moving mass. Calibrate them with a
camera run that shows the ringing (its period gives M, its decay c).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

from . import air
from .geometry import Geometry
from .linear import G

RHO = 1000.0
P0 = air.P0
END_LENGTH = 0.10        # m, est.: the turn from the pipe into the elbow and the outlet's end correction
K_EXIT = 1.0             # est.: the outlet's kinetic head is lost
K_TURN = 1.2             # est.: the elbow's sharp 90 degree turn into a duct half the bore's area
K_CONTRACTION = 0.2      # est.: bore to duct
FRICTION_F = 0.02        # est.: Darcy friction factor in the duct


def motor_limits(g: Geometry) -> Dict:
    m = g.motor or {}
    cont = m.get("continuous_force_n", {}) or {}
    return {"peak_n": float(m.get("peak_force_n", 1020)), "border_share": float(m.get("border_force_share", 0.79)),
            "cont_passive_n": float(cont.get("passive", 200)), "cont_fan_n": float(cont.get("fan", 340)),
            "speed_mm_s": float(m.get("max_speed_mm_s", 2000)),
            "standard_stroke_mm": float(m.get("standard_stroke_mm", 360)),
            "extended_stroke_mm": float(m.get("extended_stroke_mm", 510))}


def hydraulic_diameter(g: Geometry) -> float:
    w, h = g.duct_in_width, g.duct_in_height
    return 4 * w * h / (2 * (w + h))


def loss_coefficient(g: Geometry, column: int) -> float:
    """Head lost in the duct per (duct speed)^2 / 2g, est."""
    if not g.ducted:
        return K_EXIT
    return K_EXIT + K_TURN + K_CONTRACTION + FRICTION_F * g.duct_length(column) / hydraulic_diameter(g)


def row_state(g: Geometry, column: int, depth: float, seal: str = "tight", n: float = 1.4,
              piston_mm: Optional[float] = None) -> Dict:
    """One row at rest with its discs at `piston_mm` (parked by default): the air spring,
    the water's mass, and the frequency it rings at."""
    pos = g.park_mm if piston_mm is None else piston_mm
    zb = g.row_bottom(column)
    A = g.bore_area
    out = {"row": column, "bottom_m": zb, "wet": zb < depth, "duct_m": g.duct_length(column) if g.ducted else 0.0,
           "outlet_m": g.outlet_band(column) if g.ducted else (zb, zb)}
    if zb >= depth:
        out.update(air_m=g.disc_height(pos) - zb, mass_kg=0.0, k_n_per_m=0.0, f_n_hz=float("nan"), follow=0.0,
                   water_in_pipe_m=0.0, gauge_kpa=0.0)
        return out
    st = air.rest_state(g.disc_height(g.park_mm), g.disc_height(pos), zb, depth, seal, n)
    L = st["air_length_m"]
    p = P0 + 1000 * st["air_gauge_kpa"]
    k = A * (n * p / L + RHO * G)
    if g.ducted:
        M = RHO * A * st["water_column_m"] + RHO * A * A * (g.duct_length(column) + END_LENGTH) / g.duct_area
    else:
        M = RHO * A * (st["water_column_m"] + END_LENGTH)
    out.update(air_m=L, gauge_kpa=st["air_gauge_kpa"], water_in_pipe_m=st["water_column_m"], k_n_per_m=k,
               mass_kg=M, f_n_hz=math.sqrt(k / M) / (2 * math.pi), follow=st["follow"], p_pa=p)
    return out


def damping(g: Geometry, column: int, depth: float, mass: float, k: float, w_peak: float, n_wet_running: int) -> float:
    """Linear damping (N s/m on the inner surface): the duct's losses, linearised for a
    sinusoid of peak speed `w_peak` (m/s, inner surface), plus the waves the running
    outlets radiate down the channel (they act together, so each sees all of them)."""
    A = g.bore_area
    r = g.outlet_ratio if g.ducted else 1.0
    c_loss = (8 / (3 * math.pi)) * 0.5 * RHO * loss_coefficient(g, column) * r * r * A * max(w_peak, 1e-3)
    c0 = math.sqrt(G * depth)
    c_rad = max(n_wet_running, 1) * RHO * G * A * A / (g.channel_width * c0)
    return c_loss + c_rad


def response(s: np.ndarray, k: float, c: float, M: float) -> np.ndarray:
    """H(s) = k / (k + c s + M s^2): inner water displacement over its quasi-static value."""
    return k / (k + c * s + M * s * s)


def table(g: Geometry, depth: float, seal: str = "tight", piston_mm: Optional[float] = None) -> List[Dict]:
    return [row_state(g, c, depth, seal, piston_mm=piston_mm) for c in range(1, g.columns + 1)]


def max_steady_accel(g: Geometry, extra_force: float = 0.0) -> float:
    """Highest disc acceleration (m/s^2) the peak force allows with the air at rest: (F - m g - extra) / m."""
    lim = motor_limits(g)
    return max(0.0, (lim["peak_n"] - g.moving_mass * G - extra_force) / g.moving_mass)
