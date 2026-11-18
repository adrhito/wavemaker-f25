"""The water the pistons move, in time: surface, flow and the water in the pipes.

This is the simulator behind ``wm.py flow`` and the 3D viewer's Simulate
panel (``flow.js`` is the same engine for the browser, checked against this
file by ``tests/test_flow.py``).  It chains:

1. **Piston motion** (``motion.py``): the LinMot profile for each command.
   The pistons rest at the bottom of their travel (370 mm), so a ``single``
   run strokes up and back down, and ``continuous`` repeats that;
   ``push_down`` starts raised (lifted slowly beforehand and left to settle)
   and makes one down leg; ``raise`` makes one up leg from the bottom.
2. **Water out of each pipe** (``air.pipe_flux``): the air trapped under
   the disc, quasi-static, tight seal by default.  A row whose pipe bottom
   is above the still water moves only air.
3. **The channel** (``calibration/channel.json``): a linear dispersive
   channel fitted to the camera's measurements (``tools/camera/
   vision_channel.py``).  Each row's flux enters at the channel mouth with a
   fitted gain and delay; waves run at the linear dispersion speed for the
   depth, are damped, reflect partly at the far end (the gate is open, so
   the echo comes from an effective ``L`` beyond it) and partly at the head:

       eta(x, w) = S(w) [e^{-ikx} + R_e e^{-ik(2L - x)}] / (1 - R_e R_h e^{-iw tau_h} e^{-2ikL})
       S(w)      = sum_r g_r Q_r(w) e^{-iw tau_r} / (b c(w))

4. **Flow through the depth** (linear potential theory, per frequency): the
   depth-integrated flux q = c (right-going - left-going), and

       u(x, z) = q k cosh k(z + h) / sinh kh,   w(x, z) = (d eta/dt) sinh k(z + h) / sinh kh

   with z measured up from the still surface.

Why linear: the waves here are 5-30 mm on 400-830 mm of water (a/h about
0.01-0.05), where linear theory is within a few percent of the full
equations, and what limits accuracy now is the forcing (the air in the
pipes, the fitted gains) rather than the solver.  ``metrics`` warns when a
run leaves that range (a/h > 0.1).  How good the whole chain is, measured
against the camera, is in ``calibration/history.json``; it improves only as
cleaner measurements come in and the channel is refitted (``wm.py
flow-calibrate``).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import air, motion
from .geometry import Geometry, load as load_geometry
from .linear import G

HERE = Path(__file__).resolve().parent.parent
CALIBRATION = HERE / "calibration" / "channel.json"
HISTORY = HERE / "calibration" / "history.json"

FINE = 0.002           # s, piston-motion step (as motion.py and vision_physics)
DT = 0.05              # s, output and channel step
KINDS = ("single", "continuous", "push_down", "raise")
BOTTOM_MM = 370.0      # where the pistons rest
#: Velocities are reported up to this frequency; the pistons put almost
#: nothing above it, and cosh(kh) is meaningless noise there.
F_FLOW_MAX = 3.0
#: Window centre, m from the channel mouth (vision/rigs/channel_window_185in.json).
CAMERA_X = 5.18
#: A push-down's slow raise beforehand (tools/camera/vision_soliton_down.py
#: raises gently and waits for the water to settle): mm/s, and s of settling.
RAISE_MM_S = 40.0
SETTLE_S = 10.0


@dataclass
class Command:
    """One group of pistons and what they do, named as on the app's screens."""

    pistons: List[int]                 # display numbers 1-30
    stroke: float = 250.0              # mm
    speed: float = 400.0               # mm/s
    accel: float = 10000.0             # mm/s^2
    decel: Optional[float] = None      # mm/s^2, default = accel
    jerk: float = 2000.0               # mm/s^3
    kind: str = "single"
    seconds: float = 10.0              # continuous: how long it runs
    start: float = 1.0                 # s after the simulation starts
    profile: int = motion.BESTEHORN

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError("kind must be one of %s" % (KINDS,))
        if self.decel is None:
            self.decel = self.accel


