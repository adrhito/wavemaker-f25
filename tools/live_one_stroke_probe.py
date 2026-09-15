"""What does One stroke actually do at the machine?

The application believes One stroke is a full out-and-back: `_stroke_seconds`
budgets the travel each way plus the dwells, and the mock returns the piston to
Position 1 at the end. The operator reports that the real array only goes up.

If that is right, the ladder's Run_1 is a single move rather than an
out-and-back, and the application has been holding the bit for twice as long as
the motion it actually commands.

This settles it by running one stroke on ONE piston and recording where it goes,
leg by leg. It moves that piston through the stroke you give it -- 120 mm at a
gentle 150 mm/s by default -- and parks it afterwards.

    python tools/live_one_stroke_probe.py
    python tools/live_one_stroke_probe.py --piston 5 --stroke 120
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drive_status, params, tags
from app.plc import PlcClient
from Model import MachineState, Model, NullBridge, RunMode


def health(client, axis):
    status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
    warn = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
    return {
        "status": status, "warn": warn,
        "homed": drive_status.is_homed(status),
        "enabled": bool(status & 1),
        "position_mm": round(params.to_mm(
            client.read(tags.axis_field(axis, tags.ACTUAL_POSITION))), 3),
        "problems": drive_status.problems(warn, status),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", default="192.168.1.1")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--piston", type=int, default=5)
    parser.add_argument("--low", type=int, default=0)
    parser.add_argument("--stroke", type=int, default=120)
    parser.add_argument("--speed", type=int, default=150)
    args = parser.parse_args(argv)

    axis = tags.axis_from_display(args.piston)
    high = args.low + args.stroke
    record = {"piston": args.piston, "low": args.low, "high": high,
              "speed": args.speed}

    client = PlcClient(args.ip, args.slot)
    model = None
    try:
        if not client.connect():
            print("Controller not reachable.")
            return 2
        if any(client.read(t) for t in
               (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)):
            print("A run bit is already high; something else is driving the "
                  "machine. Nothing attempted.")
            return 1

        record["before"] = health(client, axis)
        print("piston {0}: {1}".format(args.piston, record["before"]))

        model = Model(transport=client, is_live=True)
        model.register_bridge(NullBridge())
        model.set_selection([axis])
        model.create_set("One stroke probe")
        for motor in model.all_motors:
            motor.set_param("Position 1", args.low)
            motor.set_param("Position 2", high)
            motor.set_param("Speed 1", args.speed)
            motor.set_param("Speed 2", args.speed)
        model.rest_position = "hold"

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
        record["after_prepare"] = health(client, axis)
        print("prepared at {0} mm".format(record["after_prepare"]["position_mm"]))

        # Sample throughout, on this thread, while the run happens on another.
        samples = []
        started = time.time()

        import threading
        done = threading.Event()

        def run():
            try:
                model.start(RunMode.SINGLE)
            finally:
                done.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()

        # Watch for well past the budgeted out-and-back time.
        budget = (args.stroke / float(args.speed)) * 2 + 6.0
        while time.time() - started < budget:
            samples.append({
                "at": round(time.time() - started, 3),
                "mm": round(params.to_mm(client.read(
                    tags.axis_field(axis, tags.ACTUAL_POSITION))), 3),
                "run_1": bool(client.read(tags.RUN_SINGLE)),
            })
            time.sleep(0.05)
        done.wait(timeout=10.0)

        record["samples"] = samples
        record["after"] = health(client, axis)

        start_mm = samples[0]["mm"]
        peak = max(s["mm"] for s in samples)
        trough = min(s["mm"] for s in samples)
        end = samples[-1]["mm"]
        held = [s["at"] for s in samples if s["run_1"]]

        print("\n--- what happened ---")
        print("started at   {0:.1f} mm".format(start_mm))
        print("furthest out {0:.1f} mm  (commanded {1})".format(peak, high))
        print("lowest       {0:.1f} mm".format(trough))
        print("ended at     {0:.1f} mm".format(end))
        if held:
            print("Run_1 held    {0:.1f} s (from {1:.1f} to {2:.1f})".format(
                held[-1] - held[0], held[0], held[-1]))
        print("one leg at {0} mm/s takes {1:.1f} s".format(
            args.speed, args.stroke / float(args.speed)))

        returned = abs(end - start_mm) < args.stroke * 0.25
        record["returned"] = returned
        print("\nVERDICT: One stroke went {0}".format(
            "OUT AND BACK -- it returned to where it started"
            if returned else
            "OUT ONLY -- it stayed at the far end, so Run_1 is a single move "
            "and the application's out-and-back assumption is wrong"))
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
        target = folder / ("one-stroke-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        target.write_text(json.dumps(record, indent=2, default=str),
                          encoding="utf-8")
        print("Saved {0}".format(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
