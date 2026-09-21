"""Application state and every command that reaches the machine.

The model owns the motor sets, the machine state, and the worker thread that
talks to the PLC.  Screens call the commands here and are told what happened
through a :class:`UiBridge`; they never touch the PLC themselves and never wait
on it, so the window stays responsive while the machine works.
"""

from __future__ import annotations

import math
import threading
import time
from enum import Enum
from logging import Logger, getLogger
from typing import Callable, Dict, Iterable, Iterator, List, Optional

from app import params, paths, plc as plc_module, tags, waves
from app.plc import PlcError, Transport
from Motor import Motor
from modules.logging.log_utils import LOGGER_NAME
from preset_options.PresetProcessor import default_parameters

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
#: Floor for how long a run bit is held, unless the configured motion needs
#: longer -- see :meth:`Model._stroke_seconds`. One stroke no longer uses it:
#: Run_1 is a move to Position 1, so a stroke is built from two of them and
#: timed by :meth:`Model._leg_seconds` instead. This is left serving the curve
#: path, which holds a bit and waits.
SINGLE_STROKE_SECONDS = 5.0
#: Never hold a run bit longer than this, however slow the parameters are.
MAX_STROKE_SECONDS = 120.0
#: Speed used by the movement test in diagnostics. Deliberately gentle: it is
#: asking whether a piston can move at all, not how fast.
PROBE_SPEED = 100
#: How long the movement test waits for the piston to go somewhere.
PROBE_SECONDS = 3.0
#: Speed used to move pistons to their staggered starting points. Gentle: this
#: is positioning, not part of the wave.
STAGE_SPEED = 200
#: How long to wait for staging before cancelling the run.
STAGE_SECONDS = 12.0
#: How long the parity pulse holds continuous motion so every drive
#: commits to the same leg of its cycle. Long enough to be taken, short
#: enough that no piston reaches the far end and turns round again.
PARITY_PULSE_SECONDS = 0.5
#: Time for the pistons to come to rest after the parity pulse.
PARITY_SETTLE_SECONDS = 1.2
#: Close enough to a staging point, in mm.
STAGE_TOLERANCE = 5
#: How long Run_Curve is held high for one curve run.
CURVE_SECONDS = 5.0
#: Gap between polls of the drives while homing.
HOME_POLL_SECONDS = 5.0
#: Polls allowed on the settling pass and on the real pass.
HOME_SETTLE_POLLS = 2
HOME_POLLS = 7

# --- Parking ------------------------------------------------------------------
# After a stop the pistons are left wherever the halt caught them, which is
# usually part-way up. Returning them to the bottom of the stroke leaves the
# array in a known, tidy resting state.
#
# NOTE FOR THE LAB: 370 is the down limit the application enforces (see
# app/params.py; -20 is the top of the stroke, 370 the bottom). If the piston
# should rest somewhere else, this is the one number to change.

#: Where pistons are sent after a stop when resting down: the bottom of travel.
PARK_POSITION = 370
#: Speed used to get there -- deliberately gentle, not whatever the run used.
PARK_SPEED = 200
#: Longest the parking move is allowed to take before giving up.
PARK_SECONDS = 15.0
#: A piston counts as arrived when it is this close, in mm.
PARK_TOLERANCE = 6
#: Set False to leave the pistons where they stop.
PARK_ON_STOP = True
#: How long the resting move waits for _busy to come free before giving up.
#: The Stop worker reaches _park_worker well before the run thread it just
#: interrupted has unwound -- _hold_and_watch/_record_positions hold _busy for
#: the whole run -- so an immediate, non-blocking acquire loses that race on
#: almost every stop. This is a short wait for that thread's own cleanup, not
#: a wait for an unrelated command; it still gives up rather than blocking
#: forever if _busy is genuinely held by something else.
PARK_ACQUIRE_TIMEOUT_SECONDS = 2.0

# Where the pistons come to rest when stopped.
REST_DOWN = "down"     # the bottom of travel, PARK_POSITION
REST_UP = "up"         # the top of each piston's own stroke (its Position 1)
REST_HOLD = "hold"     # wherever the stop caught them

# --- Stopping gracefully -------------------------------------------------------
# "It's best to hit stop when the motor shafts are up and down" -- the operator
# had to time the button press. The positions are published continuously, so the
# application can wait for the end of a stroke instead of making a person do it.
#
# Pressing Stop a second time, or Escape, halts immediately without waiting.

#: Longest to wait for the pistons to reach the end of a stroke before halting
#: anyway. A staggered wave may never have all thirty aligned at once, and the
#: resting move that follows puts them somewhere known regardless.
GRACEFUL_STOP_SECONDS = 6.0
#: How close to Position 1 or Position 2 counts as the end of a stroke, in mm.
STROKE_END_TOLERANCE = 12
#: Position polling rate while stopping. Faster than the display's rate, so the
#: end of a stroke is not missed at speed.
STOP_POLL_INTERVAL = 0.05

# --- Watching for trouble ------------------------------------------------------
# Pistons wear at different rates, so they drift out of step, and one can stall
# entirely -- "the last row, one goes up real slow, then gets stuck". Demanded
# and actual position are both published, so a piston that stops following its
# demand can be flagged rather than noticed by eye.

# Detection works on how far each piston actually travels, not on the gap
# between ComDemandPosition and ComActualPosition.
#
# That gap was the obvious measure and the first thing tried, but it depends on
# what the PLC publishes as the demanded position. If it is the instantaneous
# interpolated command, the gap is the following error and is small. If it is
# the *endpoint* of the stroke, then every healthy piston mid-travel looks
# hundreds of millimetres behind and the whole array lights up. Which of the two
# this controller publishes is not established, so the measure is not used.
#
# How far a piston moves does not depend on that at all. A piston that is
# dragging or seized stops covering its stroke, whatever the tags mean, and a
# staggered wave does not upset it because each piston is compared against its
# own commanded stroke rather than against its neighbours.

#: Seconds of movement history used to judge whether a piston is travelling.
MOVEMENT_WINDOW = 3.0
#: A piston is flagged when it covers less than this fraction of the stroke it
#: was told to make. Generous, so only a real problem trips it.
STUCK_FRACTION = 0.35
#: Strokes shorter than this are not judged; there is too little to measure.
MIN_JUDGED_STROKE = 25

#: How often live piston positions are read while the machine runs.
#: Four times a second is enough for the array to read as moving without
#: putting meaningful extra traffic on the controller.
MONITOR_INTERVAL = 0.25


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


class UiBridge:
    """How the model reports back to whatever is displaying it.

    Every method is called from the worker thread, so an implementation that
    drives Tk must hop back to the main thread (see ``View.post``).

    A plain base class, not ``typing.Protocol``, which needs Python 3.8 -- the
    lab PC is Windows 7 and cannot go past 3.8. :class:`NullBridge` and
    ``View`` both satisfy it by duck typing.
    """

    def status(self, message: str) -> None:
        raise NotImplementedError

    def state_changed(self, state: "MachineState") -> None:
        raise NotImplementedError

    def progress(self, fraction: float, label: str) -> None:
        raise NotImplementedError

    def progress_done(self, artifact: Optional[str]) -> None:
        raise NotImplementedError

    def problem(self, title: str, message: str) -> None:
        raise NotImplementedError

    def positions(self, readings: Dict[int, float]) -> None:
        """Live piston positions, several times a second while running."""
        raise NotImplementedError