def row_of(piston: int, g: Geometry) -> int:
    """William's row (1 at the head end, 10 at the channel mouth) of a display number."""
    return (piston - 1) // g.rows + 1


def disc_mm(cmd: Command, n: int, kj: float) -> np.ndarray:
    """Piston position, mm (down = larger), on the fine grid of n samples."""
    top = BOTTOM_MM - cmd.stroke
    dur = (n - 1) * FINE
    up = motion.Leg(cmd.speed, cmd.accel, cmd.decel, cmd.jerk)
    if cmd.kind == "push_down":
        p = motion.Params(position1=top, position2=BOTTOM_MM, speed2=cmd.speed, accel2=cmd.accel,
                          decel2=cmd.decel, jerk2=cmd.jerk, profile=cmd.profile)
        cycles = 0.5
    else:
        # from the bottom: up first (Position 2 here is the top), then back down
        p = motion.Params(position1=BOTTOM_MM, position2=top, speed1=up.speed, speed2=up.speed,
                          accel1=up.accel, accel2=up.accel, decel1=up.decel, decel2=up.decel,
                          jerk1=up.jerk, jerk2=up.jerk, profile=cmd.profile)
        if cmd.kind == "raise":
            cycles = 0.5
        elif cmd.kind == "single":
            cycles = 1.0
        else:
            cyc = motion.cycle_time(p, kj)
            cycles = max(1, int(round(cmd.seconds / cyc))) if cyc == cyc and cyc > 0 else 1
    tr = motion.run(p, dur, kj=kj, dt=FINE, delay=cmd.start, cycles=cycles)
    pos = tr.pos
    if len(pos) < n:
        pos = np.concatenate([pos, np.full(n - len(pos), pos[-1])])
    return pos[:n]


# ---------------------------------------------------------------------------
# calibration

def load_calibration(path: Path = CALIBRATION) -> Dict:
    cal = json.loads(Path(path).read_text())
    return cal


def row_gain_delay(cal: Dict, row: int):
    """Gain and delay for a row; rows the fit never saw take the fitted rows' mean."""
    p = cal["params"]
    fitted = cal.get("fitted_rows", [])
    if row in fitted:
        return p["g%d" % row], p["tau%d" % row], True
    gs = [p["g%d" % r] for r in fitted] or [1.0]
    return float(np.mean(gs)), 0.0, False


def wavenumbers(w, h):
    """Linear dispersion w^2 = g k tanh(k h) for every real w at once (as vision_channel)."""
    w = np.asarray(w, float)
    k = np.maximum(w / math.sqrt(G * h), 1e-12)
    k = np.maximum(k, w ** 2 / G)
    for _ in range(30):
        th = np.tanh(k * h)
        f = G * k * th - w ** 2
        df = G * th + G * k * h * (1 - th ** 2)
        k = np.maximum(k - f / df, 1e-12)
    return np.where(w > 0, k, 0.0)


def wavenumbers_complex(w, sigma, h):
    """k for the complex frequency w - i sigma (time factor e^{i w t}), the decaying branch.

    Newton from the real root shifted by -i sigma / c_g, so for w = 0 it
    starts at -i sigma / sqrt(g h): e^{-ikx} then decays along x, as a
    causal wave leaving the mouth must.
    """
    w = np.asarray(w, float)
    kr = wavenumbers(w, h)
    th = np.tanh(kr * h)
    cg = np.where(w > 0, 0.5 * (w / np.maximum(kr, 1e-12)) * (1 + 2 * kr * h / np.maximum(np.sinh(2 * kr * h), 1e-12)),
                  math.sqrt(G * h))
    del th
    W = w - 1j * sigma
    k = kr - 1j * sigma / cg
    for _ in range(30):
        th = np.tanh(k * h)
        f = G * k * th - W ** 2
        df = G * th + G * k * h * (1 - th ** 2)
        k = k - f / df
    return k


