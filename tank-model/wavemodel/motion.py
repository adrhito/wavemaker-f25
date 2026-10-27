"""Piston motion from the controller's parameters.

The LinMot drives move each piston between Position 1 and Position 2. A move
to Position k uses Speed k, Accel k, Decel k and Jerk k, and is followed by a
dwell of Time k (ms). A continuous run starts at Position 1 and goes to
Position 2 first. Positions are mm with -20 the top of the travel and 370 the
bottom, so a piston moving down (position increasing) pushes water out of its
cylinder into the tank.

Profiles (app/params.py): 0 Trapezoidal, 1 Bestehorn, 2 S-curve, 3 Sine.

* Trapezoidal: constant acceleration, cruise, constant deceleration.
* S-curve: linear jerk ramps (7-segment), jerk limit J.
* Bestehorn: sin^2-shaped acceleration (sinusoidal jerk). Its effective jerk
  is J times a factor fitted to the one measured cycle time we have
  (BIGGER WAVE.csv: 0-350 mm, 500 mm/s, 10000 mm/s^2, jerk 2000 ->
  3.834 s per cycle); see geometry.json pistons.bestehorn_jerk_factor.
* Sine: half-cosine position between the end points, as slow as the speed,
  acceleration and jerk limits require.

With the default jerk of 2000 mm/s^3 the pistons rarely reach the commanded
speed: a 350 mm leg peaks near 365 mm/s. The model reports the speed actually
reached.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import numpy as np

TRAPEZOIDAL, BESTEHORN, S_CURVE, SINE = 0, 1, 2, 3
PROFILE_NAMES = {0: "Trapezoidal", 1: "Bestehorn", 2: "S-curve", 3: "Sine"}


@dataclass
class Leg:
    speed: float = 500.0     # mm/s
    accel: float = 10000.0   # mm/s^2
    decel: float = 10000.0   # mm/s^2
    jerk: float = 2000.0     # mm/s^3
    dwell_ms: float = 0.0


@dataclass
class Params:
    """One piston's settings, named as on the application's screens."""

    position1: float = 0.0
    position2: float = 350.0
    speed1: float = 500.0
    speed2: float = 500.0
    accel1: float = 10000.0
    accel2: float = 10000.0
    decel1: float = 10000.0
    decel2: float = 10000.0
    jerk1: float = 2000.0
    jerk2: float = 2000.0
    time1: float = 0.0
    time2: float = 0.0
    profile: int = BESTEHORN

    def leg_to(self, k: int) -> Leg:
        if k == 1:
            return Leg(self.speed1, self.accel1, self.decel1, self.jerk1, self.time1)
        return Leg(self.speed2, self.accel2, self.decel2, self.jerk2, self.time2)

    @property
    def stroke(self) -> float:
        return abs(self.position2 - self.position1)

    @classmethod
    def simple(cls, stroke_mm: float, speed: float, accel: float = 10000.0, jerk: float = 2000.0,
               profile: int = BESTEHORN, bottom: float = 370.0) -> "Params":
        """A symmetric stroke built up from the bottom of the travel, as the Wave tab does."""
        return cls(position1=bottom - stroke_mm, position2=bottom, speed1=speed, speed2=speed,
                   accel1=accel, accel2=accel, decel1=accel, decel2=accel,
                   jerk1=jerk, jerk2=jerk, profile=profile)


# ---------------------------------------------------------------------------
# One point-to-point move

def _ramp(v_c: float, a_max: float, jerk: float, profile: int, kj: float) -> Tuple[float, object]:
    """Time to change speed by v_c, and v(t) for 0 <= t <= T, for one ramp."""
    if v_c <= 0:
        return 0.0, lambda t: np.zeros_like(t)
    if profile == TRAPEZOIDAL or jerk <= 0:
        T = v_c / a_max
        return T, lambda t: a_max * t
    if profile == BESTEHORN:
        J = jerk * kj
        A = min(a_max, math.sqrt(2.0 * J * v_c / math.pi))
        T = 2.0 * v_c / A
        return T, lambda t: A * (t / 2.0 - T * np.sin(2 * math.pi * t / T) / (4 * math.pi))
    # S-curve, linear jerk
    J = jerk
    if v_c <= a_max ** 2 / J:
        Tj = math.sqrt(v_c / J)
        T = 2 * Tj

        def v(t):
            t = np.asarray(t, float)
            return np.where(t < Tj, 0.5 * J * t ** 2, v_c - 0.5 * J * (T - t) ** 2)
        return T, v
    Tj = a_max / J
    Tc = v_c / a_max - Tj
    T = 2 * Tj + Tc

    def v(t):
        t = np.asarray(t, float)
        return np.where(t < Tj, 0.5 * J * t ** 2,
                        np.where(t < Tj + Tc, 0.5 * J * Tj ** 2 + a_max * (t - Tj),
                                 v_c - 0.5 * J * (T - t) ** 2))
    return T, v


def move(distance: float, leg: Leg, profile: int, kj: float = 1.0, dt: float = 0.002):
    """A rest-to-rest move of `distance` mm. Returns (t, x, v, info)."""
    d = abs(float(distance))
    if d <= 1e-9:
        t = np.array([0.0])
        return t, np.zeros(1), np.zeros(1), {"time": 0.0, "peak_speed": 0.0}
    v_set = max(leg.speed, 1e-6)

    if profile == SINE:
        J = max(leg.jerk * kj, 1e-6) if leg.jerk > 0 else float("inf")
        T = max(math.pi * d / (2 * v_set),
                math.pi * math.sqrt(d / (2 * max(min(leg.accel, leg.decel), 1e-6))),
                (math.pi ** 3 * d / (2 * J)) ** (1 / 3) if J != float("inf") else 0.0)
        t = np.arange(0.0, T + dt / 2, dt)
        t[-1] = T
        x = d * (1 - np.cos(math.pi * t / T)) / 2
        v = d * math.pi / (2 * T) * np.sin(math.pi * t / T)
        return t, x, v, {"time": T, "peak_speed": float(v.max())}

    def dist(vc):
        Ta, _ = _ramp(vc, leg.accel, leg.jerk, profile, kj)
        Td, _ = _ramp(vc, leg.decel, leg.jerk, profile, kj)
        return vc * (Ta + Td) / 2.0

    if dist(v_set) <= d:
        v_c = v_set
    else:
        lo, hi = 0.0, v_set
        for _ in range(80):
            mid = (lo + hi) / 2
            if dist(mid) < d:
                lo = mid
            else:
                hi = mid
        v_c = (lo + hi) / 2
    Ta, va = _ramp(v_c, leg.accel, leg.jerk, profile, kj)
    Td, vd = _ramp(v_c, leg.decel, leg.jerk, profile, kj)
    Tc = max(0.0, (d - v_c * (Ta + Td) / 2.0) / v_c)
    T = Ta + Tc + Td
    t = np.arange(0.0, T + dt / 2, dt)
    v = np.where(t < Ta, va(np.minimum(t, Ta)),
                 np.where(t < Ta + Tc, v_c, v_c - vd(np.clip(t - Ta - Tc, 0, Td))))
    v = np.clip(v, 0, None)
    x = np.concatenate([[0.0], np.cumsum((v[1:] + v[:-1]) / 2 * np.diff(t))])
    if x[-1] > 0:
        scale = d / x[-1]
        x, v = x * scale, v * scale
    return t, x, v, {"time": T, "peak_speed": float(v_c)}


# ---------------------------------------------------------------------------
# Runs

@dataclass
class Trajectory:
    t: np.ndarray            # s
    pos: np.ndarray          # mm, controller convention (down = larger)
    vel: np.ndarray          # mm/s, positive down
    cycle_time: float
    peak_speed: float
    legs: List[Dict] = field(default_factory=list)


def run(p: Params, duration: float, kj: float = 1.249, dt: float = 0.002,
        delay: float = 0.0, cycles: Optional[float] = None, finish_leg: bool = True) -> Trajectory:
    """Continuous run: rest at Position 1 until `delay`, then cycle 1 -> 2 -> 1 ...

    Stops after `cycles` full cycles if given, otherwise when `duration` ends
    (finishing the leg in progress, as the real drives do).
    """
    n = int(round(duration / dt)) + 1
    t = np.arange(n) * dt
    pos = np.full(n, float(p.position1))
    vel = np.zeros(n)
    k_from, k_to = 1, 2
    now = delay
    done_legs = 0
    legs = []
    peak = 0.0
    max_legs = int(round(2 * cycles)) if cycles is not None else 10 ** 9
    while now < duration and done_legs < max_legs:
        a = p.position1 if k_from == 1 else p.position2
        b = p.position2 if k_to == 2 else p.position1
        leg = p.leg_to(k_to)
        lt, lx, lv, info = move(b - a, leg, p.profile, kj, dt)
        sign = 1.0 if b >= a else -1.0
        i0 = int(round(now / dt))
        i1 = min(n, i0 + len(lt))
        m = i1 - i0
        if m > 0:
            pos[i0:i1] = a + sign * lx[:m]
            vel[i0:i1] = sign * lv[:m]
        end = now + info["time"]
        dwell = leg.dwell_ms / 1000.0
        j0 = min(n, int(round(end / dt)))
        j1 = min(n, int(round((end + dwell) / dt)) + 1)
        pos[j0:] = b
        vel[j0:] = 0.0
        legs.append({"to": k_to, "start": now, "time": info["time"], "peak_speed": info["peak_speed"]})
        peak = max(peak, info["peak_speed"])
        now = end + dwell
        done_legs += 1
        k_from, k_to = k_to, k_from
        if not finish_leg and now >= duration:
            break
    cycle = sum(l["time"] for l in legs[:2]) + (p.time1 + p.time2) / 1000.0 if len(legs) >= 2 else float("nan")
    return Trajectory(t, pos, vel, cycle, peak, legs)


def cycle_time(p: Params, kj: float = 1.249) -> float:
    """Seconds per full cycle (Position 1 -> 2 -> 1) including dwells."""
    t1 = move(p.stroke, p.leg_to(2), p.profile, kj)[3]["time"]
    t2 = move(p.stroke, p.leg_to(1), p.profile, kj)[3]["time"]
    return t1 + t2 + (p.time1 + p.time2) / 1000.0


def fit_bestehorn_factor(measured_cycle: float = 3.834) -> float:
    """The jerk factor that reproduces a measured Bestehorn cycle (BIGGER WAVE.csv)."""
    p = Params(0, 350, 500, 500, 10000, 10000, 10000, 10000, 2000, 2000, 0, 0, BESTEHORN)
    lo, hi = 0.3, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if cycle_time(p, mid) > measured_cycle:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
