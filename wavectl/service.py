"""What a session can be asked to do.

Every operation here is a thin layer over ``Model``: it calls the same command
the window's buttons call, waits for the model to finish, and turns what the
model reported into one line. Nothing in this module writes a run bit or a
parameter itself. The order of preparation, the stop guards and the limits all
stay where they are tested, in ``Model``.

The operations are plain methods so the tests can call them without a socket;
``daemon`` is what puts them on the wire.
"""

from __future__ import annotations

import threading
import time
from logging import getLogger
from typing import Callable, Dict, List, NamedTuple, Optional

from app import drive_status, params, tags
from app.plc import PlcError
from Model import MachineState, Model
from modules.logging.log_utils import LOGGER_NAME
from wavectl import live, pistons
from wavectl.journal import Journal, one_line

LOGGER = getLogger(LOGGER_NAME)

#: How long a session waits for the model's own startup before giving up. On
#: the machine that is the fault-clearing pulse, five seconds, plus the reads.
STARTUP_SECONDS = 30.0
#: Gap between looks at the model while waiting for it.
POLL_SECONDS = 0.05


class Refusal(Exception):
    """The request was understood and will not be done.

    The text says why and what to do instead; it is the whole answer the
    person or agent at the terminal gets, so it has to stand on its own.
    """

    def __init__(self, text: str, data: Optional[dict] = None) -> None:
        Exception.__init__(self, text)
        self.data = data or {}


class Result(NamedTuple):
    text: str
    data: dict


