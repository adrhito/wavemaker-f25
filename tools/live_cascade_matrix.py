"""Cascade both ways, through the code path the Start button actually uses.

Two directions:

* **vertically** -- across the three ROWS of a column, so one row sits near the
  top of its stroke, one near the middle and one near the bottom;
* **horizontally** -- across the ten COLUMNS, so a crest runs front to back
  along the chamber.

and both together, which is the case most likely to be broken, because the two
staggers have to combine into one starting position per piston rather than
fighting each other.

Everything goes through ``Model.prepare`` and ``Model.start`` rather than
writing tags directly, because the fault being chased only appears when the
application drives it: staging by hand works every time.

    python tools/live_cascade_matrix.py
    python tools/live_cascade_matrix.py --only rows --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drive_status, params, patterns, tags
from app.plc import PlcClient
from Model import MachineState, Model, NullBridge, RunMode

CASES = {
    "none": (None, 0),
    "rows": (patterns.ACROSS_ROWS, 100),
    "columns": (patterns.ACROSS_COLUMNS, 60),
    "both": ("both", 0),
}


def health(client, axes) -> List[dict]:
    rows = []
    for axis in axes:
        status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
        warn = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
        rows.append({
            "piston": tags.display_number(axis),
            "status": status & 0xFFFF,
            "enabled": bool(status & 1),
            "homed": drive_status.is_homed(status),
            "mm": round(params.to_mm(client.read(
                tags.axis_field(axis, tags.ACTUAL_POSITION))), 2),
            "problems": drive_status.problems(warn, status),
        })
    return rows


def apply_case(model, axes, case: str, low: int, high: int, speed: int):
    """Set the stroke and whichever stagger this case calls for."""
    for motor in model.all_motors:
        motor.set_param("Position 1", low)
        motor.set_param("Position 2", high)
        motor.set_param("Speed 1", speed)
        motor.set_param("Speed 2", speed)
        motor.set_param("Curve Offset", 0)

    if case == "none":
        return
    if case == "both":
        # A row stagger and a column stagger added together. Each piston ends
        # up with its own offset, which is exactly what cascade_targets is now
        # keyed on -- before it was keyed per column, this case was impossible.
        rows = patterns.build(axes, "Curve Offset", patterns.STAGGER,
                              start=0, step=100, across=patterns.ACROSS_ROWS)
        cols = patterns.build(axes, "Curve Offset", patterns.STAGGER,
                              start=0, step=60, across=patterns.ACROSS_COLUMNS)
        for motor in model.all_motors:
            motor.set_param("Curve Offset",
                            rows.values[motor.axis] + cols.values[motor.axis])
        return

    across, step = CASES[case]
    built = patterns.build(axes, "Curve Offset", patterns.STAGGER,
                           start=0, step=step, across=across)
    for motor in model.all_motors:
        motor.set_param("Curve Offset", built.values[motor.axis])


def run_case(client, model, axes, case, args, record):
    print("\n=== {0} ===".format(case), flush=True)
    apply_case(model, axes, case, args.low, args.high, args.speed)

    targets = model.cascade_targets()
    staged = {tags.display_number(a): t for a, t in sorted(targets.items())}
    print("staging targets: {0}".format(staged or "none -- all together"), flush=True)

    started = time.time()
    if not model.start(RunMode.CONTINUOUS):
        print("start() refused"); return {"case": case, "started": False}
    deadline = time.time() + 60.0
    while model.state is not MachineState.RUNNING and time.time() < deadline:
        if model.state in (MachineState.READY, MachineState.IDLE):
            break
        time.sleep(0.2)
    reached = model.state is MachineState.RUNNING
    print("reached RUNNING in {0:.1f}s: {1}".format(time.time() - started, reached),
          flush=True)

    track = {a: [] for a in axes}
    if reached:
        for _ in range(40):
            time.sleep(0.25)
            for a in axes:
                track[a].append(params.to_mm(client.read(
                    tags.axis_field(a, tags.ACTUAL_POSITION))))
    model.stop(immediate=True, park=False)
    deadline = time.time() + 30.0
    while model.state is MachineState.RUNNING and time.time() < deadline:
        time.sleep(0.2)

    travel = {tags.display_number(a): round(max(v) - min(v), 1)
              for a, v in track.items() if v}
    stroke = float(args.high - args.low)
    ran = travel and min(travel.values()) > stroke * 0.5
    print("travel: {0}".format(travel), flush=True)

    # Did the stagger survive into the run? Within a column the three rows
    # should differ for a row cascade; between columns they should differ for
    # a column cascade.
    spread = {}
    if track and all(track.values()):
        mid = len(next(iter(track.values()))) // 2
        by_row, by_col = {}, {}
        for a in axes:
            place = tags.display_number(a) - 1
            by_col.setdefault(place // tags.ROWS_PER_COLUMN, []).append(track[a][mid])
            by_row.setdefault(place % tags.ROWS_PER_COLUMN, []).append(track[a][mid])
        spread["within_a_column"] = round(max(
            max(v) - min(v) for v in by_col.values()), 1)
        spread["between_columns"] = round(
            max(sum(v) / len(v) for v in by_col.values())
            - min(sum(v) / len(v) for v in by_col.values()), 1)
    print("spread at mid-run: {0}".format(spread), flush=True)
    print("VERDICT: {0}".format("ran" if ran else "DID NOT RUN"), flush=True)

    return {"case": case, "started": True, "reached_running": reached,
            "staging_targets": staged, "travel": travel, "spread": spread,
            "ran": bool(ran)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", default="192.168.1.1")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--pistons", default="1-9")
    parser.add_argument("--low", type=int, default=0)
    parser.add_argument("--high", type=int, default=180)
    parser.add_argument("--speed", type=int, default=225)
    parser.add_argument("--only", choices=sorted(CASES), action="append")
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
    cases = args.only or ["none", "rows", "columns", "both"]

    if args.dry_run:
        print("Would run {0} on pistons {1}".format(cases, sorted(set(shown))))
        return 0

    record = {"pistons": sorted(set(shown)), "results": []}
    client = PlcClient(args.ip, args.slot)
    model = None
    try:
        if not client.connect():
            print("Controller not reachable."); return 2
        if any(client.read(t) for t in
               (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)):
            print("A run bit is already high; nothing attempted."); return 1

        record["before"] = health(client, axes)
        model = Model(transport=client, is_live=True)
        model.register_bridge(NullBridge())
        model.set_selection(axes)
        model.create_set("Cascade matrix")
        model.rest_position = "hold"

        # Prepare once. Every case after this reuses the homing, which is what
        # an operator does too -- they do not re-home between wave settings.
        apply_case(model, axes, "none", args.low, args.high, args.speed)
        print("Preparing (boot and home)...", flush=True)
        if not model.prepare():
            print("prepare() refused."); return 1
        deadline = time.time() + 240.0
        while model.state is MachineState.PREPARING and time.time() < deadline:
            time.sleep(0.5)
        if model.state is not MachineState.HOMED:
            print("prepare ended in {0}".format(model.state)); return 1
        print("prepared.", flush=True)

        for case in cases:
            record["results"].append(run_case(client, model, axes, case, args, record))
            time.sleep(1.0)

        record["after"] = health(client, axes)
        print("\n================ SUMMARY ================")
        for r in record["results"]:
            print("  {0:<8} ran={1!s:5}  within-column={2}  between-columns={3}".format(
                r["case"], r.get("ran"),
                r.get("spread", {}).get("within_a_column"),
                r.get("spread", {}).get("between_columns")))
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
        out = folder / ("cascade-matrix-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        print("\nSaved {0}".format(out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
