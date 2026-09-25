"""Wavemaker System Control -- entry point.

    python main.py                 connect to the PLC, fall back to the mock
    python main.py --mock          simulated wavemaker; no hardware contacted
    python main.py --ip 10.0.0.5   use a different PLC address

Written for the UNC Fluids Lab.

GUI developed by Jasper Christie, Marc Lewis, Chelsea Rowe and Ezri White.
Original GUI by Raphael Provosty and Schuyler Moss.
Later work by Sicheng Wang and the COMP 523 teams.

Uses the pylogix library by Burt Peterson, maintained by Dustin Roeder --
https://github.com/dmroeder/pylogix -- vendored under modules/.

Why the crash guard at the bottom exists
----------------------------------------
``Open Wavemaker.cmd`` starts the application with ``pythonw.exe`` / ``pyw.exe``
so no console window sits behind the interface. Those interpreters have no
console at all, which means ``sys.stdout`` and ``sys.stderr`` are ``None``: if
anything goes wrong before the window appears, Python writes the traceback
nowhere and exits. What the operator sees is the launcher's command window
flash up and vanish, and no application -- with nothing on screen, nothing in
the day's log, and nothing to report to whoever has to fix it.

So every failure on the way up is caught here and written somewhere a person
can find it: ``logs/startup-error.txt``, the console if there is one, the day's
error log, and a message box if Tk is working well enough to show one.
"""

from __future__ import annotations

import argparse
import sys

# Nothing belonging to the application is imported here, deliberately. Anything
# imported at module level runs before the crash guard at the bottom of this
# file can catch it, and would take the process down with no message -- which
# is the exact failure this guard exists to make visible. ``log_utils`` imports
# tkinter, and a Python installed without "tcl/tk and IDLE" is a real and
# common Windows fault, so that import in particular has to happen somewhere
# the guard can see it. Every application import is therefore inside main().


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py", description="Wavemaker System Control"
    )
    parser.add_argument(
        "--mock",
        "--simulate",
        dest="mock",
        action="store_true",
        help="run a simulated wavemaker instead of the real one; no hardware "
        "is contacted and nothing physical moves",
    )
    parser.add_argument(
        "--ip",
        default=None,
        metavar="ADDRESS",
        help="PLC address (default 192.168.1.1)",
    )
    parser.add_argument(
        "--fresh-connection",
        action="store_true",
        help="open a new PLC session per read/write, as the original code did; "
        "use if the persistent connection ever misbehaves",
    )
    parser.add_argument(
        "--slot",
        type=int,
        default=None,
        metavar="N",
        help="processor slot (default 1)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # Imported here rather than at module level so the crash guard below
    # catches a failure in any of them -- see the note at the top of the file.
    from app import paths
    from modules.logging.log_utils import setup_file_logging

    paths.ensure_directories()
    setup_file_logging()

    # Imported after logging is configured so the connection attempt is recorded.
    from Model import Model
    from View import View

    model = Model(
        ip_address=args.ip,
        processor_slot=args.slot,
        simulate=args.mock,
        persistent_connection=not args.fresh_connection,
    )

    # The window is built and shown first; connecting to the machine and
    # clearing it happens on a worker thread from View.run().  Doing that work
    # before the window existed is why launching used to show nothing at all
    # for the first fifteen seconds or so.
    view = View(model)
    view.run()
    return 0


def report_startup_failure(exc_info) -> str:
    """Record a failure that happened before the window appeared.

    Four places, in order of how likely each is to work. Every one of them is
    wrapped, because this runs when something has already gone wrong and it
    must not add a second failure on top of the first.

    Returns the path written, or "" if even that was not possible.
    """
    import traceback

    text = "".join(traceback.format_exception(*exc_info))
    header = (
        "Wavemaker System Control could not start.\n"
        "Python:     {0}\n"
        "Executable: {1}\n"
        "Platform:   {2}\n\n"
    ).format(sys.version.replace("\n", " "), sys.executable, sys.platform)

    # 1. A plain text file. Needs no logging, no Tk and no PLC, so it is the
    #    one that survives almost anything -- including tkinter being missing,
    #    which is a real Windows install fault and used to be invisible.
    written = ""
    try:
        from app import paths

        paths.ensure_directories()
        target = paths.LOG_DIR / "startup-error.txt"
    except Exception:
        # app.paths itself may be what failed. Fall back to plain os.path and
        # the folder this file sits in, which needs nothing but the stdlib.
        import os

        target = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "startup-error.txt"
        )
    try:
        with open(str(target), "w", encoding="utf-8") as handle:
            handle.write(header + text)
        written = str(target)
    except Exception:
        pass

    # 2. The console, when there is one. Under pythonw.exe there is not, which
    #    is the whole reason for the rest of this function -- but 'Diagnose
    #    Wavemaker.cmd' runs the application with console python precisely so
    #    the error is visible, and printing nothing there defeats the point.
    try:
        if sys.stderr is not None:
            sys.stderr.write("\n" + header + text + "\n")
            sys.stderr.flush()
    except Exception:
        pass

    # 3. The day's error log, if logging got far enough to exist.
    try:
        from logging import getLogger

        from modules.logging.log_utils import LOGGER_NAME

        getLogger(LOGGER_NAME).critical("Startup failed", exc_info=exc_info)
    except Exception:
        pass

    # 4. Something on screen. The operator is standing at a machine that did
    #    nothing when they double-clicked it; a message box is the only way
    #    they learn why without going and finding a log.
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Wavemaker could not start",
            "{0}\n\n{1}\n\nThe full details are in:\n{2}\n\n"
            "Run 'Diagnose Wavemaker.cmd' to see this in a window that "
            "stays open.".format(
                exc_info[0].__name__ if exc_info[0] else "Error",
                exc_info[1],
                written or "(the error file could not be written either)",
            ),
        )
        root.destroy()
    except Exception:
        pass

    return written


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - a silent exit is the bug being fixed
        report_startup_failure(sys.exc_info())
        sys.exit(1)
