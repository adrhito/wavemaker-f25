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
    ap.add_argument("--head", default="0.01")
    ap.add_argument("--lengths", default="17.77",
                    help="channel lengths, m (gate open: the reservoir adds about 5.6 m)")
    ap.add_argument("--depth", type=float, default=0.406)
    ap.add_argument("--out", default=str(ROOT / "analytics" / "rig" / "calibration.json"))
    a = ap.parse_args()
    results = []
    log = open(a.out.replace(".json", ".log"), "a")
    for L, d, h in itertools.product([float(v) for v in a.lengths.split(",")],
                                     [float(v) for v in a.damping.split(",")],
                                     [float(v) for v in a.head.split(",")]):
        hp = tank.HydroParams(channel_damping=d, head_absorption=h)
        comps = {r: synth(prepared(r, a.depth, before=10, after=60), hp, channel_length=L)
                 for r in a.train + a.test}
        # one gain for all training runs, least squares
        num = sum(np.nansum(c["pred"] * c["meas"]) for r, c in comps.items() if r in a.train)
        den = sum(np.nansum(c["pred"] ** 2 * ~np.isnan(c["meas"])) for r, c in comps.items() if r in a.train)
        gain = float(num / den)
        train = {r: rel_error(comps[r], gain) for r in a.train}
        test = {r: rel_error(comps[r], gain) for r in a.test}
        row = {"channel_length": L, "channel_damping": d, "head_absorption": h, "gain": gain,
               "train": train, "test": test,
               "train_mean": float(np.mean(list(train.values()))),
               "test_mean": float(np.mean(list(test.values()))) if test else None}
        results.append(row)
        print(json.dumps(row), flush=True)
        log.write(json.dumps(row) + chr(10))
        log.flush()
    best = min(results, key=lambda r: r["train_mean"])
    Path(a.out).write_text(json.dumps({"best": best, "all": results}, indent=1))
    print("BEST", json.dumps(best))


if __name__ == "__main__":
    main()
