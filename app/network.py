"""Getting this PC onto the controller's network, without a person doing it.

The fault this file exists to prevent
-------------------------------------
On 26 September 2026 the lab could not run the wavemaker at all. Nothing was
wrong with the application, the cable, the PLC or the ladder logic. The PC's
Ethernet adapter had simply lost its static address and fallen back to a
169.254.x.x link-local one, which has no route to the controller at
192.168.1.1. The application said "No PLC at 192.168.1.1: timed out" and ran
the mock, and six good connections four days earlier were no help at all.

That is a thirty-second fix that cost a lab session, and it will happen again:
Windows clears an adapter's static IPv4 configuration on some driver
reinstalls and network resets, and a brand-new laptop has never had one. So the
application diagnoses it and offers to fix it, rather than reporting a timeout
and leaving the operator to guess.

What this module will and will not do
-------------------------------------
* It tells apart the three cases that all look like "no PLC": no wired link at
  all, a wired link with no usable address, and a correct address with a silent
  controller. Only the operator can fix the first and the last, and saying
  which is most of the value here.
* It can set a static address, which needs administrator rights. It asks
  Windows to elevate one command; the operator sees the standard prompt and
  clicks Yes. Nothing is elevated without that.
* It never touches the default route. The PLC segment has no router, and
  claiming a default gateway here would take the whole PC off the internet --
  on the lab's Windows 7 machine that also means losing the UNC network.

Windows 7 is a hard constraint
------------------------------
``netsh`` is used rather than the ``New-NetIPAddress`` PowerShell cmdlet, which
does not exist before Windows 8. Adapters are enumerated through
``GetAdaptersAddresses`` rather than by parsing command output, because the
text ``netsh`` and ``ipconfig`` print is translated and the lab has no
guarantee of an English Windows. Both work on Windows 7 and on Windows 11.
"""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import subprocess
import time
from typing import List, NamedTuple, Optional

LOGGER = logging.getLogger("logger")

#: The controller's network. The PLC holds a static address and this PC has to
#: be on the same /24 or there is no route to it at all.
PLC_PREFIX_LENGTH = 24
PLC_NETMASK = "255.255.255.0"

#: Host addresses tried, in order, when claiming one. 192.168.1.1 is the PLC
#: and .100 is what the application's own error message has always suggested,
#: so it is what an operator will have been told to use.
CANDIDATE_HOSTS = (100, 101, 102, 110, 120, 150, 200)

#: How long to wait for an address to appear after netsh is asked to set it.
#: Elevation puts a UAC prompt in the way, so this has to allow for a person.
REPAIR_TIMEOUT_SECONDS = 45.0
REPAIR_POLL_SECONDS = 0.5

# What went wrong, as something a caller can branch on rather than a sentence.
OK = "ok"
NO_LINK = "no-link"
NO_ADDRESS = "no-address"
WRONG_SUBNET = "wrong-subnet"
UNSUPPORTED = "unsupported"


class Adapter(NamedTuple):
    """One network interface, as Windows describes it."""

    index: int
    #: The name shown in Network Connections, e.g. "Ethernet". What netsh wants.
    name: str
    description: str
    #: True when a cable is in and the link is up.
    up: bool
    wired: bool
    addresses: List[str]

    def on_plc_network(self, plc_ip: str) -> bool:
        for address in self.addresses:
            if same_network(address, plc_ip):
                return True
        return False


class Diagnosis(NamedTuple):
    """Why the controller cannot be reached, and whether software can fix it."""

    reason: str
    #: A sentence for the operator. Says what to do, not merely what is wrong.
    message: str
    #: True when :func:`repair` has a realistic chance. False means a cable, a
    #: power switch or a keyswitch, and no amount of retrying will help.
    repairable: bool
    #: The adapter that would be configured, when there is an obvious one.
    adapter: Optional[Adapter] = None
    #: The address this PC would use to reach the PLC, if it has a route.
    local_address: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.reason == OK


# -- reading the network ------------------------------------------------------

def on_windows() -> bool:
    return os.name == "nt"


