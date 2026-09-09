"""One piston of the wavemaker.

A :class:`Motor` holds the parameters an operator has entered for a piston and
knows how to push them to the PLC and read the drive's state back.

For the drive state, warn word and status word, the meaning of each bit is in
``0185-1093-E_6V7_MA_MotionCtrlSW-SG5-SG7.pdf``.
"""

from __future__ import annotations

from logging import Logger, getLogger
from typing import Any, Dict, List, NamedTuple, Optional

from app import params, tags
from app.plc import PlcError, Transport
from modules.logging.log_utils import LOGGER_NAME

#: Bit of the drive Status Word that reports "homed", counting from zero.
#:
#: The old code found this bit by turning the status word into a string with
#: ``bin()`` and indexing twelve characters from the right, which raised an
#: exception whenever the word was small enough that ``bin()`` dropped leading
#: zeroes -- i.e. exactly when the drive was least ready.  Shifting the integer
#: gets the same bit without the string handling.
HOMED_BIT = 11


class MotorStatus(NamedTuple):
    """A snapshot of what the drive reports about itself."""

    state: int
    warn_word: int
    status_word: int
    control_word: int

    @property
    def homed(self) -> bool:
        return bool((self.status_word >> HOMED_BIT) & 1)

    @property
    def has_warning(self) -> bool:
        return self.warn_word != 0

    def describe(self) -> str:
        return (
            "state={0:#b} status={1:#b} control={2:#b} warn={3:#b}".format(
                self.state, self.status_word, self.control_word, self.warn_word
            )
        )


