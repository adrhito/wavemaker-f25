"""Transport layer between the application and the ControlLogix PLC.

The rest of the application talks to the machine only through :class:`PlcClient`
or :class:`SimulatedPlc`.  Both expose the same small interface, so every screen
and every test can run with no hardware attached.

Why this exists
---------------
The previous code repeated this block at roughly forty call sites::

    with PLC() as comm:
        comm.IPAddress = self.IP_ADDRESS
        comm.ProcessorSlot = self.PROCESSOR_SLOT
        comm.Write(...)

Each block built a fresh TCP connection, registered a CIP session, opened a
forward connection, discovered the tag's data type, wrote one value, and tore
the whole thing down again.  Writing the eighteen parameters of thirty pistons
therefore cost 540 full connection cycles.  It also meant a failed write raised
out of whichever background thread happened to be running, with nothing to catch
it, and that connection settings were duplicated everywhere.

:class:`PlcClient` keeps one connection open, guards it with a lock so the UI
and worker threads cannot interleave requests, and reconnects once if the
session has gone stale.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from modules.eip import PLC

LOGGER = logging.getLogger("logger")

#: Seconds the socket waits for the PLC before giving up (set by pylogix).
SOCKET_TIMEOUT = 5.0

#: Tag read by :meth:`PlcClient.connect` purely to prove the PLC is reachable.
#: It is a machine-wide command bit that always exists, and reading it has no
#: effect on the machine.
PROBE_TAG = "Program:Wave_Control.Clear_Motor_Error"


class PlcError(RuntimeError):
    """A read or write to the PLC did not succeed."""


class Transport:
    """The interface the application depends on.

    A plain base class rather than ``typing.Protocol``: the lab PC runs
    Windows 7, whose last supported Python is 3.8, and the repository history
    suggests 3.7 is what is actually installed. ``Protocol`` did not arrive
    until 3.8. Nothing subclasses this -- :class:`PlcClient` and
    :class:`SimulatedPlc` are matched by duck typing -- it documents the
    interface and serves as a type annotation.
    """

    connected = False

    def read(self, tag: str) -> Any:
        raise NotImplementedError

    def write(self, tag: str, value: int) -> None:
        raise NotImplementedError

    def write_many(self, values: Mapping[str, int]) -> None:
        raise NotImplementedError

    def read_many(self, tags: Sequence[str]) -> Dict[str, Any]:
        raise NotImplementedError

    def keepalive(self) -> None:
        raise NotImplementedError

    def identity(self) -> Optional[str]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class PlcClient:
    """A single, reusable connection to the PLC.

    All public methods are safe to call from any thread.  Every method raises
    :class:`PlcError` on failure rather than letting a pylogix exception escape,
    so callers have one exception type to handle.
    """

    def __init__(
        self, ip_address: str, processor_slot: int, persistent: bool = True
    ) -> None:
        """``persistent=False`` closes the session after every operation.

        That is what the original code did -- it built and tore down a whole CIP
        session for each read and write. Holding one open is far faster and is
        how pylogix is meant to be used, but it is the biggest change to the
        transport in this rework, and the vendored pylogix 0.2.0 does no
        response/request correlation: :func:`modules.eip._getBytes` is a single
        ``send`` and ``recv``. Serialising every request behind ``_lock`` and
        rebuilding the session on any error covers that, but if the machine ever
        behaves oddly in a way that smells like crossed responses -- a read
        returning another tag's value, writes landing on the wrong parameter --
        this switch restores the old one-session-per-operation behaviour exactly.

        Start the application with ``--fresh-connection`` to set it.
        """
        self.ip_address = ip_address
        self.processor_slot = processor_slot
        self.persistent = persistent
        self._lock = threading.RLock()
        self._plc = None
        self.connected = False

    # -- connection management ------------------------------------------------

    def _open(self):
        """Return a PLC handle, creating one if needed. Caller holds the lock."""
        if self._plc is None:
            plc = PLC()
            plc.IPAddress = self.ip_address
            plc.ProcessorSlot = self.processor_slot
            self._plc = plc
        return self._plc

    def _discard(self) -> None:
        """Drop the current handle so the next call builds a fresh session."""
        plc, self._plc = self._plc, None
        self.connected = False
        if plc is not None:
            try:
                plc.Close()
            except Exception:  # pragma: no cover - teardown must never raise
                pass

    def connect(self) -> bool:
        """Open the connection. Returns True if the PLC answered.

        This is the probe used at startup to decide between live and simulated
        operation, so it reports failure by returning False rather than raising.
        """
        with self._lock:
            try:
                self._open().Read(PROBE_TAG)
            except Exception as exc:
                LOGGER.info("No PLC at %s: %s", self.ip_address, exc)
                self._discard()
                return False
            self.connected = True
            return True

    def close(self) -> None:
        with self._lock:
            self._discard()

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- reads and writes -----------------------------------------------------

    def _attempt(self, description: str, action) -> Any:
        """Run action(plc), retrying once on a fresh session.

        A ControlLogix session can be dropped by the PLC (a program download, a
        network blip, an idle timeout).  The first failure is therefore treated
        as "reconnect and try again"; a second failure is reported to the caller.
        """
        with self._lock:
            for attempt in (1, 2):
                try:
                    result = action(self._open())
                except Exception as exc:
                    self._discard()
                    if attempt == 2:
                        raise PlcError("{0} failed: {1}".format(description, exc)) from exc
                    LOGGER.debug("%s failed (%s); reconnecting", description, exc)
                else:
                    self.connected = True
                    if not self.persistent:
                        self._discard()
                        self.connected = True
                    return result

    def read(self, tag: str) -> Any:
        value = self._attempt("Read " + tag, lambda plc: plc.Read(tag))
        if value is None:
            raise PlcError("Read " + tag + " returned no value")
        return value

    def write(self, tag: str, value: int) -> None:
        self._attempt(
            "Write {0}={1}".format(tag, value), lambda plc: plc.Write(tag, value)
        )

    def write_many(self, values: Mapping[str, int]) -> None:
        """Write several tags over one connection, in the given order."""
        with self._lock:
            for tag, value in values.items():
                self.write(tag, value)

    def read_many(self, tags: Sequence[str]) -> Dict[str, Any]:
        """Read several tags over one connection."""
        with self._lock:
            return dict((tag, self.read(tag)) for tag in tags)

    def keepalive(self) -> None:
        """Exchange a cheap message so an idle session is not dropped.

        Used by the homing loop, which otherwise sits silent for up to forty
        seconds while it polls the drives.
        """
        from app import tags as tag_names

        self._attempt(
            "Keepalive",
            lambda plc: plc.GetProgramTagList(tag_names.PROGRAM_TAG_LIST),
        )

    def identity(self) -> Optional[str]:
        """A one-line description of the controller, for the log.

        Worth recording because the application no longer makes the operator
        open Studio 5000 first: this is the evidence of which controller it
        actually reached.
        """
        try:
            device = self._attempt(
                "Identity",
                lambda plc: plc.GetModuleProperties(self.processor_slot),
            )
        except PlcError as exc:
            LOGGER.debug("Could not read the controller identity: %s", exc)
            return None
        if device is None or not getattr(device, "ProductName", ""):
            return None
        return "{0} rev {1} (serial {2})".format(
            device.ProductName, device.Revision, device.SerialNumber
        )


class SimulatedPlc:
    """An in-memory stand-in for the PLC.

    The application runs against this whenever the real PLC cannot be reached,
    which makes the whole GUI usable for training and makes the test suite
    hardware-free.  Unlike the old "mock mode", writes are actually stored, so
    reading a tag back returns what was written and tests can assert on it.
    """

    def __init__(self) -> None:
        self.connected = True
        self._lock = threading.RLock()
        self.values: Dict[str, Any] = {}
        #: Every write, in order, as (tag, value) -- handy in tests.
        self.history: List[Tuple[str, Any]] = []

    def read(self, tag: str) -> Any:
        with self._lock:
            return self.values.get(tag, 0)

    def write(self, tag: str, value: int) -> None:
        with self._lock:
            self.values[tag] = value
            self.history.append((tag, value))

    def write_many(self, values: Mapping[str, int]) -> None:
        for tag, value in values.items():
            self.write(tag, value)

    def read_many(self, tags: Sequence[str]) -> Dict[str, Any]:
        return dict((tag, self.read(tag)) for tag in tags)

    def keepalive(self) -> None:
        pass

    def identity(self) -> Optional[str]:
        return "Simulated controller"

    def close(self) -> None:
        self.connected = False

    # -- helpers for tests ----------------------------------------------------

    def writes_to(self, tag: str) -> List[Any]:
        """Every value written to tag, in order."""
        return [value for written, value in self.history if written == tag]

    def clear_history(self) -> None:
        self.history.clear()


def connect(
    ip_address: str,
    processor_slot: int,
    simulate: bool = False,
    persistent: bool = True,
):
    """Return (transport, is_live) for the machine.

    Falls back to :class:`SimulatedPlc` when simulate is set or the PLC does not
    answer, so the application always has a usable transport and never has to
    check for None.
    """
    if simulate:
        LOGGER.info("Simulation requested: running without the PLC.")
        return SimulatedPlc(), False

    client = PlcClient(ip_address, processor_slot, persistent=persistent)
    if client.connect():
        LOGGER.info(
            "Connected to PLC at %s slot %s (%s connection).",
            ip_address,
            processor_slot,
            "persistent" if persistent else "fresh-per-operation",
        )
        return client, True

    LOGGER.warning("No PLC at %s: running in simulation. Nothing will move.", ip_address)
    return SimulatedPlc(), False
