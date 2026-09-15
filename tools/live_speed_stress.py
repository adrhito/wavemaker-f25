"""Bounded live test for speed changes. Never runs unless --execute is given.

The test deliberately uses only warning-free pistons 1, 2 and 11 from the
operator-approved 1..12 set. It does not home, boot, clear faults, or touch
any other piston. A failed preflight aborts before the first motion write.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Model import MachineState, Model, NullBridge, RunMode  # noqa: E402
from app import drive_status, params, tags  # noqa: E402
from app.plc import PlcClient  # noqa: E402

ALLOWED_DISPLAY = (1, 2, 11)
MAX_SPEED = 75
STROKE_MM = 4
# Feedback is quantized to 0.1 micrometres; the nominal 370 mm application
# limit can therefore be exceeded by a few thousandths in a readback.
FEEDBACK_EDGE_TOLERANCE = 1.0


def read_snapshot(client):
    rows = []
    for shown in ALLOWED_DISPLAY:
        axis = tags.axis_from_display(shown)
        status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
        warning = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
        position = params.to_mm(client.read(tags.axis_field(axis, tags.ACTUAL_POSITION)))
        rows.append({
            "piston": shown, "axis": axis, "status": status,
            "warn": warning, "position_mm": position,
            "homed": drive_status.is_homed(status),
            "enabled": bool(status & 1),
            "problems": drive_status.problems(warning, status),
        })
    return rows


def wait_idle(model, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not model.busy and model.state in (MachineState.HOMED, MachineState.READY):
            return
        time.sleep(0.05)
    raise RuntimeError("Model did not return to an idle state")


def command_snapshot(client):
    return {name: client.read(tag) for name, tag in (
        ("Run_1", tags.RUN_SINGLE), ("Run_2", tags.RUN_CONTINUOUS),
        ("Run_Curve", tags.RUN_CURVE), ("Home", tags.HOME_BUTTON),
    )}


def run(client, allow_controller_hot=False):
    before = read_snapshot(client)
    failures = []
    for row in before:
        problems = row["problems"]
        if allow_controller_hot:
            problems = [p for p in problems if not p.startswith("Controller Hot -")]
        if not row["homed"] or not row["enabled"] or problems:
            failures.append("piston {0}: {1}".format(row["piston"], problems or "not ready"))
        if (not math.isfinite(row["position_mm"])
                or not -20 - FEEDBACK_EDGE_TOLERANCE
                <= row["position_mm"]
                <= 370 + FEEDBACK_EDGE_TOLERANCE):
            failures.append("piston {0}: position outside application limits".format(row["piston"]))
    if failures:
        raise RuntimeError("Preflight refused motion: " + "; ".join(failures))
    if any(command_snapshot(client).values()):
        raise RuntimeError("Preflight refused motion: a machine command is already active")

    model = Model(transport=client, is_live=True)
    model.register_bridge(NullBridge())
    axes = [row["axis"] for row in before]
    for axis, row in zip(axes, before):
        model.toggle(axis, True)
        model.set_pending_param("Speed 1", 50)
        model.set_pending_param("Speed 2", 50)
    model.create_set("Live speed stress")
    for motor, row in zip(model.all_motors, before):
        centre = row["position_mm"]
        low = max(-20, min(370 - STROKE_MM, int(math.floor(centre - STROKE_MM / 2))))
        motor.set_param("Position 1", low)
        motor.set_param("Position 2", low + STROKE_MM)
        motor.set_param("Speed 1", 50)
        motor.set_param("Speed 2", 50)
    # Preflight already proved the drive references are valid; do not home.
    model._homed_axes = set(axes)
    model._set_state(MachineState.HOMED)
    model.rest_position = "hold"
    # The standalone harness did not run the normal Prepare path, so explicitly
    # establish the selected Live_Motors bits before any run command.
    model._mark_live_motors()
    evidence = {"before": before, "events": []}

    if not model.start(RunMode.SINGLE):
        raise RuntimeError("Could not start bounded single stroke")
    wait_idle(model)
    evidence["events"].append({"name": "single_{0}mm".format(STROKE_MM),
                                "commands": command_snapshot(client),
                                "positions": read_snapshot(client)})

    if not model.start(RunMode.CONTINUOUS):
        raise RuntimeError("Could not start continuous run")
    time.sleep(0.8)
    model.change_speed_live(25)
    evidence["events"].append({"name": "speed_25", "commands": command_snapshot(client)})
    time.sleep(0.8)
    model.change_speed_live(MAX_SPEED)
    evidence["events"].append({"name": "speed_75", "commands": command_snapshot(client)})
    time.sleep(0.8)
    model.stop(immediate=True, park=False)
    wait_idle(model)
    evidence["events"].append({"name": "stopped", "commands": command_snapshot(client),
                                "positions": read_snapshot(client)})
    if any(command_snapshot(client).values()):
        raise RuntimeError("Run command remained high after Stop")
    return evidence


def main():
    global ALLOWED_DISPLAY, STROKE_MM
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="perform the bounded live test; default is a refusal")
    parser.add_argument("--pistons", default="1,2,11",
                        help="display numbers 1..12 to test, comma-separated")
    parser.add_argument("--allow-controller-hot", action="store_true",
                        help="allow an existing Controller Hot warning as a baseline")
    parser.add_argument("--stroke", type=int, default=STROKE_MM,
                        help="bounded visible stroke in mm (default 4)")
    args = parser.parse_args()
    if not args.execute:
        print("Dry run: pass --execute to authorize the bounded live test.")
        return 0
    try:
        selected = tuple(int(item) for item in args.pistons.split(","))
    except ValueError:
        raise SystemExit("--pistons must be comma-separated display numbers")
    if not selected or any(number < 1 or number > 12 for number in selected):
        raise SystemExit("--pistons must contain only numbers 1 through 12")
    if args.stroke < 1 or args.stroke > 180:
        raise SystemExit("--stroke must be between 1 and 180 mm")
    ALLOWED_DISPLAY = selected
    STROKE_MM = args.stroke
    client = PlcClient("192.168.1.1", 1)
    evidence = None
    try:
        if not client.connect():
            raise RuntimeError("Controller not reachable")
        evidence = run(client, allow_controller_hot=args.allow_controller_hot)
        print(json.dumps(evidence, indent=2))
        return 0
    finally:
        folder = Path(__file__).resolve().parent.parent / "logs" / "verification"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / ("live-speed-stress-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        client.close()
        print("Saved " + str(output))


if __name__ == "__main__":
    raise SystemExit(main())