def same_network(local_ip, target_ip, prefix_length=PLC_PREFIX_LENGTH) -> bool:
    """Whether two IPv4 addresses share the first ``prefix_length`` bits.

    ipaddress.ip_network would do this, but building two network objects per
    call to compare a pair of dotted quads is more machinery than one shift
    needs, and this is called from a refresh path.
    """
    try:
        here = _packed(local_ip)
        there = _packed(target_ip)
    except (TypeError, ValueError):
        return False
    if prefix_length <= 0:
        return True
    mask = (0xFFFFFFFF << (32 - prefix_length)) & 0xFFFFFFFF
    return (here & mask) == (there & mask)


def _packed(dotted) -> int:
    parts = str(dotted).split(".")
    if len(parts) != 4:
        raise ValueError("not an IPv4 address: {0!r}".format(dotted))
    value = 0
    for part in parts:
        octet = int(part)
        if not 0 <= octet <= 255:
            raise ValueError("not an IPv4 address: {0!r}".format(dotted))
        value = (value << 8) | octet
    return value


def is_link_local(address) -> bool:
    """Windows' own 169.254.x.x fallback, which reaches nothing routable.

    Worth naming because it is the state the lab was found in, and because it
    means "DHCP answered nothing and no static address is configured" rather
    than "misconfigured". It is the signature of the fault, not a choice
    anybody made.
    """
    return same_network(address, "169.254.0.0", 16)


