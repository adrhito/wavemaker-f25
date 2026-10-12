"""A whole solitary-wave trial as one command.

Staging, waiting for the water, firing and measuring are four steps that only
mean anything together, and two of them have to happen at the same instant:
the camera must already be recording still water when the pulse goes. Done as
separate commands that instant is left to whoever is typing. Here one job
does all of it, on the session's own clock:

    open the camera and find the rig      (before anything moves)
    stage the floor                       (homes first only if asked to)
    wait for the water                    (a set time, or until the camera
                                           says the surface is still)
    start recording, wait a moment        (so the clip opens on still water)
    fire the pulse
    keep recording while the wave passes
    measure the clip

A camera is optional. Without one this is stage, wait, fire.

The measurement is written beside the clip and reported, and that is all:
it is not entered in the trial's own record unless that is asked for, because
``vision`` has so far been checked only against a drawn tank.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from app import paths, soliton_records
from wavectl import camera, pistons, places, soliton
from wavectl.jobs import Job, Refusal, Result
from wavectl.timing import MEASURE_SECONDS, PREPARE_SECONDS, PULSE_SECONDS

#: The longest the water may be waited for.
MAX_SETTLE_SECONDS = 1800.0
#: The clip must open on at least this much still water: it is what every
#: height in the clip is then measured from.
STILL_SECONDS = 1.0
MIN_PRE_SECONDS = 1.5
MAX_POST_SECONDS = 120.0
#: The water is not called still on the strength of less than this much watching.
MIN_WATCHED_SECONDS = 3.0

class TrialOperations:
    """The trial half of :class:`wavectl.service.Service`."""

    TRIAL_OPERATIONS: Dict[str, str] = {"trial": "trial", "camera": "camera_check"}

    def camera_check(self, camera_source: str, rig: Optional[str] = None,
                     seconds: float = 3.0) -> Result:
        """Open the camera as a trial would, and say what it gives.

        Moves nothing. It is the session process that opens the camera in a
        trial, so this is where to find out that it has not been allowed to,
        or that the rig's markers are out of view, before the floor is staged.
        """
        seconds = min(max(float(seconds), 1.0), 30.0)
        recorder = camera.Recorder(camera_source, rig)
        try:
            seen = recorder.open()
            time.sleep(seconds)
            rate = recorder.rate()
            movement = recorder.restlessness_mm()
            picture = recorder.save_picture(
                places.output_dir() / "camera-{0}.png".format(
                    datetime.now().strftime("%Y%m%d-%H%M%S")))
            failure = recorder.failure()
        except camera.Unavailable as exc:
            raise Refusal(str(exc)) from exc
        finally:
            recorder.close()
        if failure:
            raise Refusal("Camera {0} opened, but {1}.".format(camera_source, failure))
        parts = ["camera {0}: {1} x {2}, {3:.1f} pictures a second".format(
            camera_source, seen["width"], seen["height"], rate)]
        if recorder.rig is not None:
            error = seen.get("marker_error_mm")
            parts.append("rig found, {0}{1:.2f} mm a pixel".format(
                "marker error {0:.2f} mm, ".format(error) if error is not None else "",
                seen["mm_per_pixel"]))
            parts.append("surface moving {0:.2f} mm".format(movement)
                         if movement is not None else "surface not read yet")
        else:
            parts.append("no rig given, so nothing was measured")
        if picture is not None:
            parts.append("picture " + places.shown(picture))
        return Result(" | ".join(parts), dict(
            seen, achieved_fps=round(rate, 2),
            still_movement_mm=round(movement, 3) if movement is not None else None,
            picture=str(picture) if picture is not None else None))

    def trial(self, crest_mm: float, depth_mm: float,
              width_mm: Optional[float] = None, lift_mm: Optional[int] = None,
              settle: float = 60.0, camera_source: Optional[str] = None,
              rig: Optional[str] = None, still_mm: Optional[float] = None,
              pre: float = 2.0, post: float = 8.0,
              station_mm: Optional[float] = None,
              record_observation: bool = False, prepare: bool = False,
              sample_hz: Optional[float] = None, wait: bool = True,
              patience: Optional[float] = None) -> Result:
        """Stage, wait for the water, fire one pulse, and measure it if asked."""
        model = self.model
        settle, pre, post = float(settle), float(pre), float(post)
        if not model.all_motors:
            raise Refusal("Nothing is selected. Choose the floor sections "
                          "first: wavectl select c1-3")
        planned, note = self._build(crest_mm, depth_mm, width_mm, lift_mm)
        if not 0 <= settle <= MAX_SETTLE_SECONDS:
            raise Refusal("--settle is between 0 and {0:.0f} seconds.".format(
                MAX_SETTLE_SECONDS))
        if camera_source is None:
            if rig or still_mm is not None or record_observation:
                raise Refusal("--rig, --still-mm and --record-observation all "
                              "need a camera: add --camera N.")
        else:
            synthetic = str(camera_source) == "synthetic"
            if still_mm is not None and not (rig or synthetic):
                raise Refusal("--still-mm reads the surface, which needs the "
                              "rig file for this camera position: add --rig.")
            if record_observation and not (rig or synthetic):
                raise Refusal("--record-observation needs a measurement, which "
                              "needs the rig file: add --rig.")
            if record_observation and (self.mode != "live" or synthetic):
                raise Refusal("A trial's record takes observations of real "
                              "water only. This is {0}.".format(
                                  "a drawn tank" if synthetic else "the mock"))
            if not MIN_PRE_SECONDS <= pre <= 60:
                raise Refusal("--pre is between {0:g} and 60 seconds: the clip "
                              "has to open on still water.".format(MIN_PRE_SECONDS))
            if not 1 <= post <= MAX_POST_SECONDS:
                raise Refusal("--post is between 1 and {0:.0f} seconds.".format(
                    MAX_POST_SECONDS))
            try:
                camera._vision()
            except camera.Unavailable as exc:
                raise Refusal(str(exc)) from exc
        self._refuse_unless_at_rest("a trial")
        self._refuse_if_driven()
        self._refuse_if_troubled(False)
        if model.needs_homing and not prepare:
            raise Refusal(
                "Pistons {0} are not homed. wavectl prepare boots and homes "
                "them first (about a minute; every selected piston moves), or "
                "add --prepare to do both.".format(pistons.describe(model.live_axes)))

        options = {"settle": settle, "camera": camera_source, "rig": rig,
                   "still_mm": still_mm, "pre": pre, "post": post,
                   "station_mm": station_mm, "sample_hz": sample_hz,
                   "record_observation": record_observation}
        if patience is None:
            patience = (PREPARE_SECONDS + settle + pre + PULSE_SECONDS + post
                        + MEASURE_SECONDS)
        job = self._start_job(
            "trial", lambda job: self._trial_work(job, planned, note, options))
        return self._answer(job, wait, patience)

    # -- the job ---------------------------------------------------------------------

    def _trial_work(self, job: Job, planned, note: Optional[str],
                    options: dict) -> Result:
        model = self.model
        recorder = None
        clip: dict = {}
        seen: dict = {}
        try:
            if options["camera"] is not None:
                recorder = camera.Recorder(options["camera"], options["rig"],
                                           planned.target.water_depth_mm)
                try:
                    seen = recorder.open()
                except camera.Unavailable as exc:
                    raise Refusal("{0} Nothing was moved.".format(exc)) from exc

            self._stage_work(job, planned, note)
            job.reached("staged pistons {0}; firing when the water has "
                        "settled (up to {1:g} s)".format(
                            pistons.describe(model.live_axes), options["settle"]))
            waited, movement = self._settle(planned, recorder, options)

            if recorder is not None:
                target = paths.ANALYTICS_DIR / "vision" / "trial-{0}.mp4".format(
                    datetime.now().strftime("%Y%m%d-%H%M%S"))
                try:
                    recorder.begin(target)
                except camera.Unavailable as exc:
                    raise Refusal("{0} The floor is staged and was not fired."
                                  .format(exc)) from exc
                self._hold_still(planned, options["pre"])

            sent = time.monotonic()
            pulse_note = "trial pulse, pistons {0}, {1}".format(
                pistons.describe(model.live_axes), soliton.describe_trial(planned))
            try:
                fired = self._sampled(options["sample_hz"], pulse_note, lambda: self._fire_work(
                    job, planned, options["station_mm"],
                    before_pulse=recorder.launch if recorder is not None else None))
            except Refusal as exc:
                if recorder is None:
                    raise
                # A pulse that went wrong is the one worth looking at.
                kept = recorder.end()
                raise Refusal("{0} | clip kept at {1}".format(
                    exc, places.shown(kept.get("clip", target))), exc.data) from exc

            if recorder is not None:
                # The wave is still on its way to the camera when the floor stops.
                time.sleep(max(options["post"] - (time.monotonic() - sent), 0.0))
                clip = recorder.end()
                if recorder.failure():
                    clip["failure"] = recorder.failure()
        finally:
            if recorder is not None:
                recorder.close()

        return self._report(planned, fired, recorder, seen, clip, waited,
                            movement, options)

    def _still_staged(self, planned) -> None:
        if not self.model.soliton_ready_for(planned):
            raise Refusal("The trial was abandoned: a stop or an edit cancelled "
                          "the staging while it waited. The pulse was not fired.")

    def _settle(self, planned, recorder, options: dict):
        """Wait for the water. Returns seconds waited and the last movement read."""
        began = time.monotonic()
        wanted = options["still_mm"]
        movement = None
        while True:
            self._still_staged(planned)
            waited = time.monotonic() - began
            if recorder is not None:
                if recorder.failure():
                    raise Refusal("The trial was abandoned: {0}. The floor is "
                                  "staged and was not fired.".format(recorder.failure()))
                movement = recorder.restlessness_mm()
            if (wanted is not None and movement is not None and movement <= wanted
                    and waited >= min(MIN_WATCHED_SECONDS, options["settle"])):
                return waited, movement
            if waited >= options["settle"]:
                break
            time.sleep(0.1)
        if wanted is not None and (movement is None or movement > wanted):
            raise Refusal(
                "The water was still moving after {0:.0f} s: the surface read "
                "{1} against the {2:g} mm asked for. The floor is staged and "
                "was not fired; run the trial again to keep waiting, or allow "
                "more with --still-mm.".format(
                    waited, "{0:.2f} mm".format(movement) if movement is not None
                    else "nothing usable", wanted))
        return waited, movement

    def _hold_still(self, planned, seconds: float) -> None:
        """Let the clip run on still water, staying ready to be stopped."""
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self._still_staged(planned)
            time.sleep(0.05)

    # -- what happened -----------------------------------------------------------------

    def _report(self, planned, fired: Result, recorder, seen: dict, clip: dict,
                waited: float, movement: Optional[float], options: dict) -> Result:
        # The pulse's own answer ends with where its record is; that is said
        # once, last, after what the camera has to add.
        parts = [part for part in fired.text.split(" | ")
                 if not part.startswith("record ")]
        data = dict(fired.data, trial=soliton.trial_record(planned),
                    settled_seconds=round(waited, 1),
                    still_movement_mm=round(movement, 3) if movement is not None else None)
        parts.append("water left {0:.0f} s{1}".format(
            waited, ", moving {0:.2f} mm at the pulse".format(movement)
            if movement is not None else ""))
        failed = None

        if recorder is not None and clip:
            path = Path(clip["clip"])
            asserted = (fired.data.get("motion_timing") or {}).get("run_asserted_utc")
            if asserted and clip.get("first_frame_epoch") is not None:
                clip["pulse_at_clip_s"] = round(
                    datetime.fromisoformat(asserted).timestamp()
                    - clip["first_frame_epoch"], 3)
            warning = camera.timing_note(clip)
            measurement = None
            if recorder.rig is not None and not clip.get("failure"):
                try:
                    measurement = camera.measure(
                        path, recorder.rig, recorder.depth_mm,
                        options["station_mm"], STILL_SECONDS)
                except Exception as exc:  # noqa: BLE001 - the clip is kept either way
                    failed = "the clip could not be measured: {0}".format(exc)
            if clip.get("failure"):
                failed = clip["failure"]

            sidecar = dict(clip, camera=seen, measurement=measurement,
                           measurement_failure=failed, timing_warning=warning,
                           trial=data["trial"], trial_record=fired.data.get("record"),
                           still_movement_mm=data["still_movement_mm"],
                           session=self.mode)
            path.with_suffix(".json").write_text(
                json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            data["clip"] = dict((key, value) for key, value in clip.items()
                                if key != "frame_arrival_s")
            data["measurement"] = measurement
            data["timing_warning"] = warning

            if measurement is not None:
                parts.append(self._measured_phrase(measurement, recorder.synthetic))
                if options["record_observation"]:
                    parts.append(self._enter_observation(fired, measurement, path))
            parts.append("clip {0} ({1} pictures at {2:.1f} a second)".format(
                places.shown(path), clip.get("frames", 0), clip.get("achieved_fps") or 0.0))
            if warning:
                parts.append(warning)

        if fired.data.get("record"):
            parts.append("record " + places.shown(fired.data["record"]))
        if failed:
            raise Refusal("The pulse was fired and the clip kept, but {0} | {1}".format(
                failed, " | ".join(parts)), data)
        return Result(" | ".join(parts), data)

    @staticmethod
    def _measured_phrase(measurement: dict, drawn: bool) -> str:
        said = ["{0} crest {1:.1f} mm".format(
            "drawn tank shows" if drawn else "camera read",
            measurement["crest_rise_mm"])]
        if measurement.get("fwhm_mm") is not None:
            said.append("width {0:.0f} mm".format(measurement["fwhm_mm"]))
        if measurement.get("speed_mm_s") is not None:
            said.append("speed {0:.0f} mm/s".format(measurement["speed_mm_s"]))
        said.append("at {0:.0f} mm".format(measurement["station_mm"]))
        text = ", ".join(said)
        if not measurement.get("crest_seen", True):
            text += " (TOP OF THE WAVE NOT SEEN: the crest height is too low)"
        return text

    def _enter_observation(self, fired: Result, measurement: dict, clip: Path) -> str:
        record = fired.data.get("record")
        if not record:
            return "no trial record to enter the measurement in"
        if not measurement.get("crest_seen", True):
            return "not entered in the record: the top of the wave was not seen"
        try:
            soliton_records.record_observation(
                Path(record), measurement["crest_rise_mm"],
                measurement.get("crest_to_trough_mm"), measurement["station_mm"],
                notes="wavectl trial, camera clip {0}".format(clip.name),
                fwhm_width_mm=measurement.get("fwhm_mm"))
        except (ValueError, OSError) as exc:
            return "not entered in the record: {0}".format(exc)
        return "entered in the record"
