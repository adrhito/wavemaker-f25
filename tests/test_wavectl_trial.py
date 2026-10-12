"""A whole trial as one command: stage, wait, record, fire, measure.

The machine is the moving mock and the camera, where one is used, is the drawn
tank from ``vision.synthetic``. Neither has water in it. What is checked is
that the steps happen in order and at the right moments, and that the drawn
wave -- whose size is known exactly -- is measured correctly out of a clip
this code recorded.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import app.simulator as sim_module
import Model as model_module
from app import solitons, tags
from app.simulator import SimulatedMachine
from Model import Model
from wavectl import camera
from wavectl.jobs import Refusal
from wavectl.service import Service

WAVE = {"crest_mm": 40, "depth_mm": 406}


@pytest.fixture
def service(monkeypatch, tmp_path):
    for name in ("BOOT_PULSE_SECONDS", "CLEAR_FAULT_SECONDS", "HOME_POLL_SECONDS"):
        monkeypatch.setattr(model_module, name, 0.05)
    monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
    monkeypatch.setattr(solitons, "STAGE_SPEED_MM_S", 900)
    monkeypatch.setattr("app.paths.ANALYTICS_DIR", tmp_path / "analytics")
    built = Service(Model(transport=SimulatedMachine(tick=0.005), is_live=False,
                          simulate=True), "mock")
    built.open()
    assert built.handle("select", {"pistons_text": "c1-3"})["ok"]
    yield built
    built.close()


def run_writes(service):
    return [value for tag, value in service.plc.history if tag == tags.RUN_SINGLE]


class TestWithoutACamera:
    def test_it_stages_waits_and_fires(self, service):
        began = time.monotonic()
        reply = service.handle("trial", dict(WAVE, prepare=True, settle=0.5))
        assert reply["ok"], reply["text"]
        assert time.monotonic() - began >= 0.5
        assert "pulse complete on pistons 1-9" in reply["text"]
        assert "water left 1 s" in reply["text"] or "water left 0 s" in reply["text"]
        record = json.loads(Path(reply["data"]["record"]).read_text())
        assert record["pulse_status"] == "completed"
        assert reply["data"]["trial"]["lift_mm"] == record["floor_lift_mm"]

    def test_it_will_not_home_unasked(self, service):
        reply = service.handle("trial", dict(WAVE, settle=0))
        assert not reply["ok"] and "--prepare" in reply["text"]

    def test_a_stop_while_the_water_settles_abandons_the_pulse(self, service):
        assert service.handle("prepare")["ok"]
        started = service.handle("trial", dict(WAVE, settle=30, wait=False))
        assert started["ok"], started["text"]
        # Not waiting answers as soon as the floor starts down. Let it finish
        # staging, so the stop lands in the wait for the water.
        deadline = time.monotonic() + 10
        while "water has settled" not in (service._job.interim or ""):
            assert time.monotonic() < deadline, service._job.interim
            time.sleep(0.02)
        pulses_before = run_writes(service).count(1)
        assert service.handle("stop")["ok"]
        ended = service.handle("wait", {"patience": 10})
        assert not ended["ok"] and "abandoned" in ended["text"]
        # Staging raised Run_1 once to lower the floor; nothing raised it again.
        assert run_writes(service).count(1) == pulses_before

    @pytest.mark.parametrize("extra, said", [
        ({"rig": "rig.json"}, "need a camera"),
        ({"still_mm": 0.5}, "need a camera"),
        ({"settle": 5000}, "--settle"),
        ({"camera_source": "0", "still_mm": 0.5}, "--rig"),
        ({"camera_source": "synthetic", "pre": 0.2}, "--pre"),
        ({"camera_source": "synthetic", "record_observation": True}, "real water"),
    ])
    def test_arguments_that_cannot_work_are_refused_first(self, service, extra, said):
        pytest.importorskip("cv2")
        before = len(service.plc.history)
        reply = service.handle("trial", dict(WAVE, prepare=True, **extra))
        assert not reply["ok"] and said in reply["text"]
        assert len(service.plc.history) == before


class TestTimingNote:
    def test_an_even_clip_has_nothing_to_say(self):
        assert camera.timing_note({"header_fps": 60.0, "achieved_fps": 59.9,
                                   "worst_gap_s": 0.02}) is None

    def test_a_rate_that_drifted_is_said(self):
        note = camera.timing_note({"header_fps": 60.0, "achieved_fps": 54.0,
                                   "worst_gap_s": 0.02})
        assert "54.00 a second" in note

    def test_a_dropped_picture_is_said(self):
        note = camera.timing_note({"header_fps": 60.0, "achieved_fps": 59.9,
                                   "worst_gap_s": 0.1})
        assert "dropped" in note


class _Restless:
    """A camera that never sees the water settle."""

    rig = object()

    def failure(self):
        return None

    def restlessness_mm(self):
        return 5.0


class TestWaitingForTheWater:
    def test_water_that_never_settles_is_not_fired_into(self, service):
        assert service.handle("soliton_stage", dict(WAVE, prepare=True))["ok"]
        planned = service._staged_trial
        with pytest.raises(Refusal) as refused:
            service._settle(planned, _Restless(), {"still_mm": 1.0, "settle": 0.4})
        assert "still moving" in str(refused.value)
        assert "5.00 mm against the 1 mm" in str(refused.value)
        # Still staged, so the trial can simply be run again.
        assert service.model.soliton_ready_for(planned)

    def test_without_a_limit_the_movement_is_only_reported(self, service):
        assert service.handle("soliton_stage", dict(WAVE, prepare=True))["ok"]
        waited, movement = service._settle(
            service._staged_trial, _Restless(), {"still_mm": None, "settle": 0.3})
        assert waited >= 0.3 and movement == 5.0


class TestWithTheDrawnTank:
    def test_a_camera_that_will_not_open_stops_the_trial_before_it_moves(
            self, service, monkeypatch):
        pytest.importorskip("cv2")

        def broken(self):
            raise camera.Unavailable("Camera 7 did not open.")

        monkeypatch.setattr(camera.Recorder, "open", broken)
        before = len(service.plc.history)
        reply = service.handle("trial", dict(WAVE, prepare=True, camera_source="7"))
        assert not reply["ok"]
        assert "Camera 7 did not open. Nothing was moved." in reply["text"]
        assert len(service.plc.history) == before

    def test_the_drawn_wave_is_recorded_and_measured(self, service):
        pytest.importorskip("cv2")
        from vision.synthetic import SyntheticTank

        drawn = SyntheticTank()
        reply = service.handle("trial", {
            "crest_mm": 30, "depth_mm": 150, "prepare": True, "settle": 10,
            "still_mm": 1.0, "camera_source": "synthetic", "pre": 2.0,
            "post": drawn.crest_time_s(1400.0) + 1.5})
        if not reply["ok"] and "cannot write an mp4" in reply["text"]:
            pytest.skip("this OpenCV build cannot write an mp4 to test with")
        assert reply["ok"], reply["text"]

        seen = reply["data"]["measurement"]
        assert seen["crest_seen"] is True
        assert seen["crest_rise_mm"] == pytest.approx(drawn.crest_mm, abs=1.5)
        # Looser than vision's own test of a perfectly even clip: this one was
        # recorded in real time, so its pictures are a few milliseconds uneven.
        assert seen["speed_mm_s"] == pytest.approx(drawn.speed_mm_s, rel=0.05)
        assert seen["fwhm_mm"] == pytest.approx(drawn.fwhm_mm, rel=0.08)
        assert "drawn tank shows crest" in reply["text"]

        clip = reply["data"]["clip"]
        path = Path(clip["clip"])
        assert path.is_file()
        assert path.with_suffix(".surface.npz").is_file()
        assert path.with_suffix(".crest.png").is_file()
        # The clip opens on still water and the pulse goes after it.
        assert clip["pulse_at_clip_s"] == pytest.approx(2.0, abs=0.3)
        assert clip["achieved_fps"] == pytest.approx(clip["header_fps"], rel=0.03)

        beside = json.loads(path.with_suffix(".json").read_text())
        assert len(beside["frame_arrival_s"]) == clip["frames"]
        assert beside["trial_record"] == reply["data"]["record"]
        assert beside["session"] == "mock"

        # Nothing was entered in the trial's own record: the mock has no water.
        record = json.loads(Path(reply["data"]["record"]).read_text())
        assert record["observed_crest_rise_mm"] is None

    def test_the_camera_is_let_go_of_when_the_trial_ends(self, service):
        pytest.importorskip("cv2")
        reply = service.handle("trial", {
            "crest_mm": 30, "depth_mm": 150, "prepare": True, "settle": 0,
            "camera_source": "synthetic", "pre": 1.5, "post": 1.0})
        if not reply["ok"] and "cannot write an mp4" in reply["text"]:
            pytest.skip("this OpenCV build cannot write an mp4 to test with")
        # A wave that had not arrived when recording stopped is a measuring
        # failure, not a failed pulse, and the clip is kept either way.
        assert Path(reply["data"]["clip"]["clip"]).is_file()
        assert not [thread for thread in threading.enumerate()
                    if thread.name == "wavectl camera"]
