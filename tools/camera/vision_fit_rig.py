"""Fit and cross-validate the wave model on the continuous rig record.

The record from ``tools/camera/vision_rig.py`` and the strokes logged by
``tools/camera/vision_campaign.py`` are loaded as one long series.  For each fold,
the windows after some strokes (from just before firing to the next
stroke) are left out of the fit, and the model, which still knows every
piston's motion, must predict them.  The score is the RMS error over a
held-out window and the stations, over the RMS of the measured wave there.

    python tools/camera/vision_fit_rig.py --models shared column piston
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "camera"))

from vision import wavefit  # noqa: E402
from vision_fit import WORKING, groupings  # noqa: E402


def windows(probe, runs, before=1.0):
    """A mask per run: from just before its fire to the next fire."""
    fires = sorted(r["fire"] for r in runs)
    out = []
    for r in runs:
        later = [f for f in fires if f > r["fire"]]
        end = min(later[0] if later else r["fire"] + 40.0, r["fire"] + 40.0)
        out.append((probe.t >= r["fire"] - before) & (probe.t < end))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", default=str(ROOT / "analytics" / "rig"))
    parser.add_argument("--models", nargs="+", default=["shared", "column"])
    parser.add_argument("--taps", type=int, default=400)
    parser.add_argument("--ridge", type=float, default=1.0)
    parser.add_argument("--smooth", type=float, default=10.0)
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--every", type=int, default=3)
    parser.add_argument("--since", type=float, help="epoch seconds to start from")
    parser.add_argument("--until", type=float, help="epoch seconds to stop at")
    args = parser.parse_args()

    probe, runs = wavefit.load_rig(args.folder, t_start=args.since,
                                   t_end=args.until)
    print("{0:.0f} s of record, {1} strokes, {2} stations".format(
        probe.t[-1], len(runs), len(probe.stations_px)))
    px = probe.stations_px[::args.every]
    wins = windows(probe, runs)
    # Steps the model cannot explain: before the first logged stroke's
    # influence and where the line was lost.
    first = min(r["fire"] for r in runs)
    base = probe.t >= first - 1.0
    groups = groupings(WORKING)
    order = np.arange(len(runs))
    for name in args.models:
        results = []
        for fold in range(args.folds):
            held = order[fold::args.folds]
            hold = np.zeros(len(probe.t), bool)
            for k in held:
                hold |= wins[k]
            model = wavefit.fit([probe], groups[name], args.taps, args.ridge,
                                args.smooth, px, masks=[base & ~hold])
            for k in held:
                s = wavefit.score(model, probe, mask=wins[k])
                results.append((runs[k]["name"], s))
        rel = np.array([s["rel_rms"] for _, s in results])
        print("\n{0}: median {1:.1%}, mean {2:.1%}, worst {3:.1%}".format(
            name, np.median(rel), rel.mean(), rel.max()))
        for run, s in sorted(results, key=lambda r: r[1]["rel_rms"]):
            print("  {0:18s} {1:6.1%}  wave rms {2:5.2f} mm  peak {3:5.1f}  err {4:4.2f} mm".format(
                run, s["rel_rms"], s["rms_mm"], s["peak_mm"], s["err_mm"]))


if __name__ == "__main__":
    main()
