"""Tests for behaviour that could move the machine unexpectedly.

Each test here corresponds to a defect that was present in the shipped code.
They are the ones worth running before any change reaches the lab.
"""

from __future__ import annotations



from app import tags
from app.plc import PlcError
from Model import MachineState, RunMode


class TestStopNeverStarts:
    """Stop must only ever write zeroes."""

    def test_stop_never_commands_continuous_motion(self, homed_model, plc):
        """The old stop path wrote Run_2=1 and then Run_2=0.

        ``motion(2, tracker=1)`` was used for both starting and stopping
        continuous motion, and unconditionally wrote 1 before checking the
        tracker.  Pressing Stop therefore commanded a moment of continuous
        motion first -- as did turning the motors off, which called it.

        Stop may afterwards move the pistons to the bottom (see
        :class:`TestParkOnStop`), but it must never re-start a continuous run.
        """
        homed_model.start(RunMode.CONTINUOUS)
        plc.clear_history()

        homed_model.stop(park=False)

        assert plc.writes_to(tags.RUN_CONTINUOUS) == [0]
        assert all(value == 0 for _tag, value in plc.history)

    def test_the_halt_itself_writes_only_zeroes(self, homed_model, plc):
        """Whatever parking does later, the halt is zeroes and nothing else."""
        homed_model.start(RunMode.CONTINUOUS)
        plc.clear_history()

        homed_model.stop()

        # Everything up to the last run-bit zero is the halt.
        history = plc.history
        run_bits = (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)
        last_halt = max(
            i for i, (tag, value) in enumerate(history)
            if tag in run_bits and value == 0
        )
        halt = history[: last_halt + 1]
        assert [t for t, _ in halt[:3]] == list(run_bits)
        assert all(value == 0 for _tag, value in halt[:3])

    def test_stop_drops_every_run_bit(self, homed_model, plc):
        """Stop means stop, whichever mode was started."""
        homed_model.stop(park=False)
        for tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE):
            assert plc.writes_to(tag) == [0], tag

    def test_motors_off_does_not_start_anything(self, model, plc):
        plc.clear_history()
        model.motors_off()
        started = [
            (tag, value)
            for tag, value in plc.history
            if tag in (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE)
            and value != 0
        ]
        assert started == []

    def test_stop_works_while_a_command_holds_the_worker(self, homed_model, plc):
        """Stop must not queue behind the command it is trying to interrupt."""
        homed_model._busy.acquire()
        try:
            assert homed_model.stop() is True
            assert plc.writes_to(tags.RUN_CONTINUOUS) == [0]
        finally:
            homed_model._busy.release()

    def test_failed_stop_is_reported_not_swallowed(self, homed_model, monkeypatch):
        def refuse(_tag, _value):
            raise PlcError("network unplugged")

        monkeypatch.setattr(homed_model.plc, "write", refuse)
        assert homed_model.stop() is False
        assert homed_model.bridge.problems  # the operator is told


class TestTagNames:
    """A mistyped tag used to fail silently."""

    def test_continuous_run_tag_is_spelled_correctly(self, homed_model, plc):
        """The old code wrote ``Program:wave_Control.Run_2`` -- lower-case w.

        The PLC has no such tag, so starting a continuous run raised inside a
        worker thread with nothing to catch it and the machine never moved.
        """
        homed_model.start(RunMode.CONTINUOUS)
        assert tags.RUN_CONTINUOUS == "Program:Wave_Control.Run_2"
        assert plc.writes_to("Program:Wave_Control.Run_2") == [1]
        assert plc.writes_to("Program:wave_Control.Run_2") == []


