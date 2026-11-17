"""Row gain and shift fitting in tools/camera/vision_eval.py, on synthetic data."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "camera"))
vision_eval = pytest.importorskip("vision_eval")


def _pulse(t, at, width, sign=1.0):
    return sign * np.exp(-((t - at) / width) ** 2) * np.sin(2.5 * (t - at))


def _runs(true_g, true_tau, n_runs=4, rows=4, stations=3, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    tm = np.arange(-20, 60, 0.04)
    t = np.arange(-1, 40, 0.1)
    runs = []
    for k in range(n_runs):
        R = np.zeros((rows, stations, len(tm)))
        for r in range(rows - 1):  # the last row never moves
            for s in range(stations):
                amp = rng.uniform(0.5, 2.0)
                R[r, s] = amp * _pulse(tm, 3 + 2 * r + 3 * s + k, 1.0 + 0.3 * r)
        run = {"tm": tm, "rows": R, "t": t, "meas": np.zeros((len(t), stations))}
        run["meas"] = vision_eval.predict(run, true_g, true_tau) + noise * rng.standard_normal((len(t), stations))
        runs.append(run)
    return runs


def test_recovers_row_gains_and_shifts():
    g = np.array([0.6, 1.4, 0.9, 1.0])
    tau = np.array([0.32, -0.5, 0.0, 0.0])
    runs = _runs(g, tau, noise=0.01)
    fg, ft = vision_eval.fit_rows(runs, rows=[0, 1, 2])
    assert np.allclose(fg[:3], g[:3], atol=0.03)
    assert np.allclose(ft[:3], tau[:3], atol=0.02)
    # The still row keeps its defaults.
    assert fg[3] == 1.0 and ft[3] == 0.0
    err = np.mean([vision_eval.rel_err(vision_eval.predict(r, fg, ft), r["meas"]) for r in runs])
    assert err < 0.05


def test_shift_is_bounded():
    g = np.array([1.0, 1.0, 1.0, 1.0])
    tau = np.array([1.2, 0.0, 0.0, 0.0])
    runs = _runs(g, tau)
    fg, ft = vision_eval.fit_rows(runs, rows=[0, 1, 2], max_shift=0.5)
    assert np.all(np.abs(ft) <= 0.5 + 1e-9)


def test_global_gain_and_error():
    runs = _runs(np.full(4, 0.5), np.zeros(4))
    G = vision_eval.fit_global_gain(runs)
    assert G == pytest.approx(0.5, rel=1e-6)
    assert vision_eval.rel_err(vision_eval.plain(runs[0]) * G, runs[0]["meas"]) < 1e-6


def test_kind_names():
    assert vision_eval.kind_of({"kind": "soliton_down"}) == "pulse_down"
    assert vision_eval.kind_of({"run": {"groups": [{}]}}) == "single"
    assert vision_eval.kind_of({"kind": "continuous"}) == "continuous"
    assert vision_eval.kind_of({"kind": "weird"}) == "other"