# ---------------------------------------------------------------------------
# the simulation

#: Exponential window: forcing weighted by e^{-sigma t} with sigma * window =
#: this, so a level change that never comes back (the net volume pushed) is
#: represented instead of being folded round by the FFT (as predict.py).
SIGMA_DECADES = 6.9


def piston_flux(commands: Sequence[Command], depth: float, nt: int, g: Geometry, seal: str = "tight"):
    """Disc positions [30, nt] (mm), pipe water levels [nt, 30] (m) and row flux [10, nt] (m^3/s)."""
    kj = g.bestehorn_jerk_factor
    step = int(round(DT / FINE))
    nf = (nt - 1) * step + 1
    npist = g.rows * g.columns
    zd_park = g.disc_height(g.park_mm)
    pos = np.full((npist, nf), float(g.park_mm))
    tf = np.arange(nf) * FINE
    # Commands for the same piston follow one another: each takes over from
    # its own start (a push-down from its slow raise before that).
    for cmd in sorted(commands, key=lambda c: c.start):
        traj = disc_mm(cmd, nf, kj)
        i0 = int(round(cmd.start / FINE))
        if cmd.kind == "push_down":
            # raised slowly (RAISE_MM_S) and left to settle (SETTLE_S) before the push
            t_r = cmd.stroke / RAISE_MM_S
            r1 = cmd.start - SETTLE_S
            r0 = r1 - t_r
            ramp = np.clip((tf - r0) / t_r, 0.0, 1.0)
            traj = np.where(tf < cmd.start, BOTTOM_MM - cmd.stroke * (1 - np.cos(np.pi * ramp)) / 2, traj)
            i0 = max(0, int(round(r0 / FINE)))
        for pn in cmd.pistons:
            if not 1 <= pn <= npist:
                raise ValueError("piston %r is not 1-%d" % (pn, npist))
            pos[pn - 1, i0:] = traj[i0:]
    Qp = np.zeros((npist, nt))
    inner = np.full((nt, npist), np.nan)
    for i in range(npist):
        zb = g.row_bottom(row_of(i + 1, g))
        if zb >= depth:
            continue
        zd = g.disc_height(pos[i])
        # the flux of interval j is placed at its start, as vision_physics does
        q, info = air.pipe_flux(zd, zb, depth, g.bore_area, FINE, step, nt - 1, zd_park, seal)
        Qp[i, :-1] = q
        inner[:, i] = info["inner_start_m"] - np.concatenate([[0.0], np.cumsum(q)]) * DT / g.bore_area
    Qrows = np.array([Qp[r * g.rows:(r + 1) * g.rows].sum(axis=0) for r in range(g.columns)])
    idx = np.minimum(np.arange(nt) * step, nf - 1)
    return pos[:, idx], inner, Qrows


