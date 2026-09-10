"""Analytics recording, reset, and the startup/shutdown path."""

from __future__ import annotations

from app import paths, tags
from Model import MachineState, RunMode


class TestAnalytics:
    def test_columns_and_samples_cover_the_same_motors(self, homed_model, plc):
        """Header and rows must describe the same pistons.

        The old ``record_positions`` wrote a column heading per motor in
        ``live_motors_sets`` but sampled ``live_motors``, a separate dictionary
        that had been emptied by then -- so the headings and the numbers could
        not line up.
        """
        homed_model.record_analytics = True
        homed_model.analytics_duration = 0.05
        homed_model.analytics_interval = 0.01

        for axis in (0, 1, 2):
            plc.write(tags.axis_field(axis, tags.DEMAND_POSITION), 100 + axis)
            plc.write(tags.axis_field(axis, tags.ACTUAL_POSITION), 90 + axis)

        homed_model.start(RunMode.CONTINUOUS)

        text = paths.analytics_file().read_text(encoding="utf-8")
        header = [line for line in text.splitlines() if "motor" in line][-1]
        for axis in (0, 1, 2):
            assert "motor {0}".format(axis) in header
        # Three pistons -> three demand/actual pairs on a data row.
        data_rows = [
            line for line in text.splitlines() if line and line[0].isdigit()
        ]
        assert data_rows
        assert data_rows[0].count("100") >= 1

    def test_analytics_are_skipped_when_not_requested(self, homed_model):
        homed_model.record_analytics = False
        homed_model.analytics_duration = 0.05
        homed_model.start(RunMode.CONTINUOUS)
        assert not paths.analytics_file().exists()

    def test_a_missing_database_does_not_stop_a_run(self, homed_model, monkeypatch):
        """MongoDB is optional; a run must finish without it."""
        homed_model.record_analytics = True
        homed_model.analytics_duration = 0.02
        homed_model.analytics_interval = 0.01
        monkeypatch.setattr(
            homed_model, "_save_to_database", lambda _s: (_ for _ in ()).throw(
                RuntimeError("no mongo")
            )
        )
        try:
            homed_model.start(RunMode.CONTINUOUS)
        except RuntimeError:
            raise AssertionError("a database failure must not escape the run")


class TestLifecycle:
    def test_reset_clears_sets_and_turns_the_motors_off(self, homed_model, plc):
        assert homed_model.sets
        plc.clear_history()
        homed_model.reset()

        assert homed_model.sets == []
        assert homed_model.state is MachineState.IDLE
        assert homed_model.selected_axes() == []
        assert plc.writes_to(tags.RUN_CONTINUOUS) == [0]
        for axis in range(tags.MOTOR_COUNT):
            assert plc.read(tags.live_motor(axis)) == 0

    def test_reset_restores_default_parameters(self, homed_model):
        homed_model.set_pending_param("Speed 1", 800)
        homed_model.reset()
        from app import params

        assert homed_model.pending_params == params.defaults()

    def test_state_follows_the_set_list(self, model):
        assert model.state is MachineState.IDLE
        model.toggle(0, True)
        motor_set = model.create_set()
        assert model.state is MachineState.READY
        model.remove_set(motor_set)
        assert model.state is MachineState.IDLE

    def test_sets_are_renumbered_after_a_deletion(self, model):
        for axis in (0, 1, 2):
            model.toggle(axis, True)
            model.create_set()
        assert [s.name for s in model.sets] == ["Set 1", "Set 2", "Set 3"]
        model.remove_set(model.sets[0])
        assert [s.name for s in model.sets] == ["Set 1", "Set 2"]

    def test_startup_leaves_the_machine_in_a_known_state(self, model, plc):
        plc.clear_history()
        model.startup()
        assert model.state is MachineState.IDLE
        for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE):
            assert plc.writes_to(tag) == [0]
        assert plc.writes_to(tags.HOME_BUTTON) == [0]

    def test_homing_stops_when_a_stop_is_requested(self, model, plc, monkeypatch):
        """Stop must be able to abort a prepare that is still homing.

        The stop has to arrive *during* homing: ``_prepare_worker`` clears the
        flag when it starts, so setting it beforehand proves nothing.
        """
        model.toggle(0, True)
        model.create_set()
        plc.write(tags.axis_field(0, tags.STATUS_WORD), 0)  # never homes

        polls = {"n": 0}
        motor = model.sets[0].motors[0]

        def poll_then_stop(_plc):
            polls["n"] += 1
            model._stop_requested.set()  # the operator presses Stop
            return False

        monkeypatch.setattr(motor, "is_homed", poll_then_stop)

        model.prepare()

        # It gave up straight away instead of polling out the full timeout.
        assert polls["n"] == 1
        assert model.state is MachineState.READY
        # And the home command bit was left low.
        assert plc.writes_to(tags.HOME_BUTTON)[-1] == 0


class TestStartupWithoutStudio5000:
    """The launcher no longer opens Studio 5000 or waits for a keypress, so the
    application has to cope with being started before the controller is ready."""

    def test_a_missing_plc_falls_back_to_simulation_not_a_crash(self, monkeypatch):
        from app import plc as plc_module
        from Model import Model

        monkeypatch.setattr(plc_module.PlcClient, "connect", lambda self: False)
        model = Model(ip_address="10.255.255.1")
        model._spawn = lambda name, work: work()
        model.startup()

        assert model.is_live is False
        assert model.state is MachineState.IDLE
        assert isinstance(model.plc, plc_module.SimulatedPlc)

    def test_reconnect_picks_the_machine_up_without_a_restart(self, monkeypatch):
        from app import plc as plc_module
        from Model import Model

        attempts = {"n": 0}

        def flaky_connect(self):
            attempts["n"] += 1
            return attempts["n"] > 1  # offline first, online on retry

        monkeypatch.setattr(plc_module.PlcClient, "connect", flaky_connect)
        monkeypatch.setattr(
            plc_module.PlcClient, "identity", lambda self: "Test controller"
        )
        monkeypatch.setattr(plc_module.PlcClient, "write", lambda self, t, v: None)
        monkeypatch.setattr(plc_module.PlcClient, "read", lambda self, t: 0)

        model = Model(ip_address="10.255.255.1")
        model._spawn = lambda name, work: work()
        model.startup()
        assert model.is_live is False

        model.reconnect()
        assert model.is_live is True

    def test_reconnect_is_refused_in_simulate_mode(self):
        from Model import Model

        model = Model(simulate=True)
        model._spawn = lambda name, work: work()
        assert model.reconnect() is False
        assert model.bridge.problems

    def test_the_simulator_reports_an_identity(self, plc):
        assert plc.identity() == "Simulated controller"


class TestConnectionBanner:
    """Simulation on purpose and simulation by accident need different words,
    and only one of them offers Reconnect."""

    def test_simulate_mode_is_distinguishable_from_a_failed_connection(self):
        from Model import Model

        deliberate = Model(simulate=True)
        assert deliberate._simulate is True

        fallback = Model(transport=None, ip_address="10.255.255.1")
        assert fallback._simulate is False

    def test_reconnect_is_refused_and_says_why_in_simulate_mode(self):
        from Model import Model

        model = Model(simulate=True)
        model._spawn = lambda name, work: work()
        assert model.reconnect() is False
        title, message = model.bridge.problems[-1]
        assert "--simulate" in message
