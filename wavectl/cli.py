"""The commands typed at a terminal.

Each one asks the session to do a single thing and prints the answer on one
line, led by ``mock:`` or ``LIVE:`` so no answer can be mistaken for the other
kind. ``--json`` prints the whole reply instead, for a program to read.

Exit status: 0 done, 1 refused or failed, 2 no session to ask.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Optional

from wavectl import client, live, sessionfile, timing

#: How long to wait for a session to finish ending before saying it has not.
END_SECONDS = 20.0


def _label(mode) -> str:
    return "LIVE" if mode == "live" else str(mode or "?")


def _show(reply: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(reply, sort_keys=True))
    else:
        print("{0}: {1}".format(_label(reply.get("mode")), reply.get("text", "")))
    return 0 if reply.get("ok") else 1


def _fail(text: str, as_json: bool, mode: Optional[str] = None, status: int = 2) -> int:
    if as_json:
        print(json.dumps({"ok": False, "mode": mode, "text": text, "data": {}},
                         sort_keys=True))
    else:
        print(text if mode is None else "{0}: {1}".format(_label(mode), text))
    return status


def _no_session(as_json: bool) -> int:
    return _fail("No session. Start one with: wavectl session start --mock "
                 "(a simulated wavemaker) or --live (the machine).", as_json)


def _silent(exc: client.Silent, as_json: bool) -> int:
    info = exc.info
    return _fail(
        "the session (process {0}) is not answering: {1}. If it has died, "
        "clear it with: wavectl session stop --force".format(info.get("pid", "?"), exc),
        as_json, info.get("mode"))


def _ask(args, operation: str, arguments: Optional[dict] = None,
         seconds: float = client.QUICK_SECONDS) -> int:
    try:
        reply = client.request(operation, arguments, seconds)
    except client.NoSession:
        return _no_session(args.json)
    except client.Silent as exc:
        return _silent(exc, args.json)
    return _show(reply, args.json)


# -- session -------------------------------------------------------------------

def _session_start(args) -> int:
    holder = sessionfile.read()
    if holder is not None and sessionfile.is_session(holder.get("pid")):
        return _fail(
            "a session is already running (process {0}, since {1}). End it "
            "with: wavectl session stop".format(
                holder.get("pid", "?"), holder.get("started", "?")),
            args.json, holder.get("mode"), status=1)
    reply = client.start_session(mock=args.mock, ip=args.ip, slot=args.slot,
                                 fast=args.fast)
    return _show(reply, args.json)


def _wait_for_exit(pid, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not sessionfile.process_exists(pid):
            return True
        time.sleep(0.1)
    return not sessionfile.process_exists(pid)


def _session_stop(args) -> int:
    info = sessionfile.read()
    if info is None:
        return _no_session(args.json)
    mode, pid = info.get("mode"), info.get("pid")
    try:
        client.request("ping", seconds=client.ALIVE_SECONDS)
        reply = client.request("shutdown")
    except client.Silent as exc:
        if not sessionfile.is_session(pid):
            sessionfile.release()
            gone = ("The session (process {0}) is gone; its session file is "
                    "cleared.".format(pid))
            if mode == "live":
                # However it died, it may not have dropped the run bits.
                return _stop_directly(args, info.get("ip"), info.get("slot"), gone)
            return _fail(gone, args.json, mode, status=0)
        if not args.force:
            return _silent(exc, args.json)
        reply = None

    asked = reply is not None and reply.get("ok")
    if reply is not None and not asked and not args.force:
        return _show(reply, args.json)
    if asked and _wait_for_exit(pid, END_SECONDS):
        if mode != "live":
            return _fail("session ended.", args.json, mode, status=0)
        # Not taken on trust: the session has gone, so the run bits are
        # written low and read back from here before saying they are.
        return _stop_directly(
            args, info.get("ip"), info.get("slot"),
            "Session ended and the drives of its pistons switched off, so one "
            "left above the bottom of travel may have sunk.")
    if not args.force:
        return _fail("the session (process {0}) was asked to end and has not "
                     "yet. Repeat with --force to end it anyway.".format(pid),
                     args.json, mode, status=1)

    if not sessionfile.is_session(pid):
        sessionfile.release()
        return _fail("process {0} is not a wavectl session, so it was left "
                     "alone; the stale session file is cleared.".format(pid),
                     args.json, mode, status=0)
    # Asked nicely first, so the session gets the chance to drop the run bits
    # and close its connection itself.
    for sent in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        if not sessionfile.process_exists(pid):
            break
        try:
            os.kill(int(pid), sent)
        except OSError:
            break
        _wait_for_exit(pid, 5.0)
    sessionfile.release()
    if mode == "live":
        # Killed, it had no chance to drop the run bits on its way out.
        return _stop_directly(args, info.get("ip"), info.get("slot"),
                              "Session (process {0}) ended by force.".format(pid))
    return _fail("session (process {0}) ended by force.".format(pid),
                 args.json, mode, status=0)


def _session_info(args) -> int:
    info = sessionfile.read()
    if info is None:
        return _no_session(args.json)
    shown = dict((key, value) for key, value in info.items() if key != "token")
    try:
        client.request("ping")
        answering = True
    except client.Silent:
        answering = False
    shown["answering"] = answering
    text = "{0} session, process {1}, started {2}{3}; {4}".format(
        info.get("mode", "?"), info.get("pid", "?"), info.get("started", "?"),
        " at {0}".format(info["ip"]) if info.get("ip") else "",
        "answering" if answering else "NOT answering")
    return _show({"ok": answering, "mode": info.get("mode"), "text": text,
                  "data": shown}, args.json)


# -- reading -------------------------------------------------------------------

def _status(args) -> int:
    return _ask(args, "status", {"pistons_table": args.pistons, "every": args.all})


def _log(args) -> int:
    return _ask(args, "log", {"count": args.count, "full": args.full})


# -- choosing what will move -----------------------------------------------------

def _select(args) -> int:
    return _ask(args, "select", {"pistons_text": args.pistons})


def _set(args) -> int:
    values = {}
    for pair in args.values:
        key, equals, value = pair.partition("=")
        if not equals or not key or not value:
            return _fail('"{0}" is not name=value, for example stroke=120 '
                         "speed=150.".format(pair), args.json, status=1)
        values[key] = value
    return _ask(args, "set", {"values": values, "pistons_text": args.pistons})


def _params(args) -> int:
    return _ask(args, "params", {"pistons_text": args.pistons})


def _preset(args) -> int:
    if args.list or not args.name:
        return _ask(args, "presets")
    return _ask(args, "preset", {"name": args.name, "select": args.select})


# -- motion --------------------------------------------------------------------

def _waited(args, operation: str, arguments: dict, patience: float) -> int:
    """Ask for something slow, waiting a little longer than the session will."""
    arguments = dict(arguments, patience=patience)
    return _ask(args, operation, arguments,
                seconds=patience + timing.ANSWER_MARGIN_SECONDS)


def _prepare(args) -> int:
    return _waited(args, "prepare", {
        "wait": not args.no_wait, "ignore_warnings": args.ignore_warnings},
        args.timeout or timing.PREPARE_SECONDS)


def _calibrate(args) -> int:
    return _waited(args, "calibrate", {"wait": not args.no_wait},
                   args.timeout or timing.PREPARE_SECONDS)


def _run(args) -> int:
    mode = "single" if args.single else "curve" if args.curve else "continuous"
    continuous = mode == "continuous"
    seconds = args.seconds if continuous else None
    if not continuous and args.seconds is not None:
        return _fail("--seconds sets how long a continuous run lasts; one "
                     "stroke or one curve ends by itself.", args.json, status=1)
    wait = True if args.wait else False if args.no_wait else None
    length = (seconds if seconds is not None else timing.DEFAULT_RUN_SECONDS)
    patience = args.timeout or (
        (length + timing.REST_SECONDS if continuous else timing.ONCE_SECONDS)
        + timing.START_SECONDS
        + (timing.PREPARE_SECONDS if args.prepare else 0.0))
    return _waited(args, "run", {
        "mode": mode, "seconds": seconds, "wait": wait, "prepare": args.prepare,
        "ignore_warnings": args.ignore_warnings, "sample_hz": args.sample},
        patience)


def _stop_directly(args, ip, slot, because: str = "") -> int:
    """Drop the command bits on a connection of this command's own."""
    try:
        ok, text = live.stop_directly(ip or live.DEFAULT_IP,
                                      live.DEFAULT_SLOT if slot is None else slot)
    except live.Unreachable as exc:
        ok, text = False, "{0} Use the physical stop.".format(exc)
    return _fail((because + " " if because else "") + text, args.json, "live",
                 status=0 if ok else 1)


