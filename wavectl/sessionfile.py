"""The file that says a session exists.

The application has no way to tell that something else is already driving the
controller: two instances will write over each other's parameters and neither
says a word. A soak test was lost that way. The controller itself offers
nothing to lock, so the lock is this file -- created only if it does not
already exist, and holding the process number of the session that made it.

It is a lock against a second *session*, not against the window application
or a script under ``tools/``, which know nothing about it. The session checks
the controller's run bits before it starts for that reason.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from wavectl import places


class Taken(RuntimeError):
    """Another session already holds the file."""

    def __init__(self, holder: dict) -> None:
        RuntimeError.__init__(self, "a session is already running")
        self.holder = holder


def read() -> Optional[dict]:
    """What the session file says, or None if there is no usable one."""
    try:
        text = places.session_file().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        info = json.loads(text)
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


def process_exists(pid) -> bool:
    """Whether a process with this number is running."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill is no way to ask on Windows: any signal number it does not
        # recognise, zero included, is passed to TerminateProcess.
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        # A session started by a program that is still running stays in the
        # process table after it exits, until that program collects it, and
        # signalling it goes on succeeding. If it is ours, collect it.
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass  # not started by this process, which is the usual case
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists; it is simply not ours to signal
    return True


def is_session(pid) -> bool:
    """Whether the process with this number is a wavectl session.

    A process number says little by itself: after a crash or a restart the
    number in a stale session file is handed to something else, which a
    second session would then be refused on account of, and which
    ``session stop --force`` would kill.
    """
    if not process_exists(pid):
        return False
    if sys.platform == "win32":
        return True   # no cheap way to ask; never killed on its strength, see cli
    try:
        shown = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="],
                               capture_output=True, text=True, timeout=5).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return True   # cannot tell, so assume the file is telling the truth
    return "wavectl.daemon" in shown


def _write(path: Path, info: dict, exclusive: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    # Owner-only: the file carries the token that lets a client command the
    # session, and on a shared computer that should not be everyone.
    descriptor = os.open(str(path), flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(info, handle, indent=2, sort_keys=True)
        handle.write("\n")


def claim(info: dict) -> None:
    """Create the session file, or raise :class:`Taken`.

    A file left behind by a session that has died is replaced. One whose
    process is still there is not, even if that process has stopped
    answering: something holding the PLC connection may still be alive, and
    deciding it is not is for a person (``session stop --force``).
    """
    path = places.session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in (1, 2):
        try:
            _write(path, info, exclusive=True)
            return
        except FileExistsError:
            holder = read()
            if holder is not None and is_session(holder.get("pid")):
                raise Taken(holder) from None
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    raise Taken(read() or {})


def update(info: dict) -> None:
    """Rewrite the file this process already holds."""
    path = places.session_file()
    temporary = path.with_name(path.name + ".tmp")
    _write(temporary, info, exclusive=False)
    os.replace(str(temporary), str(path))


def release(pid: Optional[int] = None) -> None:
    """Remove the file, but only if it still belongs to ``pid``.

    A session that was forced out and replaced must not delete its
    successor's file on its own way down.
    """
    holder = read()
    if pid is not None and holder is not None and holder.get("pid") != pid:
        return
    try:
        places.session_file().unlink()
    except FileNotFoundError:
        pass
