"""The wavectl session: its lock file, its status line, and starting and ending it.

Nothing here can reach the controller: ``conftest.py`` sets
``WAVECTL_MOCK_ONLY`` for every test and points the session file at a
temporary folder.
"""

from __future__ import annotations

import os

import pytest

from app import tags
from app.plc import SimulatedPlc
from wavectl import cli, live, pistons, places, sessionfile
from wavectl.journal import Journal, one_line
from wavectl.service import Service


class TestPistonLists:
    def test_numbers_and_ranges_are_display_numbers(self):
        assert pistons.shown(pistons.parse("1-3,7")) == [1, 2, 3, 7]

    def test_piston_one_is_axis_twenty_nine(self):
        assert pistons.parse("1") == [29]

    def test_a_column_is_three_pistons_from_the_front(self):
        assert pistons.shown(pistons.parse("c1-3")) == list(range(1, 10))

    def test_a_row_runs_the_length_of_the_chamber(self):
        assert pistons.shown(pistons.parse("r1")) == list(range(1, 31, 3))

    def test_all_and_none(self):
        assert len(pistons.parse("all")) == tags.MOTOR_COUNT
        assert pistons.parse("none") == []

    @pytest.mark.parametrize("text", ["0", "31", "9-1", "c11", "r4", "front", "1-x"])
    def test_nonsense_is_refused_not_guessed(self, text):
        with pytest.raises(ValueError):
            pistons.parse(text)

    def test_described_as_ranges(self):
        assert pistons.describe(pistons.parse("1-9,12,14-15")) == "1-9, 12, 14-15"
        assert pistons.describe([]) == "none"


class TestSessionFile:
    @pytest.fixture(autouse=True)
    def this_process_counts_as_a_session(self, monkeypatch):
        # These claim the file in the test's own name, and the test runner is
        # not a session. What is and is not one has its own tests below.
        monkeypatch.setattr(sessionfile, "is_session", sessionfile.process_exists)

    def test_a_second_claim_is_refused_while_the_holder_lives(self):
        sessionfile.claim({"pid": os.getpid(), "mode": "mock"})
        with pytest.raises(sessionfile.Taken) as refused:
            sessionfile.claim({"pid": os.getpid(), "mode": "live"})
        assert refused.value.holder["mode"] == "mock"

    def test_a_dead_holder_is_replaced(self):
        # Process numbers this large are not handed out, so nothing has it.
        sessionfile.claim({"pid": 2 ** 22 + 12345, "mode": "mock"})
        sessionfile.claim({"pid": os.getpid(), "mode": "live"})
        assert sessionfile.read()["mode"] == "live"

    def test_release_leaves_a_successors_file_alone(self):
        sessionfile.claim({"pid": os.getpid(), "mode": "mock"})
        sessionfile.release(pid=os.getpid() + 1)
        assert sessionfile.read() is not None
        sessionfile.release(pid=os.getpid())
        assert sessionfile.read() is None

    def test_the_file_is_private_to_its_owner(self):
        sessionfile.claim({"pid": os.getpid(), "token": "secret"})
        if os.name == "posix":
            assert places.session_file().stat().st_mode & 0o077 == 0


class TestWhatCountsAsASession:
    def test_another_program_with_the_same_number_is_not_one(self):
        # After a crash the number in a stale file is handed to something
        # else. That something must not block a new session, or be killed.
        assert sessionfile.process_exists(os.getpid())
        assert not sessionfile.is_session(os.getpid())

    def test_a_stale_file_naming_another_program_is_replaced(self):
        sessionfile.claim({"pid": os.getpid(), "mode": "live"})
        sessionfile.claim({"pid": os.getpid(), "mode": "mock"})
        assert sessionfile.read()["mode"] == "mock"

    def test_force_leaves_another_program_alone(self, capsys):
        import subprocess
        import sys

        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            sessionfile.claim({"pid": bystander.pid, "mode": "mock", "port": 1,
                               "token": "x", "started": "earlier"})
            assert cli.main(["session", "stop", "--force"]) == 0
            assert bystander.poll() is None          # still running
            assert sessionfile.read() is None
        finally:
            bystander.kill()
            bystander.wait()


class TestJournal:
    def test_a_problem_is_cut_to_its_first_paragraph(self):
        assert one_line("Nothing moved.\nThe command was sent.\n\nLikely causes:\n - a") \
            == "Nothing moved. The command was sent."

    def test_only_what_was_said_since_the_mark(self):
        journal = Journal()
        journal.status("before")
        mark = journal.mark()
        journal.status("after")
        journal.problem("Machine error", "it broke")
        assert journal.last_status_since(mark) == "after"
        assert journal.problems_since(mark) == ["Machine error: it broke"]

    def test_an_unchanged_state_is_not_repeated(self):
        journal = Journal()
        journal.state_changed("idle")
        journal.state_changed("idle")
        assert len(journal.last(10)) == 1