class TestGuards:
    def test_cannot_start_before_preparing(self, model):
        model.toggle(0, True)
        model.create_set()
        assert model.state is MachineState.READY
        assert model.start(RunMode.CONTINUOUS) is False
        assert model.bridge.problems

    def test_out_of_range_parameters_never_reach_the_machine(self, model, plc):
        """Validation happens before anything is energised.

        The old code validated inside ``Motor.write_*`` -- after Clear_Motor_Error
        and Motor_Boot had already been pulsed -- and raised out of a worker
        thread, leaving the buttons disabled with no message.
        """
        model.toggle(0, True)
        model.create_set()
        model.sets[0].motors[0].write_params["Position 1"] = 5000
        plc.clear_history()

        assert model.prepare() is False
        assert plc.history == []
        assert "Position 1" in model.bridge.problems[0][1]

    def test_a_second_prepare_is_refused_while_one_is_running(self, model):
        model.toggle(0, True)
        model.create_set()
        model._busy.acquire()
        try:
            assert model.prepare() is False
        finally:
            model._busy.release()

    def test_shutdown_clears_the_machine(self, homed_model, plc):
        homed_model.start(RunMode.CONTINUOUS)
        plc.clear_history()
        homed_model.shutdown()
        assert plc.writes_to(tags.RUN_CONTINUOUS) == [0]

    def test_only_selected_motors_are_marked_live(self, model, plc):
        for axis in (2, 5):
            model.toggle(axis, True)
        model.create_set()
        for axis in range(tags.MOTOR_COUNT):
            plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
        model.prepare()

        for axis in range(tags.MOTOR_COUNT):
            expected = 1 if axis in (2, 5) else 0
            assert plc.read(tags.live_motor(axis)) == expected, axis


class TestConnectionModes:
    """The persistent connection is the biggest transport change; the old
    one-session-per-operation behaviour stays available as a fallback."""

    def test_fresh_connection_mode_closes_after_each_operation(self, monkeypatch):
        from app import plc as plc_module

        closed = {"n": 0}

        class FakePLC:
            IPAddress = ""
            ProcessorSlot = 0

            def Read(self, tag):
                return 7

            def Write(self, tag, value):
                return None

            def Close(self):
                closed["n"] += 1

        monkeypatch.setattr(plc_module, "PLC", FakePLC)

        client = plc_module.PlcClient("1.2.3.4", 1, persistent=False)
        client.read("SomeTag")
        client.write("SomeTag", 1)
        assert closed["n"] == 2  # a session per operation, as before

    def test_persistent_mode_reuses_one_session(self, monkeypatch):
        from app import plc as plc_module

        opened = {"n": 0}

        class FakePLC:
            IPAddress = ""
            ProcessorSlot = 0

            def __init__(self):
                opened["n"] += 1

            def Read(self, tag):
                return 7

            def Write(self, tag, value):
                return None

            def Close(self):
                return None

        monkeypatch.setattr(plc_module, "PLC", FakePLC)

        client = plc_module.PlcClient("1.2.3.4", 1, persistent=True)
        client.read("SomeTag")
        client.write("SomeTag", 1)
        client.read("SomeTag")
        assert opened["n"] == 1


