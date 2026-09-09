"""Application state and every command that reaches the machine.

The model owns the motor sets, the machine state, and the worker thread that
talks to the PLC.  Screens call the commands here and are told what happened
through a :class:`UiBridge`; they never touch the PLC themselves and never wait
on it, so the window stays responsive while the machine works.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from logging import Logger, getLogger
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Protocol

from app import params, paths, plc as plc_module, tags
from app.plc import PlcError, Transport
from Motor import Motor
from modules.logging.log_utils import LOGGER_NAME

LOGGER: Logger = getLogger(LOGGER_NAME)

# --- Timings -----------------------------------------------------------------
# The PLC acts on a command bit while it is held high, so the application sets a
# bit, waits, and clears it.  These are the wait lengths the machine has always
# been driven with.  They are named constants so the test suite can shorten them
# and so nobody has to guess what a bare ``time.sleep(5)`` was waiting for.

#: How long Motor_Boot is held high to boot the drives.
BOOT_PULSE_SECONDS = 5.0
#: How long Clear_Motor_Error is held high to clear motion faults.
CLEAR_FAULT_SECONDS = 5.0
#: How long Run_1 is held high for one stroke.
SINGLE_STROKE_SECONDS = 5.0
#: How long Run_Curve is held high for one curve run.
CURVE_SECONDS = 5.0
#: Gap between polls of the drives while homing.
HOME_POLL_SECONDS = 5.0
#: Polls allowed on the settling pass and on the real pass.
HOME_SETTLE_POLLS = 2
HOME_POLLS = 7


class MachineState(Enum):
    """Where the machine is in the prepare-run-stop cycle.

    This replaces the bare integers ``-1``, ``0``, ``1`` and ``2`` that used to
    be assigned in twenty places, several of which disagreed about what each
    number meant.
    """

    #: No pistons chosen yet.
    IDLE = "idle"
    #: Sets defined but not written and homed. "Prepare" is the next step.
    READY = "ready"
    #: Writing parameters or homing. Everything is disabled.
    PREPARING = "preparing"
    #: Written and homed. Safe to start.
    HOMED = "homed"
    #: Pistons are moving.
    RUNNING = "running"


class RunMode(Enum):
    SINGLE = "single"
    CONTINUOUS = "continuous"
    CURVE = "curve"


class UiBridge(Protocol):
    """How the model reports back to whatever is displaying it.

    Every method is called from the worker thread, so an implementation that
    drives Tk must hop back to the main thread (see ``View.post``).
    """

    def status(self, message: str) -> None: ...
    def state_changed(self, state: "MachineState") -> None: ...
    def progress(self, fraction: float, label: str) -> None: ...
    def progress_done(self, artifact: Optional[str]) -> None: ...
    def problem(self, title: str, message: str) -> None: ...


class NullBridge:
    """A bridge that records instead of displaying. Used before the UI exists,
    and by the tests."""

    def __init__(self) -> None:
        self.messages: List[str] = []
        self.states: List[MachineState] = []
        self.problems: List[tuple] = []

    def status(self, message: str) -> None:
        self.messages.append(message)

    def state_changed(self, state: "MachineState") -> None:
        self.states.append(state)

    def progress(self, fraction: float, label: str) -> None:
        pass

    def progress_done(self, artifact: Optional[str]) -> None:
        pass

    def problem(self, title: str, message: str) -> None:
        self.problems.append((title, message))
        LOGGER.error("%s: %s", title, message)


class MotorSet:
    """A group of pistons that run together with the same parameters.

    Sets are the reason this application exists: the lab needs several groups of
    pistons moving with different parameters at the same time.  Each set keeps
    its own parameters, which is what makes that possible -- the previous
    version applied every edit to every set at once, so a second set could only
    ever be a copy of the first.
    """

    def __init__(self, name: str, motors: Iterable[Motor]) -> None:
        self.name = name
        self.motors: Dict[int, Motor] = dict((m.axis, m) for m in motors)

    def __len__(self) -> int:
        return len(self.motors)

    def __iter__(self) -> Iterator[Motor]:
        return iter(self.motors.values())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MotorSet({0!r}, axes={1})".format(self.name, self.axes)

    @property
    def axes(self) -> List[int]:
        return sorted(self.motors)

    def describe(self) -> str:
        return "{0}: motors {1}".format(self.name, ", ".join(str(a) for a in self.axes))

    # -- parameters -----------------------------------------------------------

    def set_param(self, name: str, value: int) -> None:
        """Apply one parameter to every piston in this set, and no others."""
        for motor in self:
            motor.set_param(name, value)

    def common_value(self, name: str) -> Optional[int]:
        """The value of ``name`` if every piston agrees, otherwise ``None``.

        A preset can give pistons in one set different values, so the entry box
        needs a way to say "these differ" rather than silently showing the first
        piston's value and overwriting the rest on the next keystroke.
        """
        values = set(motor.write_params.get(name) for motor in self)
        if len(values) == 1:
            return values.pop()
        return None

    def params_snapshot(self) -> Dict[str, Optional[int]]:
        return dict((spec.name, self.common_value(spec.name)) for spec in params.PARAMS)

    def validate(self) -> List[str]:
        problems: List[str] = []
        for motor in self:
            problems.extend(motor.validate())
        return problems

    @property
    def is_synced(self) -> bool:
        return all(motor.is_synced for motor in self)


class Model:
    """Application state, and the only thing that issues machine commands."""

    IP_ADDRESS: str = "192.168.1.1"
    PROCESSOR_SLOT: int = 1

    LOGGER: Logger = LOGGER
    #: Kept for screens and presets that iterate the parameter names.
    ALL_PARAMS: List[str] = params.PARAM_NAMES

    def __init__(
        self,
        transport: Optional[Transport] = None,
        is_live: Optional[bool] = None,
        ip_address: Optional[str] = None,
        processor_slot: Optional[int] = None,
        simulate: bool = False,
    ) -> None:
        """Build the model.

        Passing ``transport`` skips the connection attempt entirely, which is
        how the tests run.  Otherwise the PLC is not contacted here at all: the
        probe costs up to five seconds when the machine is off, and doing it in
        the constructor meant the window could not appear until it finished.
        :meth:`startup` connects from the worker thread instead, and until it
        does the model is pointed at a simulator so nothing can be left
        holding ``None``.
        """
        self.ip_address = ip_address or self.IP_ADDRESS
        self.processor_slot = (
            processor_slot if processor_slot is not None else self.PROCESSOR_SLOT
        )
        self._simulate = simulate
        #: False until :meth:`startup` has tried to reach the PLC.
        self._connection_attempted = transport is not None

        if transport is not None:
            self.plc: Transport = transport
            self.is_live: bool = bool(is_live)
        else:
            self.plc = plc_module.SimulatedPlc()
            self.is_live = False

        self.bridge: UiBridge = NullBridge()

        #: Which pistons are ticked in the grid but not yet grouped into a set.
        self.selection: Dict[int, bool] = dict(
            (axis, False) for axis in range(tags.MOTOR_COUNT)
        )
        #: Parameters the operator is editing for the pending selection.
        self.pending_params: Dict[str, int] = params.defaults()
        #: The confirmed sets, in the order they were created.
        self.sets: List[MotorSet] = []

        self._state = MachineState.IDLE
        self._busy = threading.Lock()
        self._stop_requested = threading.Event()
        self._current_command: Optional[str] = None

        #: Replaced in tests so commands run inline instead of on a thread.
        self._spawn: Callable[[str, Callable[[], None]], None] = self._spawn_thread

        self.record_analytics: bool = False
        self.analytics_interval: float = 0.25
        self.analytics_duration: float = 10.0

        paths.ensure_directories()

    # -- wiring ---------------------------------------------------------------

    def register_bridge(self, bridge: UiBridge) -> None:
        self.bridge = bridge
        self.bridge.state_changed(self._state)

    # -- state ----------------------------------------------------------------

    @property
    def state(self) -> MachineState:
        return self._state

    def _set_state(self, state: MachineState) -> None:
        if state is not self._state:
            self._state = state
            LOGGER.debug("State -> %s", state.value)
        self.bridge.state_changed(state)

    def _refresh_idle_state(self) -> None:
        """Recompute the resting state after the set list or parameters change."""
        if self._state in (MachineState.PREPARING, MachineState.RUNNING):
            return
        self._set_state(MachineState.READY if self.sets else MachineState.IDLE)

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    @property
    def all_motors(self) -> List[Motor]:
        return [motor for motor_set in self.sets for motor in motor_set]

    @property
    def live_axes(self) -> List[int]:
        return sorted(motor.axis for motor in self.all_motors)

    # -- selecting pistons ----------------------------------------------------

    def toggle(self, axis: int, selected: bool) -> None:
        """Tick or untick a piston in the grid."""
        if axis not in self.selection:
            raise ValueError("No such motor: {0}".format(axis))
        self.selection[axis] = bool(selected)

    def selected_axes(self) -> List[int]:
        return sorted(axis for axis, on in self.selection.items() if on)

    def axis_owner(self, axis: int) -> Optional[MotorSet]:
        """The set a piston already belongs to, if any."""
        for motor_set in self.sets:
            if axis in motor_set.motors:
                return motor_set
        return None

    def set_pending_param(self, name: str, value: int) -> None:
        """Record a parameter for the pistons that have not been grouped yet."""
        spec = params.BY_NAME.get(name)
        if spec is None:
            raise KeyError("Unknown parameter: {0}".format(name))
        problem = spec.validate(value)
        if problem:
            raise ValueError(problem)
        self.pending_params[name] = value

    def create_set(self, name: Optional[str] = None) -> MotorSet:
        """Group the ticked pistons into a new set with the current parameters.

        A piston can only be in one set: grouping it again would mean two sets
        writing different parameters to the same drive, with whichever wrote
        last silently winning.
        """
        axes = self.selected_axes()
        if not axes:
            raise ValueError("Tick at least one motor before creating a set.")

        clashes = [
            (axis, owner.name)
            for axis, owner in ((axis, self.axis_owner(axis)) for axis in axes)
            if owner is not None
        ]
        if clashes:
            raise ValueError(
                "Already in another set: "
                + ", ".join(
                    "motor {0} ({1})".format(axis, owner) for axis, owner in clashes
                )
            )

        motors = []
        for axis in axes:
            motor = Motor(axis)
            motor.update_params(self.pending_params)
            motors.append(motor)

        motor_set = MotorSet(name or "Set {0}".format(len(self.sets) + 1), motors)
        self.sets.append(motor_set)

        for axis in axes:
            self.selection[axis] = False

        LOGGER.log(15, "Created %s", motor_set.describe())
        self._refresh_idle_state()
        return motor_set

    def remove_set(self, motor_set: MotorSet) -> None:
        """Delete one set, freeing its pistons."""
        if motor_set not in self.sets:
            return
        self.sets.remove(motor_set)
        for index, remaining in enumerate(self.sets, start=1):
            if remaining.name.startswith("Set "):
                remaining.name = "Set {0}".format(index)
        LOGGER.info("Removed %s", motor_set.name)
        self._refresh_idle_state()

    def mark_unprepared(self) -> None:
        """Note that the machine no longer matches what the operator asked for.

        Called after a parameter or set changes.  It never interrupts a run --
        stopping the machine is an explicit action, not a side effect of typing.
        """
        if self._state in (MachineState.HOMED,):
            self._set_state(MachineState.READY)
        else:
            self._refresh_idle_state()

    # -- running commands -----------------------------------------------------

    def _spawn_thread(self, name: str, work: Callable[[], None]) -> None:
        threading.Thread(target=work, name=name, daemon=True).start()

    def _command(self, name: str, work: Callable[[], None]) -> bool:
        """Run ``work`` on the worker thread unless another command is running.

        Returns False if the machine was busy.  This is what stops a second
        click on "Prepare" from starting a second homing sequence alongside the
        first -- previously each click spawned another thread, and two homing
        loops would fight over the Home_Button bit.
        """
        if not self._busy.acquire(blocking=False):
            LOGGER.warning(
                "Ignored %s: %s is still running.", name, self._current_command
            )
            return False

        self._current_command = name

        def run() -> None:
            try:
                work()
            except PlcError as exc:
                LOGGER.error("%s failed: %s", name, exc)
                self.bridge.problem("Machine error", str(exc))
                self.bridge.status("{0} failed. See the Feedback tab.".format(name))
                self._recover_after_failure()
            except ValueError as exc:
                LOGGER.error("%s rejected: %s", name, exc)
                self.bridge.problem("Check the parameters", str(exc))
                self.bridge.status("{0} cancelled.".format(name))
                self._recover_after_failure()
            except Exception as exc:  # pragma: no cover - last resort
                LOGGER.exception("%s crashed: %s", name, exc)
                self.bridge.problem("Unexpected error", str(exc))
                self._recover_after_failure()
            finally:
                self._current_command = None
                self._busy.release()

        self._spawn(name, run)
        return True

    def _recover_after_failure(self) -> None:
        """Put the state back somewhere the operator can act from."""
        if self._state is MachineState.PREPARING:
            self._set_state(MachineState.READY if self.sets else MachineState.IDLE)
        else:
            self.bridge.state_changed(self._state)

    def _sleep(self, seconds: float) -> None:
        """Wait, but wake early if a stop has been requested."""
        self._stop_requested.wait(seconds)

    # -- machine commands -----------------------------------------------------

    def clear_faults(self) -> None:
        """Pulse Clear_Motor_Error, which also clears drive motion faults."""
        self.plc.write(tags.CLEAR_MOTOR_ERROR, 1)
        self._sleep(CLEAR_FAULT_SECONDS)
        self.plc.write(tags.CLEAR_MOTOR_ERROR, 0)
        LOGGER.info("Motion faults cleared.")

    def all_stop(self) -> None:
        """Drop every run bit.

        Writes zero to all three run bits, so Stop means stop regardless of
        which mode was started.  The old stop path wrote Run_2 **high** and then
        low, because it reused the same function for starting and stopping --
        pressing Stop therefore commanded a moment of continuous motion first.
        """
        for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE):
            self.plc.write(tag, 0)

    def _clear_live_motors(self) -> None:
        """Zero the Live_Motors array so only chosen pistons can be commanded."""
        for axis in range(tags.MOTOR_COUNT):
            self.plc.write(tags.live_motor(axis), 0)

    def _mark_live_motors(self) -> None:
        """Set the Live_Motors bit for every piston in a set, clear the rest."""
        live = set(self.live_axes)
        for axis in range(tags.MOTOR_COUNT):
            self.plc.write(tags.live_motor(axis), 1 if axis in live else 0)

    def motors_off(self) -> None:
        """Stop everything, clear faults, and deselect every piston on the PLC.

        This is the safe resting state and is what the application does at
        startup and at shutdown.
        """
        self.all_stop()
        self.plc.write(tags.HOME_BUTTON, 0)
        self.clear_faults()
        self._clear_live_motors()
        LOGGER.info("Motors off; run bits and motion faults cleared.")

    def boot_motors(self) -> None:
        """Pulse Motor_Boot to energise the drives."""
        self.plc.write(tags.MOTOR_BOOT, 1)
        self._sleep(BOOT_PULSE_SECONDS)
        self.plc.write(tags.MOTOR_BOOT, 0)
        LOGGER.log(15, "Motors booted.")

    # -- prepare --------------------------------------------------------------

    def prepare(self) -> bool:
        """Write parameters to the machine and home every piston.

        This is step one of every run.  It validates first, so a typo is caught
        before anything is energised rather than raising out of a worker thread
        half-way through writing.
        """
        if not self.sets:
            self.bridge.problem(
                "Nothing to prepare",
                "Add at least one motor set on the Define Motors tab first.",
            )
            return False

        problems: List[str] = []
        for motor_set in self.sets:
            problems.extend(
                "{0}: {1}".format(motor_set.name, problem)
                for problem in motor_set.validate()
            )
        if problems:
            self.bridge.problem("Check the parameters", "\n".join(problems))
            return False

        return self._command("Prepare", self._prepare_worker)

    def _prepare_worker(self) -> None:
        self._stop_requested.clear()
        self._set_state(MachineState.PREPARING)

        self.bridge.status("Selecting motors...")
        self._mark_live_motors()

        self.bridge.status("Clearing motion faults...")
        self.clear_faults()

        self.bridge.status("Booting motors...")
        self.boot_motors()

        total = len(self.all_motors)
        written = 0
        for motor_set in self.sets:
            for motor in motor_set:
                written += 1
                self.bridge.status(
                    "Writing parameters {0}/{1} ({2})...".format(
                        written, total, motor_set.name
                    )
                )
                motor.write_to(self.plc)
        LOGGER.log(15, "Parameters written to %s motors.", total)

        if self._home_motors():
            self._set_state(MachineState.HOMED)
            self.bridge.status("Motors homed and ready to run.")
            LOGGER.log(15, "Motors homed.")
        else:
            self._set_state(MachineState.READY)

    def _home_motors(self) -> bool:
        """Home every piston. Returns True once all drives report homed.

        Homing runs twice on purpose.  The first pass is short and exists
        because the pistons have been observed to home against a high position
        if commanded from certain starting states; the second pass is the one
        that counts.  The reason has never been established, so the behaviour is
        kept as-is -- see docs/OPERATING.md.
        """
        passes = ((HOME_SETTLE_POLLS, False), (HOME_POLLS, True))

        for polls, is_final in passes:
            if self._stop_requested.is_set():
                self.bridge.status("Homing cancelled.")
                return False

            self.plc.write(tags.HOME_BUTTON, 0)
            self._sleep(HOME_POLL_SECONDS)
            self.plc.write(tags.HOME_BUTTON, 1)

            homed = False
            try:
                for poll in range(1, polls + 1):
                    if self._stop_requested.is_set():
                        break
                    self.bridge.status(
                        "Homing motors, pass {0} of 2 ({1}s)...".format(
                            2 if is_final else 1, int(poll * HOME_POLL_SECONDS)
                        )
                    )
                    # Keeps the CIP session alive through a long quiet poll.
                    self.plc.keepalive()
                    self._sleep(HOME_POLL_SECONDS)

                    if all(motor.is_homed(self.plc) for motor in self.all_motors):
                        homed = True
                        break
            finally:
                self.plc.write(tags.HOME_BUTTON, 0)

            if is_final:
                if not homed:
                    timeout = int(polls * HOME_POLL_SECONDS)
                    message = "Motors did not home within {0} seconds.".format(timeout)
                    LOGGER.error(message)
                    self.bridge.status(message)
                    self.bridge.problem("Homing failed", message)
                return homed

        return False

    # -- running --------------------------------------------------------------

    def start(self, mode: RunMode) -> bool:
        """Start the machine in the given mode."""
        if self._state is not MachineState.HOMED:
            self.bridge.problem(
                "Not ready",
                "Press Prepare Motor(s) first: parameters must be written and "
                "the pistons homed before they can run.",
            )
            return False
        return self._command("Start", lambda: self._start_worker(mode))

    def _start_worker(self, mode: RunMode) -> None:
        self._stop_requested.clear()

        # Parameters may have been edited since homing. Push the differences
        # rather than making the operator home the machine again.
        changed = [m for m in self.all_motors if not m.is_synced]
        if changed:
            self.bridge.status("Updating {0} changed motor(s)...".format(len(changed)))
            for motor in changed:
                motor.write_to(self.plc)

        if mode is RunMode.SINGLE:
            self._set_state(MachineState.RUNNING)
            self.bridge.status("Running one stroke...")
            self.plc.write(tags.RUN_SINGLE, 1)
            self._sleep(SINGLE_STROKE_SECONDS)
            self.plc.write(tags.RUN_SINGLE, 0)
            LOGGER.log(15, "Single stroke complete.")
            self._set_state(MachineState.HOMED)
            self.bridge.status("Stroke complete. Ready to run again.")

        elif mode is RunMode.CURVE:
            if self.plc.read(tags.RUN_CURVE):
                LOGGER.warning("A curve is already running; ignoring.")
                return
            self._set_state(MachineState.RUNNING)
            self.bridge.status("Running curve...")
            self.plc.write(tags.RUN_CURVE, 1)
            try:
                if self.record_analytics:
                    self._record_positions(CURVE_SECONDS)
                else:
                    self._sleep(CURVE_SECONDS)
            finally:
                self.plc.write(tags.RUN_CURVE, 0)
            LOGGER.log(15, "Curve complete.")
            self._set_state(MachineState.HOMED)
            self.bridge.status("Curve complete. Ready to run again.")

        else:  # continuous
            self._set_state(MachineState.RUNNING)
            self.plc.write(tags.RUN_CONTINUOUS, 1)
            LOGGER.log(15, "Continuous motion started.")
            self.bridge.status("Running continuously. Press Stop when finished.")
            if self.record_analytics:
                self._record_positions(self.analytics_duration)

    def stop(self) -> bool:
        """Stop the machine.

        Deliberately does not go through :meth:`_command`: Stop must work while
        another command holds the worker, which is exactly when it is needed.
        The run bits are written straight away on the calling thread.
        """
        self._stop_requested.set()
        try:
            self.all_stop()
        except PlcError as exc:
            LOGGER.critical("STOP FAILED: %s", exc)
            self.bridge.problem(
                "Stop failed",
                "The machine did not acknowledge the stop command:\n{0}\n\n"
                "Use the physical stop and check the connection.".format(exc),
            )
            return False

        LOGGER.log(15, "Motors stopped.")
        self.bridge.status("Motors stopped.")
        if self._state in (MachineState.RUNNING, MachineState.PREPARING):
            self._set_state(MachineState.HOMED if self.sets else MachineState.IDLE)
        return True

    # -- analytics ------------------------------------------------------------

    def _record_positions(self, duration: float) -> None:
        """Sample demanded and actual position while the machine runs.

        Writes a table to ``analytics/<date>.txt`` and, if MongoDB happens to be
        running locally, adds the same data there.  The columns are taken from
        the same motor list the samples are, which they were not before: the
        header iterated the sets while the rows iterated a separate dictionary,
        so the numbers could sit under the wrong headings.
        """
        motors = self.all_motors
        if not motors:
            return

        interval = max(self.analytics_interval, 0.01)
        target = paths.analytics_file()
        samples: Dict[str, Dict[str, Dict[str, float]]] = dict(
            ("Motor {0}".format(m.axis), {}) for m in motors
        )

        with open(target, "a+", encoding="utf-8") as handle:
            handle.write("\n----- Run {0} -----\n".format(time.asctime()))
            handle.write("{0:<12}".format("t"))
            for motor in motors:
                handle.write("{0:<24}".format("motor {0}".format(motor.axis)))
            handle.write("\n{0:<12}".format(""))
            for _ in motors:
                handle.write("{0:<12}{1:<12}".format("demand", "actual"))
            handle.write("\n")

            elapsed = 0.0
            while elapsed < duration and not self._stop_requested.is_set():
                self.bridge.progress(
                    min(elapsed / duration, 1.0), "Recording analytics"
                )
                handle.write("{0:<12.4f}".format(elapsed))
                for motor in motors:
                    reading = motor.read_positions(self.plc)
                    samples["Motor {0}".format(motor.axis)]["{0}".format(elapsed)] = {
                        "Actual Position": reading["actual"],
                        "Expected Position": reading["demand"],
                        "Displacement": reading["displacement"],
                    }
                    handle.write(
                        "{0:<12}{1:<12}".format(reading["demand"], reading["actual"])
                    )
                handle.write("\n")
                self._sleep(interval)
                elapsed += interval

        self.bridge.progress(1.0, "Recording analytics")
        self._save_to_database(samples)
        self.bridge.progress_done(str(target))

    def _save_to_database(self, samples: Dict) -> None:
        """Best-effort copy of the run into MongoDB. Never blocks a run."""
        try:
            from database.database import update_database

            update_database(
                time.asctime(), self.analytics_interval, self.analytics_duration, samples
            )
        except Exception as exc:  # pragma: no cover - optional dependency
            LOGGER.info("Analytics not saved to the database: %s", exc)

    # -- lifecycle ------------------------------------------------------------

    def startup(self) -> bool:
        """Bring the machine to a known resting state.

        Run once, in the background, as the window opens.  Doing this on the
        main thread was why the application showed nothing at all for the first
        fifteen seconds after launch.
        """
        return self._command("Startup", self._startup_worker)

    def _startup_worker(self) -> None:
        if not self._connection_attempted:
            self._connection_attempted = True
            self.bridge.status("Looking for the PLC at {0}...".format(self.ip_address))
            self.plc, self.is_live = plc_module.connect(
                self.ip_address, self.processor_slot, simulate=self._simulate
            )
            # Tell the screens, so the connection banner stops saying "looking".
            self.bridge.state_changed(self._state)

        if self.is_live:
            self.bridge.status("Connected. Clearing the machine...")
            self.motors_off()
            self.bridge.status("Ready. Choose motors on the Define Motors tab.")
        else:
            self.bridge.status(
                "Simulation mode: no PLC at {0}. Nothing will move.".format(
                    self.ip_address
                )
            )
        self._set_state(MachineState.IDLE)

    def reset(self) -> bool:
        """Return the whole application to its just-launched state."""
        return self._command("Reset", self._reset_worker)

    def _reset_worker(self) -> None:
        self._stop_requested.set()
        self.motors_off()
        self._stop_requested.clear()

        self.sets = []
        self.selection = dict((axis, False) for axis in range(tags.MOTOR_COUNT))
        self.pending_params = params.defaults()
        self.record_analytics = False
        self.analytics_interval = 0.25
        self.analytics_duration = 10.0

        self._set_state(MachineState.IDLE)
        self.bridge.status("Reset. Choose motors on the Define Motors tab.")
        LOGGER.info("Application reset to its initial state.")

    def shutdown(self) -> None:
        """Stop the machine and close the connection. Called when the window closes."""
        self._stop_requested.set()
        try:
            self.motors_off()
        except PlcError as exc:
            LOGGER.error("Could not clear the machine on shutdown: %s", exc)
        finally:
            try:
                self.plc.close()
            except Exception:  # pragma: no cover
                pass
