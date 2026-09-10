"""What the drives are actually telling us.

Every drive publishes a status word and a warn word, and both were being read
and reported as raw hex -- "warn word 0x40" tells an operator nothing. The bit
meanings are in the LinMot manual, section 3.24 and 3.25 of
``0185-1093-E_6V7_MA_MotionCtrlSW-SG5-SG7.pdf`` (pages 21 and 22), and they are
transcribed here so a fault can be named.

Two things fell out of reading it that are worth knowing:

* **Status bit 11 is Homed.** That confirms the value ``Motor.HOMED_BIT`` has
  been using, which until now was inferred from the original code's string
  slicing rather than from the manual.
* **Warn bit 7 is "Motor Not Homed"**, which every drive sets before it has been
  homed. It is normal, not a fault, and treating it as one would flag the whole
  array every time. :func:`problems` leaves it out; :func:`describe` still names
  it if you ask for everything.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple


class Bit(NamedTuple):
    name: str
    #: What it means when set, in the operator's terms rather than the manual's.
    meaning: str
    #: True when the bit being set is a problem worth stopping for.
    is_fault: bool = False
    #: True when it is expected in normal use and should not raise an alarm.
    is_routine: bool = False


#: Warn Word, manual section 3.25.
WARN_BITS: Dict[int, Bit] = {
    0: Bit("Motor Hot Sensor", "the motor's temperature sensor has tripped", True),
    1: Bit("Motor Overload", "the motor is calculated to be overheating", True),
    2: Bit("Supply Voltage Low", "motor supply voltage has fallen to the warn limit", True),
    3: Bit("Supply Voltage High", "motor supply voltage has risen to the warn limit", True),
    4: Bit("Position Lag", "the piston is falling behind its commanded position", True),
    5: Bit("Reserved", "reserved"),
    6: Bit("Controller Hot", "the servo controller is running hot", True),
    7: Bit("Not Homed", "this piston has not been homed yet", False, True),
    8: Bit("PTC Sensor 1 Hot", "temperature sensor 1 has tripped", True),
    9: Bit("PTC Sensor 2 Hot", "temperature sensor 2 has tripped", True),
    10: Bit("Brake Resistor Hot", "the regeneration resistor is calculated to be hot", True),
    11: Bit("Speed Lag", "the piston is not reaching its commanded speed", True),
    12: Bit("Position Sensor", "the position sensor is in a warning condition", True),
    13: Bit("Reserved", "reserved"),
    14: Bit("Interface Warning", "the drive's interface layer raised a warning", True),
    15: Bit("Application Warning", "the drive's application layer raised a warning", True),
}

#: Status Word, manual section 3.24.
STATUS_BITS: Dict[int, Bit] = {
    0: Bit("Operation Enabled", "the drive is enabled"),
    1: Bit("Switch On Active", "switch-on is enabled"),
    2: Bit("Enable Operation", "operation is enabled"),
    3: Bit("Error", "the drive is in an error state", True),
    4: Bit("Voltage Enable", "the power bridge is on"),
    5: Bit("Quick Stop", "quick stop is not active"),
    6: Bit("Switch On Locked", "switch-on is locked", True),
    7: Bit("Warning", "one or more warn word bits are set"),
    8: Bit("Event Handler Active", "an event handler is set up"),
    9: Bit("Special Motion Active", "a special command such as homing is running"),
    10: Bit("In Target Position", "the piston is at its target"),
    11: Bit("Homed", "the position sensor is referenced"),
    12: Bit("Fatal Error", "a fatal error, which cannot be acknowledged", True),
    13: Bit("Motion Active", "setpoint generation is running"),
    14: Bit("Range Indicator 1", "in range 1"),
    15: Bit("Range Indicator 2", "in range 2"),
}

#: Bit 11 of the status word, confirmed against the manual.
HOMED_BIT = 11
#: Bit 3, the drive's own error flag.
ERROR_BIT = 3
#: Bit 12, which the manual notes cannot be acknowledged.
FATAL_ERROR_BIT = 12


def _set_bits(word: int, table: Dict[int, Bit]) -> List[int]:
    return [bit for bit in sorted(table) if (word >> bit) & 1]


def describe(word: int, table: Dict[int, Bit]) -> List[str]:
    """Every bit that is set, named."""
    return [table[bit].name for bit in _set_bits(word, table)]


def problems(warn_word: int, status_word: int = 0) -> List[str]:
    """What is actually wrong with this drive, in plain words.

    Routine bits are left out. In particular a drive that has simply not been
    homed yet sets warn bit 7, which is expected and would otherwise flag every
    piston in the machine before the first homing of the day.
    """
    found: List[str] = []
    for bit in _set_bits(warn_word, WARN_BITS):
        entry = WARN_BITS[bit]
        if entry.is_fault and not entry.is_routine:
            found.append("{0} - {1}".format(entry.name, entry.meaning))
    for bit in _set_bits(status_word, STATUS_BITS):
        entry = STATUS_BITS[bit]
        if entry.is_fault:
            found.append("{0} - {1}".format(entry.name, entry.meaning))
    return found


def is_lagging(warn_word: int) -> bool:
    """Whether the drive itself reports the piston falling behind.

    The drive's own judgement, from its configured following-error limit, and
    a far better signal than watching positions from outside.
    """
    return bool((warn_word >> 4) & 1) or bool((warn_word >> 11) & 1)


def has_error(status_word: int) -> bool:
    return bool((status_word >> ERROR_BIT) & 1) or bool(
        (status_word >> FATAL_ERROR_BIT) & 1
    )


def is_homed(status_word: int) -> bool:
    return bool((status_word >> HOMED_BIT) & 1)


def summary(warn_word: int, status_word: int) -> str:
    """One line for the log: what this drive is doing and what is wrong."""
    faults = problems(warn_word, status_word)
    state = "homed" if is_homed(status_word) else "not homed"
    if not faults:
        return "{0}, no faults".format(state)
    return "{0}; {1}".format(state, "; ".join(faults))
