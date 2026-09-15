"""Re-analyse a saved sync-drift run: do the pistons separate, or just sit apart?

`live_sync_drift.py` reports the spread between pistons and the cycle time of
each. Neither answers the question on its own:

* Spread oscillates through every cycle by construction. Two pistons a fixed
  hair out of phase are furthest apart at mid-stroke, where they are moving
  fastest, and together at both ends. A big spread is not by itself drift.
* Cycle time inferred from direction reversals is noisy over a short run. A
  45 s run put the spread across the array at 15 ms; 240 s put it at 2.6 ms.
  The first was mostly measurement error, and a prediction built on it was
  wrong by a factor of six.

So this measures the phase lag of each piston against a reference piston
directly, once near the start and once near the end, and reports how much it
*moved*. A lag that holds steady is a fixed offset and harmless. A lag that
grows is real drift, and its slope says how fast.

    python tools/analyse_sync_drift.py                    the newest run
    python tools/analyse_sync_drift.py logs/verification/sync-drift-....json
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional


def reversal_times(times: List[float], series: List[float],
                   noise: float = 2.0) -> List[float]:
    """When the piston turned round, ignoring encoder jitter below ``noise``."""
    marks: List[float] = []
    direction: Optional[bool] = None
    anchor = series[0]
    for when, where in zip(times, series):
        if abs(where - anchor) < noise:
            continue
        rising = where > anchor
        if direction is not None and rising != direction:
            marks.append(when)
        direction = rising
        anchor = where
    return marks


def lag_series(reference: List[float], other: List[float]) -> List[float]:
    """Lag of ``other`` behind ``reference``, reversal by reversal.

    Both lists are reversal times. They are paired by index, which is sound
    only while the two pistons are within half a cycle of each other -- past
    that the pairing slips and the numbers stop meaning anything. The caller
    checks that by watching whether the lag stays small.
    """
    return [b - a for a, b in zip(reference, other)]


def fit_slope(xs: List[float], ys: List[float]) -> float:
    """Least-squares slope, in y-units per x-unit."""
    if len(xs) < 2:
        return 0.0
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y)
               for x, y in zip(xs, ys)) / denominator


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        path = Path(argv[0])
    else:
        found = sorted(glob.glob("logs/verification/sync-drift-*.json"))
        if not found:
            print("No sync-drift runs in logs/verification/.")
            return 2
        path = Path(found[-1])

    record = json.loads(path.read_text(encoding="utf-8"))
    samples = record.get("samples")
    if not samples:
        print("{0} has no samples -- the run aborted before it started."
              .format(path.name))
        return 2

    axes = sorted(int(a) for a in samples[0]["positions"])
    times = [s["at"] for s in samples]
    series = {a: [s["positions"][str(a)] if str(a) in s["positions"]
                  else s["positions"][a] for s in samples] for a in axes}

    print("{0}\n{1} pistons, {2} samples over {3:.0f} s\n".format(
        path.name, len(axes), len(samples), times[-1]))

    marks = {a: reversal_times(times, series[a]) for a in axes}
    usable = min(len(m) for m in marks.values())
    if usable < 8:
        print("Only {0} reversals captured; run for longer.".format(usable))
        return 2

    reference = axes[0]
    ref_marks = marks[reference][:usable]
    span = ref_marks[-1] - ref_marks[0]

    # Display numbers, since that is what the operator sees.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from app import tags
        name = tags.display_number
    except Exception:  # noqa: BLE001 - fall back to the raw axis
        def name(axis):
            return axis

    print("Phase lag against piston {0}, in milliseconds:".format(name(reference)))
    print("  {0:>7} {1:>10} {2:>10} {3:>10} {4:>12}".format(
        "piston", "first", "last", "change", "drift/hour"))

    worst = 0.0
    rows = []
    for axis in axes:
        lags = lag_series(ref_marks, marks[axis][:usable])
        early = statistics.fmean(lags[:max(len(lags) // 5, 1)]) * 1000.0
        late = statistics.fmean(lags[-max(len(lags) // 5, 1):]) * 1000.0
        slope = fit_slope(ref_marks, lags) * 1000.0      # ms per second
        per_hour = slope * 3600.0
        rows.append((axis, early, late, late - early, per_hour))
        worst = max(worst, abs(late - early))
        print("  {0:>7} {1:>9.1f} {2:>9.1f} {3:>9.1f} {4:>11.0f}".format(
            name(axis), early, late, late - early, per_hour))

    cycle = statistics.fmean(
        [(marks[a][-1] - marks[a][0]) / ((usable - 1) / 2.0) for a in axes])

    print("\nMeasured over {0:.0f} s ({1:.0f} cycles of {2:.3f} s).".format(
        span, span / cycle, cycle))

    # A tenth of a cycle apart is where a wave visibly stops being one wave.
    budget = cycle * 1000.0 * 0.1
    fastest = max(rows, key=lambda r: abs(r[4]))
    if worst < budget * 0.1:
        print("\nVERDICT: the pistons hold their phase. The largest lag moved "
              "{0:.1f} ms over the whole run, against a {1:.0f} ms cycle -- "
              "they sit at a fixed offset rather than separating.".format(
                  worst, cycle * 1000.0))
    else:
        hours = budget / abs(fastest[4]) if fastest[4] else float("inf")
        print("\nVERDICT: the pistons are separating. The worst lag moved "
              "{0:.1f} ms over {1:.0f} s. Piston {2} drifts fastest at "
              "{3:.0f} ms/hour, so the array goes a tenth of a cycle out "
              "after about {4:.1f} hours of continuous running.".format(
                  worst, span, name(fastest[0]), fastest[4], hours))

    spreads = [max(s["positions"].values()) - min(s["positions"].values())
               for s in samples]
    print("Position spread ranged {0:.1f} to {1:.1f} mm, which is the fixed "
          "offset seen at mid-stroke, not drift.".format(
              min(spreads), max(spreads)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