def _stop(args) -> int:
    info = sessionfile.read() or {}
    if args.direct:
        return _stop_directly(args, args.ip or info.get("ip"),
                              args.slot if args.slot is not None else info.get("slot"))
    park = True if args.park else False if args.no_park else None
    try:
        # Asked first whether it is there at all. A stop may take most of a
        # minute to answer, because it waits for the pistons to come to
        # rest; a session that is alive but hung would be waited on for all
        # of that, with the machine still running, before this command took
        # over. This way it is three seconds.
        client.request("ping", seconds=client.ALIVE_SECONDS)
        reply = client.request(
            "stop", {"now": args.now, "park": park},
            timing.REST_SECONDS + timing.STILL_CAP_SECONDS
            + timing.ANSWER_MARGIN_SECONDS)
    except client.NoSession:
        return _fail("No session, so nothing started here is driving the "
                     "machine. If something else left a run going, drop it "
                     "with: wavectl stop --direct", args.json)
    except client.Silent as exc:
        if exc.info.get("mode") != "live":
            return _silent(exc, args.json)
        # Stop means stop. The session was ours and so are its run bits, so
        # when it cannot be asked the bits are written from here instead.
        return _stop_directly(
            args, exc.info.get("ip"), exc.info.get("slot"),
            "The session (process {0}) did not answer ({1}), so this command "
            "stopped the machine itself. Clear the session with: wavectl "
            "session stop --force.".format(exc.info.get("pid", "?"), exc))
    if reply.get("ok") or reply.get("mode") != "live":
        return _show(reply, args.json)
    # The session answered but could not stop the machine, which usually
    # means its connection to the controller is what failed. Try a fresh one.
    return _stop_directly(args, info.get("ip"), info.get("slot"),
                          "The session could not stop the machine ({0}), so "
                          "this command tried directly.".format(reply.get("text", "")))


