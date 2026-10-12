"""Preparing, running and stopping.

Every operation here ends in a ``Model`` command -- ``prepare``, ``run``,
``stop`` -- and so inherits the order and the guards those commands enforce.
What this module adds is what a window gets from the person in front of it:

* **A run always ends.** The window runs continuously until somebody presses
  Stop. A command line may be driven by something that loses interest, so a
  continuous run here is given a length and the session stops it.
* **Homing is asked for, not assumed.** Homing boots the drives, which drops
  every selected piston to zero and makes a wave. The window asks before
  doing it; here ``run`` refuses until ``prepare`` has been run, unless told
  to do both.
* **Checks before motion.** A run bit that is already high, or a selected
  drive reporting a fault, stops the command before anything is written.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from app import tags
from app.plc import PlcError
from Model import MachineState, RunMode
from wavectl import live, pistons
from wavectl.jobs import Job, Refusal, Result
from wavectl.timing import (DEFAULT_RUN_SECONDS, MAX_RUN_SECONDS, ONCE_SECONDS,
                            PREPARE_SECONDS, REST_SECONDS, START_SECONDS)

_MODES = {"continuous": RunMode.CONTINUOUS, "single": RunMode.SINGLE,
          "curve": RunMode.CURVE}


class MotionOperations:
    """The motion half of :class:`wavectl.service.Service`."""

    MOTION_OPERATIONS: Dict[str, str] = {
        "prepare": "prepare",
        "calibrate": "calibrate",
        "run": "run",
        "stop": "stop",
        "park": "park",
        "speed": "speed",
        "stroke": "stroke",
        "wait": "wait",
    }

    # -- checks made before anything is commanded --------------------------------

    def _at_rest(self) -> bool:
        return not self.model._in_motion() and not self.model.busy

    def _refuse_unless_at_rest(self, what: str) -> None:
        if not self._at_rest():
            raise Refusal("The machine is {0}; {1} has to wait until it has "
                          "stopped (wavectl stop).".format(self._state_phrase(), what))

    def _refuse_if_driven(self) -> None:
        """Refuse if a command bit is high that this session did not raise.

        Only meaningful while this session is at rest, which is when every
        caller asks: then any bit that is high was set by another program.
        """
        high = live.bits_high(self.plc)
        if high:
            raise Refusal(
                "{0} high on the controller, and this session did not raise "
                "it: something else is driving the machine. Nothing was "
                "commanded.".format(" and ".join(high)), {"bits_high": high})

    def _refuse_if_troubled(self, ignore: bool) -> None:
        """Refuse if a selected drive reports a fault, unless told to go on."""
        problems = self.model.check_drives(self.model.live_axes)
        if not problems or ignore:
            return
        named = "; ".join("piston {0}: {1}".format(tags.display_number(axis), why)
                          for axis, why in sorted(
                              problems.items(), key=lambda item: tags.display_number(item[0])))
        raise Refusal(
            "Nothing was commanded: {0}. Preparing clears motion faults; if "
            "that is what these need, repeat with --ignore-warnings."
            .format(named),
            {"problems": dict((str(tags.display_number(a)), why)
                              for a, why in problems.items())})

    def _refuse_if_selection_differs(self) -> None:
        """Refuse if the controller's selection is not the one this session wrote.

        The run bits act on whatever ``Live_Motors`` holds. While the model
        says HOMED that is exactly the pistons it marked at prepare, so a
        difference means another program has written to the controller since.
        """
        ours = set(self.model.live_axes)
        theirs = set(axis for axis in range(tags.MOTOR_COUNT)
                     if self.plc.read(tags.live_motor(axis)))
        if ours == theirs:
            return
        raise Refusal(
            "The controller has pistons {0} selected, not this session's {1}: "
            "something else has written to it. Nothing was commanded. "
            "wavectl prepare writes this session's selection again.".format(
                pistons.describe(theirs), pistons.describe(ours)))

    def _why(self, mark: int, otherwise: str) -> str:
        """What the model said went wrong since ``mark``."""
        problems = self.journal.problems_since(mark)
        if problems:
            return " ".join(problems)
        if self.model.busy and self.model._current_command:
            return "The machine is busy: {0} is still running.".format(
                self.model._current_command)
        return otherwise

    def _where(self) -> str:
        """Where the selected pistons are standing, in a few words."""
        places = []
        for motor in self.model.all_motors:
            try:
                places.append(motor.read_position(self.plc))
            except (PlcError, TypeError, ValueError):
                return "positions unreadable"
        if not places:
            return "no pistons selected"
        low, high = min(places), max(places)
        if high - low <= 2.0:
            return "pistons at {0:.0f} mm".format((low + high) / 2.0)
        return "pistons between {0:.0f} and {1:.0f} mm".format(low, high)

    # -- prepare -------------------------------------------------------------------

    def prepare(self, wait: bool = True, patience: float = PREPARE_SECONDS,
                ignore_warnings: bool = False) -> Result:
        """Write the parameters and home the selected pistons.

        Boots the drives, which drops every selected piston to zero, then
        homes them: about a minute, and the water is disturbed. Pistons this
        session has already homed are not homed again.
        """
        model = self.model
        if not model.all_motors:
            raise Refusal("Nothing is selected. Choose pistons first: wavectl select 1-9")
        self._refuse_unless_at_rest("preparing")
        self._refuse_if_driven()
        self._refuse_if_troubled(ignore_warnings)
        homing = model.needs_homing
        job = self._start_job("prepare", self._prepare_work)
        job.reached("preparing pistons {0}{1}".format(
            pistons.describe(model.live_axes),
            ": booting and homing, about a minute" if homing
            else ": already homed, writing parameters"))
        return self._answer(job, wait, patience)

    def _prepare_work(self, job: Job) -> Result:
        model = self.model
        mark = self.journal.mark()
        asked = pistons.describe(model.live_axes)
        if not model.prepare():
            raise Refusal(self._why(mark, "Prepare did not start."))
        if not self._until(lambda: not model.busy, PREPARE_SECONDS):
            raise Refusal("Still preparing after {0:.0f} seconds; see wavectl "
                          "status.".format(PREPARE_SECONDS))
        if model.state is not MachineState.HOMED:
            raise Refusal(self._why(
                mark, self.journal.last_status_since(mark) or
                "Preparing ended without the pistons homed."))
        parts = ["homed pistons {0}".format(pistons.describe(model.live_axes))]
        if model.dropped_axes:
            parts.append("piston(s) {0} would not home and were dropped (asked "
                         "for {1})".format(pistons.describe(model.dropped_axes), asked))
        parts.append(self._where())
        troubled = model.check_drives(model.live_axes)
        if troubled:
            parts.append("; ".join("piston {0}: {1}".format(
                tags.display_number(a), why) for a, why in troubled.items()))
        return Result(" | ".join(parts), {
            "pistons": pistons.shown(model.live_axes),
            "dropped": pistons.shown(model.dropped_axes),
            "state": model.state.value})

    def calibrate(self, wait: bool = True, patience: float = PREPARE_SECONDS) -> Result:
        """Home all thirty pistons, whatever is selected.

        Worth doing once at the start of a long sitting: afterwards no
        selection has to wait for homing.
        """
        self._refuse_unless_at_rest("calibrating")
        self._refuse_if_driven()
        job = self._start_job("calibrate", self._calibrate_work)
        job.reached("homing all thirty pistons, about a minute")
        return self._answer(job, wait, patience)

    def _calibrate_work(self, job: Job) -> Result:
        model = self.model
        mark = self.journal.mark()
        if not model.calibrate_all():
            raise Refusal(self._why(mark, "Calibration did not start."))
        if not self._until(lambda: not model.busy, PREPARE_SECONDS):
            raise Refusal("Still calibrating after {0:.0f} seconds.".format(
                PREPARE_SECONDS))
        said = self.journal.last_status_since(mark) or "calibration ended"
        stuck = pistons.shown(model.unhomed_axes)
        if stuck or "calibrated" not in said:
            raise Refusal(self._why(mark, said), {"would_not_home": stuck})
        return Result(said, {"would_not_home": []})

    # -- run -------------------------------------------------------------------------

    def run(self, mode: str = "continuous", seconds: Optional[float] = None,
            wait: Optional[bool] = None, patience: Optional[float] = None,
            prepare: bool = False, ignore_warnings: bool = False) -> Result:
        """Run the selection: continuously for a set time, or once.

        A continuous run stops itself after ``seconds`` and rests the pistons,
        exactly as a Stop from the window would. One stroke and one curve end
        by themselves.
        """
        model = self.model
        if mode not in _MODES:
            raise Refusal("mode must be continuous, single or curve.")
        run_mode = _MODES[mode]
        continuous = run_mode is RunMode.CONTINUOUS
        if continuous:
            seconds = DEFAULT_RUN_SECONDS if seconds is None else float(seconds)
            if not 0 < seconds <= MAX_RUN_SECONDS:
                raise Refusal("A run lasts between a moment and {0:.0f} seconds; "
                              "{1:g} is not in that range.".format(
                                  MAX_RUN_SECONDS, seconds))
        if not model.all_motors:
            raise Refusal("Nothing is selected. Choose pistons first: wavectl select 1-9")
        self._refuse_unless_at_rest("a run")
        self._refuse_if_driven()
        self._refuse_if_troubled(ignore_warnings)
        if model.needs_homing:
            if not prepare:
                raise Refusal(
                    "Pistons {0} are not homed. wavectl prepare boots and homes "
                    "them first (about a minute; every selected piston moves), "
                    "or add --prepare to do both.".format(
                        pistons.describe(model.live_axes)))
        elif model.state is MachineState.HOMED and not model._live_motors_stale:
            self._refuse_if_selection_differs()

        if wait is None:
            # A stroke is over in seconds, so its answer is worth waiting
            # for. A continuous run is as long as it was asked to be.
            wait = not continuous
        if patience is None:
            patience = ((seconds + REST_SECONDS if continuous else ONCE_SECONDS)
                        + START_SECONDS + (PREPARE_SECONDS if prepare else 0.0))
        if continuous:
            job = self._start_job("run", lambda job: self._continuous_work(job, seconds))
        else:
            job = self._start_job(
                "stroke" if run_mode is RunMode.SINGLE else "curve",
                lambda job: self._once_work(job, run_mode))
        return self._answer(job, wait, patience)

    def _continuous_work(self, job: Job, seconds: float) -> Result:
        model = self.model
        mark = self.journal.mark()
        self.journal.clear_extent()
        shown = pistons.describe(model.live_axes)
        if not model.run(RunMode.CONTINUOUS):
            raise Refusal(self._why(mark, "The run did not start."))
        # The worker prepares if it has to, stages a travelling wave, raises
        # Run_2 and returns, leaving the state at RUNNING.
        if not self._until(lambda: not model.busy,
                           START_SECONDS + PREPARE_SECONDS):
            raise Refusal("The run was still starting after {0:.0f} seconds; "
                          "see wavectl status.".format(START_SECONDS + PREPARE_SECONDS))
        if model.state is not MachineState.RUNNING:
            raise Refusal(self._why(
                mark, self.journal.last_status_since(mark) or "The run did not start."))

        began = time.monotonic()
        self._run_deadline = began + seconds
        job.reached("running pistons {0} continuously for {1:g} s, then stopping "
                    "by itself | {2}".format(shown, seconds, self._motion_phrase()))
        try:
            # Ends early if anything else stops the machine first.
            self._until(lambda: model.state is not MachineState.RUNNING
                        or model._stopping.is_set(), seconds)
            lasted = time.monotonic() - began
            timed = model.state is MachineState.RUNNING and not model._stopping.is_set()
            if timed:
                self.journal.note("Run time of {0:g} s is up; stopping.".format(seconds))
                if not model.stop():
                    raise Refusal("STOP FAILED at the end of the run. " + self._why(
                        mark, "Use the physical stop and check the connection."))
        finally:
            self._run_deadline = None
        rested = self._until(self._at_rest, REST_SECONDS)

        extent = self.journal.extent()
        parts = ["ran pistons {0} for {1:.0f} s".format(shown, lasted) if timed
                 else "run on pistons {0} was stopped after {1:.0f} s".format(shown, lasted)]
        if extent:
            parts.append("seen between {0:.0f} and {1:.0f} mm".format(
                min(low for low, _high in extent.values()),
                max(high for _low, high in extent.values())))
        parts.append(self.journal.last_status_since(mark) or "stopped")
        if not rested:
            parts.append("still coming to rest")
        high = live.bits_high(self.plc)
        data = {"seconds": round(lasted, 1), "timed_out": timed,
                "bits_high": high,
                "seen_mm": dict((str(tags.display_number(axis)), [round(low, 1), round(high_, 1)])
                                for axis, (low, high_) in extent.items())}
        problems = self.journal.problems_since(mark)
        if high:
            problems.append("{0} still high after the stop.".format(" and ".join(high)))
        if problems:
            raise Refusal(" | ".join(parts + problems), data)
        return Result(" | ".join(parts), data)

    def _once_work(self, job: Job, run_mode: RunMode) -> Result:
        model = self.model
        mark = self.journal.mark()
        what = "one stroke" if run_mode is RunMode.SINGLE else "one curve"
        if not model.run(run_mode):
            raise Refusal(self._why(mark, "{0} did not start.".format(what.capitalize())))
        job.reached("{0} under way on pistons {1}".format(
            what, pistons.describe(model.live_axes)))
        if not self._until(self._at_rest, ONCE_SECONDS + PREPARE_SECONDS):
            raise Refusal("{0} had not finished after {1:.0f} seconds; see "
                          "wavectl status.".format(what.capitalize(), ONCE_SECONDS))
        said = self.journal.last_status_since(mark) or "{0} ended".format(what)
        problems = self.journal.problems_since(mark)
        if problems:
            raise Refusal(" ".join(problems))
        return Result("{0} | {1}".format(said, self._where()),
                      {"state": model.state.value})

    # -- stop ------------------------------------------------------------------------

    def stop(self, now: bool = False, park: Optional[bool] = None) -> Result:
        """Stop the machine.

        By default each piston finishes the stroke it is on and the array
        then moves to its resting position, as the window's Stop does.
        ``now`` drops the run bits at once and leaves the pistons where they
        are. Never queued behind anything: it is answered while a prepare or
        a run is still holding its own request open.
        """
        model = self.model
        mark = self.journal.mark()
        if park is None and (self._at_rest() or now):
            # The model rests the pistons after any stop from HOMED, moving
            # or not. Asked to stop a machine that is standing still, or to
            # stop this instant, the answer must not be to start a move.
            park = False
        if not model.stop(immediate=bool(now), park=park):
            raise Refusal("STOP FAILED. {0}".format(self._why(
                mark, "The controller did not acknowledge it. Use the "
                      "physical stop and check the connection.")))
        rested = self._until(self._at_rest, REST_SECONDS)
        high = live.bits_high(self.plc)
        if high:
            raise Refusal("Stop was sent, but {0} still read high. Use the "
                          "physical stop.".format(" and ".join(high)),
                          {"bits_high": high})
        parts = [self.journal.last_status_since(mark) or "stopped", self._where(),
                 self._state_phrase()]
        if not rested:
            parts.append("still coming to rest; see wavectl status")
        return Result(" | ".join(parts), {"state": model.state.value,
                                          "bits_high": high, "at_rest": rested})

    def park(self) -> Result:
        """Move the selected pistons to their resting position."""
        model = self.model
        if not model.all_motors:
            raise Refusal("Nothing is selected, so there is nothing to move.")
        self._refuse_unless_at_rest("moving to rest")
        if model.rest_position == "hold":
            raise Refusal("The resting position is set to hold, which means "
                          "stay put. Choose one first: wavectl set rest=down")
        self._refuse_if_driven()
        if model.needs_homing:
            raise Refusal("Pistons {0} are not homed, so they cannot be moved "
                          "yet: wavectl prepare".format(
                              pistons.describe(model.live_axes)))
        mark = self.journal.mark()
        if model.state is not MachineState.HOMED:
            # Still homed, but an edit took the model out of HOMED and it
            # only rests the pistons from there. With nothing to home this
            # just writes the parameters.
            if not model.prepare() or not self._until(
                    lambda: not model.busy, PREPARE_SECONDS) \
                    or model.state is not MachineState.HOMED:
                raise Refusal(self._why(mark, "The pistons could not be made ready to move."))
        mark = self.journal.mark()
        if not model.stop(immediate=True, park=True):
            raise Refusal("STOP FAILED. " + self._why(mark, "Use the physical stop."))
        if not self._until(self._at_rest, REST_SECONDS):
            raise Refusal("The pistons were still moving to rest after {0:.0f} "
                          "seconds; see wavectl status.".format(REST_SECONDS))
        return Result("{0} | {1}".format(
            self.journal.last_status_since(mark) or "at rest", self._where()),
            {"state": model.state.value})

    # -- changes while running ---------------------------------------------------------

    def speed(self, value) -> Result:
        """Change both travel speeds on a run that is already going."""
        mark = self.journal.mark()
        try:
            self.model.change_speed_live(int(value))
        except ValueError as exc:
            raise Refusal("{0} At rest, use: wavectl set speed={1}".format(
                exc, value)) from exc
        return Result(self.journal.last_status_since(mark) or "speed changed", {})

    def stroke(self, value) -> Result:
        """Change the stroke length on a run that is already going.

        Applied when each piston next reaches an end of its stroke, so the
        phase between pistons is kept.
        """
        mark = self.journal.mark()
        try:
            self.model.change_stroke_live(int(value))
        except ValueError as exc:
            raise Refusal("{0} At rest, use: wavectl set stroke={1}".format(
                exc, value)) from exc
        return Result(self.journal.last_status_since(mark) or "stroke change queued", {})

    # -- waiting -----------------------------------------------------------------------

    def wait(self, patience: float = 120.0) -> Result:
        """Wait for whatever was started to finish, and give its answer."""
        job = self._job
        if job is None:
            return Result("nothing has been started | " + self._state_phrase(), {})
        return self._answer(job, True, float(patience))