def local_address_for(target_ip: str) -> Optional[str]:
    """The address this PC would use to reach ``target_ip``, or None.

    This asks the routing table and sends nothing: a UDP socket's connect()
    only fixes the peer locally, so there is no packet, no timeout, and no
    dependence on the PLC being switched on. That last part is the point --
    this has to give a straight answer about a machine that is powered down.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((target_ip, 9))       # discard port; nothing is sent
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


# IfType values from iptypes.h. Anything that is not loopback, not wireless and
# not a tunnel counts as a cable for our purposes.
_IF_TYPE_LOOPBACK = 24
_IF_TYPE_WIFI = 71
_IF_TYPE_TUNNEL = 131
_OPER_STATUS_UP = 1
_AF_UNSPEC = 0
_GAA_FLAG_SKIP_ANYCAST = 0x0002
_GAA_FLAG_SKIP_MULTICAST = 0x0004
_GAA_FLAG_SKIP_DNS_SERVER = 0x0008
_ERROR_BUFFER_OVERFLOW = 111


class _SOCKADDR(ctypes.Structure):
    _fields_ = [("sa_family", ctypes.c_ushort),
                ("sa_data", ctypes.c_ubyte * 26)]


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.POINTER(_SOCKADDR)),
                ("iSockaddrLength", ctypes.c_int)]


class _IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


# Only the leading fields are declared, up to the last one actually read.
# ctypes works out offsets from this declaration, so the prefix has to match
# the real layout exactly -- but the tail can be left off safely, and leaving
# it off avoids depending on members that differ between Windows versions.
# The leading 8 bytes are a union of a ULONGLONG with {ULONG, ULONG}, which is
# why Length and Flags appear as two longs here.
_IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", _SOCKET_ADDRESS),
]


class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


_IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_ulong),
]


def _ipv4_of(sockaddr_ptr) -> Optional[str]:
    """The dotted quad in a SOCKADDR, or None when it is not IPv4."""
    if not sockaddr_ptr:
        return None
    sockaddr = sockaddr_ptr.contents
    if sockaddr.sa_family != socket.AF_INET:
        return None
    # sa_data holds the port in its first two bytes, then the four address
    # bytes, so the address starts at offset 2.
    octets = sockaddr.sa_data[2:6]
    return ".".join(str(int(b)) for b in octets)


def _adapters_via_iphlpapi() -> List[Adapter]:
    get = ctypes.windll.iphlpapi.GetAdaptersAddresses
    flags = (_GAA_FLAG_SKIP_ANYCAST | _GAA_FLAG_SKIP_MULTICAST
             | _GAA_FLAG_SKIP_DNS_SERVER)

    size = ctypes.c_ulong(15000)
    buffer = ctypes.create_string_buffer(size.value)
    result = get(_AF_UNSPEC, flags, None, buffer, ctypes.byref(size))
    if result == _ERROR_BUFFER_OVERFLOW:
        # The call has written the size it needs into `size`.
        buffer = ctypes.create_string_buffer(size.value)
        result = get(_AF_UNSPEC, flags, None, buffer, ctypes.byref(size))
    if result != 0:
        raise OSError("GetAdaptersAddresses failed: {0}".format(result))

    found = []
    node = ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
    while node:
        entry = node.contents
        addresses = []
        unicast = entry.FirstUnicastAddress
        while unicast:
            dotted = _ipv4_of(unicast.contents.Address.lpSockaddr)
            if dotted:
                addresses.append(dotted)
            unicast = unicast.contents.Next
        found.append(Adapter(
            index=int(entry.IfIndex),
            name=entry.FriendlyName or "",
            description=entry.Description or "",
            up=int(entry.OperStatus) == _OPER_STATUS_UP,
            wired=int(entry.IfType) not in (
                _IF_TYPE_LOOPBACK, _IF_TYPE_WIFI, _IF_TYPE_TUNNEL),
            addresses=addresses,
        ))
        node = entry.Next
    return found


def adapters() -> List[Adapter]:
    """Every network interface, from the Windows IP helper API.

    Returns an empty list off Windows, where there is nothing this module can
    configure anyway, and on any failure -- a diagnosis that cannot see the
    adapters is still better than an exception out of a refresh.
    """
    if not on_windows():
        return []
    try:
        return _adapters_via_iphlpapi()
    except (OSError, AttributeError):
        LOGGER.exception("Could not enumerate the network adapters")
        return []


def wired_candidates(found: Optional[List[Adapter]] = None) -> List[Adapter]:
    """Wired adapters with a link, best first.

    "Best" means already on the PLC's network, then anything else with a cable
    in. A dock or a USB dongle is a different interface from the built-in port
    and carries its own blank configuration, so the built-in one cannot be
    assumed to be the one the cable is in.
    """
    if found is None:
        found = adapters()
    live = []
    for adapter in found:
        if adapter.wired and adapter.up:
            live.append(adapter)
    live.sort(key=lambda a: (not a.addresses, a.index))
    return live


# -- diagnosing ---------------------------------------------------------------

#: Adapters whose description matches this are sorted last among the wired
#: candidates. Bluetooth personal-area networking reports the same IfType as a
#: real Ethernet port, so it cannot be told apart by type -- but it is never
#: the port the PLC cable is in. This only ever changes the ORDER of the
#: candidates, never excludes one, because the operator is shown which adapter
#: is about to be configured and a wrong guess here must stay recoverable.
_UNLIKELY = ("bluetooth", "virtual", "vmware", "hyper-v", "loopback", "tap")


def _unlikely(adapter: Adapter) -> bool:
    haystack = (adapter.name + " " + adapter.description).lower()
    for word in _UNLIKELY:
        if word in haystack:
            return True
    return False


def diagnose(plc_ip: str) -> Diagnosis:
    """Why the controller cannot be reached, in terms of what to do about it.

    Three failures look identical from the application's point of view -- the
    PLC does not answer -- and have completely different remedies. Reporting a
    timeout for all three is what left the lab guessing.

    Note that "is there a route to the PLC" is NOT the question, and answering
    it is actively misleading. Measured on the session laptop: with the
    Ethernet port on a 169.254 address and Wi-Fi up, the operating system
    happily routed 192.168.1.1 out of the Wi-Fi default gateway and reported a
    local address of 10.23.16.189. There was a route; it went to the internet.
    The question is whether some adapter holds an address on the PLC's own /24.
    """
    if not on_windows():
        return Diagnosis(
            UNSUPPORTED,
            "Automatic network setup only works on Windows. Give this PC an "
            "address on the {0}.x network by hand.".format(
                plc_ip.rsplit(".", 1)[0]),
            repairable=False,
            local_address=local_address_for(plc_ip),
        )

    found = adapters()
    for adapter in found:
        if adapter.on_plc_network(plc_ip):
            return Diagnosis(
                OK,
                "This PC is on the controller's network as {0}.".format(
                    ", ".join(adapter.addresses)),
                repairable=False,
                adapter=adapter,
                local_address=local_address_for(plc_ip),
            )

    candidates = wired_candidates(found)
    candidates.sort(key=lambda a: (_unlikely(a), not a.addresses, a.index))

    if not candidates:
        return Diagnosis(
            NO_LINK,
            "No network cable is connected. Plug this PC into the wavemaker's "
            "switch, then try again.",
            repairable=False,
            local_address=local_address_for(plc_ip),
        )

    chosen = candidates[0]
    network_part = plc_ip.rsplit(".", 1)[0]
    only_link_local = True
    for address in chosen.addresses:
        if not is_link_local(address):
            only_link_local = False
            break

    if not chosen.addresses or only_link_local:
        # The signature of the fault: DHCP answered nothing and no static
        # address is configured, so Windows invented a 169.254 one.
        return Diagnosis(
            NO_ADDRESS,
            "The cable is in on {0}, but this PC has no address on the "
            "controller's network, so there is no way to reach it. It can be "
            "set up for you.".format(chosen.name),
            repairable=True,
            adapter=chosen,
            local_address=local_address_for(plc_ip),
        )

    return Diagnosis(
        WRONG_SUBNET,
        "{0} is on {1}, which cannot reach the controller at {2}. An address "
        "on {3}.x can be added for you.".format(
            chosen.name, ", ".join(chosen.addresses), plc_ip, network_part),
        repairable=True,
        adapter=chosen,
        local_address=local_address_for(plc_ip),
    )


# -- repairing ----------------------------------------------------------------

def already_admin() -> bool:
    """Whether this process can configure an adapter without a UAC prompt."""
    if not on_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def choose_address(plc_ip: str, taken=None) -> Optional[str]:
    """A host address on the PLC's network that nothing here already holds.

    Deliberately conservative: it only avoids addresses this PC can see. It
    cannot know what else is on the lab switch, which is why the operator is
    told which address was claimed and the run refuses rather than proceeding
    if the controller still does not answer.
    """
    if taken is None:
        taken = []
        for adapter in adapters():
            taken.extend(adapter.addresses)
    network_part = plc_ip.rsplit(".", 1)[0]
    try:
        plc_host = int(plc_ip.rsplit(".", 1)[1])
    except (IndexError, ValueError):
        plc_host = 1
    for host in CANDIDATE_HOSTS:
        if host == plc_host:
            continue
        candidate = "{0}.{1}".format(network_part, host)
        if candidate not in taken:
            return candidate
    return None


def netsh_command(adapter: Adapter, address: str, replace: bool) -> List[str]:
    """The netsh command line that gives ``adapter`` ``address``.

    ``replace`` picks between two genuinely different operations, and getting
    it wrong is how this feature could break somebody's laptop:

    * ``set address ... static`` replaces whatever the adapter had and turns
      DHCP off. Right when the adapter has nothing but a 169.254 fallback,
      because there is nothing to preserve.
    * ``add address`` puts a second, static address alongside the existing
      configuration and leaves DHCP alone. Right when the adapter already holds
      a real address, because on somebody's own laptop that cable may be their
      internet, and taking it away to reach a PLC would be a poor trade.

    ``netsh`` is used rather than New-NetIPAddress because the lab PC is
    Windows 7, where that cmdlet does not exist.
    """
    verb = "set" if replace else "add"
    command = ["netsh", "interface", "ipv4", verb, "address",
               "name={0}".format(adapter.name)]
    if replace:
        command.append("static")
    command.extend([address, PLC_NETMASK])
    # No gateway, deliberately. The PLC segment has no router, and claiming a
    # default route here would take the whole PC off the internet.
    return command


#: ShellExecuteW returns a value above this on success. It says only that the
#: process was launched, never that netsh liked its arguments -- which is why
#: success is judged afterwards by looking at the adapter, not at this.
_SHELL_EXECUTE_MIN_SUCCESS = 32
_SW_HIDE = 0


def _quoted(command: List[str]) -> str:
    """Render an argument list for ShellExecuteW, which wants one string.

    Only values containing spaces are quoted, and only the value half of a
    ``name=...`` pair, because netsh wants ``name="Local Area Connection"``
    and chokes on ``"name=Local Area Connection"``.
    """
    parts = []
    for argument in command[1:]:          # the executable is passed separately
        if "=" in argument:
            key, _, value = argument.partition("=")
            if " " in value:
                parts.append('{0}="{1}"'.format(key, value))
                continue
        if " " in argument:
            parts.append('"{0}"'.format(argument))
        else:
            parts.append(argument)
    return " ".join(parts)


def _run_directly(command: List[str]) -> bool:
    """Run netsh in this process's own rights, with no console window."""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = _SW_HIDE
    try:
        completed = subprocess.call(command, startupinfo=startup)
    except OSError:
        LOGGER.exception("Could not run netsh")
        return False
    if completed != 0:
        LOGGER.warning("netsh exited with %s", completed)
    return completed == 0