def channel(Qrows: np.ndarray, depth: float, x: np.ndarray, cal: Dict, width: float,
            z: Optional[np.ndarray] = None) -> Dict:
    """Surface, flux per width and (with ``z``) velocities along the channel, from each row's flux."""
    p = cal["params"]
    nt = Qrows.shape[1]
    nfft = 1 << int(math.ceil(math.log2(nt + int(300.0 / DT))))
    sigma = SIGMA_DECADES / (nfft * DT)
    t = np.arange(nfft) * DT
    wv = 2 * math.pi * np.fft.rfftfreq(nfft, DT)
    W = wv - 1j * sigma
    h_eff = depth * p.get("h_scale", 1.0)
    k = wavenumbers_complex(wv, sigma, h_eff)
    c = W / k
    delta = math.exp(p["log_d0"]) + math.exp(p["log_d1"]) * wv + math.exp(p["log_d2"]) * wv ** 2
    kc = k - 1j * delta
    weight = np.exp(-sigma * t[:nt])
    S = np.zeros(len(wv), complex)
    for r in range(Qrows.shape[0]):
        if not np.any(Qrows[r]):
            continue
        gain, tau, _ = row_gain_delay(cal, r + 1)
        S += gain * np.fft.rfft(Qrows[r] * weight, nfft) * np.exp(-1j * W * tau)
    S /= width * c
    L, Re, Rh = p["L"], p["Re"], p["Rh"]
    loop = 1.0 - Re * Rh * np.exp(-1j * W * p["tau_h"]) * np.exp(-2j * kc * L)
    right = np.exp(-1j * np.outer(x, kc)) / loop                      # [nx, nw]
    left = Re * np.exp(-1j * np.outer(2 * L - x, kc)) / loop
    etaF = S * (right + left)
    qF = S * c * (right - left)
    back = np.exp(sigma * t[:nt])

    def inv(F):
        return (np.fft.irfft(F, nfft, axis=-1)[..., :nt] * back)

    out = {"eta": inv(etaF).T, "q": inv(qF).T}
    if z is not None:
        keep = wv <= 2 * math.pi * F_FLOW_MAX
        detadt = 1j * W * etaF
        U = np.zeros((nt, len(z), len(x)))
        Wz = np.zeros_like(U)
        e2 = np.exp(-2 * k * depth)
        for j, zj in enumerate(z):
            # cosh k(z+h)/sinh kh and sinh k(z+h)/sinh kh without overflow
            a, b = np.exp(k * zj), np.exp(-k * (zj + 2 * depth))
            pu = np.where(keep, k * (a + b) / (1 - e2), 0.0)
            pw = np.where(keep, (a - b) / (1 - e2), 0.0)
            pu[0] = 1.0 / depth if keep[0] else 0.0          # the long-wave limit at w -> 0
            pw[0] = (zj + depth) / depth if keep[0] else 0.0
            U[:, j] = inv(qF * pu).T
            Wz[:, j] = inv(detadt * pw).T
        out.update(u=U, w=Wz)
    return out


def simulate(commands: Sequence[Command], depth: float, duration: float = 60.0,
             g: Optional[Geometry] = None, cal: Optional[Dict] = None, seal: str = "tight",
             dx: float = 0.1, z_levels: int = 6, flow: bool = True) -> Dict:
    """Everything the viewer and the CLI show, on a common time grid.

    Returns arrays (SI units unless named): ``t`` [nt]; ``x`` [nx] from the
    channel mouth; ``eta`` [nt, nx] surface, m; ``q`` [nt, nx] flux per
    width, m^2/s; ``z`` [nz] below the still surface; ``u``, ``w`` [nt, nz,
    nx] when ``flow``; ``disc_mm`` [nt, 30]; ``inner`` (pipe water level, m
    above the floor, NaN for a dry pipe) [nt, 30]; ``Qrows`` [10, nt],
    m^3/s; plus ``rows`` (per-row facts) and ``metrics``.
    """
    g = g or load_geometry()
    cal = cal or load_calibration()
    nt = int(round(duration / DT)) + 1
    t = np.arange(nt) * DT
    pos, inner, Qrows = piston_flux(commands, depth, nt, g, seal)
    x = np.arange(0.0, g.channel_length + dx / 2, dx)
    z = -depth * (np.arange(z_levels) + 0.5) / z_levels if flow else None
    ch = channel(Qrows, depth, x, cal, g.channel_width, z)
    rows_info = []
    for r in range(g.columns):
        gain, tau, fitted = row_gain_delay(cal, r + 1)
        zb = g.row_bottom(r + 1)
        rows_info.append({"row": r + 1, "pistons": [r * g.rows + j + 1 for j in range(g.rows)],
                          "bottom_m": zb, "wet": zb < depth, "gain": gain, "delay_s": tau,
                          "fitted": fitted, "volume_out_l": float(np.max(np.cumsum(Qrows[r]) * DT) * 1000)})
    out = {"t": t, "x": x, "disc_mm": pos.T, "inner": inner, "Qrows": Qrows,
           "rows": rows_info, "depth": depth, "seal": seal}
    out.update(ch)
    if z is not None:
        out["z"] = z
    out["metrics"] = metrics(out)
    return out


