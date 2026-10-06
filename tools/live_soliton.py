"""Run one solitary wave at the machine, or print what it would do.

    python tools/live_soliton.py --dry-run          contacts nothing
    python tools/live_soliton.py --depth 250 --height 40 --dry-run
    python tools/live_soliton.py --depth 250 --height 40 --go

``--dry-run`` is the default and prints the schedule without opening a socket.
Nothing moves until ``--go`` is passed.

Which route this uses
---------------------
``--go`` runs the **sequenced** route: the application holds the single-stroke
bit high and lets each column into ``Live_Motors`` when its turn comes. That
needs no stored curve, and it rests on one thing nobody has checked at the
machine -- that a piston whose ``Live_Motors`` bit goes high while ``Run_1`` is
already high sets off then. If that is wrong, every column sets off together
and you get a hump rather than a soliton; the travel report at the end shows it
either way.

The **curve** route is the proper one and does not live here: set the design up
on the Soliton tab, then press Start Curve. It needs a curve loaded on the
drives, which `tools/live_curve_probe.py` checks without moving anything.

Safety
------
Read `.claude/skills/wavemaker/SKILL.md` first. Never run this while anything
else is driving the controller -- the application has no exclusive-ownership
check, and two sessions will write over each other in silence. Every run bit is
dropped in a finally, including on Ctrl-C.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import solitons, tags                                 # noqa: E402
from app.plc import PlcError                                   # noqa: E402
from Model import MachineState, Model, RunMode                 # noqa: E402


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--depth", type=int, default=solitons.DEFAULT_DEPTH,
                        help="still-water depth in mm")
    parser.add_argument("--height", type=int, default=solitons.DEFAULT_AMPLITUDE,
                        help="crest rise above still water, in mm")
    parser.add_argument("--columns", type=int, default=None,
                        help="how many columns to use, from the end wall")
    parser.add_argument("--pitch", type=float,
                        default=solitons.DEFAULT_COLUMN_PITCH,
                        help="spacing between columns along the tank, in mm")
    parser.add_argument("--gain", type=float, default=1.0,
                        help="multiplier on the push, for calibration")
    parser.add_argument("--ip", default="192.168.1.1")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--go", action="store_true",
                        help="actually move the pistons. Without this, "
                             "nothing is contacted and nothing moves.")
    # Accepted so that the safe thing can be said out loud, which is how the
    # other tools in here are driven. It is already the default; naming it
    # costs nothing and reads better in a lab notebook than its absence.
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="print the schedule and contact nothing "
                             "(the default)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse(argv)
    design = solitons.design(args.depth, args.height, gain=args.gain,
                             pitch=args.pitch, columns=args.columns)
    plan = solitons.sequence_plan(design)

    print(solitons.describe_plan(design, plan))
    print()
    for problem in design.problems:
        print("REFUSED: {0}".format(problem))
    for warning in design.warnings:
        print("Note: {0}".format(warning))
    if design.problems:
        return 2

    if args.dry_run and args.go:
        print("REFUSED: --dry-run and --go contradict each other. "
              "Pick one.")
        return 2

    if not args.go:
        print("Dry run. Nothing was contacted and nothing moved.")
        print("Pass --go to run it at the machine.")
        return 0

    columns = args.columns or solitons.submerged_columns(design.depth)
    axes = solitons.axes_for(columns)
    print("About to move pistons {0}.".format(tags.display_list(axes)))

    model = Model(ip_address=args.ip, processor_slot=args.slot)
    model.startup()
    _wait_until(model, lambda: model.state is not MachineState.PREPARING, 60)
    if not model.is_live:
        print("The controller did not answer. Nothing was run.")
        return 2

    # Refuse if something else already owns the machine. The application has no
    # ownership check of its own, so this is the only gate there is.
    for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE,
                tags.HOME_BUTTON):
        try:
            if model.plc.read(tag):
                print("REFUSED: {0} is already high. Something else is "
                      "driving the machine.".format(tag))
                return 2
        except PlcError as exc:
            print("Could not read {0}: {1}".format(tag, exc))
            return 2

    try:
        model.set_selection(axes)
        model.create_set()
        from app import params

        values = params.defaults()
        far = params.BY_NAME["Position 2"].maximum
        near = int(max(params.BY_NAME["Position 1"].minimum,
                       far - design.stroke))
        speed = int(min(round(design.peak_speed),
                        params.BY_NAME["Speed 1"].maximum))
        values.update({
            "Position 1": near, "Position 2": far,
            "Speed 1": speed, "Speed 2": speed,
            "Profile": 2, "Move Type": 0,
        })
        for motor_set in model.sets:
            for motor in motor_set:
                motor.update_params(values)

        print("Preparing (this homes the pistons and takes about a minute)...")
        if not model.prepare():
            print("Prepare was refused. Nothing was run.")
            return 2
        _wait_until(model, lambda: model.state is MachineState.HOMED, 240)
        if model.state is not MachineState.HOMED:
            print("The pistons did not home. Nothing was run.")
            return 2

        print("Running the soliton...")
        model.run_soliton(design, dry_run=False)
        _wait_until(model, lambda: not model.busy, 120)
        print("Done. Check logs/<date>.log for how far each piston travelled.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted; stopping.")
        return 1
    finally:
        # Unconditional: every bit down, whatever happened, including Ctrl-C.
        try:
            model.all_stop(include_home=True)
        except PlcError as exc:
            print("WARNING: could not clear the run bits: {0}".format(exc))
            print("Use the physical stop.")
        try:
            model.shutdown()
        except Exception:                                   # noqa: BLE001
            pass


def _wait_until(model, done, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and not done():
        time.sleep(0.25)


if __name__ == "__main__":
    raise SystemExit(main())
