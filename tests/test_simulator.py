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
