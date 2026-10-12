"""Where the session keeps its files.

Two kinds of file, kept in two places on purpose.

The **session file** says that a session exists and how to reach it. It lives
in the user's home folder, not in the repository, because its whole job is to
stop a second session being started against the same controller, and a file
inside one checkout cannot do that: a second checkout or a git worktree of
this repository would have a ``logs/`` of its own and see nothing.

Everything a session **produces** -- its own log, position samples -- goes
under the checkout's ``logs/wavectl/``, beside the application's logs.
"""

from __future__ import annotations

import os
from pathlib import Path

from app import paths

#: Set to keep the session file somewhere other than ``~/.wavectl``. The tests
#: use it so they never see, or disturb, a session someone really has open.
HOME_VARIABLE = "WAVECTL_HOME"
#: Set to write logs and analytics under this folder instead of the checkout.
DATA_VARIABLE = "WAVECTL_DATA"
#: Set to make every attempt to reach a real controller fail before it opens
#: a socket. For rehearsing and for the tests: with this set, no mistake in a
#: command line can touch the machine.
MOCK_ONLY_VARIABLE = "WAVECTL_MOCK_ONLY"


def home() -> Path:
    override = os.environ.get(HOME_VARIABLE)
    return Path(override) if override else Path.home() / ".wavectl"


def session_file() -> Path:
    return home() / "session.json"


def use_data_override() -> None:
    """Point the application's log and analytics folders at ``WAVECTL_DATA``.

    ``app.paths`` reads its module-level folders each time it is asked for a
    file, so replacing them here, before anything is logged, moves everything
    the model writes as well as what this package writes.
    """
    override = os.environ.get(DATA_VARIABLE)
    if not override:
        return
    root = Path(override)
    paths.LOG_DIR = root / "logs"
    paths.ERROR_LOG_DIR = paths.LOG_DIR / "errors"
    paths.ANALYTICS_DIR = root / "analytics"


def output_dir() -> Path:
    """The folder for what a session writes, created if it is missing."""
    folder = paths.LOG_DIR / "wavectl"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def mock_only() -> bool:
    return os.environ.get(MOCK_ONLY_VARIABLE, "") not in ("", "0")
