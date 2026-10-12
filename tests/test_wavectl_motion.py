"""Preparing, running and stopping through wavectl.

The refusals are checked against the plain tag store, where nothing can move
and every write is on record. The runs themselves are checked against the
moving mock, on real threads, because what is being tested is the waiting.
"""

from __future__ import annotations

import time

import pytest

import app.simulator as sim_module
import Model as model_module
from app import tags
from app.simulator import SimulatedMachine
from Model import MachineState, Model
from wavectl import cli, pistons, sessionfile
from wavectl.service import Service


def axis(shown: int) -> int:
    return tags.axis_from_display(shown)


@pytest.fixture
def moving(monkeypatch):
    """A session on the moving mock, sped up, with three pistons chosen."""
    for name in ("BOOT_PULSE_SECONDS", "CLEAR_FAULT_SECONDS", "HOME_POLL_SECONDS"):
        monkeypatch.setattr(model_module, name, 0.05)
    monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
    # The resting move is 370 mm at a deliberately gentle 200 mm/s. Hurry it,
    # so a stop is over in a moment without changing what is being checked.
    monkeypatch.setattr(model_module, "PARK_SPEED", 3000)
    sim = SimulatedMachine(tick=0.005)
    service = Service(Model(transport=sim, is_live=False, simulate=True), "mock")
    service.open()
    assert service.handle("select", {"pistons_text": "1-3"})["ok"]
    assert service.handle("set", {"values": {"stroke": "100", "speed": "900"}})["ok"]
    yield service
    service.close()


@pytest.fixture
def still(homed_model) -> Service:
    """A session on the plain tag store, with pistons 28-30 homed."""
    return Service(homed_model, "live")


class TestChecksBeforeMotion:
    def test_run_names_prepare_when_the_pistons_are_not_homed(self, model, plc):
        service = Service(model, "live")
        service.handle("select", {"pistons_text": "1-3"})
        plc.clear_history()
        reply = service.handle("run")
        assert not reply["ok"]
        assert "not homed" in reply["text"] and "wavectl prepare" in reply["text"]
        assert plc.history == []

    def test_nothing_selected_is_said_plainly(self, model):
        reply = Service(model, "live").handle("prepare")
        assert not reply["ok"] and "wavectl select" in reply["text"]

    def test_a_run_bit_set_elsewhere_stops_the_command(self, still, plc):
        plc.write(tags.RUN_CONTINUOUS, 1)
        plc.clear_history()
        reply = still.handle("run", {"seconds": 5})
        assert not reply["ok"]
        assert "Run_2 high" in reply["text"] and "something else" in reply["text"]
        assert plc.history == []

    def test_a_drive_fault_stops_the_command(self, still, plc):
        plc.write(tags.axis_field(1, tags.WARN_WORD), 1 << 6)
        plc.clear_history()
        reply = still.handle("run", {"seconds": 5})
        assert not reply["ok"]
        assert "piston {0}: Controller Hot".format(tags.display_number(1)) in reply["text"]
        assert "--ignore-warnings" in reply["text"]
        assert plc.history == []

    def test_a_selection_changed_on_the_controller_stops_the_command(self, still, plc):
        plc.write(tags.live_motor(axis(5)), 1)
        plc.clear_history()
        reply = still.handle("run", {"seconds": 5})
        assert not reply["ok"] and "something else has written" in reply["text"]
        assert plc.history == []

    @pytest.mark.parametrize("seconds", [0, -1, 3601])
    def test_a_run_has_to_have_an_end(self, still, seconds):
        reply = still.handle("run", {"seconds": seconds})
        assert not reply["ok"] and "3600" in reply["text"]

    def test_live_changes_need_a_run(self, still):
        reply = still.handle("speed", {"value": 300})
        assert not reply["ok"] and "wavectl set speed=300" in reply["text"]


