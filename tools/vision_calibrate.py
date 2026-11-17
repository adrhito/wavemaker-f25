"""Calibrate the tank model's damping and gain on camera runs; score held-out runs.

    python tools/vision_calibrate.py --train E_bigwave_1 D_250_v400 --test D_150_v300 D_350_v300
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from vision_physics import prepared, synth, rel_error, tank  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--test", nargs="+", default=[])
    ap.add_argument("--damping", default="1,3,10")
    ap.add_argument("--head", default="0.01,0.05,0.2")
    ap.add_argument("--depth", type=float, default=0.406)
    ap.add_argument("--out", default=str(ROOT / "analytics" / "rig" / "calibration.json"))
    a = ap.parse_args()
    results = []
    for d, h in itertools.product([float(v) for v in a.damping.split(",")],
                                  [float(v) for v in a.head.split(",")]):
        hp = tank.HydroParams(channel_damping=d, head_absorption=h)
        comps = {r: synth(prepared(r, a.depth), hp) for r in a.train + a.test}
        # one gain for all training runs, least squares
        num = sum(np.nansum(c["pred"] * c["meas"]) for r, c in comps.items() if r in a.train)
        den = sum(np.nansum(c["pred"] ** 2 * ~np.isnan(c["meas"])) for r, c in comps.items() if r in a.train)
        gain = float(num / den)
        train = {r: rel_error(comps[r], gain) for r in a.train}
        test = {r: rel_error(comps[r], gain) for r in a.test}
        row = {"channel_damping": d, "head_absorption": h, "gain": gain,
               "train": train, "test": test,
               "train_mean": float(np.mean(list(train.values()))),
               "test_mean": float(np.mean(list(test.values()))) if test else None}
        results.append(row)
        print(json.dumps(row), flush=True)
    best = min(results, key=lambda r: r["train_mean"])
    Path(a.out).write_text(json.dumps({"best": best, "all": results}, indent=1))
    print("BEST", json.dumps(best))


if __name__ == "__main__":
    main()
