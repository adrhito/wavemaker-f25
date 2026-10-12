"""The command's side of the conversation with the session."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from wavectl import places, sessionfile, wire

#: How long to wait for the session to accept a connection. It is on this
#: computer, so anything longer than a moment means it is not listening.
CONNECT_SECONDS = 2.0
#: How long a quick request (status, a parameter) may take to be answered.
QUICK_SECONDS = 20.0
#: How long to wait for a new session to say it is ready. A real controller
#: that is switched off costs a five-second probe, and one that answers costs
#: a five-second fault-clearing pulse.
START_SECONDS = 45.0

REPO_ROOT = Path(__file__).resolve().parent.parent


class NoSession(Exception):
    """There is no session file: nothing has been started."""


class Silent(Exception):
    """There is a session file, but nothing answered at its port."""

    def __init__(self, info: dict, why: str) -> None:
        Exception.__init__(self, why)
        self.info = info


def request(operation: str, arguments: Optional[dict] = None,
            seconds: float = QUICK_SECONDS) -> dict:
    """Ask the session to do one thing and return its reply."""
    info = sessionfile.read()
    if info is None:
        raise NoSession()
    if not info.get("port"):
        raise Silent(info, "the session has not finished starting")
    try:
        connection = socket.create_connection(
            ("127.0.0.1", int(info["port"])), timeout=CONNECT_SECONDS)
    except OSError as exc:
        raise Silent(info, str(exc)) from exc
    try:
        connection.settimeout(seconds)
        stream = connection.makefile("rwb")
        wire.send(stream, {"token": info.get("token", ""), "op": operation,
                           "args": arguments or {}})
        return wire.receive(stream)
    except socket.timeout as exc:
        raise Silent(info, "no answer within {0:.0f} seconds".format(seconds)) from exc
    except (OSError, ValueError) as exc:
        raise Silent(info, str(exc)) from exc
    finally:
        connection.close()


def start_session(mock: bool, ip: str, slot: int, fast: bool = False) -> dict:
    """Start the session process and wait for it to say how that went."""
    command = [sys.executable, "-m", "wavectl.daemon",
               "--mock" if mock else "--live"]
    if not mock:
        command += ["--ip", str(ip), "--slot", str(slot)]
    if fast:
        command.append("--fast")

    handle, ready = tempfile.mkstemp(prefix="wavectl-ready-", suffix=".json")
    os.close(handle)
    os.unlink(ready)
    command += ["--ready-file", ready]

    places.use_data_override()
    log_path = places.output_dir() / "session.log"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT)] + ([environment["PYTHONPATH"]]
                            if environment.get("PYTHONPATH") else []))
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = (subprocess.DETACHED_PROCESS |
                                    subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        # Its own session, so it outlives the terminal or tool call that
        # started it instead of being taken down with that process group.
        options["start_new_session"] = True

    with open(str(log_path), "ab") as log:
        process = subprocess.Popen(
            command, cwd=str(REPO_ROOT), env=environment,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)

    deadline = time.monotonic() + START_SECONDS
    try:
        while time.monotonic() < deadline:
            if os.path.exists(ready):
                with open(ready, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            if process.poll() is not None:
                break
            time.sleep(0.05)
        if os.path.exists(ready):
            with open(ready, "r", encoding="utf-8") as handle:
                return json.load(handle)
    finally:
        try:
            os.unlink(ready)
        except OSError:
            pass

    mode = "mock" if mock else "live"
    if process.poll() is not None:
        return {"ok": False, "mode": mode, "data": {},
                "text": "The session process exited (code {0}) before it was "
                        "ready. See {1}".format(process.returncode, log_path)}
    return {"ok": False, "mode": mode, "data": {},
            "text": "The session did not say it was ready within {0:.0f} "
                    "seconds; it may still be starting. See {1}".format(
                        START_SECONDS, log_path)}
