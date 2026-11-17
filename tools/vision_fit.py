"""Fit piston wave models to the probe recordings and score them on held-out runs.

Each model says which pistons share a response kernel:

- ``shared``: one kernel for every piston (the wave depends on total flow);
- ``column``: one per display column (pistons 3k-2..3k), whose ducts match;
- ``piston``: one per piston.

Scores are leave-one-out: each run is predicted by a model fitted to all the
others.  The score is the RMS error over the record and the stations,
divided by the RMS of the measured wave.

    python tools/vision_fit.py                       # every probe with a plan
    python tools/vision_fit.py --models shared column --taps 400
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision import wavefit  # noqa: E402

WORKING = [p for p in range(1, 31) if p != 26]


def groupings(pistons):
    columns = {}
    for p in pistons:
        columns.setdefault("c{0}".format((p - 1) // 3 + 1), []).append(p)
    return {"shared": {"all": list(pistons)},
            "column": columns,
            "piston": {"p{0}".format(p): [p] for p in pistons}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", default=str(ROOT / "analytics" / "probes"))
    parser.add_argument("--models", nargs="+", default=["shared", "column"])
    parser.add_argument("--taps", type=int, default=400)
    parser.add_argument("--ridge", type=float, default=1.0)
    parser.add_argument("--smooth", type=float, default=10.0)
    parser.add_argument("--t1", type=float, default=None,
                        help="score only up to this many seconds after firing")
    parser.add_argument("--every", type=int, default=3,
                        help="use every n-th station (speed)")
    parser.add_argument("--save", help="write the fitted kernels (all runs) here")
    args = parser.parse_args()

    probes = wavefit.all_probes(args.folder)
    print("{0} runs".format(len(probes)))
    px = wavefit._common_stations(probes)[::args.every]
    groups = groupings(WORKING)
    table = {}
    for name in args.models:
        rows = []
        for k, held in enumerate(probes):
            train = probes[:k] + probes[k + 1:]
            model = wavefit.fit(train, groups[name], args.taps, args.ridge,
                                args.smooth, px)
            s = wavefit.score(model, held, 0.0, args.t1)
            rows.append((held.run["name"], s))
        rel = np.array([s["rel_rms"] for _, s in rows])
        table[name] = rows
        print("\n{0}: median {1:.1%}, mean {2:.1%}, worst {3:.1%}".format(
            name, np.median(rel), rel.mean(), rel.max()))
        for run, s in sorted(rows, key=lambda r: r[1]["rel_rms"]):
            print("  {0:12s} {1:6.1%}  wave rms {2:5.2f} mm  peak {3:5.1f} mm  "
                  "error rms {4:4.2f} mm".format(run, s["rel_rms"], s["rms_mm"],
                                                 s["peak_mm"], s["err_mm"]))
    if args.save:
        name = args.models[-1]
        model = wavefit.fit(probes, groups[name], args.taps, args.ridge,
                            args.smooth, px)
        np.savez_compressed(args.save, kernels=model.kernels, taps=model.taps,
                            stations_px=model.stations_px,
                            groups=json.dumps(model.groups), dt=wavefit.DT)
        print("saved", args.save)


if __name__ == "__main__":
    main()