def metrics(sim: Dict) -> Dict:
    t, x, eta, h = sim["t"], sim["x"], sim["eta"], sim["depth"]
    j = int(np.argmin(np.abs(x - CAMERA_X)))
    at = eta[:, j]
    i_pk = int(np.argmax(at))
    first = np.nonzero(np.abs(at) > 0.1 * max(np.abs(at).max(), 1e-12))[0]
    peak = float(np.abs(eta).max())
    out = {
        "camera_x_m": float(x[j]),
        "crest_at_camera_mm": float(at.max() * 1000),
        "trough_at_camera_mm": float(at.min() * 1000),
        "crest_time_s": float(t[i_pk]),
        "arrival_at_camera_s": float(t[first[0]]) if len(first) else None,
        "largest_anywhere_mm": peak * 1000,
        "a_over_h": peak / h,
        "wet_rows": [r["row"] for r in sim["rows"] if r["wet"]],
        "moving_rows": [r["row"] for r in sim["rows"] if r["volume_out_l"] > 0.01],
    }
    warn = []
    if out["a_over_h"] > 0.1:
        warn.append("waves over a tenth of the depth: linear theory underestimates crests and steepening")
    unfit = [r["row"] for r in sim["rows"] if not r["fitted"] and r["volume_out_l"] > 0.01]
    if unfit:
        warn.append("rows %s have no camera fit yet; their gain is the fitted rows' mean" % unfit)
    if any(r["volume_out_l"] > 0.01 for r in sim["rows"]) is False and any(
            r["wet"] for r in sim["rows"]):
        warn.append("the selected pistons are all in dry rows: they move only air")
    out["warnings"] = warn
    return out


# ---------------------------------------------------------------------------
# checking against the camera

def command_from_run(run: Dict, start: float) -> List[Command]:
    """Commands for one logged run (analytics/rig/runs.jsonl's ``run``)."""
    from .geometry import parse_pistons
    kind = run.get("kind")
    if kind in ("soliton_down", "pulse_down") or "lift_mm" in run or "lift" in run:
        lift = run.get("lift_mm", run.get("lift"))
        return [Command(pistons=parse_pistons(str(run["pistons"])), stroke=float(lift),
                        speed=float(run.get("speed", 200)), accel=float(run["accel"]),
                        decel=float(run.get("decel", run["accel"])), jerk=float(run["jerk"]),
                        kind="push_down", start=start)]
    cont = kind == "continuous" or "seconds" in run
    out = []
    for grp in run["groups"]:
        out.append(Command(pistons=parse_pistons(str(grp["pistons"])), stroke=float(grp["stroke"]),
                           speed=float(grp["speed"]), accel=float(grp["accel"]),
                           decel=float(grp.get("decel", grp["accel"])), jerk=float(grp.get("jerk", 2000)),
                           kind="continuous" if cont else "single", seconds=float(run.get("seconds", 10)),
                           start=start))
    return out


def relative_error(pred: np.ndarray, meas: np.ndarray) -> float:
    """RMS of (prediction - measurement) over RMS of the measurement (the handoff's measure)."""
    ok = ~np.isnan(meas) & ~np.isnan(pred)
    m, e = meas[ok], pred[ok]
    return float(np.sqrt(np.mean((e - m) ** 2)) / max(np.sqrt(np.mean(m ** 2)), 1e-12))


def _cmds(spec, start):
    out = []
    for c in spec:
        c = dict(c)
        c["start"] = start
        out.append(Command(**c))
    return out


