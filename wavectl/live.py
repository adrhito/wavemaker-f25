"""Reaching the real controller, and checking who else is driving it.

Everything that opens a connection to the PLC goes through :func:`connect`,
so there is one place to refuse.
"""

from __future__ import annotations

from typing import List, Tuple

from app import drive_status, params, tags
from app.plc import PlcClient, PlcError
from wavectl import places

DEFAULT_IP = "192.168.1.1"
DEFAULT_SLOT = 1

#: The bits that make pistons move while they are held high.
COMMAND_BITS = (
    ("Run_1", tags.RUN_SINGLE),
    ("Run_2", tags.RUN_CONTINUOUS),
    ("Run_Curve", tags.RUN_CURVE),
    ("Home_Button", tags.HOME_BUTTON),
)


class Unreachable(RuntimeError):
    """The controller could not be reached, or reaching it is switched off."""


class Occupied(RuntimeError):
    """Something else is already driving the controller."""


def connect(ip: str = DEFAULT_IP, slot: int = DEFAULT_SLOT) -> PlcClient:
    """Open a connection to the controller, or raise :class:`Unreachable`.

    Unlike ``app.plc.connect`` this never falls back to the simulator. The
    window does that so it can still open and say so in a banner; a command
    that quietly ran against a mock instead of the machine would report
    motion that never happened.
    """
    if places.mock_only():
        raise Unreachable(
            "{0} is set, so no real controller is contacted. Unset it to "
            "drive the machine.".format(places.MOCK_ONLY_VARIABLE))
    client = PlcClient(ip, slot)
    if not client.connect():
        raise Unreachable(
            "No controller answered at {0}. Check the Ethernet cable is in "
            "this computer, that its adapter has a fixed address on the "
            "controller's network, and that the controller is in Run."
            .format(ip))
    return client


def connect_idle(ip: str = DEFAULT_IP, slot: int = DEFAULT_SLOT) -> PlcClient:
    """Connect, but only to a controller nothing else is driving.

    The software has no way to own the controller, so this is the nearest
    thing to asking: a command bit that is already high was set by some other
    program, and joining in is how two masters end up writing over each
    other. Only reads are made before this returns, so refusing here leaves
    the machine exactly as it was found.
    """
    client = connect(ip, slot)
    try:
        high = bits_high(client)
    except PlcError as exc:
        client.close()
        raise Unreachable(
            "The controller at {0} answered, but its command bits could not "
            "be read: {1}".format(ip, exc)) from exc
    if high:
        client.close()
        raise Occupied(
            "{0} already high on the controller at {1}: something else is "
            "driving the machine, so no session was started and nothing was "
            "written. If that is a run left behind by a program that died, "
            "drop it with: wavectl stop --direct".format(" and ".join(high), ip))
    return client


def bits_high(transport) -> List[str]:
    """Names of the command bits that read high right now."""
    return [name for name, tag in COMMAND_BITS if transport.read(tag)]


def drop_bits(transport) -> List[str]:
    """Write every command bit low. Returns what could not be written.

    Each bit is attempted even if an earlier one failed: the one that failed
    may not be the one that is moving the pistons.
    """
    failures = []
    for name, tag in COMMAND_BITS:
        try:
            transport.write(tag, 0)
        except PlcError as exc:
            failures.append("{0}: {1}".format(name, exc))
    return failures


def read_drive(transport, axis: int) -> dict:
    """What one drive reports, under its piston's display number."""
    row = {"piston": tags.display_number(axis)}
    try:
        status = int(transport.read(tags.axis_field(axis, tags.STATUS_WORD)))
        warn = int(transport.read(tags.axis_field(axis, tags.WARN_WORD)))
        position = params.to_mm(
            transport.read(tags.axis_field(axis, tags.ACTUAL_POSITION)))
    except (PlcError, TypeError, ValueError) as exc:
        row["unreadable"] = str(exc)
        return row
    row.update({
        # Adding zero turns the "-0.0" a piston resting at the top reads as
        # into "0.0".
        "mm": round(position, 1) + 0.0,
        "homed": drive_status.is_homed(status),
        "enabled": bool(status & 1),
        "problems": drive_status.problems(warn, status),
    })
    return row


def drive_line(row: dict) -> str:
    if "unreadable" in row:
        return "{0:>3}  unreadable: {1}".format(row["piston"], row["unreadable"])
    return "{0:>3}  {1:>6.1f} mm  {2}  {3}  {4}".format(
        row["piston"], row["mm"],
        "homed" if row["homed"] else "not homed",
        "enabled" if row["enabled"] else "off",
        "; ".join(row["problems"]) or "ok")


def troubled(rows: List[dict]) -> str:
    """The first drive with something wrong, and how many more there are."""
    bad = [row for row in rows if row.get("unreadable") or row.get("problems")]
    if not bad:
        return ""
    first = bad[0]
    more = " (and {0} more)".format(len(bad) - 1) if len(bad) > 1 else ""
    return "piston {0}: {1}{2}".format(
        first["piston"], first.get("unreadable") or "; ".join(first["problems"]), more)


def stop_directly(ip: str = DEFAULT_IP, slot: int = DEFAULT_SLOT) -> Tuple[bool, str]:
    """Write the command bits low on a connection of our own.

    The way out when the session cannot be asked: it has died, it has hung,
    or its own connection to the controller is what failed. Dropping a run
    bit is always the safe direction, so this does not ask who raised it.
    Returns whether every bit then read low, and a sentence saying so.
    """
    client = connect(ip, slot)
    try:
        failures = drop_bits(client)
        try:
            high = bits_high(client)
        except PlcError as exc:
            failures.append("could not read them back: {0}".format(exc))
            high = []
    finally:
        client.close()
    names = ", ".join(name for name, _tag in COMMAND_BITS)
    if failures:
        return False, ("Could not write every command bit low at {0}: {1}. "
                       "Use the physical stop.".format(ip, "; ".join(failures)))
    if high:
        return False, ("{0} written low at {1}, but {2} still read high: "
                       "something is raising it again. Use the physical stop."
                       .format(names, ip, " and ".join(high)))
    return True, "{0} written low at {1}; all read low".format(names, ip)


def survey(ip: str = DEFAULT_IP, slot: int = DEFAULT_SLOT) -> dict:
    """Read the command bits and all thirty drives. Writes nothing."""
    client = connect(ip, slot)
    try:
        high = bits_high(client)
        rows = [read_drive(client, axis) for axis in sorted(
            range(tags.MOTOR_COUNT), key=tags.display_number)]
        identity = client.identity()
    finally:
        client.close()
    return {"bits_high": high, "drives": rows, "controller": identity}