def _run_elevated(command: List[str]) -> bool:
    """Ask Windows to run netsh as administrator.

    The operator sees the standard UAC prompt and clicks Yes; nothing is
    elevated behind their back. The return value says only that the prompt was
    raised and the process started -- whether the address was actually set is
    settled afterwards by reading the adapter back, which is the thing we
    actually care about and cannot be faked.
    """
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", command[0], _quoted(command), None, _SW_HIDE
        )
    except (AttributeError, OSError):
        LOGGER.exception("Could not ask for administrator rights")
        return False
    if result <= _SHELL_EXECUTE_MIN_SUCCESS:
        # 1223 is ERROR_CANCELLED: the operator said No, which is their right
        # and is not an error worth a dialog of its own.
        LOGGER.info("Elevation was declined or failed (ShellExecute %s)", result)
        return False
    return True


def wait_until_on_network(plc_ip: str, timeout: float = REPAIR_TIMEOUT_SECONDS,
                          poll: float = REPAIR_POLL_SECONDS) -> bool:
    """Block until some adapter holds an address on the PLC's network.

    Generous, because a UAC prompt sits in the middle of this and a person has
    to read it. Called from a worker thread, never the Tk thread.
    """
    deadline = time.time() + timeout
    while True:
        for adapter in adapters():
            if adapter.on_plc_network(plc_ip):
                return True
        if time.time() >= deadline:
            return False
        time.sleep(poll)