def _survey(args) -> int:
    try:
        seen = live.survey(args.ip, args.slot)
    except live.Unreachable as exc:
        return _fail(str(exc), args.json, "live", status=1)
    rows = seen["drives"]
    parts = [", ".join(seen["bits_high"]) + " HIGH" if seen["bits_high"]
             else "run bits clear",
             "{0} of {1} drives homed, {2} enabled".format(
                 sum(1 for row in rows if row.get("homed")), len(rows),
                 sum(1 for row in rows if row.get("enabled"))),
             live.troubled(rows) or "drives ok"]
    text = "read only, {0} | ".format(seen["controller"] or args.ip) + " | ".join(parts)
    if args.pistons:
        text += "".join("\n" + live.drive_line(row) for row in rows)
    return _show({"ok": True, "mode": "live", "text": text, "data": seen}, args.json)


def _park(args) -> int:
    return _ask(args, "park", seconds=timing.PREPARE_SECONDS + timing.REST_SECONDS)


def _speed(args) -> int:
    return _ask(args, "speed", {"value": args.value})


def _stroke(args) -> int:
    return _ask(args, "stroke", {"value": args.value})


def _wait(args) -> int:
    return _waited(args, "wait", {}, args.timeout)


def _watch(args) -> int:
    return _ask(args, "watch", {"seconds": args.seconds, "hz": args.hz,
                                "pistons_text": args.pistons},
                seconds=args.seconds + timing.ANSWER_MARGIN_SECONDS)


