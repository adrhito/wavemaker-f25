"""Run a big-wave preset on the real array and measure whether it stays in sync.

The lab's complaint is that the pistons drift apart over a long run. This runs
one of the shipped big-wave presets and measures that directly, rather than
watching the tank and forming an impression.

The presets it defaults to give every moving piston the same stroke, the same
speed and a Curve Offset of zero, so the array is *commanded* to move in
unison. Any spread between the pistons is therefore drift, not design -- which
is what makes this preset the right instrument for the question.

    python tools/live_sync_drift.py                       BIGGER WAVE, 90 s
    python tools/live_sync_drift.py --seconds 240         a longer soak
    python tools/live_sync_drift.py --preset "Preset 1-Big Wave Demo.csv"
    python tools/live_sync_drift.py --dry-run             no PLC, no motion

It moves real pistons through their full stroke. It drops every run bit and
parks the array on the way out, including on Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drive_status, params, tags
from app.plc import PlcClient
from Model import MachineState, Model, NullBridge, RunMode
from preset_options.PresetProcessor import PresetProcessor

#: Samples are as fast as the link allows; this only stops a runaway loop.
MIN_SAMPLE_GAP = 0.01
#: How much of the run to treat as "early" and "late" when comparing.
WINDOW_FRACTION = 0.2


def read_positions(client, axes) -> Dict[int, float]:
    out = {}
    for axis in axes:
        out[axis] = params.to_mm(
            client.read(tags.axis_field(axis, tags.ACTUAL_POSITION)))
    return out


def read_health(client, axes) -> List[dict]:
    rows = []
    for axis in axes:
        status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
        warn = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
        rows.append({
            "piston": tags.display_number(axis),
            "axis": axis,
            "status": status,
            "warn": warn,
            "homed": drive_status.is_homed(status),
            "enabled": bool(status & 1),
            "position_mm": round(params.to_mm(
                client.read(tags.axis_field(axis, tags.ACTUAL_POSITION))), 3),
            "problems": drive_status.problems(warn, status),
        })
    return rows


def moving_axes(preset) -> List[int]:
    """The axes the preset actually asks to move.

    Read from ``preset.rows``, which holds only the axes the file names, rather
    than from ``values_for``, which fills in defaults for the rest -- and the
    default stroke is 0 to 350, so asking ``values_for`` makes every piston in
    the array look like it was asked to move.
    """
    chosen = []
    for axis, values in (preset.rows or {}).items():
        try:
            if int(values["Position 2"]) != int(values["Position 1"]):
                chosen.append(axis)
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(chosen)


def reversals(times: List[float], series: List[float], noise: float = 1.0):
    """Times at which a piston changed direction.

    ``noise`` ignores encoder jitter: a reversal only counts once the piston
    has actually turned round by more than that, not every time the least
    significant digit wobbles.
    """
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


def analyse(samples: List[dict], axes: List[int]) -> dict:
    """What the samples say about whether the array held together."""
    times = [s["at"] for s in samples]
    series = {axis: [s["positions"][axis] for s in samples] for axis in axes}

    # The headline: how far apart the pistons are at any one instant. Commanded
    # to move as one, so this should stay near zero however long it runs.
    spreads = [max(s["positions"].values()) - min(s["positions"].values())
               for s in samples]

    window = max(int(len(samples) * WINDOW_FRACTION), 1)
    early, late = spreads[:window], spreads[-window:]

    cycles = {}
    for axis in axes:
        marks = reversals(times, series[axis])
        # Two reversals make one full out-and-back cycle.
        cycles[axis] = (round((marks[-1] - marks[0]) / ((len(marks) - 1) / 2.0), 4)
                        if len(marks) >= 3 else None)

    measured = {a: c for a, c in cycles.items() if c}
    travel = {axis: round(max(series[axis]) - min(series[axis]), 2)
              for axis in axes}

    return {
        "samples": len(samples),
        "sample_hz": round(len(samples) / (times[-1] - times[0]), 2)
        if len(times) > 1 else None,
        "spread_mm": {
            "early_mean": round(statistics.fmean(early), 2),
            "late_mean": round(statistics.fmean(late), 2),
            "worst": round(max(spreads), 2),
            "growth": round(statistics.fmean(late) - statistics.fmean(early), 2),
        },
        "cycle_seconds": {tags.display_number(a): c for a, c in cycles.items()},
        "cycle_spread_seconds": round(max(measured.values()) - min(measured.values()), 4)
        if len(measured) > 1 else None,
        "travel_mm": {tags.display_number(a): t for a, t in travel.items()},
    }


def judge(result: dict, stroke: float) -> List[dict]:
    """Turn the numbers into findings, with the thresholds stated."""
    found = []
    spread = result["spread_mm"]

    # A tenth of the stroke apart is visible in the water.
    if spread["late_mean"] > stroke * 0.1:
        found.append({
            "severity": "HIGH",
            "what": "the pistons finished {0:.1f} mm apart on average, on a "
                    "{1:.0f} mm stroke they were all commanded to make "
                    "together".format(spread["late_mean"], stroke),
        })
    if spread["growth"] > stroke * 0.05:
        found.append({
            "severity": "HIGH",
            "what": "the spread grew by {0:.1f} mm between the start and the "
                    "end of the run, so they are drifting apart rather than "
                    "sitting at a fixed offset".format(spread["growth"]),
        })

    # Cycle time inferred from reversals is too noisy over a short run to
    # predict drift from: 45 s of this preset put the spread across the array
    # at 15 ms and 240 s put it at 2.6 ms, so a prediction built on the short
    # run was out by a factor of six. Report the number, and point at the tool
    # that measures phase lag directly instead of extrapolating from it.
    gap = result.get("cycle_spread_seconds")
    if gap:
        found.append({
            "severity": "INFO",
            "what": "cycle times differ by {0:.4f} s across the array. That is "
                    "an estimate from direction reversals and is only as good "
                    "as the run is long -- for the drift itself run "
                    "tools/analyse_sync_drift.py against this file, which "
                    "measures phase lag early against late.".format(gap),
        })

    shortfall = {p: t for p, t in result["travel_mm"].items() if t < stroke * 0.8}
    if shortfall:
        found.append({
            "severity": "HIGH",
            "what": "these pistons travelled less than 80% of the commanded "
                    "{0:.0f} mm: {1}".format(stroke, shortfall),
        })
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", default="192.168.1.1")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--preset", default="BIGGER WAVE.csv")
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--rest", default="down", choices=("down", "up", "hold"))
    parser.add_argument("--ignore-warnings", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    preset = PresetProcessor().load(str(root / "Presets" / args.preset))
    axes = moving_axes(preset)
    if not axes:
        print("{0} does not ask any piston to move.".format(args.preset))
        return 2

    pistons = sorted(tags.display_number(a) for a in axes)
    values = preset.values_for(axes[0])
    stroke = abs(int(values["Position 2"]) - int(values["Position 1"]))

    if args.dry_run:
        print("Would run {0} for {1:.0f}s on pistons {2} "
              "(stroke {3} mm, speed {4})".format(
                  args.preset, args.seconds, pistons, stroke, values["Speed 1"]))
        return 0

    record = {"preset": args.preset, "pistons": pistons, "stroke_mm": stroke,
              "seconds": args.seconds}
    client = PlcClient(args.ip, args.slot)
    model: Optional[Model] = None
    code = 0

    try:
        if not client.connect():
            print("Controller not reachable at {0}; nothing written.".format(args.ip))
            return 2

        before = read_health(client, axes)
        record["before"] = before
        print("Pistons {0}; stroke {1} mm at {2} mm/s".format(
            pistons, stroke, values["Speed 1"]), flush=True)

        if any(client.read(t) for t in
               (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)):
            raise RuntimeError("a run bit is already high; something else is "
                               "driving the machine")
        troubled = [(r["piston"], r["problems"]) for r in before if r["problems"]]
        if troubled and not args.ignore_warnings:
            raise RuntimeError("pistons report problems: {0}".format(troubled))
        # Deliberately no "are they homed?" gate here. Booting the drives
        # clears the homed reference and drops every piston to zero -- measured
        # on this array: pistons parked at 369.8 mm, homed and de-energised,
        # came back from a Motor_Boot at -0.06 mm with the homed bit clear and
        # the routine Not Homed warning set. So homing before booting is worth
        # nothing, and Prepare below does both in the order that works.

        model = Model(transport=client, is_live=True)
        model.register_bridge(NullBridge())
        model.set_selection(axes)
        model.create_set(args.preset)
        for motor in model.all_motors:
            motor.update_params(preset.values_for(motor.axis))

        # Prepare, rather than the shortcut the earlier live scripts all took
        # of forcing _homed_axes and MachineState.HOMED. That shortcut only
        # worked because those scripts inherited an array that was already
        # energised; run it on drives that need booting and the boot clears the
        # homed reference underneath you, so the run is commanded on unhomed
        # drives and the array sits perfectly still.
        #
        # Going through the real Prepare also means this exercises the path the
        # operator actually uses: select, clear faults, boot, write, home.
        model.rest_position = args.rest
        print("Preparing: clearing faults, booting and homing "
              "(about a minute)...", flush=True)
        if not model.prepare():
            raise RuntimeError("prepare() refused to start")
        deadline = time.time() + 240.0
        while (model.state is MachineState.PREPARING
               and time.time() < deadline):
            time.sleep(0.5)
        record["after_prepare"] = read_health(client, axes)
        if model.state is not MachineState.HOMED:
            raise RuntimeError(
                "prepare ended in {0}; unhomed pistons {1}".format(
                    model.state, tags.display_list(model.unhomed_axes)))
        print("Prepared: {0} drives energised and homed.".format(len(axes)),
              flush=True)

        if not model.start(RunMode.CONTINUOUS):
            raise RuntimeError("the run would not start")
        deadline = time.time() + 20.0
        while model.state is not MachineState.RUNNING and time.time() < deadline:
            time.sleep(0.1)
        if model.state is not MachineState.RUNNING:
            raise RuntimeError("never reached RUNNING; state is {0}".format(model.state))

        print("Running. Sampling for {0:.0f} s...".format(args.seconds), flush=True)
        samples = []
        started = time.time()
        finish = started + args.seconds
        while time.time() < finish:
            samples.append({"at": time.time() - started,
                            "positions": read_positions(client, axes)})
            time.sleep(MIN_SAMPLE_GAP)

        print("Stopping...", flush=True)
        model.stop()
        deadline = time.time() + 45.0
        while model.state is MachineState.RUNNING and time.time() < deadline:
            time.sleep(0.2)

        record["samples"] = samples
        record["after"] = read_health(client, axes)
        result = analyse(samples, axes)
        record["analysis"] = result
        record["findings"] = judge(result, float(stroke))

        print("\n--- sync over {0:.0f} s, {1} samples at {2} Hz ---".format(
            args.seconds, result["samples"], result["sample_hz"]), flush=True)
        print("spread between pistons: early {early_mean} mm, late "
              "{late_mean} mm, worst {worst} mm (growth {growth} mm)".format(
                  **result["spread_mm"]), flush=True)
        print("cycle time per piston: {0}".format(result["cycle_seconds"]), flush=True)
        print("spread in cycle time: {0} s".format(
            result["cycle_spread_seconds"]), flush=True)
        print("travel per piston: {0}".format(result["travel_mm"]), flush=True)
        if record["findings"]:
            print("")
            for finding in record["findings"]:
                print("  !! {severity}: {what}".format(**finding), flush=True)
        else:
            print("\nNo drift found against the thresholds.", flush=True)

    except KeyboardInterrupt:
        record["aborted"] = "interrupted at the keyboard"
        code = 130
    except Exception as exc:  # noqa: BLE001 - the report matters more
        record["aborted"] = "{0}: {1}".format(type(exc).__name__, exc)
        record["traceback"] = traceback.format_exc()
        print("\nABORTED -- {0}".format(record["aborted"]), flush=True)
        code = 1
    finally:
        try:
            if model is not None:
                model.stop_monitoring()
        except Exception:  # noqa: BLE001
            pass
        for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS,
                    tags.RUN_CURVE, tags.HOME_BUTTON):
            try:
                client.write(tag, 0)
            except Exception:  # noqa: BLE001 - keep clearing the rest
                pass
        try:
            record["final"] = read_health(client, axes)
        except Exception:  # noqa: BLE001
            pass
        client.close()
        folder = root / "logs" / "verification"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("sync-drift-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        target.write_text(json.dumps(record, indent=2, default=str),
                          encoding="utf-8")
        print("Saved {0}".format(target), flush=True)

    return code


if __name__ == "__main__":
    sys.exit(main())
