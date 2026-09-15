"""Does "Rows out of step" actually put the rows out of step at the machine?

The three rows of a column are commanded with the identical stroke and the
identical speed, and staggered only by where they are started along it. If it
works, then at any instant the three pistons of a column are at three clearly
different heights, and they stay that way for the whole run.

Pistons 1, 2 and 3 are one column -- the front one -- so they are the case to
watch. This applies a row stagger, runs continuously, and reports how far apart
the rows stay.

    python tools/live_row_cascade.py
    python tools/live_row_cascade.py --seconds 60 --dry-run
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drive_status, params, patterns, tags
from app.plc import PlcClient
from Model import MachineState, Model, NullBridge, RunMode


def health(client, axes):
    rows = []
    for axis in axes:
        status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
        warn = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
        rows.append({
            "piston": tags.display_number(axis),
            "homed": drive_status.is_homed(status),
            "enabled": bool(status & 1),
            "position_mm": round(params.to_mm(client.read(
                tags.axis_field(axis, tags.ACTUAL_POSITION))), 3),
            "problems": drive_status.problems(warn, status),
        })
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", default="192.168.1.1")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--pistons", default="1-9")
    parser.add_argument("--low", type=int, default=0)
    parser.add_argument("--high", type=int, default=300)
    parser.add_argument("--speed", type=int, default=300)
    parser.add_argument("--step", type=int, default=100,
                        help="Curve Offset between one row and the next")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    shown = []
    for piece in args.pistons.split(","):
        if "-" in piece:
            a, b = (int(x) for x in piece.split("-", 1))
            shown.extend(range(a, b + 1))
        elif piece.strip():
            shown.append(int(piece))
    axes = [tags.axis_from_display(n) for n in sorted(set(shown))]

    if args.dry_run:
        print("Would stagger rows by {0} on pistons {1}, stroke {2}-{3} at "
              "{4} mm/s for {5:.0f}s".format(
                  args.step, sorted(set(shown)), args.low, args.high,
                  args.speed, args.seconds))
        return 0

    record = {"pistons": sorted(set(shown)), "low": args.low, "high": args.high,
              "speed": args.speed, "step": args.step}
    client = PlcClient(args.ip, args.slot)
    model = None
    try:
        if not client.connect():
            print("Controller not reachable.")
            return 2
        if any(client.read(t) for t in
               (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)):
            print("A run bit is already high; nothing attempted.")
            return 1
        record["before"] = health(client, axes)

        model = Model(transport=client, is_live=True)
        model.register_bridge(NullBridge())
        model.set_selection(axes)
        model.create_set("Row cascade")

        stagger = patterns.build(
            axes, "Curve Offset", patterns.STAGGER,
            start=0, step=args.step, across=patterns.ACROSS_ROWS,
        )
        for motor in model.all_motors:
            motor.set_param("Position 1", args.low)
            motor.set_param("Position 2", args.high)
            motor.set_param("Speed 1", args.speed)
            motor.set_param("Speed 2", args.speed)
            motor.set_param("Curve Offset", stagger.values[motor.axis])

        staged = model.cascade_targets()
        record["staging"] = {tags.display_number(a): t
                             for a, t in staged.items()}
        print("staging targets: {0}".format(
            {p: record["staging"][p] for p in sorted(record["staging"])[:6]}))

        model.rest_position = "down"
        print("Preparing (boot and home, about a minute)...", flush=True)
        if not model.prepare():
            print("prepare() refused.")
            return 1
        deadline = time.time() + 240.0
        while model.state is MachineState.PREPARING and time.time() < deadline:
            time.sleep(0.5)
        if model.state is not MachineState.HOMED:
            print("prepare ended in {0}".format(model.state))
            return 1

        if not model.start(RunMode.CONTINUOUS):
            print("the run would not start.")
            return 1
        deadline = time.time() + 40.0
        while model.state is not MachineState.RUNNING and time.time() < deadline:
            time.sleep(0.2)
        if model.state is not MachineState.RUNNING:
            print("never reached RUNNING; state is {0}".format(model.state))
            return 1

        print("Running. Sampling for {0:.0f}s...".format(args.seconds), flush=True)
        samples = []
        started = time.time()
        while time.time() - started < args.seconds:
            samples.append({
                "at": round(time.time() - started, 3),
                "mm": {a: round(params.to_mm(client.read(
                    tags.axis_field(a, tags.ACTUAL_POSITION))), 2) for a in axes},
            })
            time.sleep(0.01)

        model.stop()
        deadline = time.time() + 60.0
        while model.state is MachineState.RUNNING and time.time() < deadline:
            time.sleep(0.2)

        record["samples"] = samples
        record["after"] = health(client, axes)

        # Within a column, the three rows should be at different heights.
        columns: Dict[int, List[int]] = {}
        for axis in axes:
            place = tags.display_number(axis) - 1
            columns.setdefault(place // tags.ROWS_PER_COLUMN, []).append(axis)

        print("\n--- how far apart the rows stay, per column ---")
        verdicts = {}
        for column, members in sorted(columns.items()):
            if len(members) < 2:
                continue
            spreads = [max(s["mm"][a] for a in members)
                       - min(s["mm"][a] for a in members) for s in samples]
            mean = statistics.fmean(spreads)
            verdicts[column] = mean
            print("  column {0} (pistons {1}): mean {2:.1f} mm, "
                  "worst {3:.1f} mm".format(
                      column + 1,
                      [tags.display_number(a) for a in members],
                      mean, max(spreads)))
        record["mean_spread_per_column"] = verdicts

        stroke = float(args.high - args.low)
        good = [c for c, m in verdicts.items() if m > stroke * 0.15]
        print("\nVERDICT: {0}".format(
            "the rows ARE out of step -- every column holds its rows apart"
            if len(good) == len(verdicts) else
            "the rows are NOT properly staggered in column(s) {0}".format(
                [c + 1 for c in verdicts if c not in good])))
    finally:
        for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS,
                    tags.RUN_CURVE, tags.HOME_BUTTON):
            try:
                client.write(tag, 0)
            except Exception:  # noqa: BLE001
                pass
        client.close()
        folder = Path(__file__).resolve().parent.parent / "logs" / "verification"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("row-cascade-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        target.write_text(json.dumps(record, indent=2, default=str),
                          encoding="utf-8")
        print("Saved {0}".format(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
