"""What does One stroke actually do at the machine?

One stroke should be one full cycle: onto the stroke at its bottom, up to its
top, and back down. The operator reported that the real array only went up --
from rest at 350 on a stroke of 0 to 150 it went 350 -> 150 -> 0 -- because
the first of two moves was spent getting onto the stroke. The application now
approaches the bottom first, so a correct run ends at the bottom having
visited the top. Smaller millimetres are higher.

This settles it by running one stroke on ONE piston and recording where it goes,
leg by leg. It moves that piston through the stroke you give it -- 120 mm at a
gentle 150 mm/s by default -- and parks it afterwards.

    python tools/live_one_stroke_probe.py
    python tools/live_one_stroke_probe.py --piston 5 --stroke 120
    python tools/live_one_stroke_probe.py --piston 1 2 3     three together
    python tools/live_one_stroke_probe.py --mock             no PLC, no motion

Several pistons run the same stroke together and each is judged on its own.
One stroke ends by itself, which is why this is also the first thing to run
over a new link to the controller: if the link drops half way, nothing is
left running that needs a second command to stop it.
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
    parser.add_argument("--piston", type=int, nargs="+", default=[5])
    parser.add_argument("--mock", action="store_true",
                        help="rehearse on the simulated machine; contacts nothing")
    parser.add_argument("--low", type=int, default=0)
    parser.add_argument("--stroke", type=int, default=120)
    parser.add_argument("--speed", type=int, default=150)
    args = parser.parse_args(argv)

    axes = {shown: tags.axis_from_display(shown) for shown in args.piston}
    high = args.low + args.stroke
    record = {"pistons": args.piston, "low": args.low, "high": high,
              "speed": args.speed, "mock": args.mock}

    if args.mock:
        from app.simulator import SimulatedMachine
        client = SimulatedMachine()
    else:
        client = PlcClient(args.ip, args.slot)
    model = None
    try:
        if not args.mock and not client.connect():
            print("Controller not reachable.")
            return 2
        if any(client.read(t) for t in
               (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)):
            print("A run bit is already high; something else is driving the "
                  "machine. Nothing attempted.")
            return 1

        record["before"] = {shown: health(client, axis) for shown, axis in axes.items()}
        for shown, state in record["before"].items():
            print("piston {0}: {1}".format(shown, state))
        troubled = {shown: state["problems"] for shown, state in record["before"].items()
                    if state["problems"]}
        if troubled:
            print("These drives already report a problem; nothing attempted: "
                  "{0}".format(troubled))
            return 1

        model = Model(transport=client, is_live=not args.mock)
        model.register_bridge(NullBridge())
        model.set_selection(list(axes.values()))
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
        record["after_prepare"] = {shown: health(client, axis)
                                   for shown, axis in axes.items()}
        print("prepared at {0} mm".format(
            {shown: state["position_mm"]
             for shown, state in record["after_prepare"].items()}))

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

        # Watch for well past the budgeted up-and-down time, plus the gentle
        # approach from wherever homing left the piston (at most the whole
        # travel at the staging speed).
        budget = (args.stroke / float(args.speed)) * 2 + 390.0 / 200.0 + 6.0
        while time.time() - started < budget:
            samples.append({
                "at": round(time.time() - started, 3),
                "mm": {shown: round(params.to_mm(client.read(
                    tags.axis_field(axis, tags.ACTUAL_POSITION))), 3)
                    for shown, axis in axes.items()},
                "run_1": bool(client.read(tags.RUN_SINGLE)),
            })
            time.sleep(0.05)
        done.wait(timeout=10.0)

        record["samples"] = samples
        record["after"] = {shown: health(client, axis) for shown, axis in axes.items()}
        held = [s["at"] for s in samples if s["run_1"]]

        print("\n--- what happened ---")
        if held:
            print("Run_1 held    {0:.1f} s (from {1:.1f} to {2:.1f})".format(
                held[-1] - held[0], held[0], held[-1]))
        print("one leg at {0} mm/s takes {1:.1f} s".format(
            args.speed, args.stroke / float(args.speed)))

        # Judged against the commanded stroke, not against where the piston
        # happened to be standing. Homing can leave it well outside Position 1
        # to Position 2, so "did it come back to where it started" answers the
        # wrong question: it reported a correct out-and-back as a failure
        # because the piston began at 350 mm on a stroke of 0 to 150.
        # Position 1 (--low) is the top here and Position 2 the bottom.
        tolerance = max(args.stroke * 0.1, 3.0)
        record["verdicts"] = {}
        for shown in axes:
            path = [s["mm"][shown] for s in samples]
            reached_top = any(abs(mm - args.low) <= tolerance for mm in path)
            ended_bottom = abs(path[-1] - high) <= tolerance
            record["verdicts"][shown] = {
                "reached_top": reached_top, "ended_at_bottom": ended_bottom}
            print("\npiston {0}".format(shown))
            print("  started at   {0:.1f} mm".format(path[0]))
            print("  furthest out {0:.1f} mm  (commanded {1})".format(max(path), high))
            print("  lowest       {0:.1f} mm".format(min(path)))
            print("  ended at     {0:.1f} mm".format(path[-1]))
            print("  reached the top ({0} mm): {1}".format(args.low, reached_top))
            print("  ended at the bottom ({0} mm): {1}".format(high, ended_bottom))
            print("  VERDICT: One stroke went {0}".format(
                "UP AND BACK DOWN -- a full cycle"
                if reached_top and ended_bottom else
                "HALF A CYCLE -- it never reached the top, or did not come down"))
        problems = {shown: state["problems"] for shown, state in record["after"].items()
                    if state["problems"]}
        if problems:
            print("\nDrive problems after the stroke: {0}".format(problems))
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
