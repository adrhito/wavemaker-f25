"""Stopping and looking without the session.

These are the paths that open a connection to the controller from the command
itself, so every test replaces ``PlcClient`` with a tag store that records
what was done to it. Nothing here reaches a real controller.
"""

from __future__ import annotations

import os
import socket

import pytest

from app import tags
from app.plc import SimulatedPlc
from wavectl import cli, client, live, sessionfile

BITS = (tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE, tags.HOME_BUTTON)


class _Controller(SimulatedPlc):
    """Stands in for the controller, and remembers every one that was opened."""

    made = []
    begins_with = {}
    stuck = ()

    def __init__(self, ip, slot):
        SimulatedPlc.__init__(self)
        self.ip, self.slot, self.closed = ip, slot, False
        self.values.update(self.begins_with)
        _Controller.made.append(self)

    def connect(self):
        return True

    def write(self, tag, value):
        self.history.append((tag, value))
        if tag not in self.stuck:
            self.values[tag] = value

    def identity(self):
        return "1756-L61 test controller"

    def close(self):
        self.closed = True


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.delenv("WAVECTL_MOCK_ONLY")
    monkeypatch.setattr(live, "PlcClient", _Controller)
    # These leave session files in the test runner's own name.
    monkeypatch.setattr(sessionfile, "is_session", sessionfile.process_exists)
    _Controller.made = []
    _Controller.begins_with = {tags.RUN_CONTINUOUS: 1}
    _Controller.stuck = ()
    return _Controller


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def leave_session(mode: str, pid: int) -> None:
    """A session file whose session is not answering."""
    sessionfile.claim({"pid": pid, "mode": mode, "ip": "192.168.1.77",
                       "slot": 2, "port": unused_port(), "token": "x",
                       "started": "earlier"})


NOBODY = 2 ** 22 + 4321   # no process has a number this large


class TestStopDirect:
    def test_it_writes_every_command_bit_low(self, controller, capsys):
        assert cli.main(["stop", "--direct"]) == 0
        plc = controller.made[0]
        assert plc.history == [(tag, 0) for tag in BITS]
        assert plc.closed
        said = capsys.readouterr().out
        assert said.startswith("LIVE: ") and "all read low" in said

    def test_it_goes_to_the_address_given(self, controller):
        assert cli.main(["stop", "--direct", "--ip", "10.0.0.5", "--slot", "3"]) == 0
        assert (controller.made[0].ip, controller.made[0].slot) == ("10.0.0.5", 3)

    def test_a_bit_that_will_not_drop_is_a_failure(self, controller, capsys):
        controller.stuck = (tags.RUN_CONTINUOUS,)
        assert cli.main(["stop", "--direct"]) == 1
        said = capsys.readouterr().out
        assert "Run_2 still read high" in said and "physical stop" in said

    def test_an_unreachable_controller_says_use_the_physical_stop(
            self, monkeypatch, capsys):
        # WAVECTL_MOCK_ONLY is still set here, so nothing is opened.
        assert cli.main(["stop", "--direct"]) == 1
        assert "physical stop" in capsys.readouterr().out


class TestStopWhenTheSessionCannotBeAsked:
    def test_no_session_writes_nothing_and_says_how(self, controller, capsys):
        assert cli.main(["stop"]) == 2
        assert controller.made == []
        assert "wavectl stop --direct" in capsys.readouterr().out

    def test_a_silent_live_session_is_stopped_from_here(self, controller, capsys):
        leave_session("live", os.getpid())
        assert cli.main(["stop"]) == 0
        plc = controller.made[0]
        assert (plc.ip, plc.slot) == ("192.168.1.77", 2)
        assert plc.read(tags.RUN_CONTINUOUS) == 0
        said = capsys.readouterr().out
        assert "did not answer" in said and "session stop --force" in said

    def test_a_silent_mock_session_touches_no_controller(self, controller, capsys):
        leave_session("mock", os.getpid())
        assert cli.main(["stop"]) == 2
        assert controller.made == []

    def test_a_session_that_could_not_stop_is_backed_up(self, controller,
                                                        monkeypatch, capsys):
        leave_session("live", os.getpid())
        monkeypatch.setattr(client, "request", lambda *a, **k: {
            "ok": False, "mode": "live", "data": {},
            "text": "STOP FAILED. no route to host"})
        assert cli.main(["stop", "--now"]) == 0
        assert controller.made[0].read(tags.RUN_CONTINUOUS) == 0
        assert "tried directly" in capsys.readouterr().out


class TestAHungSession:
    """Alive, holding its port open, and answering nothing."""

    @pytest.fixture
    def hung(self, monkeypatch):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        sessionfile.claim({"pid": os.getpid(), "mode": "live",
                           "ip": "192.168.1.77", "slot": 2, "token": "x",
                           "port": listener.getsockname()[1], "started": "earlier"})
        monkeypatch.setattr(client, "ALIVE_SECONDS", 0.4)
        yield
        listener.close()

    def test_stop_takes_over_in_seconds_not_a_minute(self, controller, hung, capsys):
        import time

        began = time.monotonic()
        assert cli.main(["stop"]) == 0
        assert time.monotonic() - began < 5
        assert controller.made[0].read(tags.RUN_CONTINUOUS) == 0
        assert "did not answer" in capsys.readouterr().out

    def test_session_stop_does_not_kill_it_unasked(self, controller, hung, capsys):
        assert cli.main(["session", "stop"]) == 2
        assert sessionfile.read() is not None
        assert "--force" in capsys.readouterr().out


class TestEndingADeadSession:
    def test_a_dead_live_session_has_its_run_bits_dropped(self, controller, capsys):
        leave_session("live", NOBODY)
        assert cli.main(["session", "stop"]) == 0
        assert sessionfile.read() is None
        assert controller.made[0].read(tags.RUN_CONTINUOUS) == 0
        assert "is gone" in capsys.readouterr().out

    def test_a_dead_mock_session_is_just_cleared(self, controller, capsys):
        leave_session("mock", NOBODY)
        assert cli.main(["session", "stop"]) == 0
        assert sessionfile.read() is None
        assert controller.made == []


class TestSurvey:
    def test_it_reads_and_writes_nothing(self, controller, capsys):
        controller.begins_with = {
            tags.RUN_CONTINUOUS: 1,
            # Homed and enabled, with the three bits a healthy drive holds
            # set (switch-on, voltage enable, and not in quick stop).
            tags.axis_field(29, tags.STATUS_WORD): (1 << 11) | 0x33,
            tags.axis_field(28, tags.WARN_WORD): 1 << 6,
        }
        assert cli.main(["survey", "-p"]) == 0
        plc = controller.made[0]
        assert plc.history == [] and plc.closed
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("LIVE: read only, 1756-L61 test controller")
        assert "Run_2 HIGH" in lines[0]
        assert "1 of 30 drives homed, 1 enabled" in lines[0]
        assert "piston 2: Controller Hot" in lines[0]
        assert len(lines) == 31 and "homed  enabled  ok" in lines[1]
