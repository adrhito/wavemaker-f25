"""The mock wavemaker: pistons that actually move, with no hardware."""

from __future__ import annotations

import time

import pytest

from app import patterns, tags
from app.simulator import HOME_POSITION, SimulatedMachine
from Model import MachineState, Model, RunMode


@pytest.fixture
def machine():
    sim = SimulatedMachine(tick=0.005)
    yield sim
    sim.close()


def settle(sim, seconds=0.25):
    """Let the simulated machine advance."""
    time.sleep(seconds)


def make_model(sim, monkeypatch):
    import Model as model_module

    for name in ("BOOT_PULSE_SECONDS", "CLEAR_FAULT_SECONDS", "HOME_POLL_SECONDS"):
        monkeypatch.setattr(model_module, name, 0.05)
    model = Model(transport=sim, is_live=False)
    model._spawn = lambda name, work: work()
    return model


class TestMotion:
    def test_pistons_move_when_told_to_run(self, machine):
        machine.write(tags.live_motor(0), 1)
        machine.write(tags.motor_field(0, "Pos_1"), 0)
        machine.write(tags.motor_field(0, "Pos_2"), 300)
        machine.write(tags.motor_field(0, "Spd_1"), 400)
        machine.write(tags.motor_field(0, "Spd_2"), 400)

        before = machine.snapshot()[0]
        machine.write(tags.RUN_CONTINUOUS, 1)
        settle(machine, 0.3)
        after = machine.snapshot()[0]

        assert after != before
        assert 0 <= after <= 300

    def test_a_piston_not_in_a_set_stays_put(self, machine):
        machine.write(tags.live_motor(0), 1)
        machine.write(tags.live_motor(1), 0)
        for axis in (0, 1):
            machine.write(tags.motor_field(axis, "Pos_2"), 300)
            machine.write(tags.motor_field(axis, "Spd_1"), 400)

        machine.write(tags.RUN_CONTINUOUS, 1)
        settle(machine, 0.3)
        snap = machine.snapshot()

        assert snap[0] != HOME_POSITION
        assert snap[1] == HOME_POSITION

    def test_stopping_halts_the_pistons(self, machine):
        machine.write(tags.live_motor(0), 1)
        machine.write(tags.motor_field(0, "Pos_2"), 300)
        machine.write(tags.motor_field(0, "Spd_1"), 400)
        machine.write(tags.RUN_CONTINUOUS, 1)
        settle(machine, 0.2)

        machine.write(tags.RUN_CONTINUOUS, 0)
        settle(machine, 0.1)
        first = machine.snapshot()[0]
        settle(machine, 0.2)
        assert machine.snapshot()[0] == first

    def test_actual_position_is_published_where_the_display_reads_it(self, machine):
        machine.write(tags.live_motor(0), 1)
        machine.write(tags.motor_field(0, "Pos_2"), 300)
        machine.write(tags.motor_field(0, "Spd_1"), 400)
        machine.write(tags.RUN_CONTINUOUS, 1)
        settle(machine, 0.3)

        published = machine.read(tags.axis_field(0, tags.ACTUAL_POSITION))
        assert abs(published - machine.snapshot()[0]) <= 1


class TestHoming:
    def test_homing_reports_the_homed_bit(self, machine, monkeypatch):
        import app.simulator as sim_module

        monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
        machine.write(tags.live_motor(0), 1)
        machine.write(tags.HOME_BUTTON, 1)
        settle(machine, 0.3)

        assert machine.read(tags.axis_field(0, tags.STATUS_WORD)) == (1 << 11)

    def test_clearing_faults_forgets_that_it_was_homed(self, machine, monkeypatch):
        import app.simulator as sim_module

        monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
        machine.write(tags.live_motor(0), 1)
        machine.write(tags.HOME_BUTTON, 1)
        settle(machine, 0.3)
        machine.write(tags.HOME_BUTTON, 0)

        machine.write(tags.CLEAR_MOTOR_ERROR, 1)
        settle(machine, 0.1)
        assert machine.read(tags.axis_field(0, tags.STATUS_WORD)) == 0


