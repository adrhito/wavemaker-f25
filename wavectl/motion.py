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
from typing import Callable, Dict, Optional

from app import tags
from app.plc import PlcError
from Model import MachineState, RunMode
from logging import getLogger

from modules.logging.log_utils import LOGGER_NAME
from wavectl import live, pistons, sampling
from wavectl.jobs import Job, Refusal, Result
from wavectl.timing import (DEFAULT_RUN_SECONDS, INSIST_SECONDS, MAX_RUN_SECONDS,
                            ONCE_SECONDS, PREPARE_SECONDS, REST_SECONDS,
                            START_SECONDS, STILL_CAP_SECONDS)

LOGGER = getLogger(LOGGER_NAME)

_MODES = {"continuous": RunMode.CONTINUOUS, "single": RunMode.SINGLE,
          "curve": RunMode.CURVE}

#: Two readings of a piston this close together, in mm, count as not moving.
STILL_MM = 0.3
#: Gap between those readings, and how many in a row make "standing still".
STILL_POLL_SECONDS = 0.1
STILL_READINGS = 3
#: The least time the pistons are given to stop moving before saying they have not.
STILL_SECONDS = 5.0
#: How far from where it was sent a piston may be and still count as parked:
#: the model's own arrival tolerance, and a little for the last of the move.
REST_TOLERANCE_MM = 8.0


