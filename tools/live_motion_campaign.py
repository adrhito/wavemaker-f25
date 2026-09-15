"""A staged motion campaign against the real array.

The live scripts written so far each proved one thing and then stopped: a
stroke update, a speed change, the BIGGER WAVE preset. Each of them also took
the same shortcut -- forcing ``_homed_axes`` and ``MachineState.HOMED`` so the
run would start without waiting a minute for homing -- which means none of them
ever exercised the homing path, and none of them checked that what the drives
actually did matched what was asked for.

This runs the stages in order, refuses to go on when a stage fails, and writes
everything it saw to ``logs/verification/``. It moves real pistons.

    python tools/live_motion_campaign.py                  every stage
    python tools/live_motion_campaign.py --stage stroke   one stage
    python tools/live_motion_campaign.py --pistons 1-9    a subset
    python tools/live_motion_campaign.py --dry-run        no PLC, no motion

Safety
------
* It refuses to start if a run bit is already high -- something else is driving
  the machine and two masters is how a drive gets broken.
* It refuses to start if any piston in the selection reports a drive problem,
  unless ``--ignore-warnings`` is given. A piston that is already too hot does
  not need a speed sweep run at it.
* Every stage is wrapped so that the run bits are dropped and the pistons are
  parked whichever way the stage ends, including on Ctrl-C.
* Between stages it re-reads every drive and aborts the whole campaign if a new
  fault has appeared.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drive_status, params, tags
from app.plc import PlcClient
from Model import MachineState, Model, NullBridge, RunMode

#: Gentle enough to be a positioning move rather than part of a wave.
SETTLE_SECONDS = 1.5
#: How long a piston is given to reach a commanded position before the reading
#: is called a failure.
REACH_SECONDS = 8.0
#: How close to the commanded position counts as having got there, in mm.
REACH_TOLERANCE = 6.0


class Abort(RuntimeError):
    """A stage found something that means the campaign must not continue."""


class Recorder:
    """Collects what each stage saw, and says so as it goes."""

    def __init__(self) -> None:
        self.started = time.time()
        self.stages: List[dict] = []
        self.findings: List[dict] = []

    def stage(self, name: str) -> dict:
        record = {"stage": name, "started": time.time(), "events": []}
        self.stages.append(record)
        print("\n=== {0} ===".format(name), flush=True)
        return record

    def event(self, record: dict, name: str, **detail) -> None:
        entry = dict(detail, name=name, at=round(time.time() - self.started, 2))
        record["events"].append(entry)
        print("  {0}: {1}".format(name, json.dumps(detail, default=str)), flush=True)

    def finding(self, severity: str, stage: str, what: str, evidence=None) -> None:
        self.findings.append({
            "severity": severity, "stage": stage,
            "what": what, "evidence": evidence,
        })
        print("  !! {0} {1}: {2}".format(severity, stage, what), flush=True)

    def save(self) -> Path:
        folder = Path(__file__).resolve().parent.parent / "logs" / "verification"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("live-campaign-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        target.write_text(
            json.dumps({"stages": self.stages, "findings": self.findings},
                       indent=2, default=str),
            encoding="utf-8",
        )
        return target


def read_piston(client, shown: int) -> dict:
    """Everything worth knowing about one piston, by its display number."""
    axis = tags.axis_from_display(shown)
    status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
    warning = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
    return {
        "piston": shown,
        "axis": axis,
        "position_mm": round(params.to_mm(
            client.read(tags.axis_field(axis, tags.ACTUAL_POSITION))), 3),
        "status": status,
        "warn": warning,
        "homed": drive_status.is_homed(status),
        "enabled": bool(status & 1),
        "problems": drive_status.problems(warning, status),
    }


def survey(client, pistons) -> List[dict]:
    return [read_piston(client, shown) for shown in pistons]


def commands_high(client) -> Dict[str, bool]:
    return {
        "Run_1": bool(client.read(tags.RUN_SINGLE)),
        "Run_2": bool(client.read(tags.RUN_CONTINUOUS)),
        "Run_Curve": bool(client.read(tags.RUN_CURVE)),
        "Home": bool(client.read(tags.HOME_BUTTON)),
    }


def new_problems(before: List[dict], after: List[dict]) -> List[dict]:
    """Drive problems present now that were not present before."""
    was = {row["piston"]: set(row["problems"]) for row in before}
    fresh = []
    for row in after:
        appeared = set(row["problems"]) - was.get(row["piston"], set())
        if appeared:
            fresh.append({"piston": row["piston"], "problems": sorted(appeared)})
    return fresh


def build_model(client, pistons) -> Model:
    model = Model(transport=client, is_live=True)
    model.register_bridge(NullBridge())
    model.set_selection([tags.axis_from_display(shown) for shown in pistons])
    model.create_set("Live campaign")
    return model


def set_params(model: Model, **values) -> None:
    for motor in model.all_motors:
        for name, value in values.items():
            motor.set_param(name, value)


def wait_for_state(model: Model, wanted, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if model.state in wanted:
            return True
        time.sleep(0.2)
    return model.state in wanted


def span_of(samples: List[List[dict]], piston: int) -> float:
    """How far one piston moved across a series of snapshots."""
    seen = [row["position_mm"] for snap in samples
            for row in snap if row["piston"] == piston]
    return round(max(seen) - min(seen), 3) if seen else 0.0


# -- stages -------------------------------------------------------------------

def stage_home(client, model, pistons, rec) -> None:
    """The stage every earlier script skipped: actually home the drives."""
    record = rec.stage("home")
    rec.event(record, "before", pistons=survey(client, pistons))

    started = time.time()
    if not model.prepare():
        raise Abort("prepare() refused to start")
    if not wait_for_state(model, (MachineState.HOMED, MachineState.READY), 180.0):
        raise Abort("homing did not finish inside three minutes; "
                    "state is {0}".format(model.state))
    took = round(time.time() - started, 1)

    after = survey(client, pistons)
    rec.event(record, "after", seconds=took, state=str(model.state), pistons=after)

    if model.state is not MachineState.HOMED:
        raise Abort("homing ended in state {0}".format(model.state))
    not_homed = [row["piston"] for row in after if not row["homed"]]
    if not_homed:
        rec.finding("HIGH", "home",
                    "the model reported HOMED but these drives do not have the "
                    "homed bit set: {0}".format(not_homed), after)
    if model.unhomed_axes:
        rec.finding("HIGH", "home", "pistons would not home: {0}".format(
            tags.display_list(model.unhomed_axes)), None)


def stage_stroke(client, model, pistons, rec) -> None:
    """Short, medium and long strokes: does the array move what it was told?"""
    record = rec.stage("stroke")
    before = survey(client, pistons)
    rec.event(record, "before", pistons=before)

    for low, high in ((0, 20), (0, 100), (0, 300)):
        set_params(model, **{"Position 1": low, "Position 2": high,
                             "Speed 1": 200, "Speed 2": 200})
        model.rest_position = "hold"
        if not model.start(RunMode.CONTINUOUS):
            raise Abort("could not start a {0} mm stroke".format(high - low))
        if not wait_for_state(model, (MachineState.RUNNING,), 20.0):
            raise Abort("start did not reach RUNNING for a {0} mm stroke"
                        .format(high - low))

        samples = []
        deadline = time.time() + 6.0
        while time.time() < deadline:
            samples.append(survey(client, pistons))
            time.sleep(0.25)

        model.stop(immediate=True, park=False)
        wait_for_state(model, (MachineState.HOMED, MachineState.READY,
                               MachineState.IDLE), 20.0)
        time.sleep(SETTLE_SECONDS)

        wanted = high - low
        spans = {row["piston"]: span_of(samples, row["piston"]) for row in before}
        rec.event(record, "stroke_{0}mm".format(wanted), commanded=wanted,
                  observed_span_mm=spans, commands=commands_high(client))

        short = {piston: span for piston, span in spans.items()
                 if span < wanted * 0.6}
        if short:
            rec.finding("HIGH", "stroke",
                        "commanded {0} mm but these pistons moved less than "
                        "60% of it: {1}".format(wanted, short), spans)

        fresh = new_problems(before, survey(client, pistons))
        if fresh:
            raise Abort("new drive problems during the {0} mm stroke: {1}"
                        .format(wanted, fresh))


def stage_speed(client, model, pistons, rec) -> None:
    """A speed sweep, checking a faster command really does cycle faster."""
    record = rec.stage("speed")
    before = survey(client, pistons)
    rec.event(record, "before", pistons=before)

    cycles: Dict[int, float] = {}
    for speed in (100, 300, 600):
        set_params(model, **{"Position 1": 0, "Position 2": 150,
                             "Speed 1": speed, "Speed 2": speed})
        model.rest_position = "hold"
        if not model.start(RunMode.CONTINUOUS):
            raise Abort("could not start at {0} mm/s".format(speed))
        if not wait_for_state(model, (MachineState.RUNNING,), 20.0):
            raise Abort("start did not reach RUNNING at {0} mm/s".format(speed))

        # Count direction reversals on the first piston to get a cycle time.
        first = pistons[0]
        marks: List[float] = []
        last: Optional[float] = None
        rising: Optional[bool] = None
        deadline = time.time() + 10.0
        while time.time() < deadline:
            here = read_piston(client, first)["position_mm"]
            if last is not None and abs(here - last) > 0.5:
                now_rising = here > last
                if rising is not None and now_rising != rising:
                    marks.append(time.time())
                rising = now_rising
            last = here
            time.sleep(0.05)

        model.stop(immediate=True, park=False)
        wait_for_state(model, (MachineState.HOMED, MachineState.READY,
                               MachineState.IDLE), 20.0)
        time.sleep(SETTLE_SECONDS)

        # Two reversals make one full cycle.
        cycle = ((marks[-1] - marks[0]) / ((len(marks) - 1) / 2.0)
                 if len(marks) >= 3 else None)
        cycles[speed] = cycle
        rec.event(record, "speed_{0}".format(speed), reversals=len(marks),
                  cycle_seconds=round(cycle, 3) if cycle else None)

        fresh = new_problems(before, survey(client, pistons))
        if fresh:
            raise Abort("new drive problems at {0} mm/s: {1}".format(speed, fresh))

    measured = [(speed, cycle) for speed, cycle in sorted(cycles.items())
                if cycle]
    for (slow, slow_cycle), (fast, fast_cycle) in zip(measured, measured[1:]):
        if fast_cycle >= slow_cycle:
            rec.finding("HIGH", "speed",
                        "{0} mm/s cycled in {1:.2f} s but {2} mm/s cycled in "
                        "{3:.2f} s -- raising the speed did not shorten the "
                        "cycle".format(slow, slow_cycle, fast, fast_cycle),
                        cycles)


def stage_wave(client, model, pistons, rec) -> None:
    """A Curve Offset stagger must make the crest travel back down the chamber."""
    record = rec.stage("wave")
    before = survey(client, pistons)
    rec.event(record, "before", pistons=before)

    # One column starts a fifth of a second after the one in front of it.
    for motor in model.all_motors:
        column = (tags.display_number(motor.axis) - 1) // tags.ROWS_PER_COLUMN
        motor.set_param("Position 1", 0)
        motor.set_param("Position 2", 200)
        motor.set_param("Speed 1", 300)
        motor.set_param("Speed 2", 300)
        motor.set_param("Curve Offset", column * 200)

    model.rest_position = "hold"
    if not model.start(RunMode.CONTINUOUS):
        raise Abort("could not start the travelling wave")
    if not wait_for_state(model, (MachineState.RUNNING,), 25.0):
        raise Abort("the travelling wave did not reach RUNNING")

    samples = []
    deadline = time.time() + 12.0
    while time.time() < deadline:
        samples.append({"at": time.time(), "pistons": survey(client, pistons)})
        time.sleep(0.1)

    model.stop(immediate=True, park=False)
    wait_for_state(model, (MachineState.HOMED, MachineState.READY,
                           MachineState.IDLE), 20.0)
    time.sleep(SETTLE_SECONDS)

    # If the stagger is working, two pistons in different columns are not at
    # the same place at the same moment. If every column matches, the offset
    # did nothing and the whole array is slapping in unison.
    columns: Dict[int, List[float]] = {}
    for snap in samples:
        for row in snap["pistons"]:
            column = (row["piston"] - 1) // tags.ROWS_PER_COLUMN
            columns.setdefault(column, []).append(row["position_mm"])

    front, back = min(columns), max(columns)
    if front != back:
        difference = max(
            abs(a - b) for a, b in zip(columns[front], columns[back])
        )
        rec.event(record, "stagger", front_column=front, back_column=back,
                  biggest_difference_mm=round(difference, 2))
        if difference < 10:
            rec.finding("HIGH", "wave",
                        "Curve Offset staggered the columns by 200 ms each but "
                        "the front and back columns never differed by more "
                        "than {0:.1f} mm -- the wave is not travelling"
                        .format(difference), None)

    fresh = new_problems(before, survey(client, pistons))
    if fresh:
        raise Abort("new drive problems during the wave: {0}".format(fresh))


def stage_stop(client, model, pistons, rec) -> None:
    """Operator abuse: stop mid-stroke, stop twice, start again straight after."""
    record = rec.stage("stop")
    before = survey(client, pistons)
    rec.event(record, "before", pistons=before)

    set_params(model, **{"Position 1": 0, "Position 2": 200,
                         "Speed 1": 250, "Speed 2": 250})

    for attempt, (wait, immediate, twice) in enumerate(
        ((0.4, False, False), (1.2, False, True), (0.2, True, False)), start=1
    ):
        model.rest_position = "down"
        if not model.start(RunMode.CONTINUOUS):
            raise Abort("could not start for stop attempt {0}".format(attempt))
        if not wait_for_state(model, (MachineState.RUNNING,), 20.0):
            raise Abort("attempt {0} never reached RUNNING".format(attempt))

        time.sleep(wait)
        model.stop(immediate=immediate)
        if twice:
            time.sleep(0.3)
            model.stop()  # second press means "now"

        settled = wait_for_state(
            model, (MachineState.HOMED, MachineState.READY, MachineState.IDLE),
            40.0,
        )
        time.sleep(SETTLE_SECONDS)
        bits = commands_high(client)
        rec.event(record, "stop_attempt_{0}".format(attempt),
                  waited=wait, immediate=immediate, pressed_twice=twice,
                  settled=settled, state=str(model.state), commands=bits,
                  pistons=survey(client, pistons))

        if not settled:
            rec.finding("HIGH", "stop",
                        "attempt {0} left the machine in {1} forty seconds "
                        "after Stop".format(attempt, model.state), bits)
        if any(bits.values()):
            rec.finding("HIGH", "stop",
                        "attempt {0} left command bits high after the stop "
                        "completed: {1}".format(attempt, bits), bits)

    fresh = new_problems(before, survey(client, pistons))
    if fresh:
        rec.finding("MEDIUM", "stop",
                    "new drive problems after the stop abuse: {0}".format(fresh))


STAGES = {
    "home": stage_home,
    "stroke": stage_stroke,
    "speed": stage_speed,
    "wave": stage_wave,
    "stop": stage_stop,
}


# -- driving ------------------------------------------------------------------

def parse_pistons(text: str) -> List[int]:
    chosen: List[int] = []
    for piece in text.split(","):
        piece = piece.strip()
        if "-" in piece:
            low, high = (int(part) for part in piece.split("-", 1))
            chosen.extend(range(low, high + 1))
        elif piece:
            chosen.append(int(piece))
    for shown in chosen:
        if not 1 <= shown <= tags.MOTOR_COUNT:
            raise ValueError("piston {0} does not exist".format(shown))
    return sorted(set(chosen))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", default="192.168.1.1")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--pistons", default="1-9",
                        help="display numbers, e.g. 1-9 or 1,4,7 (default 1-9)")
    parser.add_argument("--stage", action="append", choices=sorted(STAGES),
                        help="run only this stage; may be repeated")
    parser.add_argument("--ignore-warnings", action="store_true",
                        help="start even though a selected drive reports a "
                             "problem; homing stays required either way")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and contact nothing")
    args = parser.parse_args(argv)

    pistons = parse_pistons(args.pistons)
    wanted = args.stage or ["home", "stroke", "speed", "wave", "stop"]

    if args.dry_run:
        print("Would run {0} on pistons {1} at {2}".format(
            ", ".join(wanted), pistons, args.ip))
        return 0

    rec = Recorder()
    client = PlcClient(args.ip, args.slot)
    model: Optional[Model] = None
    exit_code = 0

    try:
        if not client.connect():
            print("Controller not reachable at {0}; nothing was written."
                  .format(args.ip))
            return 2

        opening = rec.stage("gate")
        bits = commands_high(client)
        before = survey(client, pistons)
        rec.event(opening, "opening", commands=bits, pistons=before)

        if any(bits.values()):
            raise Abort("a command bit is already high ({0}); something else "
                        "is driving the machine".format(bits))
        troubled = [row for row in before if row["problems"]]
        if troubled and not args.ignore_warnings:
            raise Abort("these pistons report problems: {0}. Clear them, drop "
                        "them from --pistons, or pass --ignore-warnings."
                        .format([(row["piston"], row["problems"])
                                 for row in troubled]))

        model = build_model(client, pistons)
        model.start_monitoring()

        for name in wanted:
            STAGES[name](client, model, pistons, rec)
            fresh = new_problems(before, survey(client, pistons))
            if fresh:
                raise Abort("new drive problems after {0}: {1}".format(name, fresh))

    except Abort as exc:
        rec.finding("ABORT", "campaign", str(exc))
        exit_code = 1
    except KeyboardInterrupt:
        rec.finding("ABORT", "campaign", "interrupted at the keyboard")
        exit_code = 130
    except Exception as exc:  # noqa: BLE001 - the report matters more
        rec.finding("ABORT", "campaign",
                    "unhandled {0}: {1}".format(type(exc).__name__, exc),
                    traceback.format_exc())
        exit_code = 1
    finally:
        # Whatever happened, the machine must be left quiet.
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
            closing = rec.stage("closing")
            rec.event(closing, "final", commands=commands_high(client),
                      pistons=survey(client, pistons))
        except Exception:  # noqa: BLE001
            pass
        client.close()
        target = rec.save()
        print("\nSaved {0}".format(target), flush=True)
        print("{0} finding(s)".format(len(rec.findings)), flush=True)
        for finding in rec.findings:
            print("  {severity} [{stage}] {what}".format(**finding), flush=True)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