class TestStoppingAtRest:
    def test_stopping_a_still_machine_moves_nothing(self, still, plc):
        plc.clear_history()
        reply = still.handle("stop")
        assert reply["ok"]
        assert plc.writes_to(tags.RUN_SINGLE) == [0]
        assert plc.writes_to(tags.RUN_CONTINUOUS) == [0]

    def test_stop_now_drops_the_bits_and_leaves_the_pistons(self, still, plc, model):
        model._set_state(MachineState.RUNNING)
        plc.clear_history()
        reply = still.handle("stop", {"now": True})
        assert reply["ok"] and model.state is MachineState.HOMED
        # A resting move would raise Run_1; this must not.
        assert 1 not in plc.writes_to(tags.RUN_SINGLE)

    def test_a_stop_the_controller_refuses_is_shouted(self, still, plc, monkeypatch):
        from app.plc import PlcError

        def refuse(tag, value):
            raise PlcError("no route to host")

        monkeypatch.setattr(plc, "write", refuse)
        reply = still.handle("stop", {"now": True})
        assert not reply["ok"] and reply["text"].startswith("STOP FAILED")
        assert "physical stop" in reply["text"]

    def test_wait_with_nothing_started(self, still):
        assert still.handle("wait")["text"].startswith("nothing has been started")


class TestWhereThePistonsCameToRest:
    """On the array a piston finishes its leg after the run bits drop."""

    def test_stop_now_waits_for_them_and_says_how_much_further_they_went(
            self, still, model, monkeypatch):
        coasting = iter([[40.0] * 3, [48.0] * 3, [55.0] * 3, [59.0] * 3])
        monkeypatch.setattr(still, "_positions", lambda: next(coasting, [60.0] * 3))
        model._set_state(MachineState.RUNNING)
        reply = still.handle("stop", {"now": True})
        assert reply["ok"], reply["text"]
        assert ("pistons at 60 mm, after finishing the leg they were on "
                "(up to 20 mm more)") in reply["text"]
        assert "run bits clear" in reply["text"]
        assert reply["data"]["moved_after_stop_mm"] == 20.0
        assert reply["data"]["standing_still"] is True

    def test_pistons_that_never_stop_are_shouted(self, still, model, monkeypatch):
        import itertools

        from wavectl import motion

        monkeypatch.setattr(motion, "STILL_SECONDS", 0.5)
        climbing = itertools.count()
        monkeypatch.setattr(still, "_positions",
                            lambda: [float(next(climbing))] * 3)
        model._set_state(MachineState.RUNNING)
        reply = still.handle("stop", {"now": True})
        assert reply["ok"]            # the run bits did drop
        assert "STILL MOVING" in reply["text"] and "physical stop" in reply["text"]
        assert reply["data"]["standing_still"] is False

    def test_a_position_just_above_zero_is_not_minus_zero(self):
        from wavectl.motion import _mm

        assert _mm(-0.03) == "0" and _mm(59.6) == "60" and _mm(-17.6) == "-18"


