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

    def test_stop_does_not_pulse_the_run_bit_high(self, homed_model, plc):
        """The old stop path wrote Run_2=1 and then Run_2=0.

        ``motion(2, tracker=1)`` was used for both starting and stopping
        continuous motion, and unconditionally wrote 1 before checking the
        tracker.  Pressing Stop therefore commanded a moment of continuous
        motion first -- as did turning the motors off, which called it.
        """
        homed_model.start(RunMode.CONTINUOUS)
        plc.clear_history()

        homed_model.stop()

        assert plc.writes_to(tags.RUN_CONTINUOUS) == [0]
        assert all(value == 0 for _tag, value in plc.history)

    def test_stop_drops_every_run_bit(self, homed_model, plc):
        """Stop means stop, whichever mode was started."""
        homed_model.stop()
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
