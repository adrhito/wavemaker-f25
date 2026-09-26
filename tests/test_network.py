"""Telling apart the three faults that all look like "the PLC did not answer".

These are pure logic tests over an injected list of adapters. Nothing here
reads the real machine's network or runs netsh, so they behave the same on the
lab PC, on a developer's laptop and in CI.

The case that motivated the module is :func:`test_a_wifi_default_route_is_not_a
_route_to_the_plc`, which is a fault the obvious implementation has.
"""

from __future__ import annotations

import pytest

from app import network


def adapter(name="Ethernet", index=8, up=True, wired=True, addresses=(),
            description="Intel Ethernet Connection"):
    return network.Adapter(
        index=index, name=name, description=description, up=up, wired=wired,
        addresses=list(addresses),
    )


@pytest.fixture
def no_real_network(monkeypatch):
    """Make every test state the adapters it means, and none touch the host."""
    monkeypatch.setattr(network, "on_windows", lambda: True)
    monkeypatch.setattr(network, "local_address_for", lambda ip: None)

    def use(*adapters):
        monkeypatch.setattr(network, "adapters", lambda: list(adapters))

    return use


# -- same_network / is_link_local ---------------------------------------------

def test_a_24_network_is_the_first_three_octets():
    assert network.same_network("192.168.1.100", "192.168.1.1")
    assert not network.same_network("192.168.2.100", "192.168.1.1")


def test_nonsense_is_not_on_any_network_rather_than_raising():
    # This is called from a refresh path, so a malformed address must not be
    # able to take the window down.
    assert not network.same_network("", "192.168.1.1")
    assert not network.same_network("192.168.1", "192.168.1.1")
    assert not network.same_network("192.168.1.999", "192.168.1.1")
    assert not network.same_network(None, "192.168.1.1")


def test_the_169_254_fallback_is_recognised():
    # The exact state the lab PC was found in: DHCP answered nothing and no
    # static address was configured, so Windows invented one.
    assert network.is_link_local("169.254.106.193")
    assert not network.is_link_local("192.168.1.100")
    assert not network.is_link_local("10.23.16.189")


# -- diagnose -----------------------------------------------------------------

def test_an_address_on_the_plc_network_is_ok(no_real_network):
    no_real_network(adapter(addresses=["192.168.1.100"]))

    found = network.diagnose("192.168.1.1")

    assert found.reason == network.OK
    assert found.ok
    # Nothing to repair: if the controller is still silent with the address
    # right, it is the machine, and pretending software can fix that wastes
    # the operator's time.
    assert found.repairable is False


def test_no_cable_is_not_repairable_in_software(no_real_network):
    no_real_network(
        adapter(name="Ethernet", up=False, addresses=["169.254.1.1"]),
        adapter(name="Wi-Fi", wired=False, up=True, addresses=["10.23.16.189"]),
    )

    found = network.diagnose("192.168.1.1")

    assert found.reason == network.NO_LINK
    assert found.repairable is False
    assert "cable" in found.message.lower()


def test_only_a_link_local_address_is_the_repairable_fault(no_real_network):
    no_real_network(adapter(addresses=["169.254.106.193"]))

    found = network.diagnose("192.168.1.1")

    assert found.reason == network.NO_ADDRESS
    assert found.repairable is True
    assert found.adapter.name == "Ethernet"


def test_no_address_at_all_is_also_repairable(no_real_network):
    no_real_network(adapter(addresses=[]))

    found = network.diagnose("192.168.1.1")

    assert found.reason == network.NO_ADDRESS
    assert found.repairable is True


def test_a_real_address_on_the_wrong_network_is_reported_as_such(no_real_network):
    no_real_network(adapter(addresses=["10.0.5.23"]))

    found = network.diagnose("192.168.1.1")

    assert found.reason == network.WRONG_SUBNET
    assert found.repairable is True
    assert "10.0.5.23" in found.message


def test_a_wifi_default_route_is_not_a_route_to_the_plc(no_real_network,
                                                        monkeypatch):
    """The trap the obvious implementation falls into.

    Measured on the session laptop: the Ethernet port held only a 169.254
    address, Wi-Fi was up with a default gateway, and the operating system
    happily reported that it would reach 192.168.1.1 from 10.23.16.189. There
    was a route. It went to the internet. Anything that decides "can we reach
    the PLC" by asking for a route says yes here and is wrong.
    """
    monkeypatch.setattr(network, "local_address_for", lambda ip: "10.23.16.189")
    no_real_network(
        adapter(name="Ethernet", addresses=["169.254.106.193"]),
        adapter(name="Wi-Fi", wired=False, addresses=["10.23.16.189"]),
    )

    found = network.diagnose("192.168.1.1")

    assert found.reason == network.NO_ADDRESS
    assert found.local_address == "10.23.16.189"   # there IS a route
    assert not found.ok                            # and it is no use


def test_bluetooth_is_not_chosen_over_a_real_port(no_real_network):
    # Bluetooth personal-area networking reports the same IfType as an Ethernet
    # port, so it cannot be excluded by type. It must not be the one picked.
    no_real_network(
        adapter(name="Bluetooth Network Connection", index=3,
                description="Bluetooth Device (Personal Area Network)",
                addresses=["169.254.232.246"]),
        adapter(name="Ethernet", index=8, addresses=["169.254.106.193"]),
    )

    found = network.diagnose("192.168.1.1")

    assert found.adapter.name == "Ethernet"


