"""The waterline-edge reader and the piston move model, on drawn pictures."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from vision import glassline, wavefit  # noqa: E402


def _picture(edge_rows, height=200, bright=90.0, dark=32.0, blur=1.2, noise=1.3,
             seed=0):
    """Bright above, dark below, the edge at a sub-pixel row per column."""
    rng = np.random.default_rng(seed)
    y = np.arange(height)[:, None]
    # A soft step whose half-way point is at edge_rows (row centres at y).
    step = 1.0 / (1.0 + np.exp((y - np.asarray(edge_rows)[None, :]) / blur * 1.7))
    img = dark + (bright - dark) * step + rng.normal(0, noise, (height, len(edge_rows)))
    return img.astype(np.float32)


def test_edge_rows_finds_a_subpixel_waterline():
    truth = 100 + 6 * np.sin(np.linspace(0, 3, 400))
    rows = glassline.edge_rows(_picture(truth), 40, 160)
    assert np.nanmax(np.abs(rows - truth)) < 0.35


def test_edge_rows_binning_and_weak_edges():
    truth = np.full(400, 80.3)
    img = _picture(truth)
    img[:, 200:] = 60.0  # no edge at all in the right half
    rows = glassline.edge_rows(img, 40, 160, bin_px=10)
    assert np.all(np.isnan(rows[20:]))
    assert np.nanmax(np.abs(rows[:20] - 80.3)) < 0.2


def test_tracking_ignores_a_stronger_edge_far_away():
    truth = np.full(100, 100.0)
    first = _picture(truth)
    later = _picture(truth + 2)
    later[50:52, 30:60] = 255  # a glint: bright line, dark below it
    out = glassline.edges([first, later], 20, 180, track=8)
    assert np.nanmax(np.abs(out[1] - 102.0)) < 0.5


def test_scale_from_tapes():
    scale = glassline.Scale.from_tapes([243.0, 397.8, 552.6], still_row=600.0)
    assert scale.mm_per_px == pytest.approx(152.4 / 154.8)
    assert scale.height_mm(590.0) == pytest.approx(10 * 152.4 / 154.8)


def test_scurve_reaches_length_and_is_monotone():
    t = np.linspace(0, 3, 3001)
    d = wavefit.scurve(t, 0.5, 150, 300, 2000, 0.2)
    assert d[0] == pytest.approx(0) and d[-1] == pytest.approx(150)
    assert np.all(np.diff(d) >= -1e-9)


def test_kernel_fit_recovers_a_known_response():
    t = np.arange(-5, 50, wavefit.DT) + wavefit.DT / 2
    true_h = np.exp(-((np.arange(200) * wavefit.DT - 6) / 1.0) ** 2) * 0.05
    probes = []
    rng = np.random.default_rng(3)
    for k in range(6):
        d1 = wavefit._stroke(t, (1 + k * 0.3, 2 + k * 0.4), 50 + 20 * k, 300, 2000, 0.2)
        u = wavefit.velocity(d1)
        eta = np.convolve(u, true_h)[:len(t)][:, None] + rng.normal(0, 0.02, (len(t), 1))
        probes.append(wavefit.Probe(None, {}, t, eta, np.array([500.0]), {1: d1}))
    model = wavefit.fit(probes[:5], {"all": [1]}, taps=200, ridge=1e-3, smooth=1e-3)
    assert wavefit.score(model, probes[5])["rel_rms"] < 0.03


def test_tape_edges_are_never_taken_for_the_waterline():
    truth = np.full(200, 120.0)
    img = _picture(truth)
    img[100:106, 50:150] = 20.0  # an opaque dark tape just above the water
    bgr = np.repeat(img[..., None], 3, axis=2).astype(np.float32)
    bgr[100:106, 50:150] = (40, 40, 200)  # red
    forbid = glassline.tape_mask(bgr, grow=2)
    last = np.full(200, 104.0)  # as if the tracker had been pulled onto the tape
    rows = glassline.edge_rows_near(img, 20, 180, last, track=25, forbid=forbid)
    assert np.nanmax(np.abs(rows - 120.0)) < 0.5
