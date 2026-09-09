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

# --- Machine-wide command bits ------------------------------------------------

MOTOR_BOOT = f"{PROGRAM}.Motor_Boot"
CLEAR_MOTOR_ERROR = f"{PROGRAM}.Clear_Motor_Error"
HOME_BUTTON = f"{PROGRAM}.Home_Button"
RUN_SINGLE = f"{PROGRAM}.Run_1"
RUN_CONTINUOUS = f"{PROGRAM}.Run_2"
RUN_CURVE = f"{PROGRAM}.Run_Curve"

#: Written by :meth:`PlcClient.keepalive` to hold the session open during homing.
PROGRAM_TAG_LIST = PROGRAM


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
