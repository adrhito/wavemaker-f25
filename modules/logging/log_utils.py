"""Logging for the application.

Three destinations:

* ``logs/<date>.log`` -- everything from INFO up, set up at startup so the
  connection attempt and any early failure are recorded.  Previously the file
  handler was only attached when the Feedback tab was built and was set to
  CRITICAL, so a day's log file stayed empty no matter what went wrong.
* ``logs/errors/<date>-errors.log`` -- WARNING and up only, so a fault can be
  found without reading a whole day's INFO/SUCCESS lines. A day with nothing
  to report never creates the file, since the handler opens it lazily.
* The Feedback tab -- everything, colour-coded, attached when that tab exists.
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
from logging import Formatter, Handler, LogRecord, getLogger
from typing import Optional

from app import paths

#: Between INFO and WARNING: an operator-visible action succeeded.
SUCCESS = 15
logging.addLevelName(SUCCESS, "SUCCESS")

LOGGER_NAME = "logger"

FORMAT = "%(asctime)s %(name)s#%(levelname)s - %(message)s"
DATE_FORMAT = "%m/%d/%Y at %I:%M:%S %p"

# Includes module/line, unlike FORMAT, because this file exists to be read on
# its own without the surrounding INFO lines for context -- the record has to
# say where it came from.  logging.Formatter appends the traceback itself
# whenever a record carries exc_info (i.e. from LOGGER.exception(...)), so
# nothing extra is needed here for that.
ERROR_FORMAT = "%(asctime)s %(levelname)s %(name)s %(module)s:%(lineno)d - %(message)s"

_LEVEL_COLOURS = {
    "SUCCESS": "lime green",
    "DEBUG": "#8ab4f8",
    "INFO": "#8ab4f8",
    "WARNING": "orange",
    "ERROR": "#ff6b6b",
    "CRITICAL": "crimson",
}

#: Set once so repeated calls do not stack duplicate handlers, which used to
#: happen whenever the Feedback tab was rebuilt.
_file_handler: Optional[Handler] = None
_error_handler: Optional[Handler] = None
_textbox_handler: Optional[Handler] = None


class TextboxLogHandler(Handler):
    """Writes log records into a Tk text widget, coloured by level.

    Records are queued and drained by a timer on the main thread.  Almost every
    interesting log line is produced by the worker thread that is driving the
    machine, and touching a Tk widget from another thread raises
    ``RuntimeError: main thread is not in main loop`` -- or, worse, silently
    corrupts the interpreter state.
    """

    #: How often the widget is refreshed from the queue, in milliseconds.
    DRAIN_INTERVAL_MS = 100

    def __init__(self, text: tk.Text, max_lines: int = 2000):
        Handler.__init__(self)
        self.text = text
        self.max_lines = max_lines
        self._pending: "queue.Queue" = queue.Queue()
        self._stopped = False

        self.text.configure(fg="#dedede")
        for level, colour in _LEVEL_COLOURS.items():
            self.text.tag_configure(
                level, foreground=colour, underline=(level == "CRITICAL")
            )
        self.text.after(self.DRAIN_INTERVAL_MS, self._drain)

    def emit(self, record: LogRecord) -> None:
        """Called from any thread. Only queues; never touches the widget."""
        try:
            self._pending.put((record.levelname, self.format(record)))
        except Exception:  # pragma: no cover - logging must not kill the app
            pass

    def close(self) -> None:
        self._stopped = True
        Handler.close(self)

    def _drain(self) -> None:
        """Move queued records into the widget. Main thread only."""
        try:
            wrote = False
            while True:
                try:
                    level, message = self._pending.get_nowait()
                except queue.Empty:
                    break
                self.text.configure(state="normal")
                start = self.text.index("end-1c")
                self.text.insert(tk.END, message + "\n")
                # Tag by the record's own level rather than by searching the
                # formatted text for punctuation, which broke on any message
                # that happened to contain the separator.
                self.text.tag_add(level, start, start + " lineend")
                wrote = True
            if wrote:
                self._trim()
                self.text.see(tk.END)
                self.text.configure(state="disabled")
        except tk.TclError:  # window closed
            self._stopped = True
            return

        if not self._stopped:
            try:
                self.text.after(self.DRAIN_INTERVAL_MS, self._drain)
            except tk.TclError:  # pragma: no cover - window closed
                self._stopped = True

    def _trim(self) -> None:
        """Drop the oldest lines so a long session cannot grow without bound."""
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > self.max_lines:
            self.text.delete("1.0", "{0}.0".format(lines - self.max_lines))


def setup_file_logging() -> Handler:
    """Attach the day's log file. Safe to call more than once."""
    global _file_handler

    logger = getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    if _file_handler is not None:
        return _file_handler

    paths.ensure_directories()
    handler = logging.FileHandler(str(paths.log_file()), encoding="utf-8")
    # SUCCESS is 15, below INFO. Filtering at INFO dropped every confirmation
    # the application logs -- "Motors booted", "Motors homed", "Ran a stroke",
    # "Motors stopped" -- so a log from the lab showed the warnings and none of
    # the things that had gone right, and a working One stroke was
    # indistinguishable from a button that did nothing.
    handler.setLevel(SUCCESS)
    handler.setFormatter(Formatter(FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(handler)
    _file_handler = handler

    # Wired in here, rather than left for a caller to remember, so every
    # entry point that gets the day's log also gets the errors-only one.
    setup_error_file_logging()

    return handler


def setup_error_file_logging() -> Optional[Handler]:
    """Attach the day's WARNING-and-up file. Safe to call more than once.

    Kept independent of ``setup_file_logging`` -- a bad path, permissions, or
    anything else wrong with ``logs/errors/`` is swallowed here rather than
    raised, so it can never take down the handler that writes the main log.
    """
    global _error_handler

    if _error_handler is not None:
        return _error_handler

    logger = getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    try:
        paths.ensure_directories()
        # delay=True means the file is not opened until the first record
        # actually reaches this handler, i.e. the first WARNING or worse. An
        # error-free day therefore leaves logs/errors/ untouched instead of
        # littering it with empty files.
        handler = logging.FileHandler(
            str(paths.error_log_file()), encoding="utf-8", delay=True
        )
        handler.setLevel(logging.WARNING)
        handler.setFormatter(Formatter(ERROR_FORMAT, datefmt=DATE_FORMAT))
    except OSError:
        return None

    logger.addHandler(handler)
    _error_handler = handler
    return handler


def log_setup(text: tk.Text):
    """Attach the Feedback tab's text widget. Safe to call more than once."""
    global _textbox_handler

    logger = getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    if _textbox_handler is not None:
        logger.removeHandler(_textbox_handler)

    handler = TextboxLogHandler(text)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(Formatter(FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(handler)
    _textbox_handler = handler

    return handler, setup_file_logging()
