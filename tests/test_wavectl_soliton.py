"""Staging and firing a solitary-wave pulse through wavectl.

Run against the moving mock, which has no water in it: these check what is
commanded and recorded, never the wave.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.simulator as sim_module
import Model as model_module
from app import solitons, tags
from app.simulator import SimulatedMachine
from app.solitons import SolitaryTarget
from Model import Model
from wavectl import soliton
from wavectl.service import Service

WAVE = {"crest_mm": 40, "depth_mm": 406}


@pytest.fixture
def service(monkeypatch, tmp_path):
    """A session on the moving mock with the front three columns chosen."""
    for name in ("BOOT_PULSE_SECONDS", "CLEAR_FAULT_SECONDS", "HOME_POLL_SECONDS"):
        monkeypatch.setattr(model_module, name, 0.05)
    monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
    # Staging lowers the floor 370 mm at a gentle 200 mm/s; hurry it.
    monkeypatch.setattr(solitons, "STAGE_SPEED_MM_S", 900)
    monkeypatch.setattr("app.paths.ANALYTICS_DIR", tmp_path / "analytics")
    built = Service(Model(transport=SimulatedMachine(tick=0.005), is_live=False,
                          simulate=True), "mock")
    built.open()
    assert built.handle("select", {"pistons_text": "c1-3"})["ok"]
    yield built
    built.close()


def floor(service):
    snapshot = service.plc.snapshot()
    return [snapshot[axis] for axis in service.model.live_axes]


class TestPlanning:
    def test_width_and_lift_are_the_tabs_automatic_values(self):
        trial, note = soliton.build_trial(40, 406, piston_count=9)
        theory = SolitaryTarget(40, 1, 406).theoretical_width_mm
        assert trial.target.width_mm == round(theory)
        assert trial.floor_lift_mm == round(
            solitons.volume_lift_mm(trial.target, 9))
        assert note is None

    def test_too_few_pistons_is_limited_and_said(self):
        trial, note = soliton.build_trial(80, 406, piston_count=3)
        assert trial.floor_lift_mm == solitons.MAX_TRIAL_LIFT_MM
        assert "select more pistons" in note

    def test_plan_moves_nothing(self, service):
        before = len(service.plc.history)
        reply = service.handle("soliton_plan", WAVE)
        assert reply["ok"]
        assert "crest 40 mm in 406 mm of water" in reply["text"]
        assert reply["data"]["floor_end_mm"] == 370 - reply["data"]["lift_mm"]
        assert len(service.plc.history) == before

    def test_the_lift_needs_a_selection_to_be_worked_out(self, service):
        service.handle("select", {"pistons_text": "none"})
        reply = service.handle("soliton_plan", WAVE)
        assert not reply["ok"] and "Select the pistons first" in reply["text"]

    def test_a_lift_past_the_travel_is_refused(self, service):
        reply = service.handle("soliton_plan", dict(WAVE, lift_mm=500))
        assert not reply["ok"] and "between 1 and 390" in reply["text"]


class TestStagingAndFiring:
    def test_staging_will_not_home_unasked(self, service):
        reply = service.handle("soliton_stage", WAVE)
        assert not reply["ok"] and "--prepare" in reply["text"]
        assert tags.MOTOR_BOOT not in [tag for tag, _value in service.plc.history]

    def test_fire_needs_a_staging(self, service):
        reply = service.handle("soliton_fire")
        assert not reply["ok"] and "wavectl soliton stage" in reply["text"]

    def test_stage_then_fire_makes_one_recorded_pulse(self, service):
        staged = service.handle("soliton_stage", dict(WAVE, prepare=True))
        assert staged["ok"], staged["text"]
        assert all(abs(where - 370) <= 2 for where in floor(service))
        assert "floor staged for a pulse" in service.handle("status")["text"]

        fired = service.handle("soliton_fire", {"station_mm": 1500})
        assert fired["ok"], fired["text"]
        top = staged["data"]["floor_end_mm"]
        assert all(abs(where - top) <= 2 for where in floor(service))
        assert "floor held raised" in service.handle("status")["text"]

        record = json.loads(Path(fired["data"]["record"]).read_text())
        assert record["pulse_status"] == "completed"
        assert record["source"] == "simulator"
        assert record["display_pistons"] and record["floor_end_mm"] == top
        assert record["planned_measurement_station_mm"] == 1500
        assert record["motion_timing"]["run_asserted_utc"]

        again = service.handle("soliton_fire")
        assert not again["ok"] and "Nothing is staged" in again["text"]

    def test_an_edit_after_staging_cancels_it(self, service):
        assert service.handle("soliton_stage", dict(WAVE, prepare=True))["ok"]
        assert service.handle("set", {"values": {"speed": "300"}})["ok"]
        reply = service.handle("soliton_fire")
        assert not reply["ok"] and "Nothing is staged" in reply["text"]

    def test_a_stop_after_the_pulse_leaves_the_floor_up(self, service):
        staged = service.handle("soliton_stage", dict(WAVE, prepare=True))
        assert service.handle("soliton_fire")["ok"]
        assert service.handle("stop")["ok"]
        top = staged["data"]["floor_end_mm"]
        assert all(abs(where - top) <= 2 for where in floor(service))

    def test_the_pulse_can_be_sampled(self, service):
        staged = service.handle("soliton_stage", dict(WAVE, prepare=True))
        fired = service.handle("soliton_fire", {"sample_hz": 50})
        assert fired["ok"], fired["text"]
        travel = fired["data"]["samples"]["travel_mm"]
        assert len(travel) == 9
        assert all(abs(seen - staged["data"]["lift_mm"]) <= 3 for seen in travel.values())


    def test_parking_after_a_pulse_ends_floor_held_raised(self, service):
        assert service.handle("soliton_stage", dict(WAVE, prepare=True))["ok"]
        assert service.handle("soliton_fire")["ok"]
        assert "floor held raised" in service.handle("status")["text"]
        assert service.handle("park")["ok"]
        assert "floor held raised" not in service.handle("status")["text"]

    def test_a_refused_pulse_does_not_answer_with_the_last_ones_record(self, service):
        assert service.handle("soliton_stage", dict(WAVE, prepare=True))["ok"]
        first = service.handle("soliton_fire")
        assert first["ok"]
        assert service.handle("soliton_stage", WAVE)["ok"]
        # Knock the floor off its staged position, so the model refuses to fire.
        for a in service.model.live_axes:
            service.plc.place(a, 300.0)
        second = service.handle("soliton_fire")
        assert not second["ok"]
        assert second["data"].get("record") != first["data"]["record"]
        assert second["data"].get("pulse_status") != "completed"


class TestObservations:
    def test_a_measurement_goes_on_the_last_pulse(self, service):
        assert service.handle("soliton_stage", dict(WAVE, prepare=True))["ok"]
        fired = service.handle("soliton_fire")
        reply = service.handle("soliton_observe", {
            "crest_rise_mm": 31.5, "width_mm": 700, "station_mm": 1500,
            "notes": "by eye"})
        assert reply["ok"], reply["text"]
        record = json.loads(Path(fired["data"]["record"]).read_text())
        assert record["observed_crest_rise_mm"] == 31.5
        assert record["observed_fwhm_width_mm"] == 700
        assert record["status"] == "water_observation_recorded"

    def test_an_observation_needs_a_height(self, service):
        assert service.handle("soliton_stage", dict(WAVE, prepare=True))["ok"]
        assert service.handle("soliton_fire")["ok"]
        reply = service.handle("soliton_observe", {"station_mm": 1500})
        assert not reply["ok"] and "at least one observed water height" in reply["text"]

    def test_with_no_pulse_it_asks_which_record(self, service):
        reply = service.handle("soliton_observe", {"crest_rise_mm": 30})
        assert not reply["ok"] and "--record" in reply["text"]
