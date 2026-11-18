"""wavemodel/flow.py: the simulator's physics checks, and flow.js against it.

    python -m pytest -q tests        (from tank-model/)
"""

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from wavemodel import flow  # noqa: E402
from wavemodel.geometry import load  # noqa: E402

G = load()
DEPTH = 0.406
PUSH = dict(pistons=list(range(1, 13)), stroke=180.0, speed=200.0, accel=50.0, jerk=150.0, kind="push_down", start=1.0)


@pytest.fixture(scope="module")
def push():
    return flow.simulate([flow.Command(**PUSH)], DEPTH, duration=40.0, g=G)


def test_still_water_stays_still():
    sim = flow.simulate([], DEPTH, duration=20.0, g=G, flow=False)
    assert np.abs(sim["eta"]).max() < 1e-9


def test_dry_rows_move_no_water():
    # rows 7-10 (pistons 19-30) are above 406 mm of water
    sim = flow.simulate([flow.Command(pistons=list(range(19, 31)), stroke=300, speed=500)], DEPTH,
                        duration=30.0, g=G, flow=False)
    assert np.abs(sim["Qrows"]).max() == 0
    assert np.abs(sim["eta"]).max() < 1e-9


def test_volume_pushed_matches_the_discs(push):
    # tight seal: a slow push moves nearly the disc's swept volume (the air gives a few per cent)
    swept = 12 * G.bore_area * 0.180 * 1000
    out = sum(r["volume_out_l"] for r in push["rows"])
    assert 0.85 * swept < out < 1.0 * swept


def test_crest_travels_at_the_long_wave_speed(push):
    h_eff = DEPTH * flow.load_calibration()["params"]["h_scale"]
    c0 = math.sqrt(9.81 * h_eff)
    x, t, eta = push["x"], push["t"], push["eta"]
    i5, i12 = np.argmin(abs(x - 5.0)), np.argmin(abs(x - 12.0))
    first = t < 15                                      # before the echo
    t5 = t[first][np.argmax(eta[first, i5])]
    t12 = t[first][np.argmax(eta[first, i12])]
    assert abs((12.0 - 5.0) / (t12 - t5) - c0) < 0.12 * c0


def test_flux_and_surface_obey_continuity(push):
    # d eta/dt + dq/dx = 0 for the linear channel
    eta, q, dt, dx = push["eta"], push["q"], flow.DT, push["x"][1] - push["x"][0]
    lhs = np.gradient(eta, dt, axis=0)
    rhs = -np.gradient(q, dx, axis=1)
    inner = (slice(40, 400), slice(5, -5))
    assert np.abs(lhs[inner] - rhs[inner]).max() < 0.08 * np.abs(lhs[inner]).max()


def test_velocity_integrates_to_the_flux(push):
    # depth-averaged u times depth is q (same frequencies kept)
    u, z = push["u"], push["z"]
    q_from_u = u.mean(axis=1) * DEPTH
    j = np.argmin(abs(push["x"] - 5.0))
    assert np.abs(q_from_u[:, j] - push["q"][:, j]).max() < 0.05 * np.abs(push["q"][:, j]).max()


def test_the_push_down_matches_its_measured_crest(push):
    # 16 Nov 21:47: pistons 1-12 pushed down 180 mm gave about a 10 mm crest at the window
    assert 6.0 < push["metrics"]["crest_at_camera_mm"] < 14.0


def test_validation_cases_load_and_score():
    cases = json.loads((HERE / "calibration" / "validation.json").read_text())["cases"]
    assert len(cases) >= 30
    r = flow.check_case(next(c for c in cases if c["name"] == "SD_lift180_a50_d50_j150"), G)
    assert 0.0 < r["error"] < 1.5


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_flow_js_matches_flow_py(tmp_path):
    cmds = [dict(PUSH), dict(pistons=[4, 5, 6], stroke=250.0, speed=400.0, accel=10000.0, jerk=2000.0,
                             kind="single", start=3.0)]
    py = flow.simulate([flow.Command(**c) for c in cmds], DEPTH, duration=30.0, g=G)
    spec = {"commands": cmds, "depth": DEPTH, "geo": flow.viewer_geo(G), "cal": flow.load_calibration(),
            "opts": {"duration": 30.0, "dxFlow": 0.1}}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    script = tmp_path / "run.js"
    script.write_text(
        "const F = require(%s); const fs = require('fs');\n"
        "const s = JSON.parse(fs.readFileSync(%s, 'utf8'));\n"
        "const r = F.simulate(s.commands, s.depth, s.geo, s.cal, s.opts);\n"
        "fs.writeFileSync(%s, JSON.stringify({eta: Array.from(r.eta), nx: r.nx, u: Array.from(r.u),"
        " w: Array.from(r.w), disc: Array.from(r.disc), inner: Array.from(r.inner).map(v => isNaN(v) ? null : v),"
        " nz: r.z.length, nv: r.xv.length, crest: r.metrics.crest_at_camera_mm}));\n"
        % (json.dumps(str(HERE / "wavemodel" / "flow.js")), json.dumps(str(tmp_path / "spec.json")),
           json.dumps(str(tmp_path / "out.json"))))
    subprocess.run(["node", str(script)], check=True, timeout=300)
    js = json.loads((tmp_path / "out.json").read_text())
    nt = len(py["t"])
    eta = np.array(js["eta"]).reshape(nt, js["nx"])
    scale = np.abs(py["eta"]).max()
    assert np.abs(eta - py["eta"]).max() < 1e-3 * scale
    u = np.array(js["u"]).reshape(nt, js["nz"], js["nv"])
    assert np.abs(u - py["u"]).max() < 1e-3 * np.abs(py["u"]).max()
    w = np.array(js["w"]).reshape(nt, js["nz"], js["nv"])
    assert np.abs(w - py["w"]).max() < 1e-3 * np.abs(py["w"]).max()
    disc = np.array(js["disc"]).reshape(nt, 30)
    assert np.abs(disc - py["disc_mm"]).max() < 1e-3
    inner = np.array([np.nan if v is None else v for v in js["inner"]]).reshape(nt, 30)
    assert np.allclose(inner, py["inner"], atol=1e-6, equal_nan=True)
    assert abs(js["crest"] - py["metrics"]["crest_at_camera_mm"]) < 0.01