# -- soliton trials ------------------------------------------------------------

def _trial_numbers(args) -> dict:
    return {"crest_mm": args.crest, "depth_mm": args.depth,
            "width_mm": args.width, "lift_mm": args.lift}


def _soliton_plan(args) -> int:
    return _ask(args, "soliton_plan", _trial_numbers(args))


def _soliton_stage(args) -> int:
    return _waited(args, "soliton_stage", dict(
        _trial_numbers(args), prepare=args.prepare, wait=not args.no_wait),
        args.timeout or timing.PREPARE_SECONDS)


def _soliton_fire(args) -> int:
    return _waited(args, "soliton_fire", {
        "station_mm": args.station_mm, "sample_hz": args.sample,
        "wait": not args.no_wait}, args.timeout or timing.PULSE_SECONDS)


def _soliton_observe(args) -> int:
    return _ask(args, "soliton_observe", {
        "crest_rise_mm": args.crest_rise, "crest_to_trough_mm": args.crest_to_trough,
        "width_mm": args.width, "station_mm": args.station_mm,
        "notes": args.notes,
        "record": os.path.abspath(args.record) if args.record else None})


def _trial(args) -> int:
    patience = args.timeout or (
        timing.PREPARE_SECONDS + args.settle + args.pre + timing.PULSE_SECONDS
        + args.post + timing.MEASURE_SECONDS)
    return _waited(args, "trial", dict(
        _trial_numbers(args), settle=args.settle, camera_source=args.camera,
        # The session resolves paths from the checkout, not from wherever
        # this command was typed.
        rig=os.path.abspath(args.rig) if args.rig else None,
        still_mm=args.still_mm, pre=args.pre, post=args.post,
        station_mm=args.station_mm, record_observation=args.record_observation,
        prepare=args.prepare, sample_hz=args.sample, wait=not args.no_wait),
        patience)


def _camera(args) -> int:
    return _ask(args, "camera", {
        "camera_source": args.source,
        "rig": os.path.abspath(args.rig) if args.rig else None,
        "seconds": args.seconds}, seconds=args.seconds + 40.0)