def test_off_windows_it_says_so_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(network, "on_windows", lambda: False)
    monkeypatch.setattr(network, "local_address_for", lambda ip: None)

    found = network.diagnose("192.168.1.1")

    assert found.reason == network.UNSUPPORTED
    assert found.repairable is False


# -- choose_address -----------------------------------------------------------

def test_the_suggested_address_avoids_the_plc_and_anything_taken():
    chosen = network.choose_address("192.168.1.1", taken=["192.168.1.100"])
    assert chosen != "192.168.1.100"
    assert chosen != "192.168.1.1"
    assert network.same_network(chosen, "192.168.1.1")


def test_the_first_choice_is_the_one_the_old_message_told_operators_to_use():
    # docs and Model's own error text have said 192.168.1.100 for years, so an
    # operator who has been here before expects that address.
    assert network.choose_address("192.168.1.1", taken=[]) == "192.168.1.100"


def test_it_gives_up_rather_than_inventing_an_address_outside_the_network():
    taken = ["192.168.1.{0}".format(h) for h in network.CANDIDATE_HOSTS]
    assert network.choose_address("192.168.1.1", taken=taken) is None


# -- netsh_command ------------------------------------------------------------

def test_replacing_sets_a_static_address():
    command = network.netsh_command(adapter(), "192.168.1.100", replace=True)
    assert command[:5] == ["netsh", "interface", "ipv4", "set", "address"]
    assert "static" in command
    assert command[-2:] == ["192.168.1.100", "255.255.255.0"]


def test_not_replacing_adds_alongside_and_leaves_dhcp_alone():
    """On somebody's own laptop the cable may be their internet.

    Replacing a working DHCP configuration to reach a PLC would be a poor
    trade, so an adapter that already holds a real address gets a second
    address rather than losing the first.
    """
    command = network.netsh_command(adapter(), "192.168.1.100", replace=False)
    assert command[3] == "add"
    assert "static" not in command


def test_no_default_gateway_is_ever_set():
    # The PLC segment has no router. Claiming a default route here would take
    # the whole PC off the internet, and on the lab PC off the UNC network too.
    for replace in (True, False):
        command = network.netsh_command(adapter(), "192.168.1.100", replace)
        assert not any("gateway" in part.lower() for part in command)


def test_an_adapter_name_with_spaces_survives_elevation():
    # netsh wants name="Local Area Connection", not "name=Local Area
    # Connection", and ShellExecuteW takes one string rather than a list.
    named = adapter(name="Local Area Connection 2")
    rendered = network._quoted(
        network.netsh_command(named, "192.168.1.100", replace=True)
    )
    assert 'name="Local Area Connection 2"' in rendered
    assert not rendered.startswith("netsh")   # the exe is passed separately


# -- repair -------------------------------------------------------------------

def test_repair_declines_politely_when_there_is_nothing_it_can_do(no_real_network):
    no_real_network(adapter(up=False))

    worked, message = network.repair("192.168.1.1")

    assert worked is False
    assert "cable" in message.lower()


def test_repair_is_a_no_op_when_the_address_is_already_right(no_real_network):
    no_real_network(adapter(addresses=["192.168.1.100"]))

    worked, _message = network.repair("192.168.1.1")

    assert worked is True


def test_repair_reports_a_refused_uac_prompt_as_an_instruction(no_real_network,
                                                              monkeypatch):
    no_real_network(adapter(addresses=["169.254.106.193"]))
    monkeypatch.setattr(network, "already_admin", lambda: False)
    monkeypatch.setattr(network, "_run_elevated", lambda command: False)

    worked, message = network.repair("192.168.1.1")

    assert worked is False
    # Saying No is the operator's right, so the message has to leave them a way
    # forward rather than reporting a failure.
    assert "192.168.1.100" in message
    assert "255.255.255.0" in message


def test_repair_judges_success_by_the_adapter_not_by_netsh(no_real_network,
                                                           monkeypatch):
    """An elevated process cannot hand its exit status back cheaply.

    It does not matter: the question is whether this PC now holds an address on
    the controller's network, and that is directly observable. A netsh that
    launches happily and changes nothing must still be reported as a failure.
    """
    no_real_network(adapter(addresses=["169.254.106.193"]))
    monkeypatch.setattr(network, "already_admin", lambda: False)
    monkeypatch.setattr(network, "_run_elevated", lambda command: True)

    worked, message = network.repair("192.168.1.1", timeout=0.0)

    assert worked is False
    assert "did not take effect" in message


def test_repair_confirms_the_address_it_claimed(no_real_network, monkeypatch):
    before = adapter(addresses=["169.254.106.193"])
    after = adapter(addresses=["192.168.1.100"])
    state = {"done": False}

    monkeypatch.setattr(network, "on_windows", lambda: True)
    monkeypatch.setattr(network, "local_address_for", lambda ip: None)
    monkeypatch.setattr(
        network, "adapters",
        lambda: [after] if state["done"] else [before],
    )
    monkeypatch.setattr(network, "already_admin", lambda: True)

    def ran(command):
        state["done"] = True
        return True

    monkeypatch.setattr(network, "_run_directly", ran)

    worked, message = network.repair("192.168.1.1")

    assert worked is True
    # An operator who knows this PC is .100 can diagnose the rest of the lab.
    assert "192.168.1.100" in message