def _mm(value: float) -> str:
    """Whole millimetres, without the "-0" a position just above zero rounds to."""
    return "{0:d}".format(int(round(value)))


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
        "watch": "watch",
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

    def _homed(self) -> bool:
        """Whether the selected pistons are homed. Asks the drives every time.

        The model's own ``needs_homing`` answers from its state alone once
        that state is HOMED. A drive that has since lost its reference -- an
        emergency stop, a power cycle -- goes unnoticed there, and the run
        that follows drives pistons that do not know where they are.

        When the drives disagree with the model, the model is taken out of
        HOMED, so that its own next prepare homes them rather than trusting
        itself.
        """
        model = self.model
        if model._already_homed():
            return True
        if model.state is MachineState.HOMED:
            self.journal.note("The drives of pistons {0} no longer all report "
                              "homed; they must be prepared again.".format(
                                  pistons.describe(model.live_axes)))
            model.mark_unprepared()
        return False

    def _refuse_unless_homed(self, allow_prepare: bool) -> None:
        if self._homed() or allow_prepare:
            return
        raise Refusal(
            "Pistons {0} are not homed. wavectl prepare boots and homes them "
            "first (about a minute; every selected piston moves), or add "
            "--prepare to do both.".format(pistons.describe(self.model.live_axes)))

    def _why(self, mark: int, otherwise: str) -> str:
        """What the model said went wrong since ``mark``."""
        problems = self.journal.problems_since(mark)
        if problems:
            return " ".join(problems)
        if self.model.busy and self.model._current_command:
            return "The machine is busy: {0} is still running.".format(
                self.model._current_command)
        return otherwise

    def _positions(self) -> Optional[list]:
        """Where each selected piston is, or None if one cannot be read."""
        places = []
        for motor in self.model.all_motors:
            try:
                places.append(motor.read_position(self.plc))
            except (PlcError, TypeError, ValueError):
                return None
        return places

    def _come_to_rest(self, cap: Optional[float] = None) -> Optional[float]:
        """Wait for the selected pistons to stop moving. Returns how long it took.

        The model calls the machine stopped once the run bits are low, and
        the pistons are not. Measured on the array on 12 October 2026, on
        pistons 1 to 3 with a 60 mm stroke at 150 mm/s: after an immediate
        stop each piston carried on to the end of the move it was on: 15 mm
        and 0.2 s later in one run, about 55 mm in another, and 115 mm in a
        third, where the stop came while they were still travelling from
        their resting place onto the stroke. An answer that read the
        positions as soon as the bits dropped said "between 1 and 5 mm" of
        pistons that came to rest at 60.

        So where the pistons are is read only once it has stopped changing.
        Returns None if they were still moving when the wait ran out, or
        could not be read.
        """
        limit = max(self.model._stroke_seconds(STILL_SECONDS), STILL_SECONDS)
        if cap is not None:
            limit = min(limit, cap)
        began = time.monotonic()
        previous = self._positions()
        quiet = 0
        while time.monotonic() - began < limit:
            time.sleep(STILL_POLL_SECONDS)
            now = self._positions()
            if (previous is not None and now is not None
                    and len(previous) == len(now)
                    and all(abs(a - b) <= STILL_MM for a, b in zip(previous, now))):
                quiet += 1
                if quiet >= STILL_READINGS:
                    return max(time.monotonic() - began
                               - STILL_READINGS * STILL_POLL_SECONDS, 0.0)
            else:
                quiet = 0
            previous = now
        return None

    def _where(self) -> str:
        """Where the selected pistons are standing, in a few words."""
        places = self._positions()
        if places is None:
            return "positions unreadable"
        if not places:
            return "no pistons selected"
        low, high = min(places), max(places)
        if high - low <= 2.0:
            return "pistons at {0} mm".format(_mm((low + high) / 2.0))
        return "pistons between {0} and {1} mm".format(_mm(low), _mm(high))

    def _resting_place(self) -> str:
        """Wait for the pistons to stand still, then say where they are."""
        if self._come_to_rest() is None and self.model.all_motors:
            return "{0}, STILL MOVING".format(self._where())
        return self._where()

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
        # The model only ever adds to this list, so without clearing it a
        # piston dropped by an earlier prepare would be reported again by
        # every later one.
        model.dropped_axes = []
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
        parts.append(self._resting_place())
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
        # Judged by the model's own closing sentence. Its list of pistons
        # that would not home is no guide: a piston warned about for standing
        # still early in homing stays on it after it has gone on to home.
        said = self.journal.last_status_since(mark) or "calibration ended"
        if said.startswith("All 30 pistons are calibrated"):
            return Result(said, {"calibrated": tags.MOTOR_COUNT})
        raise Refusal(said if "calibrated" in said else self._why(mark, said))

    # -- run -------------------------------------------------------------------------

    def run(self, mode: str = "continuous", seconds: Optional[float] = None,
            wait: Optional[bool] = None, patience: Optional[float] = None,
            prepare: bool = False, ignore_warnings: bool = False,
            sample_hz: Optional[float] = None) -> Result:
        """Run the selection: continuously for a set time, or once.

        A continuous run stops itself after ``seconds`` and rests the pistons,
        exactly as a Stop from the window would. One stroke and one curve end
        by themselves. ``sample_hz`` records the pistons' positions to a file
        from before the run bit goes up until they are at rest again.
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
        self._refuse_unless_homed(prepare)
        if model.state is MachineState.HOMED and not model._live_motors_stale:
            self._refuse_if_selection_differs()

        if wait is None:
            # A stroke is over in seconds, so its answer is worth waiting
            # for. A continuous run is as long as it was asked to be.
            wait = not continuous
        if patience is None:
            patience = ((seconds + REST_SECONDS if continuous else ONCE_SECONDS)
                        + START_SECONDS + (PREPARE_SECONDS if prepare else 0.0))
        note = "{0} run of pistons {1}, {2}".format(
            mode, pistons.describe(model.live_axes), self._motion_phrase())
        if continuous:
            job = self._start_job("run", lambda job: self._sampled(
                sample_hz, note, lambda: self._continuous_work(job, seconds)))
        else:
            job = self._start_job(
                "stroke" if run_mode is RunMode.SINGLE else "curve",
                lambda job: self._sampled(
                    sample_hz, note, lambda: self._once_work(job, run_mode)))
        return self._answer(job, wait, patience)

    def _sampled(self, hz: Optional[float], note: str,
                 work: Callable[[], Result]) -> Result:
        """Do ``work`` with the selected pistons' positions being recorded."""
        if not hz:
            return work()
        sampler = sampling.Sampler(self.plc, self.model.live_axes, hz, note).start()
        try:
            result = work()
        except Refusal as exc:
            # A run that went wrong is the one whose positions matter most.
            sampler.stop()
            path = sampler.save()
            raise Refusal("{0} | {1}".format(exc, sampler.summary(path)),
                          dict(exc.data, samples=sampler.record(path))) from exc
        except BaseException:
            sampler.stop()
            raise
        sampler.stop()
        path = sampler.save()
        return Result("{0} | {1}".format(result.text, sampler.summary(path)),
                      dict(result.data, samples=sampler.record(path)))

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
        lagged = set()

        def over() -> bool:
            # The model forgets its stragglers when the run ends, so they are
            # collected while it is going.
            lagged.update(model.lagging_axes)
            return (model.state is not MachineState.RUNNING
                    or model._stopping.is_set())

        try:
            # Ends early if anything else stops the machine first.
            self._until(over, seconds)
            lasted = time.monotonic() - began
            timed = model.state is MachineState.RUNNING and not model._stopping.is_set()
            stopping_from = self.journal.mark()
            if timed:
                self.journal.note("Run time of {0:g} s is up; stopping.".format(seconds))
                # Not judged by what it returns: whether the machine stopped
                # is read from the run bits themselves, below.
                model.stop()
        finally:
            self._run_deadline = None

        def settled_or_failed() -> bool:
            # A stop that could not reach the controller says so and gives
            # up, with the model still "running". Waiting out the full rest
            # time on that would be forty-five more seconds of a run nobody
            # is stopping. Told by what the model reported, not by its
            # flags: between finishing the stroke and starting the resting
            # move there is a moment when none of them is set, and taking
            # that for a failure sent a second stop that cancelled the park.
            return self._at_rest() or any(
                problem.startswith("Stop failed")
                for problem in self.journal.problems_since(stopping_from))

        self._until(settled_or_failed, REST_SECONDS)
        self._make_sure_it_stopped()
        rested = self._until(self._at_rest, STILL_SECONDS)

        extent = self.journal.extent()
        strokes = dict((motor.axis, abs(motor.write_params["Position 2"]
                                        - motor.write_params["Position 1"]))
                       for motor in model.all_motors)
        if lasted >= 3.0:
            # Long enough for the monitor to have seen a stroke or two.
            lagged.update(axis for axis, (low, top) in extent.items()
                          if strokes.get(axis, 0) >= 25
                          and top - low < 0.35 * strokes[axis])
        parts = ["ran pistons {0} for {1:.0f} s".format(shown, lasted) if timed
                 else "run on pistons {0} was stopped after {1:.0f} s".format(shown, lasted)]
        if extent:
            parts.append("seen between {0} and {1} mm".format(
                _mm(min(low for low, _high in extent.values())),
                _mm(max(high for _low, high in extent.values()))))
        parts.append(self.journal.last_status_since(mark) or "stopped")
        if not rested:
            parts.append("still coming to rest")
        high = live.bits_high(self.plc)
        data = {"seconds": round(lasted, 1), "timed_out": timed,
                "bits_high": high,
                "seen_mm": dict((str(tags.display_number(axis)), [round(low, 1), round(high_, 1)])
                                for axis, (low, high_) in extent.items())}
        data["barely_moved"] = pistons.shown(lagged)
        problems = self.journal.problems_since(mark)
        if high:
            problems.append("{0} still high after the stop.".format(" and ".join(high)))
        if lagged:
            problems.append("Piston(s) {0} did not cover their stroke.".format(
                pistons.describe(lagged)))
        if problems:
            raise Refusal(" | ".join(parts + problems), data)
        return Result(" | ".join(parts), data)

    def _make_sure_it_stopped(self) -> None:
        """Keep stopping until the run bits read low. For the end of a timed run.

        A run started here has to end here: nothing else is watching it. One
        stop that failed -- the controller not answering for a few seconds as
        the time ran out -- used to leave Run_2 high with the session
        believing the run was over, and nothing ever tried again.
        """
        model = self.model
        deadline = time.monotonic() + INSIST_SECONDS
        tried = 0
        while True:
            try:
                high, unread = live.bits_high(self.plc), None
            except PlcError as exc:
                high, unread = None, str(exc)
            if high == [] and model.state is not MachineState.RUNNING:
                if tried:
                    self.journal.note("The machine stopped on attempt {0}.".format(tried + 1))
                return
            if time.monotonic() >= deadline:
                raise Refusal(
                    "THE RUN COULD NOT BE STOPPED after {0:.0f} seconds of "
                    "trying: {1}. Use the physical stop.".format(
                        INSIST_SECONDS, unread or
                        "{0} still read high".format(" and ".join(high or ["a run bit"]))),
                    {"bits_high": high})
            tried += 1
            LOGGER.critical("Stop attempt %s at the end of a timed run: %s", tried,
                            unread or "run bits %s still high" % (high,))
            model.stop(immediate=True, park=False)
            time.sleep(2.0)

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
        where = self._resting_place()
        if "cancelled" in said or "barely moved" in said or not said.startswith("Ran a"):
            # The model says these in its status bar, not as a problem, and
            # none of them is the stroke that was asked for.
            raise Refusal("{0} | {1}".format(said, where))
        return Result("{0} | {1}".format(said, where), {"state": model.state.value})

    # -- stop ------------------------------------------------------------------------

    def stop(self, now: bool = False, park: Optional[bool] = None) -> Result:
        """Stop the machine.

        By default each piston finishes the stroke it is on and the array
        then moves to its resting position, as the window's Stop does.
        ``now`` drops the run bits at once and commands nothing further. That
        is not the same as the pistons freezing: on this machine each one
        completes the leg it is on first (see :meth:`_come_to_rest`), so the
        answer waits for them and says how much further they went.

        Never queued behind anything: it is answered while a prepare or a run
        is still holding its own request open.
        """
        model = self.model
        mark = self.journal.mark()
        self._interrupted()
        if park is None and (self._at_rest() or now):
            # The model rests the pistons after any stop from HOMED, moving
            # or not. Asked to stop a machine that is standing still, or to
            # stop this instant, the answer must not be to start a move.
            park = False
        if not model.stop(immediate=bool(now), park=park):
            raise Refusal("STOP FAILED. {0}".format(self._why(
                mark, "The controller did not acknowledge it. Use the "
                      "physical stop and check the connection.")))
        at_stop = self._positions()
        rested = self._until(self._at_rest, REST_SECONDS)
        high = live.bits_high(self.plc)
        if high:
            raise Refusal("Stop was sent, but {0} still read high. Use the "
                          "physical stop.".format(" and ".join(high)),
                          {"bits_high": high})
        still = (self._come_to_rest(STILL_CAP_SECONDS) is not None
                 or not model.all_motors)
        final = self._positions()
        further = 0.0
        if at_stop and final and len(at_stop) == len(final):
            further = max(abs(a - b) for a, b in zip(at_stop, final))

        parts = [self.journal.last_status_since(mark) or "stopped", "run bits clear"]
        if not still:
            parts.append("{0}, STILL MOVING: use the physical stop if that is "
                         "not expected".format(self._where()))
        elif now and further >= 1.0:
            parts.append("{0}, after finishing the leg they were on (up to "
                         "{1} mm more)".format(self._where(), _mm(further)))
        else:
            parts.append(self._where())
        parts.append(self._state_phrase())
        if not rested:
            parts.append("still coming to rest; see wavectl status")
        return Result(" | ".join(parts), {
            "state": model.state.value, "bits_high": high, "at_rest": rested,
            "standing_still": still, "moved_after_stop_mm": round(further, 1)})

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
        if not self._homed():
            raise Refusal("Pistons {0} are not homed, so they cannot be moved "
                          "yet: wavectl prepare".format(
                              pistons.describe(model.live_axes)))
        self._interrupted()
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
        said = self.journal.last_status_since(mark) or "at rest"
        where = self._resting_place()
        # The model reports a resting move that fell short in its status bar
        # and carries on. Here it is the whole of what was asked for, so the
        # positions are checked against where each piston was sent.
        short = []
        for motor in model.all_motors:
            try:
                off = abs(motor.read_position(self.plc) - model._rest_target(motor))
            except (PlcError, TypeError, ValueError):
                off = None
            if off is None or off > REST_TOLERANCE_MM:
                short.append(motor.axis)
        if short:
            raise Refusal("Piston(s) {0} did not reach the resting position. "
                          "{1} | {2}".format(pistons.describe(short), said, where),
                          {"short": pistons.shown(short)})
        # Lowered on purpose, so it is no longer a floor being held up.
        model._soliton_floor_raised = False
        return Result("{0} | {1}".format(said, where), {"state": model.state.value})

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

    # -- watching ----------------------------------------------------------------------

    def watch(self, seconds: float = 10.0, hz: float = sampling.DEFAULT_HZ,
              pistons_text: Optional[str] = None) -> Result:
        """Record piston positions to a file for a while. Reads only.

        Works whatever the machine is doing and alongside a run, so it is how
        to look at motion that is already under way.
        """
        seconds = float(seconds)
        if not 0 < seconds <= sampling.MAX_SECONDS:
            raise Refusal("Watch for between a moment and {0:.0f} seconds.".format(
                sampling.MAX_SECONDS))
        axes = (pistons.parse(pistons_text) if pistons_text is not None
                else self.model.live_axes)
        if not axes:
            raise Refusal("Nothing is selected. Name the pistons to watch: "
                          "wavectl watch --pistons 1-9")
        sampler = sampling.Sampler(self.plc, axes, hz, self._state_phrase()).start()
        try:
            time.sleep(seconds)
        finally:
            sampler.stop()
        path = sampler.save()
        return Result(sampler.summary(path), sampler.record(path))