def repair(plc_ip: str, diagnosis: Optional[Diagnosis] = None,
           timeout: float = REPAIR_TIMEOUT_SECONDS):
    """Give this PC an address on the controller's network.

    Returns ``(worked, message)``. The message is for the operator either way,
    and on success it names the address claimed, because an operator who knows
    the PC is 192.168.1.100 can diagnose the rest of the lab themselves.

    Success is decided by reading the adapters back, not by netsh's exit code.
    An elevated process cannot hand its status back cheaply, and more
    importantly the question that matters is "does this PC now hold an address
    on the PLC's network", which is directly observable.
    """
    if diagnosis is None:
        diagnosis = diagnose(plc_ip)
    if diagnosis.ok:
        return True, diagnosis.message
    if not diagnosis.repairable or diagnosis.adapter is None:
        return False, diagnosis.message

    address = choose_address(plc_ip)
    if address is None:
        return False, (
            "Could not find a free address on the controller's network to use. "
            "Set one by hand on {0}.".format(diagnosis.adapter.name)
        )

    # Replace only when there is nothing worth keeping. See netsh_command.
    replace = True
    for held in diagnosis.adapter.addresses:
        if not is_link_local(held):
            replace = False
            break

    command = netsh_command(diagnosis.adapter, address, replace)
    LOGGER.info("Setting %s on %s (%s)", address, diagnosis.adapter.name,
                "replacing" if replace else "adding alongside")

    if already_admin():
        started = _run_directly(command)
    else:
        started = _run_elevated(command)
    if not started:
        return False, (
            "Setting the address needs administrator rights, and the request "
            "was refused. Either answer Yes to the Windows prompt, or set {0} "
            "to {1} with mask {2} by hand in Network Connections.".format(
                diagnosis.adapter.name, address, PLC_NETMASK)
        )

    if not wait_until_on_network(plc_ip, timeout=timeout):
        return False, (
            "The address did not take effect within {0:.0f} seconds. Check {1} "
            "in Network Connections.".format(timeout, diagnosis.adapter.name)
        )
    return True, (
        "This PC is now {0} on the controller's network, using {1}.".format(
            address, diagnosis.adapter.name)
    )