def prepare_case(case: Dict, g: Geometry) -> Dict:
    """Row flux for one validation case and every run before it, on the simulator's grid.

    The case's clock has 0 at the moment the run was sent; the simulation
    starts ``lead`` seconds earlier so earlier runs' leftover waves are in it.
    """
    starts = [h["start_s"] for h in case.get("history", [])] + [case["start_s"]]
    lead = 8.0 + max(0.0, -min(starts))
    t_m = np.asarray(case["t"], float)
    nt = int(round((t_m[-1] + lead + 1) / DT)) + 1
    cmds = _cmds(case["commands"], case["start_s"] + lead)
    for h in case.get("history", []):
        cmds += _cmds(h["commands"], h["start_s"] + lead)
    _, _, Q = piston_flux(cmds, case["depth_m"], nt, g)
    meas = np.array([np.nan if v is None else v for v in case["eta_mm"]], float)
    win = (t_m >= -1.0) & (t_m <= case.get("end_s", 40.0)) & ~np.isnan(meas)
    return {"name": case["name"], "kind": case["kind"], "Q": Q, "lead": lead, "t": t_m,
            "meas": meas, "win": win, "x": np.array([case["x_m"]]), "depth": case["depth_m"]}


_K_CACHE: Dict = {}


def _spectra(pc: Dict):
    """The case's weighted row-flux spectra, computed once."""
    if "QF" not in pc:
        Q = pc["Q"]
        nt = Q.shape[1]
        nfft = 1 << int(math.ceil(math.log2(nt + int(300.0 / DT))))
        sigma = SIGMA_DECADES / (nfft * DT)
        weight = np.exp(-sigma * np.arange(nt) * DT)
        pc["nfft"], pc["sigma"] = nfft, sigma
        pc["QF"] = {r: np.fft.rfft(Q[r] * weight, nfft) for r in range(Q.shape[0]) if np.any(Q[r])}
    return pc["QF"], pc["nfft"], pc["sigma"]


def predict_case(pc: Dict, cal: Dict, width: float) -> np.ndarray:
    """Surface at the case's station, mm, on the case's clock (``channel`` at one point, faster)."""
    QF, nfft, sigma = _spectra(pc)
    p = cal["params"]
    nt = pc["Q"].shape[1]
    wv = 2 * math.pi * np.fft.rfftfreq(nfft, DT)
    W = wv - 1j * sigma
    h_eff = pc["depth"] * p.get("h_scale", 1.0)
    key = (nfft, round(h_eff, 12))
    if key not in _K_CACHE:
        if len(_K_CACHE) > 64:
            _K_CACHE.clear()
        _K_CACHE[key] = wavenumbers_complex(wv, sigma, h_eff)
    k = _K_CACHE[key]
    c = W / k
    delta = math.exp(p["log_d0"]) + math.exp(p["log_d1"]) * wv + math.exp(p["log_d2"]) * wv ** 2
    kc = k - 1j * delta
    S = np.zeros(len(wv), complex)
    for r, F in QF.items():
        gain, tau, _ = row_gain_delay(cal, r + 1)
        S += gain * F * np.exp(-1j * W * tau)
    S /= width * c
    L, Re, Rh = p["L"], p["Re"], p["Rh"]
    loop = 1.0 - Re * Rh * np.exp(-1j * W * p["tau_h"]) * np.exp(-2j * kc * L)
    xi = float(pc["x"][0])
    H = (np.exp(-1j * kc * xi) + Re * np.exp(-1j * kc * (2 * L - xi))) / loop
    eta = np.fft.irfft(S * H, nfft)[:nt] * np.exp(sigma * np.arange(nt) * DT)
    tt = np.arange(nt) * DT - pc["lead"]
    return np.interp(pc["t"], tt, eta) * 1000


def check_case(case: Dict, g: Optional[Geometry] = None, cal: Optional[Dict] = None) -> Dict:
    """Simulate one validation case from its commands and score it at the camera."""
    g = g or load_geometry()
    cal = cal or load_calibration()
    pc = prepare_case(case, g)
    pred = predict_case(pc, cal, g.channel_width)
    w = pc["win"]
    return {"name": case["name"], "kind": case["kind"], "error": relative_error(pred[w], pc["meas"][w]),
            "rms_mm": float(np.sqrt(np.mean(pc["meas"][w] ** 2))), "pred_mm": pred, "meas_mm": pc["meas"],
            "t": pc["t"]}


