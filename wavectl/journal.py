"""What the model said, kept so a command can report its own outcome.

``Model`` never returns the result of a command. It starts a worker and tells
whoever is listening what happened through a bridge: the window shows those
messages in a status bar and in message boxes. A terminal command needs the
same messages as its answer, so this bridge writes them down in order and
lets a command ask for everything said since it began.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from logging import getLogger
from typing import Dict, List, NamedTuple, Optional

from modules.logging.log_utils import LOGGER_NAME

LOGGER = getLogger(LOGGER_NAME)


class Entry(NamedTuple):
    number: int
    when: float
    kind: str      # "status", "problem", "state", "file" or "note"
    text: str


def one_line(text: str) -> str:
    """The first paragraph of a message, on one line.

    Problems are written for message boxes: a sentence saying what happened,
    then a blank line and a list of likely causes. The first paragraph is the
    part a one-line answer needs; the whole text stays in the journal.
    """
    first = str(text).strip().split("\n\n", 1)[0]
    return " ".join(first.split())


class Journal:
    """A ``UiBridge`` that records instead of displaying."""

    def __init__(self, keep: int = 500) -> None:
        self._lock = threading.Lock()
        self._entries = deque(maxlen=keep)
        self._number = 0
        self._state: Optional[str] = None
        #: The newest positions the model's monitor published, by axis.
        self.last_positions: Dict[int, float] = {}

    # -- writing ---------------------------------------------------------------

    def add(self, kind: str, text: str) -> int:
        with self._lock:
            self._number += 1
            self._entries.append(Entry(self._number, time.time(), kind, str(text)))
            return self._number

    def note(self, text: str) -> int:
        """Something this package did, as opposed to something the model said."""
        return self.add("note", text)

    # -- the bridge the model calls ---------------------------------------------

    def status(self, message: str) -> None:
        self.add("status", message)

    def state_changed(self, state) -> None:
        # The model announces its state after every command, changed or not.
        value = getattr(state, "value", str(state))
        if value != self._state:
            self._state = value
            self.add("state", value)

    def progress(self, fraction: float, label: str) -> None:
        pass

    def progress_done(self, artifact: Optional[str]) -> None:
        if artifact:
            self.add("file", artifact)

    def problem(self, title: str, message: str) -> None:
        self.add("problem", "{0}: {1}".format(title, message))
        # The window's bridge shows a box; the bridge the tools use logs the
        # problem as an error. Do the same, so the day's log reads alike
        # whichever way the machine was driven.
        LOGGER.error("%s: %s", title, message)

    def positions(self, readings: Dict[int, float]) -> None:
        self.last_positions = dict(readings)

    # -- reading ---------------------------------------------------------------

    def mark(self) -> int:
        """A place in the journal; pass it to :meth:`since` later."""
        with self._lock:
            return self._number

    def since(self, mark: int, kind: Optional[str] = None) -> List[Entry]:
        with self._lock:
            return [entry for entry in self._entries
                    if entry.number > mark and (kind is None or entry.kind == kind)]

    def last(self, count: int) -> List[Entry]:
        with self._lock:
            return list(self._entries)[-max(int(count), 0):] if count else []

    def problems_since(self, mark: int) -> List[str]:
        return [one_line(entry.text) for entry in self.since(mark, "problem")]

    def last_status_since(self, mark: int) -> Optional[str]:
        said = self.since(mark, "status")
        return one_line(said[-1].text) if said else None