# -- the parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="print the whole reply as JSON instead of one line")

    parser = argparse.ArgumentParser(
        prog="wavectl", description="Drive the wavemaker from a terminal.",
        epilog="Start with: wavectl session start --mock    then: wavectl status")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.required = True

    session = commands.add_parser(
        "session", help="start or end the process that holds the connection")
    actions = session.add_subparsers(dest="action", metavar="action")
    actions.required = True

    start = actions.add_parser("start", parents=[common],
                               help="start a session on the mock or the machine")
    kind = start.add_mutually_exclusive_group(required=True)
    kind.add_argument("--mock", action="store_true",
                      help="a simulated wavemaker; nothing physical moves")
    kind.add_argument("--live", action="store_true",
                      help="the real controller")
    start.add_argument("--ip", default=live.DEFAULT_IP,
                       help="controller address (default %(default)s)")
    start.add_argument("--slot", type=int, default=live.DEFAULT_SLOT,
                       help="processor slot (default %(default)s)")
    start.add_argument("--fast", action="store_true",
                       help="mock only: shorten the boot and homing waits")
    start.set_defaults(run=_session_start)

    stop = actions.add_parser("stop", parents=[common], help="end the session")
    stop.add_argument("--force", action="store_true",
                      help="end the process even if it does not answer")
    stop.set_defaults(run=_session_stop)

    info = actions.add_parser("info", parents=[common],
                              help="which session exists, and whether it answers")
    info.set_defaults(run=_session_info)

    status = commands.add_parser("status", parents=[common],
                                 help="where the machine is, in one line")
    status.add_argument("-p", "--pistons", action="store_true",
                        help="add a line per selected piston")
    status.add_argument("--all", action="store_true",
                        help="read all thirty drives, not only the selection")
    status.set_defaults(run=_status)

    select = commands.add_parser(
        "select", parents=[common], help="choose the pistons that will run",
        description="Pistons are the numbers drawn on the tank, 1 to 30: "
                    "1-9, 1,4,7, c1-3 (columns from the front), r1 (a row), "
                    "all or none.")
    select.add_argument("pistons")
    select.set_defaults(run=_select)

    setter = commands.add_parser(
        "set", parents=[common], help="change parameters, e.g. stroke=120 speed=150",
        description="Names: stroke, speed, accel, decel, jerk, dwell, p1, p2, "
                    "profile, offset, rest (down, up or hold), or any full "
                    "parameter name without its space, such as speed2.")
    setter.add_argument("values", nargs="+", metavar="name=value")
    setter.add_argument("--pistons", help="only these pistons of the selection")
    setter.set_defaults(run=_set)

    shown = commands.add_parser("params", parents=[common],
                                help="every parameter the selection is set to")
    shown.add_argument("--pistons", help="only these pistons of the selection")
    shown.set_defaults(run=_params)

    preset = commands.add_parser(
        "preset", parents=[common], help="apply a saved preset to the selection",
        description="The start of a preset's name is enough if it picks out "
                    "one file.")
    preset.add_argument("name", nargs="?")
    preset.add_argument("--select", action="store_true",
                        help="select the pistons the preset gives motion to")
    preset.add_argument("--list", action="store_true", help="name the presets")
    preset.set_defaults(run=_preset)

    slow = argparse.ArgumentParser(add_help=False)
    slow.add_argument("--timeout", type=float, metavar="SECONDS",
                      help="stop waiting for the answer after this long; the "
                           "machine carries on, and wavectl wait picks it up")

    prepare = commands.add_parser(
        "prepare", parents=[common, slow],
        help="write parameters and home the selection (about a minute)",
        description="Boots the drives, which drops every selected piston to "
                    "zero, then homes them. Pistons this session has already "
                    "homed are not homed again.")
    prepare.add_argument("--no-wait", action="store_true",
                         help="return at once; wavectl wait reports the outcome")
    prepare.add_argument("--ignore-warnings", action="store_true",
                         help="go ahead although a selected drive reports a fault")
    prepare.set_defaults(run=_prepare)

    calibrate = commands.add_parser(
        "calibrate", parents=[common, slow],
        help="home all thirty pistons, whatever is selected")
    calibrate.add_argument("--no-wait", action="store_true")
    calibrate.set_defaults(run=_calibrate)

    run = commands.add_parser(
        "run", parents=[common, slow],
        help="run the selection: continuously for a set time, or once",
        description="A continuous run stops itself after --seconds and rests "
                    "the pistons. The pistons must be homed first: wavectl "
                    "prepare.")
    once = run.add_mutually_exclusive_group()
    once.add_argument("--single", action="store_true", help="one stroke, up and back")
    once.add_argument("--curve", action="store_true", help="one curve run")
    run.add_argument("--seconds", type=float,
                     help="how long a continuous run lasts (default {0:.0f}, "
                          "at most {1:.0f})".format(timing.DEFAULT_RUN_SECONDS,
                                                    timing.MAX_RUN_SECONDS))
    waiting = run.add_mutually_exclusive_group()
    waiting.add_argument("--wait", action="store_true",
                         help="answer when the run is over (the default for "
                              "one stroke or curve)")
    waiting.add_argument("--no-wait", action="store_true",
                         help="answer as soon as it is under way (the default "
                              "for a continuous run)")
    run.add_argument("--prepare", action="store_true",
                     help="boot and home first if the pistons need it")
    run.add_argument("--ignore-warnings", action="store_true",
                     help="go ahead although a selected drive reports a fault")
    run.add_argument("--sample", type=float, nargs="?", const=timing.SAMPLE_HZ,
                     metavar="HZ",
                     help="record piston positions to a file for the whole run "
                          "(default %(const)g Hz); the answer names the file")
    run.set_defaults(run=_run)

    stop = commands.add_parser(
        "stop", parents=[common], help="stop the machine",
        description="By default each piston finishes its stroke and the array "
                    "then moves to its resting position. If a session on "
                    "the machine does not answer, the run bits are written "
                    "low from here instead.")
    stop.add_argument("--now", action="store_true",
                      help="drop the run bits at once; pistons stay where they are")
    resting = stop.add_mutually_exclusive_group()
    resting.add_argument("--park", action="store_true",
                         help="move to the resting position afterwards")
    resting.add_argument("--no-park", action="store_true",
                         help="leave the pistons where the stop catches them")
    stop.add_argument("--direct", action="store_true",
                      help="write the run bits low on the controller without "
                           "asking the session, whoever raised them")
    stop.add_argument("--ip", help="with --direct: the controller's address")
    stop.add_argument("--slot", type=int, help="with --direct: processor slot")
    stop.set_defaults(run=_stop)

    survey = commands.add_parser(
        "survey", parents=[common],
        help="read the run bits and all thirty drives; writes nothing",
        description="Needs no session and changes nothing, so it is the look "
                    "to take before starting one on the machine.")
    survey.add_argument("--ip", default=live.DEFAULT_IP)
    survey.add_argument("--slot", type=int, default=live.DEFAULT_SLOT)
    survey.add_argument("-p", "--pistons", action="store_true",
                        help="add a line per piston")
    survey.set_defaults(run=_survey)

    park = commands.add_parser("park", parents=[common],
                               help="move the selection to its resting position")
    park.set_defaults(run=_park)

    speed = commands.add_parser("speed", parents=[common],
                                help="change speed on a run that is going (mm/s)")
    speed.add_argument("value", type=int)
    speed.set_defaults(run=_speed)

    stroke = commands.add_parser("stroke", parents=[common],
                                 help="change stroke on a run that is going (mm)")
    stroke.add_argument("value", type=int)
    stroke.set_defaults(run=_stroke)

    wait = commands.add_parser(
        "wait", parents=[common],
        help="wait for a prepare or run to finish and report how it went")
    wait.add_argument("--timeout", type=float, default=120.0, metavar="SECONDS")
    wait.set_defaults(run=_wait)

    watch = commands.add_parser(
        "watch", parents=[common],
        help="record piston positions to a file for a while; reads only",
        description="Works alongside a run. The file is CSV: seconds, then a "
                    "column of millimetres per piston.")
    watch.add_argument("--seconds", type=float, default=10.0)
    watch.add_argument("--hz", type=float, default=timing.SAMPLE_HZ)
    watch.add_argument("--pistons", help="which pistons (default: the selection)")
    watch.set_defaults(run=_watch)

    wave = argparse.ArgumentParser(add_help=False)
    wave.add_argument("--crest", type=float, required=True, metavar="MM",
                      help="crest rise wanted above still water")
    wave.add_argument("--depth", type=float, required=True, metavar="MM",
                      help="still-water depth, as measured")
    wave.add_argument("--width", type=float, metavar="MM",
                      help="width at half height (default: the first-order "
                           "width for this crest and depth)")
    wave.add_argument("--lift", type=int, metavar="MM",
                      help="floor travel (default: what moves one soliton's "
                           "worth of water with the selected pistons)")

    soliton = commands.add_parser(
        "soliton", help="stage the floor and fire one solitary-wave pulse",
        description="The Soliton tab's two buttons. The floor is staged low, "
                    "the water is left to settle, and one upward pulse is "
                    "fired and held. No wave height is predicted here.")
    moves = soliton.add_subparsers(dest="action", metavar="action")
    moves.required = True

    plan = moves.add_parser("plan", parents=[common, wave],
                            help="what a trial would command; moves nothing")
    plan.set_defaults(run=_soliton_plan)

    stage = moves.add_parser(
        "stage", parents=[common, wave, slow],
        help="lower the selected floor sections, ready for a pulse")
    stage.add_argument("--prepare", action="store_true",
                       help="boot and home first if the pistons need it")
    stage.add_argument("--no-wait", action="store_true")
    stage.set_defaults(run=_soliton_stage)

    fire = moves.add_parser("fire", parents=[common, slow],
                            help="send the staged pulse and hold the floor up")
    fire.add_argument("--station-mm", type=float, metavar="MM",
                      help="where along the tank the wave will be measured")
    fire.add_argument("--sample", type=float, nargs="?", const=50.0, metavar="HZ",
                      help="record the floor's positions through the pulse "
                           "(default %(const)g Hz)")
    fire.add_argument("--no-wait", action="store_true")
    fire.set_defaults(run=_soliton_fire)

    observe = moves.add_parser(
        "observe", parents=[common],
        help="add a measured wave to the last pulse's record")
    observe.add_argument("--crest-rise", type=float, metavar="MM")
    observe.add_argument("--crest-to-trough", type=float, metavar="MM")
    observe.add_argument("--width", type=float, metavar="MM",
                         help="measured width at half height")
    observe.add_argument("--station-mm", type=float, metavar="MM",
                         help="where along the tank it was measured")
    observe.add_argument("--notes", default="")
    observe.add_argument("--record", metavar="PATH",
                         help="a trial file other than the last pulse's")
    observe.set_defaults(run=_soliton_observe)

    trial = commands.add_parser(
        "trial", parents=[common, wave, slow],
        help="stage, wait for the water, fire one pulse, and measure it",
        description="One job on the session's clock: open the camera and find "
                    "the rig, stage the floor, wait for the water, start "
                    "recording, fire, keep recording while the wave passes, "
                    "measure the clip. Without --camera it is stage, wait, "
                    "fire. A trial with a long --settle outlasts most "
                    "terminals' patience: use --no-wait, then wavectl wait.")
    trial.add_argument("--settle", type=float, default=60.0, metavar="SECONDS",
                       help="how long to leave the water after staging "
                            "(default %(default)g); with --still-mm, the "
                            "longest to wait")
    trial.add_argument("--camera", metavar="N",
                       help='camera number to record with, or "synthetic" for '
                            "a drawn tank to rehearse against")
    trial.add_argument("--rig", metavar="PATH",
                       help="rig file for this camera position; the clip is "
                            "measured when it is given")
    trial.add_argument("--still-mm", type=float, metavar="MM",
                       help="fire as soon as the camera reads the surface "
                            "moving less than this; do not fire if it never does")
    trial.add_argument("--pre", type=float, default=2.0, metavar="SECONDS",
                       help="still water recorded before the pulse (default %(default)g)")
    trial.add_argument("--post", type=float, default=8.0, metavar="SECONDS",
                       help="recording kept running after the pulse is sent "
                            "(default %(default)g)")
    trial.add_argument("--station-mm", type=float, metavar="MM",
                       help="where along the tank to report the height "
                            "(default: the middle of the view)")
    trial.add_argument("--record-observation", action="store_true",
                       help="also enter the camera's measurement in the "
                            "trial's own record")
    trial.add_argument("--prepare", action="store_true",
                       help="boot and home first if the pistons need it")
    trial.add_argument("--sample", type=float, nargs="?", const=50.0, metavar="HZ",
                       help="record the floor's positions through the pulse")
    trial.add_argument("--no-wait", action="store_true")
    trial.set_defaults(run=_trial)

    look = commands.add_parser(
        "camera", parents=[common],
        help="open the camera as a trial would and say what it gives",
        description="Moves nothing. Run it before the first trial of a "
                    "sitting: it is the session that opens the camera, so "
                    "this is where a missing permission or a rig out of view "
                    "shows up.")
    look.add_argument("source", metavar="N",
                      help='camera number, or "synthetic" for the drawn tank')
    look.add_argument("--rig", metavar="PATH")
    look.add_argument("--seconds", type=float, default=3.0)
    look.set_defaults(run=_camera)

    log = commands.add_parser("log", parents=[common],
                              help="the last things the machine reported")
    log.add_argument("-n", "--count", type=int, default=10)
    log.add_argument("--full", action="store_true",
                     help="whole messages, not their first paragraph")
    log.set_defaults(run=_log)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