class Service:
    """One session's worth of operations on one model."""

    #: Operation name on the wire -> method name here.
    OPERATIONS: Dict[str, str] = {
        "ping": "ping",
        "status": "status",
        "log": "log",
        "shutdown": "shutdown",
    }

    def __init__(self, model: Model, mode: str, details: Optional[dict] = None) -> None:
        self.model = model
        #: "live" or "mock". Carried on every reply so no answer can be
        #: mistaken for the other kind.
        self.mode = mode
        self.details = dict(details or {})
        self.journal = Journal()
        self._lock = threading.RLock()

    @property
    def plc(self):
        return self.model.plc

    # -- lifecycle -------------------------------------------------------------

    def open(self) -> None:
        """Bring the machine to its known resting state, as the window does.

        On the real controller this drops the run bits, pulses the fault
        clear and deselects every piston. Nothing moves.
        """
        self.model.register_bridge(self.journal)
        mark = self.journal.mark()
        if not self.model.startup():
            raise Refusal("The model was busy before the session began.")
        if not self._until(lambda: not self.model.busy, STARTUP_SECONDS):
            raise Refusal("The machine did not finish starting up within "
                          "{0:.0f} seconds.".format(STARTUP_SECONDS))
        problems = self.journal.problems_since(mark)
        if problems:
            raise Refusal("Startup failed. " + " ".join(problems))
        self.model.start_monitoring()

    def close(self) -> None:
        """Stop the machine and close the connection."""
        try:
            self.model.shutdown()
        except Exception:  # noqa: BLE001 - the session is ending either way
            LOGGER.exception("The session did not shut the model down cleanly.")

    # -- answering -------------------------------------------------------------

    def handle(self, operation: str, arguments: Optional[dict] = None) -> dict:
        """Run one operation and return its reply, never raising."""
        name = self.OPERATIONS.get(str(operation))
        if name is None:
            return self._reply(False, 'There is no "{0}" operation.'.format(operation))
        try:
            result = getattr(self, name)(**(arguments or {}))
        except Refusal as exc:
            return self._reply(False, str(exc), exc.data)
        except PlcError as exc:
            LOGGER.error("%s failed at the controller: %s", operation, exc)
            return self._reply(False, "The controller did not answer: {0}".format(exc))
        except (TypeError, ValueError) as exc:
            return self._reply(False, "{0}".format(exc))
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the session
            LOGGER.exception("%s crashed", operation)
            return self._reply(False, "{0} failed unexpectedly: {1}: {2}".format(
                operation, type(exc).__name__, exc))
        return self._reply(True, result.text, result.data)

    def _reply(self, ok: bool, text: str, data: Optional[dict] = None) -> dict:
        return {"ok": bool(ok), "mode": self.mode, "text": text, "data": data or {}}

    def _until(self, done: Callable[[], bool], seconds: float) -> bool:
        """Wait for ``done()``. Returns whether it came true in time."""
        deadline = time.monotonic() + seconds
        while True:
            if done():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)

    # -- reading the machine -----------------------------------------------------

    def _read_piston(self, axis: int) -> dict:
        """What one drive reports, with its piston's display number."""
        row = {"piston": tags.display_number(axis)}
        try:
            status = int(self.plc.read(tags.axis_field(axis, tags.STATUS_WORD)))
            warn = int(self.plc.read(tags.axis_field(axis, tags.WARN_WORD)))
            position = params.to_mm(
                self.plc.read(tags.axis_field(axis, tags.ACTUAL_POSITION)))
        except (PlcError, TypeError, ValueError) as exc:
            row["unreadable"] = str(exc)
            return row
        row.update({
            "mm": round(position, 1),
            "homed": drive_status.is_homed(status),
            "enabled": bool(status & 1),
            "problems": drive_status.problems(warn, status),
        })
        return row

    def _state_phrase(self) -> str:
        model = self.model
        state = model.state
        if state is MachineState.IDLE:
            phrase = "idle"
        elif state is MachineState.READY:
            # Editing a parameter or the selection drops the model out of
            # HOMED while the drives stay referenced; say which, because one
            # means a minute of homing before the next run and the other
            # does not.
            phrase = ("ready, needs homing" if model.needs_homing
                      else "ready, still homed")
        elif state is MachineState.PREPARING:
            phrase = "preparing"
        elif state is MachineState.HOMED:
            phrase = "homed"
        else:
            mode = model._run_mode
            phrase = "running {0}".format(mode.value) if mode else "running"
        if model._stopping.is_set():
            phrase += " (finishing the stroke)"
        elif model._parking.is_set():
            phrase += " (returning to rest)"
        elif model.busy and model._current_command:
            phrase += " ({0})".format(model._current_command.lower())
        return phrase

    def _motion_phrase(self) -> str:
        """The stroke and speed the selection is set to, if they agree."""
        motors = self.model.all_motors
        lows = set(m.write_params["Position 1"] for m in motors)
        highs = set(m.write_params["Position 2"] for m in motors)
        speeds = set((m.write_params["Speed 1"], m.write_params["Speed 2"])
                     for m in motors)
        if len(lows) == 1 and len(highs) == 1:
            stroke = "{0} to {1} mm".format(lows.pop(), highs.pop())
        else:
            stroke = "strokes differ"
        if len(speeds) == 1:
            out, back = speeds.pop()
            speed = ("{0} mm/s".format(out) if out == back
                     else "{0}/{1} mm/s".format(out, back))
        else:
            speed = "speeds differ"
        return "{0} at {1}".format(stroke, speed)

    # -- operations --------------------------------------------------------------

    def ping(self) -> Result:
        return Result("session alive", dict(self.details))

    def status(self, pistons_table: bool = False, every: bool = False) -> Result:
        """Where the machine is, in one line.

        Reads the command bits and every selected drive, so it is also the
        check to make before motion: a run bit that is high when this session
        is not running means something else is driving the controller.
        """
        model = self.model
        selected = model.live_axes
        axes = list(range(tags.MOTOR_COUNT)) if every else selected
        high = live.bits_high(self.plc)
        rows = [self._read_piston(axis) for axis in
                sorted(axes, key=tags.display_number)]

        parts = [self._state_phrase()]
        if selected:
            parts.append("pistons " + pistons.describe(selected))
            parts.append(self._motion_phrase())
            parts.append("rest " + model.rest_position)
        else:
            parts.append("no pistons selected")
        parts.append(", ".join(high) + " HIGH" if high else "run bits clear")

        troubled = [row for row in rows
                    if row.get("unreadable") or row.get("problems")]
        if troubled:
            first = troubled[0]
            what = (first.get("unreadable") or "; ".join(first["problems"]))
            more = (" (and {0} more)".format(len(troubled) - 1)
                    if len(troubled) > 1 else "")
            parts.append("piston {0}: {1}{2}".format(first["piston"], what, more))
        elif rows:
            parts.append("drives ok")
        if model.lagging_axes:
            parts.append("lagging " + pistons.describe(model.lagging_axes))
        if model.dropped_axes:
            parts.append("would not home " + pistons.describe(model.dropped_axes))

        text = " | ".join(parts)
        if pistons_table:
            text += "".join("\n" + self._piston_line(row) for row in rows)
        data = {
            "state": model.state.value,
            "busy": model.busy,
            "command": model._current_command,
            "pistons": pistons.shown(selected),
            "rest": model.rest_position,
            "bits_high": high,
            "drives": rows,
            "lagging": pistons.shown(model.lagging_axes),
            "would_not_home": pistons.shown(model.dropped_axes),
        }
        return Result(text, data)

    @staticmethod
    def _piston_line(row: dict) -> str:
        if "unreadable" in row:
            return "{0:>3}  unreadable: {1}".format(row["piston"], row["unreadable"])
        return "{0:>3}  {1:>6.1f} mm  {2}  {3}  {4}".format(
            row["piston"], row["mm"],
            "homed" if row["homed"] else "not homed",
            "enabled" if row["enabled"] else "off",
            "; ".join(row["problems"]) or "ok")

    def log(self, count: int = 10, full: bool = False) -> Result:
        """The last things the model said, oldest first."""
        entries = self.journal.last(int(count))
        lines = ["{0} {1:<7} {2}".format(
            time.strftime("%H:%M:%S", time.localtime(entry.when)), entry.kind,
            entry.text if full else one_line(entry.text)) for entry in entries]
        data = {"entries": [{"when": entry.when, "kind": entry.kind,
                             "text": entry.text} for entry in entries]}
        return Result("\n".join(lines) or "nothing has happened yet", data)

    def shutdown(self) -> Result:
        """Acknowledge; ``daemon`` ends the session once this reply is sent."""
        return Result("session ending", {})