class TestOnTheMovingMock:
    def test_prepare_homes_and_says_where_the_pistons_are(self, moving):
        reply = moving.handle("prepare")
        assert reply["ok"], reply["text"]
        assert reply["text"].startswith("homed pistons 1-3")
        assert moving.model.state is MachineState.HOMED

    def test_one_stroke_reports_how_far_the_pistons_went(self, moving):
        assert moving.handle("prepare")["ok"]
        reply = moving.handle("run", {"mode": "single"})
        assert reply["ok"], reply["text"]
        assert "Ran a stroke. Furthest piston moved" in reply["text"]

    def test_a_continuous_run_stops_itself(self, moving):
        assert moving.handle("prepare")["ok"]
        began = time.monotonic()
        reply = moving.handle("run", {"seconds": 0.6, "wait": True})
        assert reply["ok"], reply["text"]
        assert 0.6 <= time.monotonic() - began < 15
        assert reply["text"].startswith("ran pistons 1-3 for 1 s")
        assert reply["data"]["timed_out"] and reply["data"]["bits_high"] == []
        assert moving.model.state is MachineState.HOMED
        assert moving.plc.read(tags.RUN_CONTINUOUS) == 0
        # Stopped the way the window stops: at rest at the bottom of travel.
        assert all(abs(where - model_module.PARK_POSITION) < 8
                   for a, where in moving.plc.snapshot().items()
                   if a in moving.model.live_axes)

    def test_not_waiting_answers_once_it_is_running(self, moving):
        assert moving.handle("prepare")["ok"]
        reply = moving.handle("run", {"seconds": 30})
        assert reply["ok"] and "then stopping by itself" in reply["text"]
        assert moving.model.state is MachineState.RUNNING
        assert "s left" in moving.handle("status")["text"]

        again = moving.handle("run", {"seconds": 5})
        assert not again["ok"] and "wavectl stop" in again["text"]

        stopped = moving.handle("stop")
        assert stopped["ok"], stopped["text"]
        assert moving.plc.read(tags.RUN_CONTINUOUS) == 0
        ended = moving.handle("wait")
        assert ended["ok"] and "was stopped after" in ended["text"]

    def test_speed_and_stroke_change_on_a_run_that_is_going(self, moving):
        assert moving.handle("prepare")["ok"]
        assert moving.handle("run", {"seconds": 30})["ok"]
        assert moving.handle("speed", {"value": 300})["ok"]
        assert set(m.write_params["Speed 2"] for m in moving.model.all_motors) == {300}
        assert moving.handle("stroke", {"value": 60})["ok"]
        assert moving.handle("stop", {"now": True})["ok"]
        moving.handle("wait")

    def test_run_can_be_told_to_prepare_first(self, moving):
        reply = moving.handle("run", {"mode": "single", "prepare": True})
        assert reply["ok"], reply["text"]
        assert moving.model.state is MachineState.HOMED

    def test_park_moves_a_standing_selection_to_rest(self, moving):
        assert moving.handle("prepare")["ok"]
        reply = moving.handle("park")
        assert reply["ok"], reply["text"]
        assert "Pistons are at the bottom" in reply["text"]
        assert all(abs(where - model_module.PARK_POSITION) < 8
                   for a, where in moving.plc.snapshot().items()
                   if a in moving.model.live_axes)

    def test_park_after_an_edit_does_not_home_again(self, moving):
        assert moving.handle("prepare")["ok"]
        assert moving.handle("set", {"values": {"speed": "400"}})["ok"]
        boots = len(moving.plc.writes_to(tags.MOTOR_BOOT))
        assert moving.handle("park")["ok"]
        assert len(moving.plc.writes_to(tags.MOTOR_BOOT)) == boots

    def test_calibrating_everything_saves_homing_each_selection(self, moving):
        reply = moving.handle("calibrate")
        assert reply["ok"], reply["text"]
        assert reply["text"] == "All 30 pistons are calibrated."
        moving.handle("select", {"pistons_text": "10-12"})
        assert "ready, still homed" in moving.handle("status")["text"]
        boots = len(moving.plc.writes_to(tags.MOTOR_BOOT))
        assert moving.handle("run", {"mode": "single"})["ok"]
        assert len(moving.plc.writes_to(tags.MOTOR_BOOT)) == boots

    def test_a_dropped_piston_is_reported_once(self, moving):
        moving.plc.place(axis(2), 200.0)
        moving.plc.wear(axis(2), 0.0)
        assert "would not home" in moving.handle("prepare")["text"]
        assert "would not home" not in moving.handle("prepare")["text"]

    def test_a_piston_that_will_not_home_is_dropped_and_named(self, moving):
        # A seized piston never reaches the home position, so never homes.
        moving.plc.place(axis(2), 200.0)
        moving.plc.wear(axis(2), 0.0)
        reply = moving.handle("prepare")
        assert reply["ok"], reply["text"]
        assert "piston(s) 2 would not home and were dropped" in reply["text"]
        assert pistons.shown(moving.model.live_axes) == [1, 3]


@pytest.fixture
def session(capsys):
    assert cli.main(["session", "start", "--mock", "--fast"]) == 0
    capsys.readouterr()
    yield
    cli.main(["session", "stop", "--force"])


class TestFromTheCommandLine:
    def test_a_whole_sitting(self, session, capsys):
        def said(*words) -> str:
            status = cli.main(list(words))
            out = capsys.readouterr().out.strip()
            assert status == 0, out
            return out

        said("select", "1-3")
        said("set", "stroke=100", "speed=900")
        assert cli.main(["run"]) == 1
        assert "wavectl prepare" in capsys.readouterr().out

        assert said("prepare").startswith("mock: homed pistons 1-3")
        assert "Ran a stroke" in said("run", "--single")
        assert "then stopping by itself" in said("run", "--seconds", "20")
        assert "running continuous" in said("status")
        assert "homed" in said("stop")
        assert "was stopped after" in said("wait")

    def test_ending_the_session_stops_a_run(self, session, capsys):
        for words in (["select", "1-3"], ["prepare"], ["run", "--seconds", "60"]):
            assert cli.main(words) == 0
        capsys.readouterr()
        assert cli.main(["session", "stop"]) == 0
        assert sessionfile.read() is None
