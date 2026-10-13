"""Recording piston positions to a file."""

from __future__ import annotations

import time

import pytest

import app.simulator as sim_module
import Model as model_module
from app import params, tags
from app.plc import PlcError
from app.simulator import SimulatedMachine
from Model import Model
from wavectl import pistons
from wavectl.jobs import Refusal, Result
from wavectl.sampling import Sampler
from wavectl.service import Service


def read_csv(path):
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if not line.startswith("#")]
    header = lines[0].split(",")
    rows = [line.split(",") for line in lines[1:]]
    return header, rows


class TestSampler:
    def test_columns_are_display_numbers_in_counting_order(self, plc, tmp_path):
        for shown, mm in ((1, 10.0), (2, 20.0), (3, 30.0)):
            plc.write(tags.axis_field(tags.axis_from_display(shown),
                                      tags.ACTUAL_POSITION), params.to_counts(mm))
        sampler = Sampler(plc, pistons.parse("3,1,2"), hz=50).start()
        time.sleep(0.2)
        sampler.stop()
        header, rows = read_csv(sampler.save(tmp_path / "s.csv"))
        assert header == ["t_s", "p1", "p2", "p3"]
        assert len(rows) >= 5
        assert [float(value) for value in rows[0][1:]] == [10.0, 20.0, 30.0]
        assert float(rows[-1][0]) > float(rows[0][0])

    def test_a_piston_that_cannot_be_read_leaves_a_gap_not_a_crash(self, plc,
                                                                    monkeypatch,
                                                                    tmp_path):
        broken = tags.axis_field(tags.axis_from_display(2), tags.ACTUAL_POSITION)
        read = plc.read

        def flaky(tag):
            if tag == broken:
                raise PlcError("no answer")
            return read(tag)

        monkeypatch.setattr(plc, "read", flaky)
        sampler = Sampler(plc, pistons.parse("1-3"), hz=50).start()
        time.sleep(0.1)
        sampler.stop()
        path = sampler.save(tmp_path / "s.csv")
        _header, rows = read_csv(path)
        assert rows[0][2] == ""
        assert "pistons 2 unreadable" in sampler.summary(path)

    def test_the_rate_is_capped(self, plc):
        assert Sampler(plc, [0], hz=1000).hz == 50.0


    def test_files_go_where_wavectl_data_says(self, plc, tmp_path, monkeypatch):
        from wavectl import places

        monkeypatch.setenv("WAVECTL_DATA", str(tmp_path / "elsewhere"))
        sampler = Sampler(plc, [0], hz=50).start()
        time.sleep(0.05)
        sampler.stop()
        assert sampler.save().parent == tmp_path / "elsewhere" / "logs" / "wavectl"
        assert places.output_dir() == tmp_path / "elsewhere" / "logs" / "wavectl"


class TestWatch:
    def test_nothing_to_watch_is_refused(self, model):
        reply = Service(model, "mock").handle("watch", {"seconds": 0.1})
        assert not reply["ok"] and "--pistons" in reply["text"]

    def test_any_piston_may_be_watched_selected_or_not(self, model, plc):
        reply = Service(model, "mock").handle(
            "watch", {"seconds": 0.2, "hz": 50, "pistons_text": "28-30"})
        assert reply["ok"], reply["text"]
        assert reply["data"]["samples"] >= 5
        assert plc.history == []          # reads only

    def test_a_refusal_keeps_its_samples(self, model):
        service = Service(model, "mock")
        service.handle("select", {"pistons_text": "1-3"})

        def fail() -> Result:
            time.sleep(0.1)
            raise Refusal("it went wrong")

        with pytest.raises(Refusal) as refused:
            service._sampled(50, "a run", fail)
        assert "it went wrong | " in str(refused.value)
        assert refused.value.data["samples"]["samples"] >= 2


class TestSamplingARun:
    def test_one_stroke_is_recorded_from_start_to_finish(self, monkeypatch):
        for name in ("BOOT_PULSE_SECONDS", "CLEAR_FAULT_SECONDS", "HOME_POLL_SECONDS"):
            monkeypatch.setattr(model_module, name, 0.05)
        monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
        service = Service(Model(transport=SimulatedMachine(tick=0.005),
                                is_live=False, simulate=True), "mock")
        service.open()
        try:
            service.handle("select", {"pistons_text": "1-3"})
            service.handle("set", {"values": {"stroke": "100", "speed": "500"}})
            assert service.handle("prepare")["ok"]
            reply = service.handle("run", {"mode": "single", "sample_hz": 50})
            assert reply["ok"], reply["text"]
            samples = reply["data"]["samples"]
            assert set(samples["travel_mm"]) == {"1", "2", "3"}
            assert all(90 <= travel <= 101 for travel in samples["travel_mm"].values())
            assert "travel" in reply["text"] and "samples-" in reply["text"]
        finally:
            service.close()
