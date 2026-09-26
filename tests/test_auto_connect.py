"""Opening the application before switching the wavemaker on.

The application used to probe the PLC exactly once, as the window opened. Start
it first and the only way to connect was to close it and start it again, which
is what the lab was doing. These tests cover the watcher that removed that, and
the one thing it must never do: touch a machine it has just found.

Nothing here opens a socket. ``plc_module.connect`` is replaced throughout, so
the tests behave identically on the lab PC and on a developer's laptop.
"""

from __future__ import annotations

import pytest

import Model as model_module
from app import network, plc as plc_module
from app.plc import SimulatedPlc
from Model import MachineState, Model, RunMode


def diagnosis(reason=network.OK, repairable=False, adapter=None):
    return network.Diagnosis(
        reason=reason,
        message="test diagnosis: {0}".format(reason),
        repairable=repairable,
        adapter=adapter,
        local_address=None,
    )


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """A model that wanted the real machine and did not find it."""
    monkeypatch.setattr("app.paths.ANALYTICS_DIR", tmp_path / "analytics")
    monkeypatch.setattr("app.paths.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("app.paths.PRESET_DIR", tmp_path / "Presets")

    built = Model(ip_address="192.168.1.1")
    built._spawn = lambda name, work: work()
    built.is_live = False
    built._fell_back_to_mock = True
    built._connection_attempted = True
    built.plc = SimulatedPlc()
    return built


# -- the watcher does not pay for probes that cannot succeed -------------------

def test_no_probe_is_attempted_without_an_address_on_the_plc_network(offline,
                                                                    monkeypatch):
    """The whole reason the watcher consults app/network.py first.

    A failed probe costs a full five-second socket timeout. With no address on
    the controller's network it cannot possibly succeed, and app/network.py can
    say so for free, so the probe must not be attempted at all.
    """
    attempts = []
    monkeypatch.setattr(
        offline, "refresh_network_diagnosis",
        lambda: diagnosis(network.NO_ADDRESS, repairable=True),
    )
    monkeypatch.setattr(
        plc_module, "connect",
        lambda *a, **k: attempts.append(a) or (SimulatedPlc(), True),
    )

    assert offline._adopt_machine_if_present() is True   # direct call does probe
    attempts[:] = []

    # Through the watcher's own decision, it must not reach the probe.
    monkeypatch.setattr(model_module, "MACHINE_WATCH_SECONDS", 0.0)
    offline._watch_stop.clear()
    ticked = {"n": 0}

    def wait(_delay):
        ticked["n"] += 1
        if ticked["n"] >= 2:
            offline._watch_stop.set()
        return offline._watch_stop.is_set()

    monkeypatch.setattr(offline._watch_stop, "wait", wait)
    offline._watch_loop()

    assert attempts == []


def test_no_watcher_at_all_in_mock_mode(tmp_path, monkeypatch):
    # --mock is a deliberate choice to run without hardware. Quietly connecting
    # to a real machine underneath that would be a nasty surprise.
    monkeypatch.setattr("app.paths.LOG_DIR", tmp_path / "logs")
    built = Model(simulate=True)
    built.start_watching_for_machine()
    assert built._watch_thread is None


# -- adopting the machine when it appears -------------------------------------

def test_the_machine_is_adopted_once_it_answers(offline, monkeypatch):
    live = SimulatedPlc()
    monkeypatch.setattr(plc_module, "connect", lambda *a, **k: (live, True))

    assert offline._adopt_machine_if_present() is True
    assert offline.is_live is True
    assert offline.plc is live


def test_adopting_re_enables_motion(offline, monkeypatch):
    """The point of the whole feature.

    While the PLC had not answered, Prepare and Start were refused so they
    could not "succeed" against the mock. Once the real machine is adopted they
    have to work again, without the restart the lab was doing.
    """
    monkeypatch.setattr(plc_module, "connect",
                        lambda *a, **k: (SimulatedPlc(), True))
    offline.toggle(0, True)
    offline.create_set()
    assert offline.prepare() is False          # refused while offline

    offline._adopt_machine_if_present()

    assert offline._offline() is False
    assert offline._fell_back_to_mock is False


def test_a_failed_probe_leaves_the_model_offline(offline, monkeypatch):
    monkeypatch.setattr(plc_module, "connect",
                        lambda *a, **k: (SimulatedPlc(), False))

    assert offline._adopt_machine_if_present() is False
    assert offline.is_live is False
    assert offline._fell_back_to_mock is True


def test_adopting_never_commands_the_machine(offline, monkeypatch):
    """The most important test in this file.

    Startup follows a connection with motors_off, which clears run bits and
    faults. That is right when a person has just launched the application and
    wrong from a background thread: this software has no exclusive-ownership
    check, so the machine may have been powered up for somebody else's session,
    and clearing their run bits from a thread they do not know is running would
    stop their experiment with no explanation.
    """
    live = SimulatedPlc()
    monkeypatch.setattr(plc_module, "connect", lambda *a, **k: (live, True))
    live.clear_history()

    offline._adopt_machine_if_present()

    wrote = [entry for entry in live.history if entry[0] == "write"]
    assert wrote == [], "adopting the machine must not write anything"


def test_an_operator_command_keeps_the_watcher_out(offline, monkeypatch):
    # The watcher must never take the machine from under a running command.
    monkeypatch.setattr(plc_module, "connect",
                        lambda *a, **k: (SimulatedPlc(), True))
    offline._busy.acquire()
    try:
        assert offline._adopt_machine_if_present() is False
        assert offline.is_live is False
    finally:
        offline._busy.release()


# -- the diagnosis is reported once, not every tick ---------------------------

def test_an_unchanged_diagnosis_is_not_announced_twice(offline, monkeypatch):
    announced = []
    monkeypatch.setattr(offline.bridge, "state_changed",
                        lambda state: announced.append(state))
    monkeypatch.setattr(offline, "refresh_network_diagnosis",
                        lambda: diagnosis(network.NO_LINK))
    monkeypatch.setattr(model_module, "MACHINE_WATCH_SECONDS", 0.0)

    ticks = {"n": 0}

    def wait(_delay):
        ticks["n"] += 1
        if ticks["n"] >= 4:
            offline._watch_stop.set()
        return offline._watch_stop.is_set()

    offline._watch_stop.clear()
    monkeypatch.setattr(offline._watch_stop, "wait", wait)
    offline._watch_loop()

    # Four ticks, one announcement: repeating it would bury the Feedback tab,
    # which is the operator's own record of what the application has done.
    assert len(announced) == 1


# -- shutdown -----------------------------------------------------------------

def test_shutdown_stops_the_watcher(offline):
    """Or its thread outlives the window and can connect after the event.

    shutdown clears the machine and closes the connection. A watcher still
    running can then adopt the machine again, leaving an open connection behind
    a closed window.
    """
    offline.shutdown()
    assert offline._watch_stop.is_set()
    assert offline._watch_thread is None


# -- fix_network --------------------------------------------------------------

def test_fix_network_reports_what_to_do_when_it_cannot_help(offline, monkeypatch):
    monkeypatch.setattr(
        offline, "refresh_network_diagnosis",
        lambda: diagnosis(network.NO_LINK, repairable=False),
    )

    offline.fix_network()

    assert any("NO_LINK" in str(p) or "no-link" in str(p)
               for p in offline.bridge.problems)


def test_fix_network_connects_when_the_address_lands(offline, monkeypatch):
    monkeypatch.setattr(
        offline, "refresh_network_diagnosis",
        lambda: diagnosis(network.NO_ADDRESS, repairable=True,
                          adapter=network.Adapter(8, "Ethernet", "", True,
                                                  True, ["169.254.1.1"])),
    )
    monkeypatch.setattr(network, "repair", lambda ip, d=None: (True, "set .100"))
    monkeypatch.setattr(plc_module, "connect",
                        lambda *a, **k: (SimulatedPlc(), True))

    offline.fix_network()

    assert offline.is_live is True


def test_a_fixed_address_with_the_machine_off_is_not_a_failure(offline,
                                                              monkeypatch):
    """Two separate things, reported separately.

    Setting the address can succeed while the wavemaker is still switched off.
    Calling that a failure would send the operator looking for a network fault
    they have just fixed.
    """
    monkeypatch.setattr(
        offline, "refresh_network_diagnosis",
        lambda: diagnosis(network.NO_ADDRESS, repairable=True,
                          adapter=network.Adapter(8, "Ethernet", "", True,
                                                  True, ["169.254.1.1"])),
    )
    monkeypatch.setattr(network, "repair", lambda ip, d=None: (True, "set .100"))
    monkeypatch.setattr(plc_module, "connect",
                        lambda *a, **k: (SimulatedPlc(), False))

    offline.fix_network()

    assert offline.is_live is False
    assert not offline.bridge.problems
    assert any("connect by itself" in str(m) for m in offline.bridge.messages)
