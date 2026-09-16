"""Find where a cascaded run stops responding.

From the lab's log of 15 September:

    07:38:15  Motors homed.
    07:38:27  Staging did not complete within 12.0 seconds; running unstaggered.
    07:38:27  Continuous motion started.
    07:38:30  Piston 1..10 covered only 0 mm of its 180 mm stroke
    07:39:01  Pistons did not all reach the resting position.

Staging timed out, the run that followed moved nothing, and the resting move
failed too -- so after homing the drives stopped acting on Run_1 at all. A run
that skipped staging, minutes later, worked. None of it reproduces against the
simulator.

This walks the same sequence by hand with everything recorded at each step:
status word, the enabled and homed bits, position, the run bits, and what the
drive is actually holding for Position 1 and Position 2. It moves pistons.

    python tools/live_cascade_debug.py
    python tools/live_cascade_debug.py --pistons 1-3 --dry-run
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
from Model import (
    STAGE_SPEED, STAGE_TOLERANCE, MachineState, Model, NullBridge, RunMode,
)


def snap(client, axes) -> List[dict]:
    rows = []
    for axis in axes:
        status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
        warn = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
        rows.append({
            "piston": tags.display_number(axis),
            "status": status,
            "hex": hex(status),
            "enabled": bool(status & 1),
            "homed": drive_status.is_homed(status),
            "mm": round(params.to_mm(
                client.read(tags.axis_field(axis, tags.ACTUAL_POSITION))), 2),
            "held": (client.read(params.BY_NAME["Position 1"].tag(axis)),
                     client.read(params.BY_NAME["Position 2"].tag(axis))),
            "problems": drive_status.problems(warn, status),
        })
    return rows


def bits(client) -> Dict[str, bool]:
    return {"Run_1": bool(client.read(tags.RUN_SINGLE)),
            "Run_2": bool(client.read(tags.RUN_CONTINUOUS)),
            "Home": bool(client.read(tags.HOME_BUTTON))}


def show(label, client, axes) -> List[dict]:
    rows = snap(client, axes)
    print("\n-- {0} -- run bits {1}".format(label, bits(client)), flush=True)
    for r in rows:
        print("   piston {piston:2d} {hex:>7}  en={enabled!s:5} homed={homed!s:5} "
              "at {mm:7.2f}  holds {held}  {problems}".format(**r), flush=True)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", default="192.168.1.1")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--pistons", default="1-9")
    parser.add_argument("--low", type=int, default=0)
    parser.add_argument("--high", type=int, default=180)
    parser.add_argument("--speed", type=int, default=225)
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
        print("Would walk home -> stage -> continuous on pistons "
              "{0}, stroke {1}-{2}".format(sorted(set(shown)), args.low, args.high))
        return 0

    record = {"pistons": sorted(set(shown)), "steps": []}
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

        record["steps"].append(("start", show("AT START", client, axes)))

        model = Model(transport=client, is_live=True)
        model.register_bridge(NullBridge())
        model.set_selection(axes)
        model.create_set("Cascade debug")
        stagger = patterns.build(
            axes, "Curve Offset", patterns.STAGGER,
            start=0, step=100, across=patterns.ACROSS_ROWS,
        )
        for motor in model.all_motors:
            motor.set_param("Position 1", args.low)
            motor.set_param("Position 2", args.high)
            motor.set_param("Speed 1", args.speed)
            motor.set_param("Speed 2", args.speed)
            motor.set_param("Curve Offset", stagger.values[motor.axis])

        targets = model.cascade_targets()
        print("\nstaging targets: {0}".format(
            {tags.display_number(a): t for a, t in sorted(targets.items())}))

        model.rest_position = "hold"
        print("\nPreparing (clear faults, boot, write, home)...", flush=True)
        began = time.time()
        if not model.prepare():
            print("prepare() refused."); return 1
        while model.state is MachineState.PREPARING and time.time() - began < 240:
            time.sleep(0.5)
        print("prepare took {0:.1f}s, state {1}".format(
            time.time() - began, model.state), flush=True)
        record["steps"].append(("after_home", show("AFTER HOMING", client, axes)))
        if model.state is not MachineState.HOMED:
            print("did not reach HOMED; stopping here.")
            return 1

        # --- staging, by hand, watching every poll ---------------------------
        print("\nStaging by hand (Position 1 = Position 2 = target, then Run_1)",
              flush=True)
        for axis, target in targets.items():
            client.write(params.BY_NAME["Move Type"].tag(axis), 0)
            client.write(params.BY_NAME["Position 1"].tag(axis), target)
            client.write(params.BY_NAME["Position 2"].tag(axis), target)
            client.write(params.BY_NAME["Speed 1"].tag(axis), STAGE_SPEED)
            client.write(params.BY_NAME["Speed 2"].tag(axis), STAGE_SPEED)
        record["steps"].append(("staged_params", show("PARAMETERS WRITTEN", client, axes)))

        client.write(tags.RUN_SINGLE, 1)
        print("   Run_1 raised; watching for {0}s".format(14), flush=True)
        moved = {}
        for tick in range(28):
            time.sleep(0.5)
            here = {tags.display_number(a): round(params.to_mm(client.read(
                tags.axis_field(a, tags.ACTUAL_POSITION))), 1) for a in axes}
            moved[round((tick + 1) * 0.5, 1)] = here
            if tick % 6 == 5:
                print("   t+{0:4.1f}s {1}".format((tick + 1) * 0.5, here), flush=True)
            arrived = all(
                abs(params.to_mm(client.read(tags.axis_field(a, tags.ACTUAL_POSITION)))
                    - targets[a]) <= STAGE_TOLERANCE for a in axes)
            if arrived:
                print("   ARRIVED after {0:.1f}s".format((tick + 1) * 0.5), flush=True)
                break
        else:
            print("   NEVER ARRIVED -- this is the staging timeout", flush=True)
        client.write(tags.RUN_SINGLE, 0)
        record["staging_track"] = moved
        record["steps"].append(("after_staging", show("AFTER STAGING", client, axes)))

        # --- restore and run -------------------------------------------------
        print("\nRestoring parameters and starting continuous...", flush=True)
        for motor in model.all_motors:
            motor.current_params = {}
            motor.write_success = False
            motor.write_to(client)
        record["steps"].append(("restored", show("PARAMETERS RESTORED", client, axes)))

        client.write(tags.RUN_CONTINUOUS, 1)
        track = {}
        for tick in range(16):
            time.sleep(0.5)
            track[round((tick + 1) * 0.5, 1)] = {
                tags.display_number(a): round(params.to_mm(client.read(
                    tags.axis_field(a, tags.ACTUAL_POSITION))), 1) for a in axes}
        client.write(tags.RUN_CONTINUOUS, 0)
        record["run_track"] = track
        spans = {}
        for a in axes:
            seen = [v[tags.display_number(a)] for v in track.values()]
            spans[tags.display_number(a)] = round(max(seen) - min(seen), 1)
        print("\ntravel during the continuous run: {0}".format(spans), flush=True)
        record["steps"].append(("after_run", show("AFTER RUN", client, axes)))

        print("\nVERDICT: {0}".format(
            "the run moved -- the cascade path works here"
            if max(spans.values()) > (args.high - args.low) * 0.5 else
            "the run moved nothing: reproduced the fault"))
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
        out = folder / ("cascade-debug-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        print("Saved {0}".format(out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