class NullBridge:
    """A bridge that records instead of displaying. Used before the UI exists,
    and by the tests."""

    def __init__(self) -> None:
        self.messages: List[str] = []
        self.states: List[MachineState] = []
        self.problems: List[tuple] = []
        self.last_positions: Dict[int, float] = {}

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

    def positions(self, readings: Dict[int, float]) -> None:
        self.last_positions = dict(readings)


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
        return "{0}: pistons {1}".format(self.name, tags.display_list(self.axes))

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
        persistent_connection: bool = True,
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
        self._persistent_connection = persistent_connection
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
        #: Seeded from the default preset rather than the factory defaults, so
        #: a piston picked on the tank is already set up to move and the
        #: Preset Options tab is somewhere to go for a *different* preset, not
        #: a step every run has to start with.
        self.pending_params: Dict[str, int] = default_parameters()
        #: The groups of pistons, in the order they were created.
        self.sets: List[MotorSet] = []
        #: While True the tank selection builds a group directly, so an
        #: operator running one group never meets the idea of a "set" at all.
        self._implicit_group = True
        #: Index of the group the selection is currently building. Groups
        #: before it are frozen. "Add group" moves this on.
        self._live_index = 0

        self._state = MachineState.IDLE
        self._busy = threading.Lock()
        self._motion_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._current_command: Optional[str] = None
        #: Set while the parking move is running, so a second Stop cancels it.
        self._parking = threading.Event()
        self._cancel_park = threading.Event()
        #: Set while staging the cascade before a continuous run. Staging
        #: runs with the state at PREPARING (see `_start_worker`), the same
        #: state real homing uses, so `_halt_and_rest` needs this to tell the
        #: two apart -- a Stop landing during staging must not be read as an
        #: interrupted homing pass.
        self._staging = threading.Event()
        #: Set while waiting for the end of a stroke before halting.
        self._stopping = threading.Event()
        #: Set once by `shutdown()`. `probe_movement` runs on its own thread
        #: (diagnostics probes are started independent of the worker thread),
        #: takes neither `_busy` nor `_motion_lock`, and is the one command
        #: that used to clear `_stop_requested` itself -- so without this gate
        #: it could raise Run_1 after shutdown had already deselected every
        #: piston and closed the transport.
        self._shutdown = threading.Event()
        self._stop_serial = 0
        #: Where the pistons come to rest after a stop.
        self.rest_position = REST_DOWN

        #: Replaced in tests so commands run inline instead of on a thread.
        self._spawn: Callable[[str, Callable[[], None]], None] = self._spawn_thread

        self.record_analytics: bool = False
        self.analytics_interval: float = 0.25
        self.analytics_duration: float = 10.0

        #: Live position monitoring, started with the first run.
        self._monitor_stop = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        #: Axes whose position could not be read, so the display can flag them.
        self.unreadable_axes: List[int] = []
        #: Axes that are not keeping up with their demanded position -- a piston
        #: dragging or stalled. Shown on the tank and logged once.
        self.lagging_axes: List[int] = []
        #: Pistons that would not home. Shown on the tank and offered for
        #: removal so a stuck one does not block the whole array.
        self.unhomed_axes: List[int] = []
        #: Pistons dropped from the run because they would not home. Cleared
        #: from :attr:`unhomed_axes` once dropped, so this is what the tank
        #: marks and what the operator is told about.
        self.dropped_axes: List[int] = []
        #: Pistons this session has actually homed. Homing is slow, so once a
        #: piston is referenced there is no reason to do it again -- but only
        #: pistons homed under our own control count, never ones that merely
        #: have the status bit set, because homing at a wrong position is the
        #: very thing the double pass exists to guard against.
        self._homed_axes: set = set()
        #: axis -> [(when, position), ...] over the last MOVEMENT_WINDOW seconds.
        self._history: Dict[int, list] = {}
        self._lag_reported: set = set()
        #: Which mode the current run was started in.
        self._run_mode: Optional[RunMode] = None
        #: A stroke edit requested during motion, waiting for a common end.
        self._pending_live_stroke: Optional[int] = None
        self._pending_live_stroke_axes: List[int] = []
        self._pending_live_stroke_applied: set = set()

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
        self._sync_implicit_group()

    def set_selection(self, axes) -> None:
        """Replace the whole selection at once."""
        wanted = set(axes)
        for axis in self.selection:
            self.selection[axis] = axis in wanted
        self._sync_implicit_group()

    def _sync_implicit_group(self) -> None:
        """Keep the group being built in step with what is selected.

        The selection always drives the *newest* group. Earlier groups are
        frozen: their pistons are off limits and their parameters are left
        alone. "Add group" simply moves the live index on, so selecting pistons
        after pressing it builds the next group rather than doing nothing --
        which is what happened when this only ever tracked group one.
        """
        if not self._implicit_group:
            return

        frozen = self.sets[: self._live_index]
        taken = set(axis for group in frozen for axis in group.axes)
        axes = [a for a in self.selected_axes() if a not in taken]
        existing = self.sets[self._live_index] if len(self.sets) > self._live_index else None

        if not axes:
            self.sets = list(frozen)
            self._refresh_idle_state()
            return

        # Carry the current parameters onto any newly added piston, so adding
        # one to the group does not silently give it the factory defaults.
        template = dict(self.pending_params)
        if existing is not None:
            for spec in params.PARAMS:
                shared = existing.common_value(spec.name)
                if shared is not None:
                    template[spec.name] = shared

        motors = []
        for axis in axes:
            motor = existing.motors.get(axis) if existing is not None else None
            if motor is None:
                motor = Motor(axis)
                motor.update_params(template)
            motors.append(motor)

        name = (
            existing.name if existing is not None
            else "Group {0}".format(self._live_index + 1)
        )
        self.sets = list(frozen) + [MotorSet(name, motors)]
        self.mark_unprepared()

    def add_group(self) -> None:
        """Freeze the group being built and start the next one.

        This is the moment groups stop being invisible: from here on the
        operator is managing more than one and the interface says so.
        """
        if len(self.sets) <= self._live_index:
            raise ValueError("Select some pistons before adding another group.")
        self._live_index = len(self.sets)
        for axis in self.selection:
            self.selection[axis] = False

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

        # While groups are implicit the selection already *is* the group, so
        # asking to create it is asking for what already exists. Returning it
        # keeps this callable either way rather than complaining that the
        # pistons clash with the group they are already in.
        if self._implicit_group and len(self.sets) > self._live_index:
            live = self.sets[self._live_index]
            if set(axes) == set(live.axes):
                return live

        clashes = [
            (axis, owner.name)
            for axis, owner in ((axis, self.axis_owner(axis)) for axis in axes)
            if owner is not None
        ]
        if clashes:
            raise ValueError(
                "Already in another set: "
                + ", ".join(
                    "piston {0} ({1})".format(tags.display_number(axis), owner)
                    for axis, owner in clashes
                )
            )

        motors = []
        for axis in axes:
            motor = Motor(axis)
            motor.update_params(self.pending_params)
            motors.append(motor)

        motor_set = MotorSet(name or "Group {0}".format(len(self.sets) + 1), motors)
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
            if remaining.name.startswith("Group "):
                remaining.name = "Group {0}".format(index)
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
        # Reset and Reconnect are exempt from the RUNNING guard: a PlcError
        # out of a run leaves the state at RUNNING (see _recover_after_failure),
        # and those two commands are exactly how an operator gets out of a
        # wedged machine, so refusing them here would make that state
        # permanent until a Stop happened to succeed.
        running_but_exempt = name in ("Reset", "Reconnect")
        if not running_but_exempt and (
                self._state is MachineState.RUNNING
                or self._stopping.is_set() or self._parking.is_set()):
            LOGGER.warning("Ignored %s: a stop or resting move is still running.", name)
            return False
        if not self._busy.acquire(blocking=False):
            LOGGER.warning(
                "Ignored %s: %s is still running.", name, self._current_command
            )
            return False

        self._current_command = name
        self._stop_requested.clear()

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
        """Put the state back somewhere the operator can act from.

        A PlcError out of a run (for instance a failed write(tag, 0) in
        _hold_and_watch's finally) used to leave the state at RUNNING for
        ever: this rewound PREPARING but not RUNNING, and _command refuses
        Prepare, Start, Calibrate, Reset and Reconnect alike while RUNNING --
        so nothing could ever get the machine out of it again. The drives are
        still homed, exactly as after a normal stop, so this rewinds to
        HOMED rather than forcing a fresh Prepare.
        """
        if self._state is MachineState.PREPARING:
            self._set_state(MachineState.READY if self.sets else MachineState.IDLE)
        elif self._state is MachineState.RUNNING:
            self._set_state(MachineState.HOMED if self.sets else MachineState.IDLE)
        else:
            self.bridge.state_changed(self._state)

    def _sleep(self, seconds: float) -> None:
        """Wait, but wake early if a stop has been requested."""
        self._stop_requested.wait(seconds)

    # -- machine commands -----------------------------------------------------

    def clear_faults(self) -> None:
        """Pulse Clear_Motor_Error, which also clears drive motion faults.

        Waits with `self._sleep` (``_stop_requested.wait``), not a raw
        `time.sleep`: `shutdown()` sets `_stop_requested` specifically so this
        pulse cuts short, and View calls `shutdown()` on the Tk thread from
        the window's close handler, so a raw sleep here froze the window for
        the whole five seconds.
        """
        self.plc.write(tags.CLEAR_MOTOR_ERROR, 1)
        try:
            self._sleep(CLEAR_FAULT_SECONDS)
        finally:
            self.plc.write(tags.CLEAR_MOTOR_ERROR, 0)
        LOGGER.info("Motion faults cleared.")

    def all_stop(self, include_home: bool = False) -> None:
        """Drop every run bit.

        Writes zero to all three run bits, so Stop means stop regardless of
        which mode was started.  The old stop path wrote Run_2 **high** and then
        low, because it reused the same function for starting and stopping --
        pressing Stop therefore commanded a moment of continuous motion first.
        """
        with self._motion_lock:
            commands = [tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE]
            if include_home:
                commands.append(tags.HOME_BUTTON)
            errors = []
            for tag in commands:
                try:
                    self.plc.write(tag, 0)
                except PlcError as exc:
                    errors.append("{0}: {1}".format(tag, exc))
            if errors:
                raise PlcError("; ".join(errors))

    def _begin_motion(self, tag: str) -> bool:
        """Serialize command starts with Stop and recheck cancellation."""
        with self._motion_lock:
            if self._stop_requested.is_set():
                return False
            self.plc.write(tag, 1)
            return True

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
        self.all_stop(include_home=True)
        self.clear_faults()
        self._clear_live_motors()
        self._homed_axes = set()
        LOGGER.info("Motors off; run bits and motion faults cleared.")

    def boot_motors(self) -> None:
        """Pulse Motor_Boot to energise the drives.

        Waits with `self._sleep`, for the same reason as `clear_faults`: a
        raw `time.sleep` here does not wake early on `shutdown()`'s
        `_stop_requested`, and shutdown runs on the Tk thread, so the window
        would sit frozen for the whole boot pulse while closing.
        """
        self.plc.write(tags.MOTOR_BOOT, 1)
        try:
            self._sleep(BOOT_PULSE_SECONDS)
        finally:
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
                "Choose some pistons on the tank first.",
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
        self.unhomed_axes = []
        self._set_state(MachineState.PREPARING)

        self.bridge.status("Selecting motors...")
        self._mark_live_motors()

        # Pistons already homed in this session do not need homing again, and
        # homing takes the better part of a minute. This matters most after a
        # partial failure: one stuck piston used to mean every other piston was
        # re-homed on the next attempt, for nothing.
        if self._already_homed():
            self.bridge.status("Already homed. Writing parameters...")
            self._write_all_parameters()
            if self._stop_requested.is_set():
                self._set_state(MachineState.READY)
                return
            self._set_state(MachineState.HOMED)
            self.bridge.status("Ready to run.")
            LOGGER.log(15, "Skipped homing: these pistons are already homed.")
            return

        self.bridge.status("Clearing motion faults...")
        self.clear_faults()

        if self._stop_requested.is_set():
            self._set_state(MachineState.READY)
            return
        self.bridge.status("Booting motors...")
        self.boot_motors()

        self._write_all_parameters()

        if self._home_motors() and not self._stop_requested.is_set():
            self._set_state(MachineState.HOMED)
            self.bridge.status("Motors homed and ready to run.")
            LOGGER.log(15, "Motors homed.")
        elif self.unhomed_axes and not self._stop_requested.is_set():
            # One mechanically stuck piston used to cost the whole array. The
            # run ended in READY rather than HOMED, so Start did nothing and
            # every other piston sat there -- which is exactly what "the
            # wavemaker is not moving" looks like from the operating position.
            # Measured on this machine on 21 September 2026: piston 26 would
            # not home ("has not moved since homing began"), and a thirty-piston
            # Rows-out-of-step run was abandoned before it began. With piston 26
            # dropped, the other twenty-nine staged and ran correctly.
            #
            # So a piston that will not home is now dropped automatically and
            # the rest carry on. drop_unhomed() puts the state back to HOMED
            # itself when the survivors are still referenced, and says which
            # pistons went. A piston that cannot home cannot run, so keeping it
            # selected only blocks the pistons that are fine.
            stuck = list(self.unhomed_axes)
            dropped = self.drop_unhomed()
            #: Kept for the display after unhomed_axes is cleared, so the tank
            #: can still mark them and the operator can see what went.
            self.dropped_axes = list(dropped or stuck)
            if self.state is not MachineState.HOMED:
                self._set_state(MachineState.READY)
        else:
            self._set_state(MachineState.READY)

    def _write_all_parameters(self) -> None:
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

    def _already_homed(self) -> bool:
        """Whether every selected piston was homed earlier in this session.

        Both conditions must hold: this session homed it, and the drive still
        says so. The first stops a piston that merely powered up with the bit
        set from being trusted; the second catches one that has since lost its
        reference.
        """
        live = self.live_axes
        if not live:
            return False
        if not all(axis in self._homed_axes for axis in live):
            return False
        try:
            return all(motor.is_homed(self.plc) for motor in self.all_motors)
        except PlcError:
            return False

    # -- checking the drives before anything moves ---------------------------

    def check_drives(self, axes=None) -> Dict[int, str]:
        """Look for trouble without commanding any motion.

        Every drive publishes a warn word, and a healthy one reads zero. That
        was being read and thrown away. Checking it costs one read per piston
        and takes a moment, which is a great deal better than discovering a bad
        drive thirty-five seconds into a homing cycle.

        Returns axis -> what is wrong, for the pistons that have a problem.
        """
        if axes is None:
            axes = self.live_axes or list(range(tags.MOTOR_COUNT))

        problems: Dict[int, str] = {}
        for axis in axes:
            motor = Motor(axis)
            try:
                status = motor.read_status(self.plc)
            except (PlcError, TypeError, ValueError) as exc:
                problems[axis] = "cannot be read ({0})".format(exc)
                continue
            faults = status.faults
            if faults:
                problems[axis] = "; ".join(faults)
        return problems

    def drive_report(self, axes=None) -> Dict[int, str]:
        """A line per piston describing what its drive is reporting.

        Everything named rather than in hex, for the Feedback tab and for
        anyone trying to work out why a piston will not behave.
        """
        if axes is None:
            axes = self.live_axes or list(range(tags.MOTOR_COUNT))
        report: Dict[int, str] = {}
        for axis in axes:
            try:
                report[axis] = Motor(axis).read_status(self.plc).summary()
            except (PlcError, TypeError, ValueError) as exc:
                report[axis] = "could not be read ({0})".format(exc)
        return report

    def calibrate_all(self) -> bool:
        """Home every piston in the machine, whatever is selected.

        Homing is per-drive, so doing the whole array once at the start of a
        session means no later selection has to wait for it. A piston that will
        not home is named and simply left out; the rest are referenced and
        ready.
        """
        return self._command("Calibrate", self._calibrate_worker)

    def _calibrate_worker(self) -> None:
        self._set_state(MachineState.PREPARING)
        every = list(range(tags.MOTOR_COUNT))

        self.bridge.status("Checking the drives...")
        problems = self.check_drives(every)
        if problems:
            self.bridge.problem(
                "Some drives report a fault",
                "These pistons have something wrong before homing has even "
                "started:{0}{0}{1}{0}{0}They will be homed anyway, and any that "
                "will not home are named afterwards.".format(
                    chr(10),
                    chr(10).join(
                        "  piston {0}: {1}".format(tags.display_number(a), why)
                        for a, why in sorted(problems.items())
                    ),
                ),
            )
        if problems:
            LOGGER.warning(
                "Before homing: %s",
                "; ".join(
                    "piston {0} {1}".format(tags.display_number(a), why)
                    for a, why in sorted(problems.items())
                ),
            )

        self.bridge.status("Selecting every piston...")
        for axis in every:
            self.plc.write(tags.live_motor(axis), 1)

        self.bridge.status("Clearing motion faults...")
        self.clear_faults()
        homed = False
        if not self._stop_requested.is_set():
            self.bridge.status("Booting motors...")
            self.boot_motors()
            homed = self._home_motors(axes=every)

        # Put the operator's own selection back on the machine.
        self._mark_live_motors()

        if self._stop_requested.is_set():
            self._homed_axes.clear()
            self._set_state(MachineState.READY if self.sets else MachineState.IDLE)
            return
        if homed:
            self._homed_axes = set(every)
            self.bridge.status("All 30 pistons are calibrated.")
            LOGGER.log(15, "Calibrated all 30 pistons.")
        else:
            stuck = self.unhomed_axes
            self._homed_axes = set(a for a in every if a not in stuck)
            self.bridge.status(
                "{0} of 30 pistons calibrated. Piston(s) {1} did not.".format(
                    30 - len(stuck), tags.display_list(stuck)
                )
            )
        self._set_state(MachineState.READY if self.sets else MachineState.IDLE)

    def _home_motors(self, axes=None) -> bool:
        """Home the pistons. Returns True once every drive reports homed.

        Homing runs twice on purpose.  The first pass is short and exists
        because the pistons have been observed to home against a high position
        if commanded from certain starting states; the second pass is the one
        that counts.  The reason has never been established, so the behaviour is
        kept as-is -- see docs/OPERATING.md.

        A piston that is mechanically stuck is spotted while this runs rather
        than at the end of it: every healthy piston travels during homing, so
        one whose position never changes is reported within a few seconds
        instead of after the full timeout.
        """
        motors = [Motor(a) for a in axes] if axes is not None else self.all_motors
        passes = ((HOME_SETTLE_POLLS, False), (HOME_POLLS, True))
        start_positions = self._positions_of(motors)

        for polls, is_final in passes:
            if self._stop_requested.is_set():
                self.bridge.status("Homing cancelled.")
                return False

            self.plc.write(tags.HOME_BUTTON, 0)
            self._sleep(HOME_POLL_SECONDS)
            if self._stop_requested.is_set():
                return False
            if not self._begin_motion(tags.HOME_BUTTON):
                return False

            homed = False
            try:
                for poll in range(1, polls + 1):
                    if self._stop_requested.is_set():
                        break
                    self.bridge.status(
                        "Homing pistons, pass {0} of 2 ({1}s)...".format(
                            2 if is_final else 1, int(poll * HOME_POLL_SECONDS)
                        )
                    )
                    # Keeps the CIP session alive through a long quiet poll.
                    self.plc.keepalive()
                    self._sleep(HOME_POLL_SECONDS)

                    if self._stop_requested.is_set():
                        break
                    if all(motor.is_homed(self.plc) for motor in motors):
                        homed = True
                        if is_final:
                            self._homed_axes.update(m.axis for m in motors)
                        break

                    if is_final and poll == 2:
                        self._warn_about_motionless(motors, start_positions)
            finally:
                self.plc.write(tags.HOME_BUTTON, 0)

            if self._stop_requested.is_set():
                return False
            if is_final:
                if not homed:
                    self._report_homing_failure(
                        int(polls * HOME_POLL_SECONDS), motors
                    )
                return homed

        return False

    def _positions_of(self, motors) -> Dict[int, float]:
        positions = {}
        for motor in motors:
            try:
                positions[motor.axis] = motor.read_position(self.plc)
            except (PlcError, TypeError, ValueError):
                continue
        return positions

    def _warn_about_motionless(self, motors, start_positions) -> None:
        """Name pistons that have not moved at all since homing began.

        Homing drives every piston to its home position, so one that has not
        shifted by even a millimetre after several seconds is not simply slow --
        it is stuck. Saying so now, rather than at the end of the timeout, is
        the difference between a few seconds and the better part of a minute.
        """
        now = self._positions_of(motors)
        motionless = []
        for motor in motors:
            was = start_positions.get(motor.axis)
            is_now = now.get(motor.axis)
            if was is None or is_now is None:
                continue
            if abs(is_now - was) < 1.0 and not motor.is_homed(self.plc):
                motionless.append(motor.axis)

        if not motionless:
            return
        self.unhomed_axes = motionless
        names = tags.display_list(motionless)
        LOGGER.warning("Piston(s) %s have not moved since homing began.", names)
        self.bridge.status(
            "Piston(s) {0} are not moving. They may be stuck. Still waiting for "
            "the rest...".format(names)
        )

    def _report_homing_failure(self, timeout: int, motors=None) -> None:
        """Say which pistons did not home, not merely that homing failed.

        "Motors did not home within 35 seconds" gave the operator nothing to act
        on. With thirty pistons, one of them stuck, the useful facts are which
        one and what can be done about it -- so this names them, records them on
        :attr:`unhomed_axes` for the display, and leaves the rest homed so the
        run can go ahead without the bad one.
        """
        stuck: List[int] = []
        if motors is None:
            motors = self.all_motors
        for motor in motors:
            if motor.is_homed(self.plc):
                # Homed, even though a neighbour held the whole run up.
                self._homed_axes.add(motor.axis)
            else:
                stuck.append(motor.axis)
                self._homed_axes.discard(motor.axis)

        self.unhomed_axes = stuck
        total = len(motors)
        homed_count = total - len(stuck)

        if not stuck:
            # Every piston reports homed even though the loop gave up: the
            # last poll must have arrived after the timeout.
            LOGGER.warning("Homing timed out, but every piston reports homed.")
            self.bridge.status("Homing timed out, but all pistons report homed.")
            return

        names = tags.display_list(stuck)
        summary = "{0} of {1} pistons homed. Piston(s) {2} did not.".format(
            homed_count, total, names
        )
        LOGGER.error("%s Timed out after %s seconds.", summary, timeout)
        self.bridge.status(summary)
        self.bridge.problem(
            "Some pistons did not home",
            "{0}{1}{1}A piston that will not home is usually stuck or faulted "
            "mechanically -- it is not something the software can clear.{1}{1}"
            "You can carry on without it: deselect piston(s) {2} on the tank "
            "and press Start again. The rest are homed and ready.".format(
                summary, chr(10), names
            ),
        )

    # -- diagnostics ----------------------------------------------------------

    def diagnose_axis(self, axis: int, probe: bool = False):
        """Read everything about one piston and say what is wrong with it.

        Read-only unless *probe* is set. The PLC transport takes a lock per
        operation, so this is safe to run while the machine is doing something
        else -- which matters, because the most informative moment to ask why a
        piston is not moving is while the others are.
        """
        from app import diagnostics

        motor = next((m for m in self.all_motors if m.axis == axis), None)
        if motor is None:
            motor = Motor(axis)
            expected_live = None
        else:
            expected_live = True if self._state in (MachineState.HOMED, MachineState.RUNNING) else None

        probe_fn = None
        if probe:
            probe_fn = lambda: self.probe_movement(axis)  # noqa: E731
        return diagnostics.diagnose(
            motor, self.plc, expected_live=expected_live, probe=probe_fn
        )

    def diagnose_all(self, axes=None):
        """Diagnose several pistons in one pass. Never probes."""
        wanted = list(axes) if axes is not None else list(range(tags.MOTOR_COUNT))
        return [self.diagnose_axis(axis) for axis in wanted]

    def probe_movement(self, axis: int, distance: float = 10.0) -> float:
        """Ask one piston to move a little, and report how far it actually did.

        This is the question no amount of reading can answer: a drive that is
        enabled, unfaulted and sitting still looks identical whether it is
        jammed or was simply never commanded. Asking it to move ten millimetres
        separates the two.

        The piston is put back where it started, and the parameters are marked
        unwritten afterwards so the operator's own values go out again before
        any real run.

        Runs on its own thread (diagnostics probes are started independent of
        the worker thread) and takes neither `_busy` nor `_motion_lock` the
        way `_command` does, so `shutdown()` -- which sets `_stop_requested`
        and closes the transport, without taking either -- must be checked
        here explicitly. `_stop_requested` itself is never cleared here: that
        is `_command`'s job alone, so a probe run with a stop already pending
        (from a Stop that has not fully unwound, or from shutdown) stays
        refused rather than clearing the flag out from under it.
        """
        if self._shutdown.is_set():
            raise ValueError("The machine has been shut down.")
        if not self._busy.acquire(blocking=False):
            raise ValueError("The machine is busy; stop it before a movement test.")
        # _command sets this for every other holder of _busy; probe_movement
        # takes _busy directly and used to leave it None, so a command
        # blocked behind a running movement test was logged as "Ignored
        # Start: None is still running." -- true but useless for working out
        # what was actually in the way.
        self._current_command = "Movement test"
        try:
            if (self._shutdown.is_set()
                    or self._state in (MachineState.RUNNING, MachineState.PREPARING)
                    or self._parking.is_set() or self._stopping.is_set()
                    or self._stop_requested.is_set()):
                raise ValueError("Stop the machine before a movement test.")
            return self._probe_movement_worker(axis, distance)
        finally:
            self._current_command = None
            self._busy.release()

    def _probe_movement_worker(self, axis: int, distance: float) -> float:
        motor = next((m for m in self.all_motors if m.axis == axis), Motor(axis))
        start = motor.read_position(self.plc)
        spec = params.BY_NAME["Position 1"]
        if not math.isfinite(start) or spec.validate(start):
            raise ValueError("Movement test requires a finite starting position within travel limits.")
        distance = float(distance)
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError("Movement test distance must be finite and positive.")
        target = start + distance
        if spec.validate(target):
            target = start - distance
        if not math.isfinite(target) or spec.validate(target):
            raise ValueError("Movement test target is outside travel limits.")
        target, return_target = int(round(target)), int(round(start))
        if spec.validate(target) or spec.validate(return_target):
            raise ValueError("Movement test targets are outside travel limits.")
        # Save every bit before changing any: Run_1 acts on the global selection.
        saved = [(tags.live_motor(a), self.plc.read(tags.live_motor(a)))
                 for a in range(tags.MOTOR_COUNT)]
        if any(self.plc.read(bit) for bit in
               (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE, tags.HOME_BUTTON)):
            raise ValueError("A machine command is active; movement test cancelled.")
        furthest = start
        errors = []
        try:
            for a in range(tags.MOTOR_COUNT):
                self.plc.write(tags.live_motor(a), 1 if a == axis else 0)
            for name, value in (("Move Type", 0), ("Position 1", target),
                                ("Position 2", target), ("Speed 1", PROBE_SPEED),
                                ("Speed 2", PROBE_SPEED)):
                self.plc.write(params.BY_NAME[name].tag(axis), value)
            if not self._stop_requested.is_set():
                self._begin_motion(tags.RUN_SINGLE)
                deadline = time.time() + PROBE_SECONDS
                while time.time() < deadline and not self._stop_requested.is_set():
                    where = motor.read_position(self.plc)
                    if not math.isfinite(where):
                        raise ValueError("Movement test received invalid position feedback.")
                    if abs(where - start) > abs(furthest - start):
                        furthest = where
                    self._sleep(STOP_POLL_INTERVAL)
                self.plc.write(tags.RUN_SINGLE, 0)
            if not self._stop_requested.is_set():
                self.plc.write(params.BY_NAME["Position 1"].tag(axis), return_target)
                self.plc.write(params.BY_NAME["Position 2"].tag(axis), return_target)
                self._begin_motion(tags.RUN_SINGLE)
                self._sleep(PROBE_SECONDS / 2.0)
        finally:
            motor.current_params = {}
            motor.write_success = False
            cleared = False
            try:
                self.plc.write(tags.RUN_SINGLE, 0)
                cleared = True
            except PlcError as exc:
                errors.append("Could not clear movement-test run bit: {0}".format(exc))
            # Never reselect other pistons while Run_1 might still be high.
            if cleared:
                for tag, value in saved:
                    try:
                        self.plc.write(tag, value)
                    except PlcError as exc:
                        errors.append("Could not restore {0}: {1}".format(tag, exc))
            if errors:
                self._set_state(MachineState.READY if self.sets else MachineState.IDLE)
                self.bridge.problem("Movement test cleanup failed", "\n".join(errors))
                raise PlcError("; ".join(errors))

        moved = abs(furthest - start)
        LOGGER.info(
            "Movement test on piston %s: asked for %.0f mm, moved %.1f mm.",
            tags.display_number(axis), distance, moved,
        )
        return moved

    def drop_unhomed(self) -> List[int]:
        """Remove the pistons that would not home from their groups.

        Lets a session continue around a piston that is stuck, rather than
        making the whole array unusable until somebody frees it.
        """
        dropped = list(self.unhomed_axes)
        if not dropped:
            return []
        for motor_set in list(self.sets):
            for axis in dropped:
                motor_set.motors.pop(axis, None)
            if not motor_set.motors:
                self.sets.remove(motor_set)
        for axis in dropped:
            self.selection[axis] = False
        self.unhomed_axes = []
        LOGGER.info("Dropped piston(s) %s from the run.", tags.display_list(dropped))

        # The pistons that are left homed perfectly well. Going back to READY
        # would re-home all of them on the next Start, which is what made a
        # single stuck piston cost a minute every attempt.
        if self.sets and self._already_homed():
            self._set_state(MachineState.HOMED)
            self.bridge.status(
                "Removed piston(s) {0}. The rest are still homed - press Start.".format(
                    tags.display_list(dropped)
                )
            )
        else:
            self._refresh_idle_state()
        return dropped

    # -- running --------------------------------------------------------------

    @property
    def needs_homing(self) -> bool:
        """Whether a run would actually have to home the pistons.

        The state machine on its own is not enough to answer this. Editing a
        parameter or changing the selection drops the state out of HOMED, but
        the drives are still referenced, and :meth:`_prepare_worker` will skip
        homing when :meth:`_already_homed` holds. Asking "home first? this
        takes about a minute" in that case is asking about something that is
        not going to happen, and the honest answer costs the operator a minute
        of waiting they did not need to do.

        A read failure falls back to asking, since a machine that will not
        answer is not one to assume anything about.
        """
        if self._state is MachineState.HOMED:
            return False
        return not self._already_homed()

    def run(self, mode: RunMode) -> bool:
        """Do whatever is needed and then run.

        The operator asked for the pistons to move; writing parameters and
        homing are how that happens, not separate things to remember. If the
        machine is already homed this is just a start.
        """
        if not self.sets:
            self.bridge.problem(
                "No pistons selected",
                "Choose some pistons on the tank first.",
            )
            return False

        problems: List[str] = []
        for motor_set in self.sets:
            problems.extend(
                "{0}: {1}".format(motor_set.name, problem)
                for problem in motor_set.validate()
            )
        if problems:
            self.bridge.problem("Check the parameters", chr(10).join(problems))
            return False

        return self._command("Run", lambda: self._run_worker(mode))

    def _run_worker(self, mode: RunMode) -> None:
        self.clear_lag_warnings()
        if self._state is not MachineState.HOMED:
            self._prepare_worker()
            if self._state is not MachineState.HOMED:
                return  # homing failed or was cancelled; already reported
            self.bridge.status("Homing complete. Starting {0}...".format(mode.value))
        self._start_worker(mode)

    def start(self, mode: RunMode) -> bool:
        """Start the machine, assuming it is already prepared."""
        if self._state is not MachineState.HOMED:
            self.bridge.problem(
                "Not ready",
                "The pistons must be homed before they can run. Press Start "
                "and it will do that first.",
            )
            return False
        return self._command("Start", lambda: self._start_worker(mode))

    def change_speed_live(self, speed: int, axes: Optional[Iterable[int]] = None) -> bool:
        """Change both travel speeds while a run is already in progress.

        The controller reads ``Spd_1``/``Spd_2`` on each motion leg, so no run
        bit needs to be dropped and no new operation is started.  A partial
        update is reported and the affected motors are left unsynchronized
        for the next prepare.

        ``_motion_lock`` is taken only for the initial stopping check, not for
        the writes themselves. It also serializes `_begin_motion`'s and
        `all_stop`'s run-bit transitions, and holding it across the whole
        per-piston loop below -- up to sixty round trips on the real thirty-
        piston array -- used to serialize Stop behind the entire sweep, since
        `emergency_stop` takes the very same lock to drop a run bit. The
        per-piston writes touch Spd_1/Spd_2, never a run bit, so they do not
        need that lock; `_stop_requested` (a plain ``Event``, safe to read
        without one) is checked between pistons instead, so a Stop landing
        mid-sweep is noticed within one write pair rather than only after it.
        """
        if self._state is not MachineState.RUNNING or self._run_mode is None:
            raise ValueError("Live speed changes are available only while running.")
        if self._stopping.is_set() or self._parking.is_set():
            # A graceful Stop sets _stopping up to GRACEFUL_STOP_SECONDS
            # before it sets _stop_requested (it is waiting for the end of a
            # stroke first), and the state is still RUNNING the whole time --
            # so checking only _state and _stop_requested let a new speed be
            # accepted while the machine was already supposed to be coming to
            # rest.
            raise ValueError("The machine is stopping; speed was not changed.")
        value = int(speed)
        spec = params.BY_NAME["Speed 1"]
        problem = spec.validate(value)
        if problem or value <= 0:
            raise ValueError(problem or "Live speed must be greater than zero.")
        wanted_axes = sorted(set(self.live_axes if axes is None else axes))
        if not wanted_axes:
            raise ValueError("No pistons are selected for the live speed change.")
        motors = {motor.axis: motor for motor in self.all_motors}
        missing = [axis for axis in wanted_axes if axis not in motors]
        if missing:
            raise ValueError("Piston selection changed while running.")
        with self._motion_lock:
            if self._stop_requested.is_set():
                raise ValueError("The machine is stopping; speed was not changed.")
        changed = []
        try:
            for axis in wanted_axes:
                if self._stop_requested.is_set():
                    raise ValueError("the machine is stopping")
                motor = motors[axis]
                # Record the requested value before the first write. If
                # the second direction fails, the next prepare must know
                # that this motor is only partially updated.
                motor.write_params["Speed 1"] = value
                motor.write_params["Speed 2"] = value
                motor.write_success = False
                self.plc.write(params.BY_NAME["Speed 1"].tag(axis), value)
                motor.current_params["Speed 1"] = value
                self.plc.write(params.BY_NAME["Speed 2"].tag(axis), value)
                motor.current_params["Speed 2"] = value
                motor.write_success = True
                changed.append(axis)
        except (PlcError, ValueError) as exc:
            for axis in changed:
                motors[axis].write_success = True
            self.bridge.problem(
                "Live speed change failed",
                "The speed was updated on pistons {0}, then stopped at piston {1}: {2}."
                .format(tags.display_list(changed), tags.display_number(axis), exc),
            )
            raise
        self.bridge.status("Live speed set to {0} mm/s on pistons {1}.".format(
            value, tags.display_list(wanted_axes)))
        LOGGER.log(15, "Live speed changed to %d mm/s on pistons %s.",
                   value, tags.display_list(wanted_axes))
        return True

    def change_stroke_live(self, stroke: int, axes: Optional[Iterable[int]] = None) -> bool:
        """Queue a stroke change and apply it at a shared stroke endpoint.

        Position 2 is part of the controller's active trajectory. Writing it
        to one axis halfway through a leg while the other axes are still in
        flight creates the phase jump that used to make the array drift apart.
        The request is therefore retained until every selected axis reaches
        its own lower or upper endpoint. Phase offsets are preserved.
        """
        if self._state is not MachineState.RUNNING or self._run_mode is None:
            raise ValueError("Live stroke changes are available only while running.")
        if self._stopping.is_set() or self._parking.is_set():
            # See change_speed_live: _stopping can be set up to
            # GRACEFUL_STOP_SECONDS before _stop_requested, with the state
            # still RUNNING throughout, so that pair alone is not enough to
            # reject a stroke change requested while a graceful Stop waits.
            raise ValueError("The machine is stopping; stroke was not changed.")
        value = int(stroke)
        spec = params.BY_NAME["Position 2"]
        if value <= 0:
            raise ValueError("Live stroke must be greater than zero.")
        wanted_axes = sorted(set(self.live_axes if axes is None else axes))
        motors = {motor.axis: motor for motor in self.all_motors}
        if not wanted_axes or any(axis not in motors for axis in wanted_axes):
            raise ValueError("Piston selection changed while running.")
        # Validate every target before changing the pending request.
        for axis in wanted_axes:
            low = int(motors[axis].write_params["Position 1"])
            problem = spec.validate(low + value)
            if problem:
                raise ValueError("Piston {0}: {1}".format(tags.display_number(axis), problem))
        with self._motion_lock:
            if self._stop_requested.is_set():
                raise ValueError("The machine is stopping; stroke was not changed.")
            self._pending_live_stroke = value
            self._pending_live_stroke_axes = wanted_axes
            self._pending_live_stroke_applied = set()
            applied = self._apply_pending_live_stroke_locked()
        if applied:
            self.bridge.status("Live stroke set to {0} mm on pistons {1}.".format(value, tags.display_list(wanted_axes)))
        else:
            self.bridge.status("Live stroke queued for the next synchronized endpoint.")
        return True

    def _apply_pending_live_stroke_locked(self) -> bool:
        """Apply queued targets at each axis's own safe stroke endpoint.

        Called with `_motion_lock` already held (see `_apply_pending_live_stroke`
        and `change_stroke_live`), which has the same shape as the problem
        `change_speed_live` had: up to thirty per-piston reads and writes,
        with `emergency_stop` blocked behind the same lock the whole time.
        This runs on every monitor tick, so `_stop_requested` -- a plain
        Event, safe to read without the lock -- is checked between pistons in
        both loops below, and the scan gives up the moment it is set rather
        than reading or writing every remaining axis regardless.
        """
        value = self._pending_live_stroke
        axes = list(self._pending_live_stroke_axes)
        if value is None or not axes or self._state is not MachineState.RUNNING:
            return False
        motors = {motor.axis: motor for motor in self.all_motors}
        ready = []
        dropped = []
        for axis in axes:
            if axis in self._pending_live_stroke_applied:
                continue
            if self._stop_requested.is_set():
                return False
            motor = motors.get(axis)
            if motor is None:
                # Editing the tank selection mid-run is explicitly allowed
                # and never interrupts a run, but it can remove an axis the
                # queued stroke change is still waiting for. Aborting the
                # whole scan here used to wedge the queue on every later
                # monitor tick, silently, for every OTHER axis too, until the
                # next Stop -- so drop this one axis from the pending request
                # instead, say so, and let the rest still apply.
                dropped.append(axis)
                continue
            try:
                actual = motor.read_position(self.plc)
            except (PlcError, TypeError, ValueError):
                return False
            low = float(motor.write_params["Position 1"])
            high = float(motor.write_params["Position 2"])
            tolerance = max(2.0, abs(high - low) * 0.03)
            near_low = abs(actual - low) <= tolerance
            near_high = abs(actual - high) <= tolerance
            if not (near_low or near_high):
                continue
            ready.append(axis)
        if dropped:
            axes = [a for a in axes if a not in dropped]
            self._pending_live_stroke_axes = axes
            self.bridge.status(
                "Piston(s) {0} left the selection before their live stroke "
                "change applied; dropped from the pending request.".format(
                    tags.display_list(dropped)
                )
            )
            if not axes:
                self._pending_live_stroke = None
                self._pending_live_stroke_applied = set()
                return False
        if not ready:
            return False
        try:
            for axis in ready:
                if self._stop_requested.is_set():
                    break
                motor = motors[axis]
                target = int(motor.write_params["Position 1"]) + int(value)
                motor.write_success = False
                self.plc.write(params.BY_NAME["Position 2"].tag(axis), target)
                motor.write_params["Position 2"] = target
                motor.current_params["Position 2"] = target
                motor.write_success = True
                self._pending_live_stroke_applied.add(axis)
        except (PlcError, ValueError) as exc:
            self.bridge.problem("Live stroke change failed", str(exc))
            return False
        complete = self._pending_live_stroke_applied.issuperset(axes)
        if complete:
            self._pending_live_stroke = None
            self._pending_live_stroke_axes = []
            self._pending_live_stroke_applied = set()
        return complete

    def _apply_pending_live_stroke(self) -> bool:
        with self._motion_lock:
            return self._apply_pending_live_stroke_locked()

    def _start_worker(self, mode: RunMode) -> None:
        if self._stop_requested.is_set():
            return
        self._run_mode = mode

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
            travel = self._single_stroke()
            self._set_state(MachineState.HOMED)
            if travel is None:
                # _begin_motion refused: a Stop landed between the HOMED
                # check above and here. Nothing was commanded, so this is
                # not the "Nothing moved" controller fault _report_travel
                # would otherwise raise -- it is simply a cancelled start.
                self.bridge.status("Stroke cancelled.")
                return
            self._report_travel("stroke", travel)

        elif mode is RunMode.CURVE:
            if self.plc.read(tags.RUN_CURVE):
                LOGGER.warning("A curve is already running; ignoring.")
                return
            self._set_state(MachineState.RUNNING)
            self.bridge.status("Running curve...")
            if self.record_analytics:
                self._begin_motion(tags.RUN_CURVE)
                try:
                    self._record_positions(CURVE_SECONDS)
                finally:
                    self.plc.write(tags.RUN_CURVE, 0)
                travel = {}
            else:
                travel = self._hold_and_watch(tags.RUN_CURVE, self._stroke_seconds(CURVE_SECONDS))
            self._set_state(MachineState.HOMED)
            if travel is None:
                self.bridge.status("Curve cancelled.")
                return
            self._report_travel("curve", travel)

        else:  # continuous
            self._set_state(MachineState.PREPARING)
            self._staging.set()
            try:
                staged = self._stage_cascade()
            finally:
                # Leave the flag set if a stop landed during staging: whoever
                # is running _halt_and_rest for that stop (possibly on
                # another thread, right now) is the one reading it to decide
                # between HOMED and an interrupted-homing READY, and clearing
                # it here first -- all_stop()'s PLC writes are enough of a
                # scheduling gap for that to happen -- would race it back to
                # the READY branch no matter which one runs second.
                # _halt_and_rest clears it itself once it has read it.
                if not self._stop_requested.is_set():
                    self._staging.clear()
            if self._stop_requested.is_set():
                # Stop has already run _halt_and_rest while staging was still
                # in flight -- it is what decides the resting state, and when
                # the interruption was staging (not homing) it leaves the
                # machine at HOMED rather than READY so the resting move
                # below can run. Setting state here too would race that and
                # could stomp HOMED back to READY right behind it.
                self._run_mode = None
                return
            if not staged:
                self._run_mode = None
                self._set_state(MachineState.READY)
                return
            self._set_state(MachineState.RUNNING)
            if not self._begin_motion(tags.RUN_CONTINUOUS):
                self._run_mode = None
                self._set_state(MachineState.READY)
                return
            LOGGER.log(15, "Continuous motion started.")
            self.bridge.status("Running continuously. Press Stop when finished.")
            if self.record_analytics:
                self._record_positions(self.analytics_duration)

    def cascade_targets(self) -> Dict[int, int]:
        """Where each piston should start, so the wave travels front to back.

        Curve Offset is how the operator says "make this travel", but the
        controller only reads it during a curve run. In continuous motion every
        piston sets off the instant Run_2 goes high, which is why a staggered
        design still came out as thirty pistons slapping in unison.

        The stagger is honoured here instead by starting each piston from a
        different point along its stroke. The periods are identical, so the
        phase difference that creates is fixed and stays fixed -- a wave that
        marches down the chamber for as long as the run lasts.

        Keyed on the piston, not on its column. Collapsing each column to one
        offset threw away any stagger *within* a column, so "Rows out of step"
        -- three rows of a column started at the bottom, the middle and the top
        -- reached the drives correctly and was then staged as though every row
        were identical. Reading each piston's own Curve Offset covers a
        front-to-back wave, a row cascade, and any combination of the two,
        because a front-to-back stagger simply gives every piston in a column
        the same value.

        Empty when no stagger is set, which is the common case and costs
        nothing.
        """
        offsets: Dict[int, int] = {}
        for motor in self.all_motors:
            try:
                offsets[motor.axis] = int(
                    motor.write_params.get("Curve Offset", 0)
                )
            except (TypeError, ValueError):
                continue

        fractions = waves.cascade_fractions(offsets)
        if not fractions:
            return {}

        targets: Dict[int, int] = {}
        for motor in self.all_motors:
            fraction = fractions.get(motor.axis)
            if fraction is None:
                continue
            try:
                first = float(motor.write_params["Position 1"])
                second = float(motor.write_params["Position 2"])
            except (KeyError, TypeError, ValueError):
                continue
            targets[motor.axis] = int(round(first + fraction * (second - first)))
        return targets

    def _motor_for(self, axis: int):
        """The Motor object for an axis, or None if it is not selected."""
        for motor in self.all_motors:
            if motor.axis == axis:
                return motor
        return None

    def _stage_cascade(self) -> bool:
        """Put the pistons at their starting points before continuous motion.

        Returns True when staging is unnecessary or completed safely.
        """
        targets = self.cascade_targets()
        if not targets:
            return not self._stop_requested.is_set()

        self.bridge.status("Staggering the pistons for a travelling wave...")
        arrived = False
        errors = []

        # Staging a piston at a position is only half of what decides its
        # phase. Measured at the machine on 15 September 2026: pistons 4, 5 and
        # 6 were all parked at exactly 45 mm of an identical 0-180 stroke, and
        # when Run_2 went high 4 and 5 set off downwards while 6 set off
        # upwards -- dead anti-phase, and repeatable at 135 mm too. Each drive
        # remembers which leg of its cycle it is on and carries on from there,
        # and that memory survives between runs, so two pistons in the same
        # place can be half a cycle apart. That is what made a cascade come out
        # as a broken wave: the columns staged at the very bottom and the very
        # top held together, because a piston at the end of its travel cannot
        # go the wrong way, and only the ones staged mid-stroke scattered.
        #
        # So the stagger is laid down in three moves: everyone to the bottom,
        # where direction is forced; a brief continuous pulse, which sets every
        # drive off upwards from that same end and so onto the same leg; then
        # out to the individual starting points. Verified at the machine: after
        # the pulse, three pistons staged at 45 mm all set off upwards.
        floors = {}
        for axis in targets:
            motor = self._motor_for(axis)
            try:
                floors[axis] = int(min(float(motor.write_params["Position 1"]),
                                       float(motor.write_params["Position 2"])))
            except (AttributeError, KeyError, TypeError, ValueError):
                floors[axis] = targets[axis]

        def settle(wanted, what):
            """Send the pistons to ``wanted`` and wait. True once they arrive."""
            for axis, target in wanted.items():
                self.plc.write(params.BY_NAME["Move Type"].tag(axis), 0)
                self.plc.write(params.BY_NAME["Position 1"].tag(axis), target)
                self.plc.write(params.BY_NAME["Position 2"].tag(axis), target)
                self.plc.write(params.BY_NAME["Speed 1"].tag(axis), STAGE_SPEED)
                self.plc.write(params.BY_NAME["Speed 2"].tag(axis), STAGE_SPEED)
            if self._stop_requested.is_set():
                return None
            if not self._begin_motion(tags.RUN_SINGLE):
                return None
            self.bridge.status("Staggering: moving the pistons {0}...".format(what))
            got_there = False
            deadline = time.time() + STAGE_SECONDS
            try:
                while time.time() < deadline:
                    if self._stop_requested.is_set():
                        return None
                    if self._all_within(wanted, STAGE_TOLERANCE):
                        got_there = True
                        break
                    self._sleep(STOP_POLL_INTERVAL)
            finally:
                # Dropped between moves so the next one is a fresh command
                # rather than a target changed underneath a travelling piston.
                self.plc.write(tags.RUN_SINGLE, 0)
            return got_there

        try:
            if settle(floors, "to the bottom of their stroke") is None:
                return False

            # The parity pulse. Long enough for every drive to commit to the
            # upward leg, short enough that nobody reaches the top and turns
            # round again -- which would put them back out of step.
            if self._stop_requested.is_set():
                return False
            for motor in self.all_motors:
                motor.write_to(self.plc, force=True)
            if not self._begin_motion(tags.RUN_CONTINUOUS):
                return False
            try:
                self._sleep(PARITY_PULSE_SECONDS)
            finally:
                self.plc.write(tags.RUN_CONTINUOUS, 0)
            self._sleep(PARITY_SETTLE_SECONDS)

            outcome = settle(targets, "to their starting points")
            if outcome is None:
                return False
            arrived = bool(outcome)

        except PlcError as exc:
            errors.append(str(exc))
        finally:
            cleared = False
            try:
                self.plc.write(tags.RUN_SINGLE, 0)
                cleared = True
            except PlcError as exc:
                errors.append("Could not clear staging run bit: {0}".format(exc))
            for motor in self.all_motors:
                motor.current_params = {}
                motor.write_success = False
                if not cleared:
                    continue  # changing targets while a run bit may be high is unsafe
                try:
                    motor.write_to(self.plc)
                except (PlcError, ValueError) as exc:
                    errors.append("Piston {0}: {1}".format(
                        tags.display_number(motor.axis), exc))
            if errors:
                self.bridge.problem("Staging failed", "\n".join(errors))
        if errors or self._stop_requested.is_set():
            return False
        if not arrived:
            # A timeout here is "could not stage", not "was stopped".
            #
            # This used to run anyway, unstaggered, on the reasoning that an
            # unstaggered wave beats no wave. In practice that is the single
            # most confusing thing the machine does: the operator picks "Rows
            # out of step", waits twelve seconds, and gets a wave that is not
            # out of step, with the only evidence a status line that has
            # usually been replaced by the time they look. It also runs on
            # drives that have just spent twelve seconds demonstrating they
            # will not execute a move -- which is how a staging failure turns
            # into "the pistons are barely moving".
            #
            # So say which pistons did not get there, and refuse. The operator
            # can free them, deselect them, or choose "All together" on the
            # Wave tab and mean it.
            late = self._outside(targets, STAGE_TOLERANCE)
            unreadable = [axis for axis, actual, _gap in late if actual is None]
            short = [(axis, gap) for axis, _actual, gap in late if gap is not None]

            detail = []
            for axis, gap in short:
                detail.append("piston {0} is {1:.0f} mm from its start".format(
                    tags.display_number(axis), abs(gap)))
            if unreadable:
                detail.append("could not read piston(s) {0}".format(
                    tags.display_list(unreadable)))
            if not detail:
                detail.append("the pistons did not report arriving in time")

            message = (
                "The pistons could not be moved to their staggered starting "
                "points within {0:.0f} seconds, so the wave was not started:\n\n"
                "{1}\n\nFree or deselect those pistons and press Start again. "
                "To run without the stagger, choose \"All together\" on the "
                "Wave tab.".format(STAGE_SECONDS, "\n".join(detail))
            )
            self.bridge.status(
                "Staging failed: {0}. Nothing was started.".format("; ".join(detail))
            )
            self.bridge.problem("Could not stagger the pistons", message)
            LOGGER.warning(
                "Staging did not complete within %s seconds: %s. Run refused.",
                STAGE_SECONDS, "; ".join(detail),
            )
            return False

        spread = max(targets.values()) - min(targets.values())
        LOGGER.log(
            15, "Pistons staggered across %d mm for a travelling wave.", spread
        )
        return True

    def _stroke_seconds(self, floor: float = SINGLE_STROKE_SECONDS) -> float:
        """How long a full up and down actually takes at the current settings.

        One stroke is Position 1 to Position 2 and back, so the time it needs
        is the travel each way at the speed set for that direction, plus the
        dwell at each end. Holding the bit for a fixed five seconds regardless
        meant a slow or long stroke was cut off part-way: the pistons stopped
        wherever they had got to, and the run was reported as barely moving.

        The fixed value stays as a floor, so nothing that worked before gets a
        shorter window.
        """
        longest = 0.0
        for motor in self.all_motors:
            wanted = motor.write_params
            try:
                stroke = abs(wanted["Position 2"] - wanted["Position 1"])
                out_speed = max(float(wanted["Speed 1"]), 1.0)
                back_speed = max(float(wanted["Speed 2"]), 1.0)
                # Time 1 and Time 2 are dwells in milliseconds.
                dwell = (float(wanted.get("Time 1", 0)) + float(wanted.get("Time 2", 0))) / 1000.0
            except (KeyError, TypeError, ValueError):
                continue
            longest = max(longest, stroke / out_speed + stroke / back_speed + dwell)

        if not longest:
            return floor
        # Half again, so a piston that is merely slow still finishes.
        return max(floor, min(longest * 1.5, MAX_STROKE_SECONDS))

    def _single_stroke(self) -> Optional[Dict[int, float]]:
        """One stroke: out to Position 2, then back to Position 1.

        Run_1 is not a stroke. At the machine it is an absolute move to
        Position 1, and it never reads Position 2 at all. Measured on the array
        on 15 September 2026: with Position 1 = 200 and Position 2 = 300 a
        piston sitting at 0 went to 199.8; with Position 1 = 60 it then went to
        60.1; and a piston already at Position 1 did not move for three
        successive pulses of the bit. The application had been raising Run_1
        for as long as a full out-and-back would take and calling whatever
        happened a stroke, so One stroke only ever went one way -- and only to
        Position 1, which might be the way it was already facing.

        A stroke is therefore commanded as two moves, exactly the way
        :meth:`_park_moves` commands one: put the destination in Position 1,
        raise Run_1, wait for the pistons to arrive. The operator's own
        Position 1 is written back at the end, whatever happens.

        Returns travel per axis, or None if a stop landed before anything was
        commanded, so the caller can tell a cancelled stroke from a dead one.
        """
        wanted = {}
        for motor in self.all_motors:
            try:
                wanted[motor.axis] = (
                    int(motor.write_params["Position 1"]),
                    int(motor.write_params["Position 2"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        if not wanted:
            return None

        lowest: Dict[int, float] = {}
        highest: Dict[int, float] = {}

        def sample() -> None:
            for motor in self.all_motors:
                try:
                    where = motor.read_position(self.plc)
                except (PlcError, TypeError, ValueError):
                    continue
                axis = motor.axis
                lowest[axis] = min(lowest.get(axis, where), where)
                highest[axis] = max(highest.get(axis, where), where)

        sample()
        began = False
        try:
            for leg, out in enumerate((True, False)):
                targets = {axis: (high if out else low)
                           for axis, (low, high) in wanted.items()}
                for axis, target in targets.items():
                    # Absolute, or the target is taken as a relative lurch.
                    self.plc.write(params.BY_NAME["Move Type"].tag(axis), 0)
                    self.plc.write(params.BY_NAME["Position 1"].tag(axis), target)
                if self._stop_requested.is_set():
                    break
                if not self._begin_motion(tags.RUN_SINGLE):
                    break
                began = True
                self.bridge.status(
                    "Running one stroke: {0}...".format("out" if out else "back")
                )
                deadline = time.time() + self._leg_seconds(out)
                while (time.time() < deadline
                       and not self._stop_requested.is_set()):
                    sample()
                    if self._all_within(targets, PARK_TOLERANCE):
                        break
                    time.sleep(STOP_POLL_INTERVAL)
                self.plc.write(tags.RUN_SINGLE, 0)
                sample()
                if self._stop_requested.is_set():
                    break
                dwell = 0.0
                for motor in self.all_motors:
                    try:
                        dwell = max(dwell, float(motor.write_params.get(
                            "Time 2" if out else "Time 1", 0)) / 1000.0)
                    except (TypeError, ValueError):
                        continue
                if dwell:
                    self._sleep(dwell)
        finally:
            try:
                self.plc.write(tags.RUN_SINGLE, 0)
            finally:
                # Position 1 was borrowed as a destination; give it back, or
                # the next run silently uses the far end as its near end.
                for axis, (low, _high) in wanted.items():
                    try:
                        self.plc.write(
                            params.BY_NAME["Position 1"].tag(axis), low)
                    except PlcError:
                        LOGGER.exception(
                            "Could not restore Position 1 on piston %d",
                            tags.display_number(axis))
                self._forget_written_params()

        if not began:
            return None
        return dict(
            (axis, highest[axis] - lowest.get(axis, highest[axis]))
            for axis in highest
        )

    def _leg_seconds(self, outbound: bool) -> float:
        """How long one leg of a stroke should need, with headroom."""
        longest = 0.0
        for motor in self.all_motors:
            wanted = motor.write_params
            try:
                stroke = abs(float(wanted["Position 2"])
                             - float(wanted["Position 1"]))
                speed = max(float(
                    wanted["Speed 1" if outbound else "Speed 2"]), 1.0)
            except (KeyError, TypeError, ValueError):
                continue
            longest = max(longest, stroke / speed)
        # Generous: arrival is detected by position, so this is only a cap.
        return min(max(longest * 2.0 + 2.0, 3.0), MAX_STROKE_SECONDS)

    def _hold_and_watch(self, tag: str, seconds: float) -> Optional[Dict[int, float]]:
        """Hold a command bit and record how far each piston actually travels.

        Both the single stroke and the curve used to set a bit, wait a fixed
        five seconds, clear it and report success -- whether or not anything had
        moved. That is why they felt like they did nothing: there was no way to
        tell a working stroke from a bit that the ladder ignored.

        Returns None, rather than an empty travel dict, if `_begin_motion`
        refuses to raise the bit at all -- which happens when a Stop lands
        between `_start_worker`'s own HOMED/RUNNING check and here. The watch
        loop must not run in that case: it would measure zero travel from
        pistons nothing was ever commanded to move, and the caller would
        report that as the "Nothing moved ... the controller is not in Run"
        controller fault, for a stop the operator asked for themselves.
        """
        lowest: Dict[int, float] = {}
        highest: Dict[int, float] = {}

        def sample() -> None:
            for motor in self.all_motors:
                try:
                    where = motor.read_position(self.plc)
                except (PlcError, TypeError, ValueError):
                    continue
                axis = motor.axis
                lowest[axis] = min(lowest.get(axis, where), where)
                highest[axis] = max(highest.get(axis, where), where)

        sample()
        began = self._begin_motion(tag)
        try:
            if began:
                deadline = time.time() + seconds
                while time.time() < deadline and not self._stop_requested.is_set():
                    self._apply_pending_live_stroke()
                    sample()
                    time.sleep(STOP_POLL_INTERVAL)
        finally:
            # Cleared unconditionally, whether or not it was ever raised:
            # cheap, and leaves nothing to chance about the bit's state.
            self.plc.write(tag, 0)
        if not began:
            return None
        sample()

        return dict(
            (axis, highest[axis] - lowest.get(axis, highest[axis]))
            for axis in highest
        )

    def _report_travel(self, what: str, travel: Dict[int, float]) -> None:
        """Say how far the pistons actually went, rather than merely 'complete'."""
        if not travel:
            LOGGER.log(15, "Ran a %s.", what)
            self.bridge.status("Ran a {0}. Ready to run again.".format(what))
            return

        furthest = max(travel.values())
        still = sorted(a for a, d in travel.items() if d < MIN_JUDGED_STROKE)

        if furthest < MIN_JUDGED_STROKE:
            message = (
                "The {0} command was sent, but no piston moved more than "
                "{1:.0f} mm.".format(what, furthest)
            )
            LOGGER.warning("%s", message)
            self.bridge.status(message)
            self.bridge.problem(
                "Nothing moved",
                "{0}{1}{1}The command reached the controller, so this is not a "
                "connection problem. Likely causes:{1}{1}"
                "  - the keyswitch on the front of the controller is at "
                "PROG, so the ladder is not scanning. Tags can still be "
                "read and written in that mode, which is why this looked "
                "like it connected. Turn it to RUN.{1}"
                "  - for a curve: no curve is loaded on the controller "
                "for the Curve ID you set, so there is nothing to run{1}"
                "  - the stroke is set so small there is nothing to see"
                .format(message, chr(10)),
            )
            return

        message = "Ran a {0}. Furthest piston moved {1:.0f} mm.".format(
            what, furthest
        )
        if still:
            message += " Piston(s) {0} barely moved.".format(tags.display_list(still))
            self.lagging_axes = still
        LOGGER.log(15, "%s", message)
        self.bridge.status(message + " Ready to run again.")

    def stop(self, immediate: bool = False, park: Optional[bool] = None) -> bool:
        """Stop the machine and bring the pistons to rest.

        By default this lets each piston finish the stroke it is on before the
        run bits drop, so the operator no longer has to time the button press,
        and then moves them to the chosen resting position.

        ``immediate`` skips the waiting and halts at once. Escape does that, and
        so does pressing Stop a second time while it is waiting -- so a hard stop
        is always one keypress or one more click away.

        Never goes through :meth:`_command`: stopping must work while another
        command holds the worker, which is exactly when it is needed.
        """
        already_stopping = self._stopping.is_set() or self._parking.is_set()
        # Escape and the Stop button can land on this at the same instant
        # from different threads; a bare read-increment-write here could lose
        # one of them, or hand out the same serial to both, and this serial
        # is exactly what tells a stale graceful-stop worker (see
        # _graceful_stop_worker) that it has been superseded.
        with self._motion_lock:
            self._stop_serial += 1
            serial = self._stop_serial
        if already_stopping:
            immediate = True  # second press means "now"
            park = False

        self._cancel_park.set()

        graceful = (
            not immediate
            and self._state is MachineState.RUNNING
            and bool(self.all_motors)
        )
        if not graceful:
            self._stop_requested.set()
            return self._halt_and_rest(park, waited=False)

        self._stopping.set()
        self._spawn("Stop", lambda: self._graceful_stop_worker(park, serial))
        return True

    def _graceful_stop_worker(self, park: Optional[bool], serial=None) -> None:
        self._stopping.set()
        try:
            if serial is not None and serial != self._stop_serial:
                return
            self.bridge.status("Finishing the stroke...")
            waited = self._wait_for_stroke_end()
        except PlcError as exc:
            LOGGER.warning("Could not follow the pistons while stopping: %s", exc)
            waited = False
        finally:
            self._stopping.clear()
        if serial is not None and serial != self._stop_serial:
            return
        self._stop_requested.set()
        self._halt_and_rest(park, waited=waited)

    def _wait_for_stroke_end(self) -> bool:
        """Wait until every readable piston is near an end of its stroke.

        Returns True if they got there, False on timeout. A timeout is not a
        failure: with a staggered wave the pistons are deliberately out of step
        and may never all be at an end together, and the resting move that
        follows puts them somewhere known anyway.

        An axis whose position cannot be read is skipped, not counted as "not
        at rest": this is the exact fault this application exists to cope
        with, and one flaky axis used to force every graceful Stop to sit out
        the whole timeout even when every other piston was already parked on
        an endpoint. It is recorded so the display can flag it.
        """
        deadline = time.time() + GRACEFUL_STOP_SECONDS
        while time.time() < deadline:
            if self._cancel_park.is_set() and not self._stopping.is_set():
                return False
            if self._stop_requested.is_set():
                return False
            at_rest = True
            unreadable = []
            for motor in self.all_motors:
                try:
                    actual = motor.read_position(self.plc)
                except (PlcError, TypeError, ValueError):
                    unreadable.append(motor.axis)
                    continue
                if not math.isfinite(actual):
                    unreadable.append(motor.axis)
                    continue
                ends = (
                    motor.write_params["Position 1"],
                    motor.write_params["Position 2"],
                )
                if min(abs(actual - end) for end in ends) > STROKE_END_TOLERANCE:
                    at_rest = False
                    break
            if unreadable:
                self.unreadable_axes = unreadable
            if at_rest:
                return True
            time.sleep(STOP_POLL_INTERVAL)
        return False

    def _halt_and_rest(self, park: Optional[bool], waited: bool) -> bool:
        """Drop the run bits, then move the pistons to their resting position."""
        try:
            self.all_stop(include_home=self._state is MachineState.PREPARING)
        except PlcError as exc:
            LOGGER.critical("STOP FAILED: %s", exc)
            self.bridge.problem(
                "Stop failed",
                "The machine did not acknowledge the stop command:{0}{1}{0}{0}"
                "Use the physical stop and check the connection.".format(chr(10), exc),
            )
            return False

        LOGGER.log(15, "Motors stopped%s.", " at the end of a stroke" if waited else "")
        # Nothing is being watched once the run ends, so stale warnings must not
        # be left on screen looking like a live fault.
        self.clear_lag_warnings()
        # A monitor tick can be inside _apply_pending_live_stroke_locked
        # right now, holding _motion_lock while it reads and writes these
        # same fields -- clearing them here without the same lock would race
        # it: the monitor's own update could land in between and leave the
        # fields part-cleared, part-stale.
        with self._motion_lock:
            self._pending_live_stroke = None
            self._pending_live_stroke_axes = []
            self._pending_live_stroke_applied = set()
        self._run_mode = None
        if self._state is MachineState.PREPARING:
            if self._staging.is_set():
                # Staging the cascade before a continuous run also runs with
                # the state at PREPARING, but it never touches homing. Reading
                # this the same as an interrupted homing pass would throw
                # away a full Calibrate All (_homed_axes.clear()) and drop to
                # READY, which then fails the "state is HOMED" park condition
                # below -- so a Stop here left the pistons on their staging
                # points, unrested, and un-homed the whole array for nothing.
                self._staging.clear()
                self._set_state(MachineState.HOMED if self.sets else MachineState.IDLE)
            else:
                self._homed_axes.clear()
                self._set_state(MachineState.READY if self.sets else MachineState.IDLE)
        elif self._state is MachineState.RUNNING:
            self._set_state(MachineState.HOMED if self.sets else MachineState.IDLE)

        should_park = PARK_ON_STOP if park is None else park
        if (
            should_park
            and self.rest_position != REST_HOLD
            and self._state is MachineState.HOMED
            and self.all_motors
            # A single stroke, a curve and an analytics-recording continuous run
            # all hold _busy for the whole run, so a Stop landing mid-run always
            # sees busy is True. _park_worker's own non-blocking _busy.acquire()
            # is the real arbiter of "is something else already running" -- this
            # condition only needs to stop a second resting move overlapping.
            and not self._parking.is_set()   # one resting move at a time
        ):
            self._cancel_park.clear()
            self._parking.set()
            self._spawn("Rest", self._park_worker)
        else:
            self.bridge.status("Motors stopped.")
        return True

    def emergency_stop(self) -> bool:
        """Halt at once, without waiting for the end of a stroke."""
        return self.stop(immediate=True)

    def _rest_target(self, motor: Motor) -> int:
        """Where this piston should come to rest."""
        if self.rest_position == REST_UP:
            # The top of its own stroke: somewhere it was already travelling to,
            # rather than the top of the machine's travel.
            return min(motor.write_params["Position 1"], motor.write_params["Position 2"])
        return PARK_POSITION

    def _park_worker(self) -> None:
        """Move the pistons to their resting position and confirm they arrive."""
        if not self._busy.acquire(timeout=PARK_ACQUIRE_TIMEOUT_SECONDS):
            self._parking.clear()
            return
        self._parking.set()
        where = "top" if self.rest_position == REST_UP else "bottom"
        try:
            self.bridge.status("Stopped. Returning pistons to the {0}...".format(where))
            arrived = self._park_moves()
            if self._cancel_park.is_set():
                self.bridge.status("Stopped. Pistons left where they are.")
            elif arrived:
                self.bridge.status("Stopped. Pistons are at the {0}.".format(where))
                LOGGER.log(15, "Pistons at rest (%s).", where)
            else:
                self.bridge.status(
                    "Stopped, but not every piston reached the {0}.".format(where)
                )
                LOGGER.warning("Pistons did not all reach the resting position.")
        except PlcError as exc:
            LOGGER.error("Could not rest the pistons: %s", exc)
            self.bridge.status("Stopped, but the pistons could not be moved to rest.")
        finally:
            self._parking.clear()
            self._busy.release()

    def _park_moves(self) -> bool:
        """Command the resting move and watch until the pistons get there.

        Returns True once every piston is within tolerance. The original version
        held Run_1 high for a fixed six seconds and simply hoped; the positions
        are published continuously, so there is no need to guess.
        """
        try:
            return self._park_moves_inner()
        finally:
            try:
                self.plc.write(tags.RUN_SINGLE, 0)
            finally:
                self._forget_written_params()

    def _park_moves_inner(self) -> bool:
        targets = {}
        for motor in self.all_motors:
            if self._cancel_park.is_set():
                return False
            axis = motor.axis
            target = self._rest_target(motor)
            targets[axis] = target
            # Absolute, or the target would be taken as a relative lurch.
            self.plc.write(params.BY_NAME["Move Type"].tag(axis), 0)
            self.plc.write(params.BY_NAME["Position 1"].tag(axis), target)
            self.plc.write(params.BY_NAME["Position 2"].tag(axis), target)
            self.plc.write(params.BY_NAME["Speed 1"].tag(axis), PARK_SPEED)
            self.plc.write(params.BY_NAME["Speed 2"].tag(axis), PARK_SPEED)

        if self._cancel_park.is_set():
            return False

        with self._motion_lock:
            if self._cancel_park.is_set():
                return False
            self.plc.write(tags.RUN_SINGLE, 1)
        arrived = False
        deadline = time.time() + PARK_SECONDS
        while time.time() < deadline and not self._cancel_park.is_set():
            if self._all_within(targets, PARK_TOLERANCE):
                arrived = True
                break
            time.sleep(STOP_POLL_INTERVAL)
        return arrived

    def _all_within(self, targets: Dict[int, int], tolerance: float) -> bool:
        for axis, target in targets.items():
            try:
                actual = params.to_mm(
                    self.plc.read(tags.axis_field(axis, tags.ACTUAL_POSITION))
                )
            except (PlcError, TypeError, ValueError):
                return False
            if not math.isfinite(actual) or abs(actual - target) > tolerance:
                return False
        return True

    def _outside(self, targets: Dict[int, int], tolerance: float):
        """Which pistons are not within ``tolerance`` of their target, and by
        how far.

        :meth:`_all_within` answers yes or no, which is all a polling loop
        needs but useless in a failure message: "staging did not complete" with
        no piston named leaves the operator nothing to act on. This reports the
        offenders so they can be freed, deselected, or looked at.

        Returns a list of ``(axis, actual_mm_or_None, gap_mm_or_None)``, an
        unreadable axis carrying ``None`` for both.
        """
        late = []
        for axis, target in sorted(targets.items()):
            try:
                actual = params.to_mm(
                    self.plc.read(tags.axis_field(axis, tags.ACTUAL_POSITION))
                )
            except (PlcError, TypeError, ValueError):
                late.append((axis, None, None))
                continue
            if not math.isfinite(actual):
                late.append((axis, None, None))
            elif abs(actual - target) > tolerance:
                late.append((axis, actual, actual - target))
        return late

    def _forget_written_params(self) -> None:
        """The PLC now holds resting values, not the operator's.

        Forget what we believed was written, so the real parameters go out again
        before the next run instead of being assumed still present.

        The machine stays HOMED on purpose. Moving to rest puts the pistons
        somewhere known; it does not cost the drives their reference. Marking it
        unprepared would force a full homing cycle -- the better part of a
        minute -- after every single stop.
        """
        for motor in self.all_motors:
            motor.current_params = {}
            motor.write_success = False

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
            ("Piston {0}".format(tags.display_number(m.axis)), {}) for m in motors
        )

        with open(target, "a+", encoding="utf-8") as handle:
            handle.write("\n----- Run {0} -----\n".format(time.asctime()))
            handle.write("{0:<12}".format("t"))
            for motor in motors:
                handle.write("{0:<24}".format(
                    "piston {0}".format(tags.display_number(motor.axis))))
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
                    samples["Piston {0}".format(tags.display_number(motor.axis))][
                        "{0}".format(elapsed)] = {
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
        self.bridge.progress_done(str(target))

    # -- live monitoring ------------------------------------------------------

    def start_monitoring(self) -> None:
        """Begin reading piston positions in the background.

        Runs for the life of the application and only reads while the machine
        is actually running, so it costs nothing when idle. Reads go through the
        same lock as commands, so a poll can never interleave with a write.
        """
        if self._monitor_thread is not None:
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="Monitor", daemon=True
        )
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        self._monitor_stop.set()
        self._monitor_thread = None

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.is_set():
            if self._state is MachineState.RUNNING:
                try:
                    self._apply_pending_live_stroke()
                    self._poll_positions()
                except PlcError as exc:
                    # A failed poll is not worth interrupting a run for; the
                    # display simply stops updating and says so.
                    LOGGER.debug("Position poll failed: %s", exc)
            self._monitor_stop.wait(MONITOR_INTERVAL)

    def _poll_positions(self) -> None:
        readings: Dict[int, float] = {}
        unreadable: List[int] = []
        now = time.time()

        for motor in self.all_motors:
            axis = motor.axis
            try:
                actual = params.to_mm(
                    self.plc.read(tags.axis_field(axis, tags.ACTUAL_POSITION))
                )
            except (PlcError, TypeError, ValueError):
                unreadable.append(axis)
                continue

            readings[axis] = actual
            history = self._history.setdefault(axis, [])
            history.append((now, actual))
            cutoff = now - MOVEMENT_WINDOW
            while history and history[0][0] < cutoff:
                history.pop(0)

        self.unreadable_axes = unreadable
        self.lagging_axes = self._find_stragglers(now)
        if readings:
            self.bridge.positions(readings)

    def _find_stragglers(self, now: float) -> List[int]:
        """Pistons that are not covering the stroke they were given.

        Only judged during a continuous run, and only once there is a full
        window of history: a single stroke, a curve or the first second of a
        run would all look like a piston that is not moving.
        """
        if self._state is not MachineState.RUNNING:
            return []
        if self._run_mode is not RunMode.CONTINUOUS:
            return []

        stragglers: List[int] = []
        for motor in self.all_motors:
            history = self._history.get(motor.axis, [])
            if len(history) < 4 or (now - history[0][0]) < MOVEMENT_WINDOW * 0.9:
                continue

            stroke = abs(
                motor.write_params["Position 2"] - motor.write_params["Position 1"]
            )
            if stroke < MIN_JUDGED_STROKE:
                continue

            positions = [p for _t, p in history]
            travelled = max(positions) - min(positions)

            # The drive's own following-error warning is the better signal when
            # it is available: it comes from the limit configured on the drive
            # rather than from watching positions over a network.
            drive_says_so = False
            try:
                drive_says_so = motor.read_status(self.plc).is_lagging
            except (PlcError, TypeError, ValueError):
                pass

            if drive_says_so or travelled < stroke * STUCK_FRACTION:
                stragglers.append(motor.axis)
                if motor.axis not in self._lag_reported:
                    self._lag_reported.add(motor.axis)
                    LOGGER.warning(
                        "Piston %s covered only %.0f mm of its %.0f mm stroke over "
                        "the last %.0f seconds. It may be dragging or stuck.",
                        tags.display_number(motor.axis), travelled, stroke, MOVEMENT_WINDOW,
                    )
        return stragglers

    def clear_lag_warnings(self) -> None:
        """Forget which pistons were flagged, so a new run starts clean."""
        self._history = {}
        self._lag_reported = set()
        self.lagging_axes = []
        self.unreadable_axes = []

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
                self.ip_address,
                self.processor_slot,
                simulate=self._simulate,
                persistent=self._persistent_connection,
            )
            # Tell the screens, so the connection banner stops saying "looking".
            self.bridge.state_changed(self._state)

        if self.is_live:
            identity = self.plc.identity()
            if identity:
                LOGGER.info("Controller: %s", identity)
            self._check_position_scale()
            self.bridge.status("Connected. Clearing the machine...")
            self.motors_off()
            self.bridge.status("Ready. Choose pistons on the tank.")
        else:
            self.bridge.status(
                "Mock wavemaker. The pistons here are simulated - nothing "
                "physical will move."
                if self._simulate else
                "No PLC at {0}. Nothing will move. Check the controller is "
                "powered and in Run, then press Reconnect.".format(self.ip_address)
            )
        self._set_state(MachineState.IDLE)

    def _check_position_scale(self) -> None:
        """Warn if the controller stops reporting positions in drive counts.

        The scale is taken from observed data rather than from documentation,
        so if it is ever wrong the application should say so instead of quietly
        misreporting every position by a factor of ten thousand.
        """
        try:
            raw = self.plc.read(tags.axis_field(0, tags.ACTUAL_POSITION))
        except PlcError:
            return
        if params.looks_like_millimetres(raw):
            LOGGER.warning(
                "Axis 0 reports position %s, which looks like millimetres "
                "rather than drive counts. If the controller now publishes "
                "millimetres, set POSITION_COUNTS_PER_MM to 1 in app/params.py.",
                raw,
            )
        else:
            LOGGER.info(
                "Positions read in drive counts; axis 0 at %.1f mm.",
                params.to_mm(raw),
            )

    def reconnect(self) -> bool:
        """Try the PLC again without restarting the application.

        The launcher no longer walks the operator through Studio 5000, so this
        is how they recover from starting the application before the controller
        was ready.
        """
        if self._simulate:
            self.bridge.problem(
                "Simulation mode",
                "This session was started with --simulate, so it will not "
                "connect to the machine. Restart without that option.",
            )
            return False
        self._connection_attempted = False
        return self._command("Reconnect", self._startup_worker)

    def reset(self) -> bool:
        """Return the whole application to its just-launched state."""
        return self._command("Reset", self._reset_worker)

    def _reset_worker(self) -> None:
        self._stop_requested.set()
        self.motors_off()
        self._stop_requested.clear()

        self.sets = []
        self.selection = dict((axis, False) for axis in range(tags.MOTOR_COUNT))
        # "Just-launched" includes the default preset, which is what launching
        # actually gives you.
        self.pending_params = default_parameters()
        self._implicit_group = True
        self._live_index = 0
        self._homed_axes = set()
        self.rest_position = REST_DOWN
        self.record_analytics = False
        self.analytics_interval = 0.25
        self.analytics_duration = 10.0

        self._set_state(MachineState.IDLE)
        self.bridge.status("Reset. Choose pistons on the tank.")
        LOGGER.info("Application reset to its initial state.")

    def shutdown(self) -> None:
        """Stop the machine and close the connection. Called when the window closes."""
        self.stop_monitoring()
        self._shutdown.set()
        self._cancel_park.set()
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
