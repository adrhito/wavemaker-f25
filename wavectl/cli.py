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

from wavectl import client, live, sessionfile

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
    if holder is not None and sessionfile.process_exists(holder.get("pid")):
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
        reply = client.request("shutdown")
    except client.Silent as exc:
        if not sessionfile.process_exists(pid):
            sessionfile.release()
            return _fail("the session (process {0}) had already died; its "
                         "session file is cleared.".format(pid),
                         args.json, mode, status=0)
        if not args.force:
            return _silent(exc, args.json)
        reply = None

    asked = reply is not None and reply.get("ok")
    if reply is not None and not asked and not args.force:
        return _show(reply, args.json)
    if asked and _wait_for_exit(pid, END_SECONDS):
        return _fail("session ended.", args.json, mode, status=0)
    if not args.force:
        return _fail("the session (process {0}) was asked to end and has not "
                     "yet. Repeat with --force to end it anyway.".format(pid),
                     args.json, mode, status=1)

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
