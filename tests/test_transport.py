"""Transport failures tested without sockets or a connected controller."""

import pytest

from app import plc


class FakeController:
    def __init__(self, result=0, error=None):
        self.result = result
        self.error = error
        self.reads = []
        self.writes = []
        self.closed = False

    def Read(self, tag):
        self.reads.append(tag)
        if self.error:
            raise self.error
        return self.result

    def Write(self, tag, value):
        self.writes.append((tag, value))
        if self.error:
            raise self.error
        return self.result

    def Close(self):
        self.closed = True


def test_keepalive_reads_one_tag_without_upload(monkeypatch):
    controller = FakeController()
    monkeypatch.setattr(plc, "PLC", lambda: controller)
    client = plc.PlcClient("unused", 0)
    client.keepalive()
    assert controller.reads == [plc.PROBE_TAG]


def test_keepalive_reconnects_after_read_failure(monkeypatch):
    stale = FakeController(error=OSError("session dropped"))
    fresh = FakeController()
    controllers = iter([stale, fresh])
    monkeypatch.setattr(plc, "PLC", lambda: next(controllers))
    client = plc.PlcClient("unused", 0)
    client.keepalive()
    assert stale.closed
    assert fresh.reads == [plc.PROBE_TAG]
    assert client.connected


@pytest.mark.parametrize("missing", [False, True])
def test_keepalive_reports_failure(monkeypatch, missing):
    monkeypatch.setattr(plc, "PLC", lambda: FakeController(
        result=None, error=None if missing else OSError("offline")))
    with pytest.raises(plc.PlcError):
        plc.PlcClient("unused", 0).keepalive()


def test_write_retries_once_on_a_dropped_session(monkeypatch):
    """The point of the retry: the PLC can drop a session under us."""
    stale = FakeController(error=OSError("session dropped"))
    fresh = FakeController()
    controllers = iter([stale, fresh])
    monkeypatch.setattr(plc, "PLC", lambda: next(controllers))
    client = plc.PlcClient("unused", 0)
    client.write("test", 7)
    assert stale.closed
    assert fresh.writes == [("test", 7)]
    assert client.connected


def test_a_second_failure_is_reported_and_not_retried_again(monkeypatch):
    """Two attempts, then the caller is told -- never an endless reconnect."""
    controllers = [FakeController(error=OSError("offline")) for _ in range(3)]
    monkeypatch.setattr(plc, "PLC", lambda: controllers.pop(0))
    client = plc.PlcClient("unused", 0)
    with pytest.raises(plc.PlcError):
        client.read("test")
    assert len(controllers) == 1, "exactly two sessions were built"
    assert not client.connected


def test_empty_read_is_a_bad_tag_not_a_dropped_session(monkeypatch):
    """A response with no value means the tag path is wrong.

    Retrying it would rebuild a healthy connection for nothing, and at thirty
    position reads per poll that is a reconnect storm, so the read is reported
    once and the session is left alone.
    """
    empty = FakeController(result=None)
    controllers = iter([empty, FakeController(result=42)])
    monkeypatch.setattr(plc, "PLC", lambda: next(controllers))
    client = plc.PlcClient("unused", 0)
    with pytest.raises(plc.PlcError) as caught:
        client.read("test")
    assert empty.reads == ["test"], "the read was not retried"
    assert not empty.closed, "the session was not torn down"
    assert "Read test returned no value" == str(caught.value)


def test_connect_rejects_empty_responses(monkeypatch):
    monkeypatch.setattr(plc, "PLC", lambda: FakeController(result=None))
    client = plc.PlcClient("unused", 0)
    assert client.connect() is False
    assert not client.connected


def test_connect_probes_once_against_a_machine_that_is_off(monkeypatch):
    """Each attempt costs a whole socket timeout, and the startup probe exists
    to decide quickly that there is nothing there."""
    dead = FakeController(error=OSError("timed out"))
    monkeypatch.setattr(plc, "PLC", lambda: dead)
    client = plc.PlcClient("unused", 0)
    assert client.connect() is False
    assert dead.reads == [plc.PROBE_TAG], "the probe was attempted once"
    assert not client.connected


@pytest.mark.parametrize("raises", [False, True])
def test_discard_closes_socket_even_when_handshake_fails(monkeypatch, raises):
    class Socket:
        closed = False

        def close(self):
            self.closed = True

    class BrokenClose(FakeController):
        def Close(self):
            if raises:
                raise OSError("close handshake failed")
            # Vendored Close also swallows handshake failures internally.

    controller = BrokenClose()
    controller.Socket = Socket()
    monkeypatch.setattr(plc, "PLC", lambda: controller)
    client = plc.PlcClient("unused", 0)
    client.read("test")
    client.close()
    assert controller.Socket.closed
    assert not client.connected
