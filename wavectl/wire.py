"""How a command and the session talk: one line of JSON each way.

A command opens a connection to the session on this computer, sends one
request, reads one reply and closes. A request that takes a minute -- homing --
simply holds its connection open for that minute. Stop arrives on a connection
of its own, so it is never queued behind the thing it is stopping.
"""

from __future__ import annotations

import json


def send(stream, message: dict) -> None:
    stream.write((json.dumps(message) + "\n").encode("utf-8"))
    stream.flush()


def receive(stream) -> dict:
    line = stream.readline()
    if not line:
        raise ConnectionError("the other end closed without answering")
    message = json.loads(line.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("expected a JSON object")
    return message
