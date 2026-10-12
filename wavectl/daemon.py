"""The session: one long-lived process that holds the PLC connection.

Started by ``wavectl session start`` and not meant to be run by hand. It
builds the transport and the model, claims the session file, and then answers
requests on a port of this computer until it is told to end.

Why a process of its own
------------------------
``Model`` trusts a piston's homed bit only if this process homed it. A drive
can power up with the bit set while standing somewhere it was never
referenced, and running a stroke from there is how a piston gets driven into
its end stop. So whichever process homes the pistons has to be the one that
runs them, and it has to stay alive between commands.

How it ends
-----------
However it ends -- asked to, signalled, or crashed out of the serving loop --
the run bits are dropped and the session file is removed on the way out. The
one case that cannot be covered from in here is the process being killed
outright; ``wavectl stop`` covers that from outside by writing the run bits
low itself when the session it expected does not answer.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import signal
import socketserver
import sys
import threading
import time
from logging import getLogger
from pathlib import Path
from typing import Optional

from wavectl import VERSION, live, places, sessionfile, wire


class _Server(socketserver.ThreadingTCPServer):
    # A thread per request, so Stop is answered while a homing request is
    # still holding its own connection open.
    daemon_threads = True
    allow_reuse_address = True

    service = None
    token = ""


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            message = wire.receive(self.rfile)
        except (ConnectionError, ValueError, OSError):
            return
        server = self.server
        if not hmac.compare_digest(str(message.get("token", "")), server.token):
            reply = {"ok": False, "mode": server.service.mode, "data": {},
                     "text": "That request did not carry this session's token."}
        else:
            reply = server.service.handle(message.get("op"), message.get("args"))
        try:
            wire.send(self.wfile, reply)
        except OSError:
            pass  # the client gave up waiting; the work was still done
        if message.get("op") == "shutdown" and reply.get("ok"):
            # serve_forever cannot be stopped from one of its own request
            # threads without a second thread to ask it.
            threading.Thread(target=server.shutdown, daemon=True).start()


def _quicken() -> None:
    """Shorten the model's waits, for a mock that is being rehearsed against.

    The mock keeps the machine's real pulse lengths so the window behaves the
    same with and without hardware, which makes preparing it take most of a
    minute. Nothing is being waited *for* in a mock, so a rehearsal may skip
    most of it. Never applied to the real controller.
    """
    import Model as model_module

    model_module.BOOT_PULSE_SECONDS = 0.2
    model_module.CLEAR_FAULT_SECONDS = 0.2
    model_module.HOME_POLL_SECONDS = 0.5


def _announce(ready_file: Optional[str], reply: dict) -> None:
    """Tell the command that started this process how the start went."""
    if not ready_file:
        return
    target = Path(ready_file)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(reply), encoding="utf-8")
    os.replace(str(temporary), str(target))


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wavectl.daemon", description=__doc__.splitlines()[0])
    kind = parser.add_mutually_exclusive_group(required=True)
    kind.add_argument("--mock", action="store_true")
    kind.add_argument("--live", action="store_true")
    parser.add_argument("--ip", default=live.DEFAULT_IP)
    parser.add_argument("--slot", type=int, default=live.DEFAULT_SLOT)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--ready-file")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    places.use_data_override()

    # Imported here, after the data folders are settled, because importing the
    # model configures logging against them.
    from app import paths
    from modules.logging.log_utils import LOGGER_NAME, setup_file_logging

    paths.ensure_directories()
    setup_file_logging()
    logger = getLogger(LOGGER_NAME)

    from wavectl.service import Refusal, Service

    mode = "live" if args.live else "mock"
    if args.fast and args.live:
        _announce(args.ready_file, {
            "ok": False, "mode": mode, "data": {},
            "text": "--fast shortens the mock's waits and is not available "
                    "on the real controller."})
        return 1

    info = {
        "pid": os.getpid(),
        "mode": mode,
        "ip": args.ip if args.live else None,
        "slot": args.slot if args.live else None,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "token": secrets.token_hex(16),
        "port": None,
    }
    try:
        sessionfile.claim(info)
    except sessionfile.Taken as exc:
        holder = exc.holder
        _announce(args.ready_file, {
            "ok": False, "mode": holder.get("mode", mode), "data": {},
            "text": "A {0} session is already running (process {1}, since "
                    "{2}). End it with: wavectl session stop".format(
                        holder.get("mode", "?"), holder.get("pid", "?"),
                        holder.get("started", "?"))})
        return 1

    service = None
    transport = None
    try:
        try:
            if args.live:
                transport = live.connect_idle(args.ip, args.slot)
            else:
                from app.simulator import SimulatedMachine

                if args.fast:
                    _quicken()
                transport = SimulatedMachine()

            from Model import Model

            model = Model(transport=transport, is_live=args.live,
                          simulate=not args.live)
            service = Service(model, mode, {
                "pid": info["pid"], "started": info["started"],
                "ip": info["ip"], "fast": bool(args.fast),
                "controller": transport.identity()})
            service.open()
        except (Refusal, live.Unreachable, live.Occupied) as exc:
            _announce(args.ready_file, {"ok": False, "mode": mode, "data": {},
                                        "text": str(exc)})
            return 1

        server = _Server(("127.0.0.1", 0), _Handler)
        server.service = service
        server.token = info["token"]
        info["port"] = server.server_address[1]
        sessionfile.update(info)

        def end(_signal, _frame) -> None:
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, end)
        signal.signal(signal.SIGINT, end)

        where = ("controller {0} at {1}".format(
                     service.details["controller"] or "(unnamed)", args.ip)
                 if args.live else "simulated wavemaker; nothing physical will move")
        logger.info("wavectl %s session started (process %s): %s.",
                    mode, info["pid"], where)
        _announce(args.ready_file, {
            "ok": True, "mode": mode, "data": dict(service.details),
            "text": "session started (process {0}); {1}".format(info["pid"], where)})
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
        logger.info("wavectl %s session ended.", mode)
        return 0
    except Exception as exc:  # noqa: BLE001 - reported, then cleaned up below
        logger.exception("The wavectl session failed: %s", exc)
        _announce(args.ready_file, {
            "ok": False, "mode": mode, "data": {},
            "text": "The session could not start: {0}: {1}".format(
                type(exc).__name__, exc)})
        return 1
    finally:
        if service is not None:
            service.close()
        elif transport is not None:
            # Connected, but never got as far as a model. Nothing was
            # written, so there is nothing to drop; just let go of it.
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass
        sessionfile.release(info["pid"])


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