class TestParkOnStop:
    """After a stop the pistons return to the bottom of the stroke."""

    def test_stop_drops_the_run_bits_before_anything_else(self, homed_model, plc):
        """The halt must never be delayed by the tidying that follows it."""
        homed_model.start(RunMode.CONTINUOUS)
        plc.clear_history()
        homed_model.stop()

        written = [tag for tag, _ in plc.history]
        assert tags.RUN_CONTINUOUS in written
        first_run_bit = written.index(tags.RUN_CONTINUOUS)
        position_writes = [
            i for i, tag in enumerate(written) if tag.endswith(".Pos_1")
        ]
        assert all(i > first_run_bit for i in position_writes)

    def test_pistons_are_sent_to_the_bottom(self, homed_model, plc, monkeypatch):
        import Model as model_module

        monkeypatch.setattr(model_module, "PARK_SECONDS", 0.0)
        homed_model.start(RunMode.CONTINUOUS)
        plc.clear_history()
        homed_model.stop()

        for motor in homed_model.all_motors:
            park = model_module.PARK_POSITION
            assert plc.read("Program:Wave_Control.Motor_{0}.Pos_1".format(motor.axis + 1)) == park
            assert plc.read("Program:Wave_Control.Motor_{0}.Pos_2".format(motor.axis + 1)) == park

    def test_parking_forces_absolute_moves(self, homed_model, plc, monkeypatch):
        """368 read as an incremental move would be a 368 mm lurch."""
        import Model as model_module

        monkeypatch.setattr(model_module, "PARK_SECONDS", 0.0)
        for motor in homed_model.all_motors:
            motor.set_param("Move Type", 1)  # incremental
        homed_model.stop()

        for motor in homed_model.all_motors:
            tag = "Program:Wave_Control.Motor_{0}.MoveType".format(motor.axis + 1)
            assert plc.read(tag) == 0

    def test_parking_forgets_what_the_plc_holds(self, homed_model, monkeypatch):
        """The PLC now holds parking values, so the operator's parameters must
        go out again before the next run."""
        import Model as model_module

        monkeypatch.setattr(model_module, "PARK_SECONDS", 0.0)
        assert all(m.is_synced for m in homed_model.all_motors)
        homed_model.stop()
        assert not any(m.is_synced for m in homed_model.all_motors)

    def test_no_parking_when_the_machine_was_never_homed(self, model, plc):
        """Absolute moves are meaningless until the drives know where they are."""
        model.set_selection([0, 1])
        plc.clear_history()
        model.stop()
        assert not any(tag.endswith(".Pos_1") for tag, _ in plc.history)

    def test_parking_can_be_turned_off(self, homed_model, plc, monkeypatch):
        import Model as model_module

        monkeypatch.setattr(model_module, "PARK_ON_STOP", False)
        plc.clear_history()
        homed_model.stop()
        assert not any(tag.endswith(".Pos_1") for tag, _ in plc.history)


class TestHomingFailureIsActionable:
    """A piston that will not home must be named, not merely counted.

    "Motors did not home within 35 seconds" gave the operator nothing to act
    on: with thirty pistons and one stuck, the useful facts are which one and
    what can be done about it.
    """

    def _stuck_setup(self, model, plc, stuck_axis):
        model.set_selection([0, 1, 2, 3])
        for axis in range(tags.MOTOR_COUNT):
            plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
        # One piston never reports homed.
        plc.write(tags.axis_field(stuck_axis, tags.STATUS_WORD), 0)

    def test_the_stuck_piston_is_named(self, model, plc):
        self._stuck_setup(model, plc, stuck_axis=2)
        model.prepare()

        assert model.unhomed_axes == [2]
        assert any("2" in message for _title, message in model.bridge.problems)
        assert any("did not" in message for _title, message in model.bridge.problems)

    def test_the_others_are_reported_as_homed(self, model, plc):
        self._stuck_setup(model, plc, stuck_axis=2)
        model.prepare()
        _title, message = model.bridge.problems[-1]
        assert "3 of 4" in message

    def test_the_run_can_continue_without_it(self, model, plc):
        self._stuck_setup(model, plc, stuck_axis=2)
        model.prepare()

        dropped = model.drop_unhomed()
        assert dropped == [2]
        assert model.live_axes == [0, 1, 3]
        assert model.unhomed_axes == []

        # With the stuck piston gone, the rest home and the machine is ready.
        assert model.prepare()
        assert model.state is MachineState.HOMED

    def test_a_clean_home_leaves_nothing_flagged(self, model, plc):
        model.set_selection([0, 1, 2])
        for axis in range(tags.MOTOR_COUNT):
            plc.write(tags.axis_field(axis, tags.STATUS_WORD), 1 << 11)
        assert model.prepare()
        assert model.unhomed_axes == []

    def test_dropping_removes_an_empty_group(self, model, plc):
        model.set_selection([5])
        for axis in range(tags.MOTOR_COUNT):
            plc.write(tags.axis_field(axis, tags.STATUS_WORD), 0)
        model.prepare()
        assert model.unhomed_axes == [5]
        model.drop_unhomed()
        assert model.sets == []
        assert model.state is MachineState.IDLE