#: Channel parameters a refit may move, with their bounds (as vision_channel.fit).
FIT_BOUNDS = {"L": (17.0, 40.0), "Re": (-1.0, 1.0), "Rh": (-1.0, 1.0), "tau_h": (-5.0, 20.0),
              "h_scale": (0.7, 1.4), "log_d0": (-12.0, 1.0), "log_d1": (-12.0, 1.0), "log_d2": (-12.0, 1.0)}


def fit_channel(prepared: List[Dict], cal: Dict, width: float, rows: Sequence[int] = (1, 2, 3, 4),
                max_nfev: int = 60) -> Dict:
    """Refit the channel's parameters to the cases, by least squares; returns a new calibration."""
    from scipy.optimize import least_squares
    p0 = dict(cal["params"])
    free = ["g%d" % r for r in rows] + ["tau%d" % r for r in rows] + list(FIT_BOUNDS)
    bounds = dict(FIT_BOUNDS)
    for r in rows:
        bounds["g%d" % r] = (-5.0, 5.0)      # gain of row r's flux
        bounds["tau%d" % r] = (-2.0, 2.0)    # its delay, s
    lo = [bounds[n][0] for n in free]
    hi = [bounds[n][1] for n in free]
    v0 = np.clip([p0[n] for n in free], np.array(lo) + 1e-9, np.array(hi) - 1e-9)
    trial = dict(cal, fitted_rows=list(rows))

    def resid(v):
        trial["params"] = dict(p0, **dict(zip(free, v)))
        out = []
        for pc in prepared:
            m = pc["meas"][pc["win"]]
            out.append((predict_case(pc, trial, width)[pc["win"]] - m) / (np.sqrt(np.mean(m ** 2)) + 1.0))
        return np.concatenate(out)

    res = least_squares(resid, v0, bounds=(lo, hi), x_scale="jac", max_nfev=max_nfev)
    new = dict(cal, fitted_rows=list(rows))
    new["params"] = dict(p0, **{n: float(v) for n, v in zip(free, res.x)})
    return new


def score(prepared: List[Dict], cal: Dict, width: float) -> List[Dict]:
    out = []
    for pc in prepared:
        pred = predict_case(pc, cal, width)
        w = pc["win"]
        out.append({"name": pc["name"], "kind": pc["kind"], "error": relative_error(pred[w], pc["meas"][w])})
    return out


def held_out(prepared: List[Dict], cal: Dict, width: float, folds: int = 5, **kw) -> List[Dict]:
    """K-fold: fit on the other folds, score each case it never saw."""
    out = []
    for f in range(folds):
        test = prepared[f::folds]
        train = [pc for i, pc in enumerate(prepared) if i % folds != f]
        fitted = fit_channel(train, cal, width, **kw)
        out += score(test, fitted, width)
    return out


def viewer_geo(g: Geometry) -> Dict:
    """What flow.js needs to know about the tank."""
    return {"rows": g.rows, "columns": g.columns, "row_bottom": [g.row_bottom(r) for r in range(1, g.columns + 1)],
            "disc_zero": g.disc_zero, "park_mm": g.park_mm, "bore_area": g.bore_area,
            "channel_width": g.channel_width, "channel_length": g.channel_length, "kj": g.bestehorn_jerk_factor}


def as_json(sim: Dict) -> Dict:
    """The simulation as plain lists (for --json and the tests)."""
    out = {}
    for k, v in sim.items():
        out[k] = v.tolist() if isinstance(v, np.ndarray) else v
    return out


__all__ = ["Command", "simulate", "metrics", "load_calibration", "check_case", "command_from_run",
           "KINDS", "CAMERA_X", "asdict", "field"]