class Motor:
    """The parameters and drive state of a single piston.

    ``axis`` is 0..29.  ``motor_id`` is ``axis + 1`` and is what the
    ``Motor_<n>`` and ``Curve_<n>`` PLC structures are numbered by; callers
    should not need it, because :mod:`app.tags` does the conversion.
    """

    LOGGER: Logger = getLogger(LOGGER_NAME)

    def __init__(self, axis: int) -> None:
        if isinstance(axis, bool) or not isinstance(axis, int):
            raise TypeError(
                "axis must be an integer from 0 to {0}".format(tags.MOTOR_COUNT - 1)
            )
        if not 0 <= axis < tags.MOTOR_COUNT:
            raise ValueError(
                "axis must be between 0 and {0} inclusive, got {1}".format(
                    tags.MOTOR_COUNT - 1, axis
                )
            )

        self.axis: int = axis
        self.motor_id: int = axis + 1

        # Physical layout: three rows of ten.
        self.row: int = axis % 3 + 1
        self.column: int = axis // 3 + 1

        #: What the operator has asked for.
        self.write_params: Dict[str, int] = params.defaults()
        #: What has been confirmed written to the PLC. Empty until the first
        #: successful write; a key only appears once its write returned cleanly.
        self.current_params: Dict[str, int] = {}
        #: True once every parameter has been written without error.
        self.write_success: bool = False

        self.last_status: Optional[MotorStatus] = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Motor(axis={0}, row={1}, column={2})".format(
            self.axis, self.row, self.column
        )

    # -- parameters -----------------------------------------------------------

    def set_param(self, name: str, value: int) -> None:
        """Set one parameter, rejecting values outside the machine's limits."""
        spec = params.BY_NAME.get(name)
        if spec is None:
            raise KeyError("Unknown parameter: {0}".format(name))
        problem = spec.validate(value)
        if problem:
            raise ValueError(problem)
        if self.write_params.get(name) != value:
            self.write_params[name] = value
            # The PLC no longer holds what the operator has asked for.
            self.write_success = False

    def update_params(self, values: Dict[str, int]) -> None:
        """Set several parameters at once, validating all of them first.

        Either every value is applied or none is, so a bad value part-way
        through a preset cannot leave the piston half-configured.
        """
        problems = [
            problem
            for problem in (
                params.BY_NAME[name].validate(value)
                for name, value in values.items()
                if name in params.BY_NAME
            )
            if problem
        ]
        if problems:
            raise ValueError("; ".join(problems))
        for name, value in values.items():
            if name in params.BY_NAME:
                self.set_param(name, value)

    def validate(self) -> List[str]:
        """Every reason this piston is not ready to be written to the PLC."""
        return params.validate_all(self.write_params)

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    @property
    def is_synced(self) -> bool:
        """True when the PLC is known to hold exactly what the operator asked for."""
        if not self.write_success:
            return False
        return all(
            self.current_params.get(name, object()) == value
            for name, value in self.write_params.items()
        )

    def pending_changes(self) -> Dict[str, int]:
        """Parameters whose PLC value is not known to match the requested one."""
        missing = object()
        return dict(
            (name, value)
            for name, value in self.write_params.items()
            if self.current_params.get(name, missing) != value
        )

    def describe_params(self) -> str:
        """A short report for the tooltip on this piston's checkbox."""
        if not self.current_params:
            lines = ["Not yet written to the machine:"]
            lines.extend(
                "{0}: {1}".format(spec.name, self.write_params[spec.name])
                for spec in params.PARAMS
            )
            return "\n".join(lines)

        lines = []
        for spec in params.PARAMS:
            wanted = self.write_params[spec.name]
            on_plc = self.current_params.get(spec.name)
            if on_plc == wanted:
                lines.append("{0}: {1}".format(spec.name, wanted))
            else:
                lines.append(
                    "{0}: {1} -> {2} (not written yet)".format(
                        spec.name, on_plc if on_plc is not None else "?", wanted
                    )
                )
        return "\n".join(lines)

    # -- talking to the PLC ---------------------------------------------------

    def write_to(self, plc: Transport, force: bool = False) -> None:
        """Send the operator's parameters to the PLC.

        Only parameters that have changed are sent unless ``force`` is set, and
        ``current_params`` is updated one parameter at a time as each write
        returns.  If a write fails part-way through, ``current_params`` still
        describes what the PLC actually holds instead of claiming success for
        values that never arrived -- which is what the previous code did, since
        it copied the whole requested dictionary across regardless of outcome.

        Raises :class:`ValueError` if any parameter is out of range and
        :class:`app.plc.PlcError` if the machine rejects a write.
        """
        problems = self.validate()
        if problems:
            self.write_success = False
            raise ValueError(
                "Motor {0}: {1}".format(self.axis, "; ".join(problems))
            )

        outstanding = self.write_params if force else self.pending_changes()
        if not outstanding:
            self.write_success = True
            return

        # Move type and profile land before the values they govern, as the
        # original write sequence did.
        for spec in params.WRITE_ORDER:
            if spec.name not in outstanding:
                continue
            value = self.write_params[spec.name]
            plc.write(spec.tag(self.axis), value)
            self.current_params[spec.name] = value

        self.write_success = True

    def read_from(self, plc: Transport) -> Dict[str, int]:
        """Read every parameter back from the PLC.

        Returns what the machine reports and records it in ``current_params``,
        so the comparison against ``write_params`` reflects the machine rather
        than what the application hoped it had written.
        """
        readings: Dict[str, int] = {}
        for spec in params.PARAMS:
            value = plc.read(spec.tag(self.axis))
            readings[spec.name] = int(value)
        self.current_params.update(readings)
        return readings

    def read_status(self, plc: Transport) -> MotorStatus:
        """Read the drive's state, warn, status and control words."""
        status = MotorStatus(
            state=int(plc.read(tags.axis_field(self.axis, tags.STATE_VAR))),
            warn_word=int(plc.read(tags.axis_field(self.axis, tags.WARN_WORD))),
            status_word=int(plc.read(tags.axis_field(self.axis, tags.STATUS_WORD))),
            control_word=int(plc.read(tags.axis_field(self.axis, tags.CONTROL_WORD))),
        )
        self.last_status = status
        return status

    def is_homed(self, plc: Transport) -> bool:
        """Whether the drive reports itself homed.

        A read failure is reported as "not homed" rather than raised: the homing
        loop polls every drive repeatedly and one unlucky read should retry on
        the next pass, not abort homing for the whole machine.
        """
        try:
            return self.read_status(plc).homed
        except (PlcError, ValueError, TypeError) as exc:
            self.LOGGER.debug("Could not read status of motor %s: %s", self.axis, exc)
            return False

    def read_positions(self, plc: Transport) -> Dict[str, Any]:
        """Demanded and actual position, for analytics."""
        demand = plc.read(tags.axis_field(self.axis, tags.DEMAND_POSITION))
        actual = plc.read(tags.axis_field(self.axis, tags.ACTUAL_POSITION))
        return {
            "demand": demand,
            "actual": actual,
            "displacement": abs(demand - actual),
        }