class TestTravellingWave:
    def test_a_staggered_offset_makes_the_wave_travel(self, machine, monkeypatch):
        """The reason the mock exists: a front-to-back stagger must visibly
        reach the back of the chamber later than the front."""
        import app.simulator as sim_module

        monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)

        for axis in range(30):
            machine.write(tags.live_motor(axis), 1)
            machine.write(tags.motor_field(axis, "Pos_1"), 0)
            machine.write(tags.motor_field(axis, "Pos_2"), 300)
            machine.write(tags.motor_field(axis, "Spd_1"), 300)
            machine.write(tags.motor_field(axis, "Spd_2"), 300)

        result = patterns.build(
            list(range(30)), "Curve Offset", patterns.STAGGER, start=0, step=20
        )
        for axis, value in result.values.items():
            machine.write(tags.curve_field(axis, "CurveOffset"), value)

        machine.write(tags.RUN_CONTINUOUS, 1)
        settle(machine, 0.5)

        snap = machine.snapshot()
        front = snap[0]    # column 1, no delay
        back = snap[27]    # column 10, the longest delay

        assert front > back, "the front should be ahead of the back"
        assert back == HOME_POSITION, "the back should not have started yet"

    def test_without_a_stagger_the_array_moves_together(self, machine):
        for axis in range(30):
            machine.write(tags.live_motor(axis), 1)
            machine.write(tags.motor_field(axis, "Pos_2"), 300)
            machine.write(tags.motor_field(axis, "Spd_1"), 300)
            machine.write(tags.curve_field(axis, "CurveOffset"), 0)

        machine.write(tags.RUN_CONTINUOUS, 1)
        settle(machine, 0.3)

        snap = machine.snapshot()
        assert max(snap.values()) - min(snap.values()) < 5


class TestThroughTheApplication:
    def test_prepare_then_run_works_end_to_end(self, machine, monkeypatch):
        import app.simulator as sim_module

        monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
        model = make_model(machine, monkeypatch)

        for axis in range(6):
            model.toggle(axis, True)
        motor_set = model.create_set()
        motor_set.set_param("Position 2", 300)
        motor_set.set_param("Speed 1", 400)

        assert model.prepare() is True
        assert model.state is MachineState.HOMED

        model.start(RunMode.CONTINUOUS)
        assert model.state is MachineState.RUNNING
        settle(machine, 0.3)
        assert any(machine.snapshot()[a] != HOME_POSITION for a in range(6))

        model.stop()
        assert model.state is MachineState.HOMED

    def test_the_mock_identifies_itself_as_a_mock(self, machine):
        assert "mock" in machine.identity().lower()

    def test_connect_with_simulate_returns_the_moving_mock(self):
        from app import plc as plc_module

        transport, is_live = plc_module.connect("1.2.3.4", 1, simulate=True)
        try:
            assert is_live is False
            assert isinstance(transport, SimulatedMachine)
        finally:
            transport.close()


class TestMockAndMachineAreTheSameApplication:
    """The mock must never drift from the real thing.

    There is one application; --mock only swaps the transport. These guard that,
    so a change made for the machine cannot quietly miss the mock or vice versa.
    """

    def test_both_launchers_run_the_same_entry_point(self):
        import io
        import os
        import re

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        commands = {}
        for name in ("Open Wavemaker.cmd", "Mock Wavemaker (no machine).cmd"):
            text = io.open(os.path.join(here, name), encoding="utf-8").read()
            commands[name] = set(re.findall(r'main\.py"(.*?)(?:\r?\n)', text))

        real = commands["Open Wavemaker.cmd"]
        mock = commands["Mock Wavemaker (no machine).cmd"]
        assert real, "the real launcher must invoke main.py"
        assert mock, "the mock launcher must invoke main.py"
        # The only difference between them is the --mock flag.
        assert all("--mock" in line for line in mock)
        assert not any("--mock" in line for line in real)

    def test_mock_and_live_take_the_same_run_path(self, machine, monkeypatch):
        """Start behaves identically; only the transport differs."""
        import app.simulator as sim_module
        from app.plc import SimulatedPlc

        monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)

        results = {}
        for label, transport in (("mock", machine), ("live", SimulatedPlc())):
            model = make_model(transport, monkeypatch)
            model.is_live = label == "live"
            for axis in range(3):
                model.toggle(axis, True)
            if label == "live":
                for axis in range(30):
                    transport.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
            model.run(RunMode.SINGLE)
            results[label] = model.state

        assert results["mock"] == results["live"] == MachineState.HOMED

    def test_parking_happens_in_the_mock_too(self, machine, monkeypatch):
        """Stop returns the pistons to the bottom whichever transport is used."""
        import Model as model_module
        import app.simulator as sim_module

        monkeypatch.setattr(sim_module, "HOME_SECONDS", 0.05)
        # 368 mm at the real 200 mm/s takes nearly two seconds; drive it faster
        # so the test is quick without changing what is being checked.
        monkeypatch.setattr(model_module, "PARK_SPEED", 3000)
        monkeypatch.setattr(model_module, "PARK_SECONDS", 0.5)
        model = make_model(machine, monkeypatch)
        for axis in range(3):
            model.toggle(axis, True)
        model.sets[0].set_param("Position 2", 300)
        model.sets[0].set_param("Speed 1", 400)
        model.run(RunMode.CONTINUOUS)
        settle(machine, 0.2)
        model.stop()
        settle(machine, 0.7)

        for axis in range(3):
            assert abs(machine.snapshot()[axis] - model_module.PARK_POSITION) < 5
