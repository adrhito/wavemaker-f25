"""Answers, refusals, and operations that outlast the request that began them.

Homing takes a minute and a run lasts as long as it was asked to. A command
may wait for either or may not, and a later command may ask how it went, so
anything that long is a :class:`Job`: started on a thread of its own, with a
place to leave an interim word once it is known to be under way and the final
answer when it is over.
"""

from __future__ import annotations

import threading
import time
from typing import NamedTuple, Optional


class Refusal(Exception):
    """The request was understood and will not be done, or it failed.

    The text says why and what to do instead; it is the whole answer the
    person or agent at the terminal gets, so it has to stand on its own.
    """

    def __init__(self, text: str, data: Optional[dict] = None) -> None:
        Exception.__init__(self, text)
        self.data = data or {}


class Result(NamedTuple):
    text: str
    data: dict


class Job:
    """One long operation."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.began = time.time()
        #: Set once the operation is known to have got going, or to have failed.
        self.under_way = threading.Event()
        #: Set when it is over, whichever way it ended.
        self.done = threading.Event()
        #: What to say to a command that is not waiting for the end.
        self.interim: Optional[str] = None
        self.ok: Optional[bool] = None
        self.text = ""
        self.data: dict = {}

    def reached(self, text: str) -> None:
        """Record that the operation is under way, and how to describe it."""
        self.interim = text
        self.under_way.set()