class TestStatus:
    def test_an_empty_session_says_so(self, model):
        reply = Service(model, "mock").handle("status")
        assert reply["ok"] and reply["mode"] == "mock"
        assert reply["text"] == "idle | no pistons selected | run bits clear"

    def test_the_selection_and_its_motion_are_named(self, model):
        model.set_selection(pistons.parse("1-9"))
        for motor in model.all_motors:
            motor.set_param("Position 2", 120)
            motor.set_param("Speed 1", 150)
            motor.set_param("Speed 2", 150)
        text = Service(model, "mock").handle("status")["text"]
        assert "pistons 1-9" in text
        assert "0 to 120 mm at 150 mm/s" in text
        assert "ready, needs homing" in text
        assert "drives ok" in text

    def test_a_run_bit_someone_else_set_is_shouted(self, model, plc):
        plc.write(tags.RUN_CONTINUOUS, 1)
        reply = Service(model, "live").handle("status")
        assert "Run_2 HIGH" in reply["text"]
        assert reply["data"]["bits_high"] == ["Run_2"]

    def test_a_drive_problem_is_named_with_its_piston(self, model, plc):
        model.set_selection(pistons.parse("4-6"))
        axis = tags.axis_from_display(5)
        plc.write(tags.axis_field(axis, tags.WARN_WORD), 1 << 6)
        text = Service(model, "live").handle("status")["text"]
        assert "piston 5: Controller Hot" in text

    def test_the_piston_table_is_one_line_each(self, model, plc):
        model.set_selection(pistons.parse("1-3"))
        plc.write(tags.axis_field(tags.axis_from_display(2), tags.ACTUAL_POSITION),
                  1234567)
        lines = Service(model, "mock").handle(
            "status", {"pistons_table": True})["text"].splitlines()
        assert len(lines) == 4
        assert "123.5 mm" in lines[2]

    def test_an_unknown_operation_is_an_answer_not_a_crash(self, model):
        reply = Service(model, "mock").handle("launch")
        assert not reply["ok"] and "launch" in reply["text"]

    def test_starting_a_session_writes_nothing(self, model, plc):
        # The window clears the machine as it opens, which switches off any
        # drive that was holding a piston up. A session only reads.
        plc.write(tags.live_motor(29), 1)
        plc.clear_history()
        service = Service(model, "live")
        service.open()
        assert plc.history == []
        assert plc.read(tags.live_motor(29)) == 1
        assert "Nothing was written" in service.handle("log")["text"]
        model.stop_monitoring()

    def test_a_piston_resting_at_the_top_is_not_minus_zero(self, model, plc):
        from wavectl import live

        plc.write(tags.axis_field(3, tags.ACTUAL_POSITION), -400)   # -0.04 mm
        assert str(live.read_drive(plc, 3)["mm"]) == "0.0"


class _Driven(SimulatedPlc):
    """A controller something else is already running."""

    def __init__(self, ip, slot):
        SimulatedPlc.__init__(self)
        self.values[tags.RUN_CONTINUOUS] = 1
        self.closed = False

    def connect(self):
        return True

    def close(self):
        self.closed = True


class TestReachingTheController:
    def test_mock_only_refuses_before_opening_anything(self):
        with pytest.raises(live.Unreachable) as refused:
            live.connect("192.168.1.1", 1)
        assert "WAVECTL_MOCK_ONLY" in str(refused.value)

    def test_a_controller_already_being_driven_is_left_alone(self, monkeypatch):
        made = []

        def build(ip, slot):
            made.append(_Driven(ip, slot))
            return made[-1]

        monkeypatch.delenv("WAVECTL_MOCK_ONLY")
        monkeypatch.setattr(live, "PlcClient", build)
        with pytest.raises(live.Occupied) as refused:
            live.connect_idle("192.168.1.1", 1)
        assert "Run_2 already high" in str(refused.value)
        assert made[0].history == []          # nothing was written
        assert made[0].closed


@pytest.fixture
def session(capsys):
    """A mock session in a process of its own, ended whatever the test does."""
    assert cli.main(["session", "start", "--mock", "--fast"]) == 0
    capsys.readouterr()
    yield
    cli.main(["session", "stop", "--force"])


class TestTheSessionProcess:
    def test_commands_with_no_session_say_how_to_start_one(self, capsys):
        assert cli.main(["status"]) == 2
        assert "wavectl session start" in capsys.readouterr().out

    def test_live_is_refused_while_mock_only_is_set(self, capsys):
        assert cli.main(["session", "start", "--live"]) == 1
        said = capsys.readouterr().out
        assert said.startswith("LIVE: ") and "WAVECTL_MOCK_ONLY" in said
        assert sessionfile.read() is None

    def test_start_status_and_stop(self, session, capsys):
        assert cli.main(["status"]) == 0
        assert capsys.readouterr().out.strip() == \
            "mock: idle | no pistons selected | run bits clear"

        assert cli.main(["session", "start", "--mock"]) == 1
        assert "already running" in capsys.readouterr().out

        pid = sessionfile.read()["pid"]
        assert sessionfile.is_session(pid)
        assert cli.main(["session", "stop"]) == 0
        assert sessionfile.read() is None
        assert not sessionfile.process_exists(pid)

    def test_a_request_without_the_token_is_turned_away(self, session):
        from wavectl import client

        info = sessionfile.read()
        info["token"] = "not the token"
        sessionfile.update(info)
        reply = client.request("status")
        assert not reply["ok"] and "token" in reply["text"]

    def test_json_carries_the_whole_reply(self, session, capsys):
        import json

        assert cli.main(["status", "--json"]) == 0
        reply = json.loads(capsys.readouterr().out)
        assert reply["mode"] == "mock" and reply["data"]["state"] == "idle"
