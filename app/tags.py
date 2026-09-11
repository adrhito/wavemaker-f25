"""Every ControlLogix tag the application touches, in one place.

Tag names used to be formatted inline at each call site.  That is how
``Program:wave_Control.Run_2`` (lower-case ``w``) ended up in the continuous-run
path, where it silently failed instead of starting the motors.  Defining the
names once means a typo breaks every caller at import time rather than one
caller at run time.

Numbering
---------
Two different indices refer to the same physical piston and they are off by one:

* ``axis`` -- 0..29.  Used by ``Axis[...]`` and ``Live_Motors.<n>``.
* ``motor`` -- 1..30, always ``axis + 1``.  Used by ``Motor_<n>`` and ``Curve_<n>``.

The helpers below take an ``axis`` and do the conversion themselves so callers
never have to remember which convention a tag uses.
"""

from __future__ import annotations

PROGRAM = "Program:Wave_Control"

#: Number of pistons in the machine.
MOTOR_COUNT = 30
#: The array is three pistons deep and ten columns across.
ROWS_PER_COLUMN = 3
COLUMN_COUNT = 10

# --- Machine-wide command bits ------------------------------------------------

MOTOR_BOOT = f"{PROGRAM}.Motor_Boot"
CLEAR_MOTOR_ERROR = f"{PROGRAM}.Clear_Motor_Error"
HOME_BUTTON = f"{PROGRAM}.Home_Button"
RUN_SINGLE = f"{PROGRAM}.Run_1"
RUN_CONTINUOUS = f"{PROGRAM}.Run_2"
RUN_CURVE = f"{PROGRAM}.Run_Curve"

#: Written by :meth:`PlcClient.keepalive` to hold the session open during homing.
PROGRAM_TAG_LIST = PROGRAM



# --- What the operator sees ---------------------------------------------------
# Internally a piston is an axis, 0..29, because that is what the PLC tags are
# built from. On screen it is numbered 1..30, because counting from zero is a
# programming habit and this machine is operated by people who do not have it.
#
# Every message, label and tooltip uses the display number. Nothing that
# reaches the PLC does.


def display_number(axis: int) -> int:
    """The number shown on screen for this axis.

    Pistons are named by where they sit in the picture, not by their axis, so
    the grid reads 1 at the top left and 30 at the bottom right:

        1   4   7  10  13  16  19  22  25  28
        2   5   8  11  14  17  20  23  26  29
        3   6   9  12  15  18  21  24  27  30

    Three per column, which is the machine's own grouping -- "the first four
    rows" of the chamber is pistons 1 to 12.

    The drawing places axis ``a`` at row ``2 - a % 3`` and column
    ``9 - a // 3``, so the name follows from that position. Piston 1 therefore
    drives axis 29. Nothing that reaches the PLC uses these numbers.
    """
    return 30 - 3 * (axis // ROWS_PER_COLUMN) - (axis % ROWS_PER_COLUMN)


def axis_from_display(number: int) -> int:
    """The axis behind a number the operator typed or read."""
    column, row = divmod(number - 1, ROWS_PER_COLUMN)
    return (COLUMN_COUNT - 1 - column) * ROWS_PER_COLUMN + (
        ROWS_PER_COLUMN - 1 - row
    )


def display_list(axes) -> str:
    """A readable list of piston numbers, e.g. "3, 7 and 12".

    Sorted by the number shown, not by axis. Since naming follows position in
    the grid, sorting by axis would list them out of order.
    """
    numbers = sorted(display_number(a) for a in axes)
    if not numbers:
        return ""
    if len(numbers) == 1:
        return str(numbers[0])
    return ", ".join(str(n) for n in numbers[:-1]) + " and " + str(numbers[-1])

def _check_axis(axis: int) -> int:
    if not isinstance(axis, int) or isinstance(axis, bool):
        raise TypeError(f"axis must be an int, got {type(axis).__name__}")
    if not 0 <= axis < MOTOR_COUNT:
        raise ValueError(f"axis must be between 0 and {MOTOR_COUNT - 1}, got {axis}")
    return axis


def live_motor(axis: int) -> str:
    """Bit marking a piston as part of the active selection."""
    return f"{PROGRAM}.Live_Motors.{_check_axis(axis)}"


def motor_field(axis: int, field: str) -> str:
    """A motion parameter on ``Motor_<axis+1>`` (e.g. ``Pos_1``)."""
    return f"{PROGRAM}.Motor_{_check_axis(axis) + 1}.{field}"


def curve_field(axis: int, field: str) -> str:
    """A curve parameter on ``Curve_<axis+1>`` (e.g. ``TimeScale``)."""
    return f"{PROGRAM}.Curve_{_check_axis(axis) + 1}.{field}"


def axis_field(axis: int, field: str) -> str:
    """A drive status word or position on ``Axis[<axis>]``."""
    return f"{PROGRAM}.Axis[{_check_axis(axis)}].{field}"


# --- Axis fields read by the application -------------------------------------

STATE_VAR = "StateVar"
WARN_WORD = "WarnWord"
STATUS_WORD = "StatusWord"
CONTROL_WORD = "ControlWord"
DEMAND_POSITION = "ComDemandPosition"
ACTUAL_POSITION = "ComActualPosition"
