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
from typing import Callable, Dict, Optional

from app import drive_status, params, paths, tags
from app.plc import PlcError
from Model import MachineState, Model
from modules.logging.log_utils import LOGGER_NAME
from preset_options.PresetProcessor import PresetError, PresetProcessor
from wavectl import live, pistons, settings
from wavectl.jobs import Job, Refusal, Result
from wavectl.journal import Journal, one_line
from wavectl.motion import MotionOperations

LOGGER = getLogger(LOGGER_NAME)

#: How long a session waits for the model's own startup before giving up. On
#: the machine that is the fault-clearing pulse, five seconds, plus the reads.
STARTUP_SECONDS = 30.0
#: Gap between looks at the model while waiting for it.
POLL_SECONDS = 0.05


#: How long a command that is not waiting gives a job to fail straight away,
#: so "preparing..." is not the answer to something refused a moment later.
QUICK_FAILURE_SECONDS = 0.3


class Service(MotionOperations):
    """One session's worth of operations on one model."""

    #: Operation name on the wire -> method name here.
    OPERATIONS: Dict[str, str] = {
        "ping": "ping",
        "status": "status",
        "log": "log",
        "shutdown": "shutdown",
        "select": "select",
        "set": "set",
        "params": "params",
        "preset": "preset",
        "presets": "presets",
    }
    OPERATIONS.update(MotionOperations.MOTION_OPERATIONS)

    def __init__(self, model: Model, mode: str, details: Optional[dict] = None) -> None:
        self.model = model
        #: "live" or "mock". Carried on every reply so no answer can be
        #: mistaken for the other kind.
        self.mode = mode
        self.details = dict(details or {})
        self.journal = Journal()
        self._lock = threading.RLock()
        #: The long operation under way, or the last one that finished.
        self._job: Optional[Job] = None
        #: When the continuous run in progress will be stopped (monotonic).
        self._run_deadline: Optional[float] = None

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

    # -- operations that take a while ---------------------------------------------

    def _start_job(self, name: str, work: Callable[[Job], Result]) -> Job:
        """Run ``work`` on a thread of its own, one such job at a time."""
        with self._lock:
            current = self._job
            if current is not None and not current.done.is_set():
                raise Refusal(
                    "The {0} is still under way. Wait for it (wavectl wait) "
                    "or stop it (wavectl stop).".format(current.name))
            job = Job(name)
            self._job = job

        def run() -> None:
            try:
                result = work(job)
                job.ok, job.text, job.data = True, result.text, result.data
            except Refusal as exc:
                job.ok, job.text, job.data = False, str(exc), exc.data
            except PlcError as exc:
                LOGGER.error("%s failed at the controller: %s", name, exc)
                job.ok, job.text = False, "The controller did not answer: {0}".format(exc)
            except Exception as exc:  # noqa: BLE001 - reported as the job's answer
                LOGGER.exception("%s crashed", name)
                job.ok, job.text = False, "The {0} failed unexpectedly: {1}: {2}".format(
                    name, type(exc).__name__, exc)
            finally:
                job.under_way.set()
                job.done.set()

        threading.Thread(target=run, name="wavectl " + name, daemon=True).start()
        return job

    def _answer(self, job: Job, wait: bool, patience: float) -> Result:
        """The reply for a job: its end if waiting, else how it is going."""
        if wait:
            if not job.done.wait(patience):
                raise Refusal(
                    "The {0} is still under way after {1:.0f} seconds. wavectl "
                    "wait picks it up; wavectl stop halts it.".format(
                        job.name, patience), {"job": job.name, "done": False})
        else:
            job.under_way.wait(patience)
            job.done.wait(QUICK_FAILURE_SECONDS)
            if not job.done.is_set():
                return Result(job.interim or "{0} under way".format(job.name),
                              {"job": job.name, "done": False})
        if not job.ok:
            raise Refusal(job.text, job.data)
        return Result(job.text, job.data)

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
            deadline = self._run_deadline
            if deadline is not None:
                phrase += ", {0:.0f} s left".format(max(deadline - time.monotonic(), 0.0))
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

    # -- choosing what will move ---------------------------------------------------

    def _refuse_while_moving(self, what: str) -> None:
        """Selection and parameters are changed with the machine at rest.

        The window lets an operator edit while the array runs, and deals with
        the consequences one piston at a time. A command line has no way to
        show that happening, so here the order is fixed: stop, change, run.
        Speed and stroke have their own commands for changing mid-run.
        """
        if self.model._in_motion() or self.model.busy:
            raise Refusal("The machine is {0}; stop it before changing {1}.".format(
                self._state_phrase(), what))

    def _chosen(self, pistons_text: Optional[str]) -> list:
        """The motors a command applies to: the selection, or part of it."""
        motors = dict((motor.axis, motor) for motor in self.model.all_motors)
        if pistons_text is None:
            return [motors[axis] for axis in sorted(motors, key=tags.display_number)]
        wanted = pistons.parse(pistons_text)
        outside = [axis for axis in wanted if axis not in motors]
        if outside:
            raise Refusal(
                "Piston(s) {0} are not selected, so they have no parameters "
                "to set. Selected: {1}.".format(
                    pistons.describe(outside), pistons.describe(motors)))
        return [motors[axis] for axis in sorted(wanted, key=tags.display_number)]

    def _selection_line(self) -> str:
        if not self.model.all_motors:
            return "nothing selected | " + self._state_phrase()
        return " | ".join([
            "pistons " + pistons.describe(self.model.live_axes),
            self._motion_phrase(), "rest " + self.model.rest_position,
            self._state_phrase()])

    def select(self, pistons_text: str) -> Result:
        """Choose the pistons that will run. Replaces the previous choice.

        Pistons kept from the previous selection keep their parameters; new
        ones take the values the others share.
        """
        axes = pistons.parse(pistons_text)
        self._refuse_while_moving("the selection")
        # A piston dropped because it would not home is being asked for again
        # or left out on purpose; either way the old note no longer applies.
        self.model.dropped_axes = []
        self.model.set_selection(axes)
        return Result(self._selection_line(),
                      {"pistons": pistons.shown(self.model.live_axes),
                       "state": self.model.state.value})

    def set(self, values: dict, pistons_text: Optional[str] = None) -> Result:
        """Change parameters on the selection, or on some of its pistons.

        Nothing is written to the controller here. The values go out at the
        next prepare or run, exactly as they do from the window.
        """
        edit = settings.read(values)
        self._refuse_while_moving("parameters")
        model = self.model
        motors = self._chosen(pistons_text)

        if not motors:
            # Nothing selected yet: these become what the next selection
            # starts with, which is what the window's fields do too.
            new = settings.changed(model.pending_params, edit)
            for name, value in new.items():
                model.set_pending_param(name, value)
            if edit.rest is not None:
                model.rest_position = edit.rest
            return Result(
                "nothing selected; the next selection starts with {0}".format(
                    ", ".join("{0} {1}".format(n, v) for n, v in new.items())
                    or "rest " + model.rest_position), {"values": new})

        plans = []
        refused: Dict[str, list] = {}
        for motor in motors:
            try:
                plans.append((motor, settings.changed(motor.write_params, edit)))
            except ValueError as exc:
                refused.setdefault(str(exc), []).append(motor.axis)
        if refused:
            # All or nothing: half a selection on a new stroke is not a state
            # anyone asked for.
            raise Refusal("Nothing was changed. " + " ".join(
                "Piston(s) {0}: {1}.".format(pistons.describe(axes), why)
                for why, axes in refused.items()))

        for motor, new in plans:
            motor.update_params(new)
        if any(new for _motor, new in plans):
            model.mark_unprepared()
        if edit.rest is not None:
            model.rest_position = edit.rest

        return Result(self._selection_line(), {
            "pistons": pistons.shown(motor.axis for motor in motors),
            "values": dict((str(tags.display_number(motor.axis)), new)
                           for motor, new in plans),
            "rest": model.rest_position})

    def params(self, pistons_text: Optional[str] = None) -> Result:
        """Every parameter the selection is set to."""
        motors = self._chosen(pistons_text)
        if not motors:
            values = dict(self.model.pending_params)
            return Result("nothing selected; a selection would start with: "
                          + settings.summary(values), {"values": values})
        values = {}
        for spec in params.PARAMS:
            seen = set(motor.write_params[spec.name] for motor in motors)
            values[spec.name] = seen.pop() if len(seen) == 1 else None
        return Result(
            "pistons {0}: {1}".format(
                pistons.describe(motor.axis for motor in motors),
                settings.summary(values)),
            {"values": values,
             "by_piston": dict((str(tags.display_number(motor.axis)),
                                dict(motor.write_params)) for motor in motors)})

    # -- presets -------------------------------------------------------------------

    @staticmethod
    def _preset_files() -> list:
        return sorted(paths.PRESET_DIR.glob("*.csv"), key=lambda p: p.name.lower())

    def _find_preset(self, name: str):
        """The file a preset name means.

        The shipped presets have long descriptive names, so the start of one
        is enough as long as it picks out exactly one file.
        """
        typed = str(name).strip()
        direct = paths.PRESET_DIR / typed
        for candidate in (direct, direct.with_name(direct.name + ".csv")):
            if candidate.is_file():
                return candidate
        if typed and (len(typed) > 1 and typed[1] == ":" or "/" in typed or "\\" in typed):
            raise Refusal("There is no preset file at {0}.".format(typed))
        matches = [path for path in self._preset_files()
                   if path.stem.lower().startswith(typed.lower())]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise Refusal('No preset starts with "{0}". See: wavectl preset --list'
                          .format(typed))
        raise Refusal('"{0}" could be any of: {1}.'.format(
            typed, "; ".join(path.stem for path in matches)))

    def presets(self) -> Result:
        names = [path.stem for path in self._preset_files()]
        return Result("; ".join(names) or "no presets", {"presets": names})

    def preset(self, name: str, select: bool = False) -> Result:
        """Apply a saved preset to the selection.

        ``select`` first selects the pistons the preset itself gives motion
        to, which is how a preset written for the front nine is run without
        typing the nine.
        """
        self._refuse_while_moving("the preset")
        path = self._find_preset(name)
        try:
            preset = PresetProcessor().load(str(path))
        except PresetError as exc:
            raise Refusal(str(exc)) from exc
        model = self.model
        moving = sorted(axis for axis, row in preset.rows.items()
                        if preset._commands_motion(row))
        if select:
            if not moving:
                raise Refusal(
                    "{0} gives no piston a row of its own, so it does not say "
                    "which pistons to run. Select them first.".format(path.stem))
            model.dropped_axes = []
            model.set_selection(moving)
        if not model.all_motors:
            hint = (" This preset moves pistons {0}; add --select to choose "
                    "them.".format(pistons.describe(moving)) if moving else "")
            raise Refusal("Nothing is selected to apply {0} to.{1}".format(
                path.stem, hint))

        applied, skipped, clamped = [], [], set()
        for motor in model.all_motors:
            values = preset.values_for(motor.axis)
            if values is None:
                skipped.append(motor.axis)
                continue
            # Out-of-range cells are held at the limit, as the Preset Options
            # tab does: refusing the row threw away every other value in it.
            safe = {}
            for parameter, value in values.items():
                spec = params.BY_NAME.get(parameter)
                if spec is None:
                    continue
                held = value
                if spec.minimum is not None and held < spec.minimum:
                    held = spec.minimum
                if spec.maximum is not None and held > spec.maximum:
                    held = spec.maximum
                if held != value:
                    clamped.add("{0} {1} held at {2}".format(parameter, value, held))
                safe[parameter] = held
            motor.update_params(safe)
            applied.append(motor.axis)
        if not applied:
            raise Refusal("{0} has no values for the selected pistons.".format(path.stem))
        model.mark_unprepared()

        parts = ["applied {0}".format(path.stem), self._selection_line()]
        if skipped:
            parts.append("no values for pistons " + pistons.describe(skipped))
        if clamped:
            parts.append("; ".join(sorted(clamped)))
        return Result(" | ".join(parts), {
            "preset": path.stem, "applied": pistons.shown(applied),
            "skipped": pistons.shown(skipped), "clamped": sorted(clamped),
            "warnings": list(preset.warnings)})
