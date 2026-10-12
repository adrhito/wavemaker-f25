"""One-way solitary-wave trials: stage the floor, then fire one pulse.

These are the Soliton tab's two buttons. The trial is built from the same
numbers the tab's automatic mode works out -- the first-order width for the
depth, and the plate travel that moves one soliton's worth of water -- and is
handed to the same ``Model.stage_soliton`` and ``Model.fire_soliton``. The
bounds on speed, acceleration and lift stay in ``app/solitons.py``.

Staging and firing are separate commands for the reason they are separate
buttons: the water has to settle in between, and how long that takes is not
something this module can know. ``trial`` (see ``trials``) joins them when a
camera is there to say when the water is still.

Nothing here predicts the height of the wave. The mock has no water in it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, Optional

from app import soliton_records, solitons
from app.solitons import SolitaryTarget, SolitonTrial
from Model import MachineState
from wavectl import pistons, places
from wavectl.jobs import Job, Refusal, Result
from wavectl.timing import PREPARE_SECONDS, PULSE_SECONDS, REST_SECONDS


def build_trial(crest_mm: float, depth_mm: float, piston_count: int,
                width_mm: Optional[float] = None,
                lift_mm: Optional[int] = None):
    """The trial for a requested crest, and a note if a value was limited.

    Width and lift left out are worked out as the Soliton tab's automatic
    mode does. Raises ``ValueError`` for numbers that describe no trial.
    """
    crest_mm, depth_mm = float(crest_mm), float(depth_mm)
    note = None
    if width_mm is None:
        width_mm = int(round(SolitaryTarget(crest_mm, 1, depth_mm).theoretical_width_mm))
    target = SolitaryTarget(crest_mm, float(width_mm), depth_mm)
    if lift_mm is None:
        if piston_count <= 0:
            raise ValueError("Select the pistons first: the floor lift is worked "
                             "out from how many plates are pushing.")
        wanted = solitons.volume_lift_mm(target, piston_count)
        lift_mm = max(1, min(int(round(wanted)), solitons.MAX_TRIAL_LIFT_MM))
        if wanted > solitons.MAX_TRIAL_LIFT_MM:
            note = ("this wave needs about {0:.0f} mm of travel from {1} "
                    "piston(s) and only {2} mm is available; select more "
                    "pistons or ask for less height".format(
                        wanted, piston_count, solitons.MAX_TRIAL_LIFT_MM))
    return SolitonTrial(target, int(lift_mm)), note


def describe_trial(trial: SolitonTrial) -> str:
    """The pulse a trial commands, in one line."""
    target = trial.target
    parts = [
        "crest {0:g} mm in {1:g} mm of water".format(
            target.crest_height_mm, target.water_depth_mm),
        "width {0:g} mm{1}".format(
            target.width_mm, "" if target.width_matches_depth else
            " (theory {0:.0f})".format(target.theoretical_width_mm)),
        "floor {0} to {1} mm, lift {2}".format(
            solitons.BOTTOM_MM, trial.top_mm, trial.floor_lift_mm),
        "{0} mm/s, accel {1} mm/s2, jerk {2} mm/s3, about {3:.2f} s".format(
            trial.speed_mm_s, trial.accel_mm_s2, trial.jerk_mm_s3,
            trial.nominal_travel_seconds),
    ]
    if trial.speed_limited:
        parts.append("held at the motion limits, so slower than the ideal push")
    return " | ".join(parts)


def trial_record(trial: SolitonTrial) -> dict:
    return {
        "crest_mm": trial.target.crest_height_mm,
        "width_mm": trial.target.width_mm,
        "depth_mm": trial.target.water_depth_mm,
        "theory_width_mm": round(trial.target.theoretical_width_mm, 1),
        "lift_mm": trial.floor_lift_mm,
        "floor_start_mm": solitons.BOTTOM_MM,
        "floor_end_mm": trial.top_mm,
        "speed_mm_s": trial.speed_mm_s,
        "accel_mm_s2": trial.accel_mm_s2,
        "jerk_mm_s3": trial.jerk_mm_s3,
        "nominal_seconds": round(trial.nominal_travel_seconds, 3),
        "limited": trial.speed_limited,
    }


class SolitonOperations:
    """The soliton half of :class:`wavectl.service.Service`."""

    SOLITON_OPERATIONS: Dict[str, str] = {
        "soliton_plan": "soliton_plan",
        "soliton_stage": "soliton_stage",
        "soliton_fire": "soliton_fire",
        "soliton_observe": "soliton_observe",
    }

    def _build(self, crest_mm, depth_mm, width_mm, lift_mm):
        try:
            return build_trial(crest_mm, depth_mm, len(self.model.live_axes),
                               width_mm, lift_mm)
        except ValueError as exc:
            raise Refusal(str(exc)[:1].upper() + str(exc)[1:] + (
                "" if str(exc).endswith(".") else ".")) from exc

    def soliton_plan(self, crest_mm: float, depth_mm: float,
                     width_mm: Optional[float] = None,
                     lift_mm: Optional[int] = None) -> Result:
        """What a trial would command. Moves nothing and writes nothing."""
        trial, note = self._build(crest_mm, depth_mm, width_mm, lift_mm)
        text = "pistons {0} | {1}".format(
            pistons.describe(self.model.live_axes), describe_trial(trial))
        return Result(text + (" | " + note if note else ""),
                      dict(trial_record(trial), note=note))

    def soliton_stage(self, crest_mm: float, depth_mm: float,
                      width_mm: Optional[float] = None,
                      lift_mm: Optional[int] = None, prepare: bool = False,
                      wait: bool = True,
                      patience: float = PREPARE_SECONDS) -> Result:
        """Lower the selected floor sections gently, ready for one pulse.

        Homes them first if they need it and ``prepare`` allows it. The
        staging move itself disturbs the water; firing is a separate command
        so the water can be left to settle in between.
        """
        model = self.model
        if not model.all_motors:
            raise Refusal("Nothing is selected. Choose the floor sections "
                          "first: wavectl select c1-3")
        trial, note = self._build(crest_mm, depth_mm, width_mm, lift_mm)
        self._refuse_unless_at_rest("staging")
        self._refuse_if_driven()
        self._refuse_if_troubled(False)
        self._refuse_unless_homed(prepare)
        job = self._start_job("staging", lambda job: self._stage_work(job, trial, note))
        return self._answer(job, wait, patience)

    def _stage_work(self, job: Job, trial: SolitonTrial, note: Optional[str]) -> Result:
        model = self.model
        mark = self.journal.mark()
        self._staged_trial = None
        if not model.stage_soliton(trial):
            raise Refusal(self._why(mark, "Staging did not start."))
        job.reached("staging pistons {0} at {1} mm".format(
            pistons.describe(model.live_axes), solitons.BOTTOM_MM))
        if not self._until(lambda: not model.busy, PREPARE_SECONDS):
            raise Refusal("Still staging after {0:.0f} seconds; see wavectl "
                          "status.".format(PREPARE_SECONDS))
        if not model.soliton_ready_for(trial):
            raise Refusal(self._why(
                mark, self.journal.last_status_since(mark) or
                "The floor did not reach its staged position."))
        self._staged_trial = trial
        parts = ["staged pistons {0} at {1} mm".format(
            pistons.describe(model.live_axes), solitons.BOTTOM_MM),
            describe_trial(trial)]
        if note:
            parts.append(note)
        parts.append("let the water settle, then: wavectl soliton fire")
        return Result(" | ".join(parts), dict(trial_record(trial), note=note))

    def soliton_fire(self, station_mm: Optional[float] = None,
                     sample_hz: Optional[float] = None, wait: bool = True,
                     patience: float = PULSE_SECONDS) -> Result:
        """Send the one upward pulse that was staged, and hold the floor up."""
        model = self.model
        trial = getattr(self, "_staged_trial", None)
        if trial is None or not model.soliton_ready_for(trial):
            # The model forgets a staging on any stop, edit or other run.
            raise Refusal("Nothing is staged. Stage the floor first: wavectl "
                          "soliton stage --crest MM --depth MM")
        self._refuse_unless_at_rest("the pulse")
        self._refuse_if_driven()
        note = "soliton pulse, pistons {0}, {1}".format(
            pistons.describe(model.live_axes), describe_trial(trial))
        job = self._start_job("pulse", lambda job: self._sampled(
            sample_hz, note, lambda: self._fire_work(job, trial, station_mm)))
        return self._answer(job, wait, patience)

    def _fire_work(self, job: Job, trial: SolitonTrial,
                   station_mm: Optional[float],
                   before_pulse: Optional[Callable[[], None]] = None) -> Result:
        model = self.model
        mark = self.journal.mark()
        self._staged_trial = None          # one pulse per staging, as in the model
        shown = pistons.describe(model.live_axes)
        earlier = model.last_soliton_record
        if before_pulse is not None:
            before_pulse()
        if not model.fire_soliton(trial, planned_station_mm=station_mm):
            raise Refusal(self._why(mark, "The pulse was not sent."))
        job.reached("pulse under way on pistons {0}".format(shown))
        if not self._until(self._at_rest, PULSE_SECONDS + REST_SECONDS):
            raise Refusal("The pulse had not finished after {0:.0f} seconds; "
                          "see wavectl status.".format(PULSE_SECONDS))

        # A pulse refused before it began leaves the model pointing at the
        # record of the one before, which says "completed".
        path = model.last_soliton_record
        if path is not None and path == earlier:
            path = None
        record = {}
        if path is not None:
            try:
                record = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = {}
        data = {"record": str(path) if path else None,
                "pulse_status": record.get("pulse_status"),
                "motion_timing": record.get("motion_timing"),
                "start_mm": record.get("actual_start_positions_mm"),
                "end_mm": record.get("actual_end_positions_mm"),
                "source": record.get("source")}
        problems = self.journal.problems_since(mark)
        if problems or record.get("pulse_status") != "completed":
            raise Refusal(" ".join(problems) or "The pulse was {0}. {1}".format(
                record.get("pulse_status") or "not recorded",
                self.journal.last_status_since(mark) or ""), data)

        took = (record.get("motion_timing") or {}).get("endpoint_confirmed_elapsed_s")
        parts = ["pulse complete on pistons {0}: floor {1} to {2} mm{3}".format(
            shown, solitons.BOTTOM_MM, trial.top_mm,
            " in {0:.2f} s".format(took) if took is not None else ""),
            "floor held raised",
            "record " + places.shown(path) if path else "no record was written"]
        return Result(" | ".join(parts), data)

    def soliton_observe(self, crest_rise_mm: Optional[float] = None,
                        crest_to_trough_mm: Optional[float] = None,
                        width_mm: Optional[float] = None,
                        station_mm: Optional[float] = None, notes: str = "",
                        record: Optional[str] = None) -> Result:
        """Add what the water was measured to do to a trial's record.

        Goes on the last pulse this session fired unless ``record`` names
        another. This is the same entry the Soliton tab's dialog makes.
        """
        path = Path(record) if record else self.model.last_soliton_record
        if path is None:
            raise Refusal("No pulse has been fired in this session. Name the "
                          "trial file to add to: --record PATH")
        if not Path(path).is_file():
            raise Refusal("There is no trial record at {0}.".format(path))
        try:
            soliton_records.record_observation(
                Path(path), crest_rise_mm, crest_to_trough_mm, station_mm,
                notes=notes, fwhm_width_mm=width_mm)
        except (ValueError, OSError) as exc:
            raise Refusal(str(exc)) from exc
        said = ["crest rise {0:g} mm".format(crest_rise_mm)
                if crest_rise_mm is not None else None,
                "crest to trough {0:g} mm".format(crest_to_trough_mm)
                if crest_to_trough_mm is not None else None,
                "width {0:g} mm".format(width_mm) if width_mm is not None else None,
                "at {0:g} mm".format(station_mm) if station_mm is not None else None]
        return Result("recorded {0} in {1}".format(
            ", ".join(part for part in said if part), places.shown(path)),
            {"record": str(path)})

    # Named for the state it leaves, so ``status`` can say so.
    def _floor_phrase(self) -> Optional[str]:
        model = self.model
        if model.state is MachineState.HOMED and getattr(self, "_staged_trial", None) \
                is not None and model.soliton_staged:
            return "floor staged for a pulse"
        if model._soliton_floor_raised:
            return "floor held raised"
        return None
