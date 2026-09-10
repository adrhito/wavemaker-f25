"""The eighteen motion parameters, described once.

Previously this information was spread across four places that had to be kept
in step by hand: a list of names in ``Model.ALL_PARAMS``, a parallel list of
tooltip strings in ``Model.ALL_PARAM_TIPS``, a dictionary of defaults in
``Motor.__init__``, and a set of hand-written ``write_*`` methods in ``Motor``
that hard-coded both the PLC field name and the valid range.  They had already
drifted apart -- the tooltips told the operator acceleration could go to 50,000
and position to 370, while the code rejected anything above 20,000 and 368.

Everything now comes from :data:`PARAMS` below, so the entry boxes, the
tooltips, the defaults, the validation and the PLC field names cannot disagree.

A note on the position limit
----------------------------
The code enforced 368 mm for years while the GUI manual, the tooltips and the
lab's own saved presets all said 370.  That 2 mm gap was not cosmetic: applying
``Presets/massive.csv``, which stores 370, was rejected outright, so none of its
other values -- the 20,000 accelerations in particular -- ever reached the
machine.  That is the "presets do not work" fault the lab has been living with.

370 is what the manual specifies and what the lab actually uses, and it sits
well inside the drive's own configured limits of -57 to 453 mm with home at
390 mm (see ``LinMot Drive Config``).  Raised with the lab's agreement.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

from app import tags


class ParamSpec(NamedTuple):
    """One motion parameter."""

    #: Label shown in the UI and used as the dictionary key everywhere.
    name: str
    #: Field name on the PLC structure, e.g. ``Pos_1``.
    field: str
    #: Which PLC structure the field lives on: ``motor`` or ``curve``.
    structure: str
    #: Starting value for a newly selected piston.
    default: int
    #: Inclusive bounds. ``None`` means unbounded in that direction.
    minimum: Optional[int]
    maximum: Optional[int]
    #: Text shown when the operator hovers the label.
    help: str

    def tag(self, axis: int) -> str:
        """The full PLC tag for this parameter on ``axis``."""
        if self.structure == "curve":
            return tags.curve_field(axis, self.field)
        return tags.motor_field(axis, self.field)

    def describe_range(self) -> str:
        if self.minimum is None and self.maximum is None:
            return "no fixed limit"
        if self.minimum is None:
            return "at most {0:,}".format(self.maximum)
        if self.maximum is None:
            return "at least {0:,}".format(self.minimum)
        return "{0:,} to {1:,}".format(self.minimum, self.maximum)

    def validate(self, value: int) -> Optional[str]:
        """Return an error message if ``value`` is out of range, else ``None``."""
        if self.minimum is not None and value < self.minimum:
            return "{0} must be at least {1:,} (got {2:,})".format(
                self.name, self.minimum, value
            )
        if self.maximum is not None and value > self.maximum:
            return "{0} must be at most {1:,} (got {2:,})".format(
                self.name, self.maximum, value
            )
        return None


_POSITION_HELP = (
    "Stroke position in mm. The application accepts -20 to 370; -20 is the top "
    "of the stroke and 370 the bottom. Positions are measured after homing."
)
_SPEED_HELP = (
    "Speed in mm/s, 0 to 900. The achievable top speed depends on the current "
    "the drive can supply, so a high value may not be reached."
)
_ACCEL_HELP = "Acceleration in mm/s^2, 0 to 20,000."
_DECEL_HELP = "Deceleration in mm/s^2, 0 to 20,000."
_JERK_HELP = (
    "Rate of change of acceleration in mm/s^3. Should normally be larger than "
    "the acceleration and deceleration."
)
_TIME_HELP = "Dwell time at the end of the stroke. Leave at 0 unless you need a pause."
_CURVE_HELP = "Used by Start Curve only; ignored by single-stroke and continuous runs."


#: Every parameter, in the order they appear on screen and in a preset CSV.
#:
#: This order is part of the file format: the ``Presets/`` folder, including
#: ``Preset Outline (COPY ME).csv``, has used these columns in this sequence
#: since the beginning, and the lab edits those files by hand in Excel.  The
#: order the values are *written to the PLC* in is separate -- see
#: :data:`WRITE_ORDER`.
PARAMS: List[ParamSpec] = [
    ParamSpec("Position 1", "Pos_1", "motor", 0, -20, 370, _POSITION_HELP),
    ParamSpec("Position 2", "Pos_2", "motor", 350, -20, 370, _POSITION_HELP),
    ParamSpec("Speed 1", "Spd_1", "motor", 500, 0, 900, _SPEED_HELP),
    ParamSpec("Speed 2", "Spd_2", "motor", 500, 0, 900, _SPEED_HELP),
    ParamSpec("Accel 1", "Accel_1", "motor", 10000, 0, 20000, _ACCEL_HELP),
    ParamSpec("Accel 2", "Accel_2", "motor", 10000, 0, 20000, _ACCEL_HELP),
    ParamSpec("Decel 1", "Decel_1", "motor", 10000, 0, 20000, _DECEL_HELP),
    ParamSpec("Decel 2", "Decel_2", "motor", 10000, 0, 20000, _DECEL_HELP),
    ParamSpec("Jerk 1", "Jerk_1", "motor", 2000, 0, None, _JERK_HELP),
    ParamSpec("Jerk 2", "Jerk_2", "motor", 2000, 0, None, _JERK_HELP),
    ParamSpec("Time 1", "Time1", "motor", 0, 0, None, _TIME_HELP),
    ParamSpec("Time 2", "Time2", "motor", 0, 0, None, _TIME_HELP),
    ParamSpec("Profile", "Profile", "motor", 1, 0, 3,
              "Motion profile: Trapezoidal (0), Bestehorn (1), S-Curve (2), Sine (3)."),
    ParamSpec("Move Type", "MoveType", "motor", 0, 0, 1,
              "Absolute (0) moves to the position given. "
              "Incremental (1) moves by that amount from where it is."),
    ParamSpec("Curve ID", "Curve_ID", "curve", 0, 0, None,
              "Which stored curve to run. " + _CURVE_HELP),
    ParamSpec("Time Scale", "TimeScale", "curve", 0, 0, None,
              "Stretches the curve in time. " + _CURVE_HELP),
    ParamSpec("Amplitude Scale", "AmplitudeScale", "curve", 0, 0, None,
              "Scales the height of the curve. " + _CURVE_HELP),
    ParamSpec("Curve Offset", "CurveOffset", "curve", 0, 0, None,
              "Shifts the curve along the stroke. " + _CURVE_HELP),
]

#: Parameter names in display and CSV order.
PARAM_NAMES: List[str] = [spec.name for spec in PARAMS]

#: Lookup by name.
BY_NAME: Dict[str, ParamSpec] = dict((spec.name, spec) for spec in PARAMS)

#: The order values are sent to the PLC.
#:
#: Move Type and Profile go first because they govern how the PLC interprets
#: the positions and speeds that follow. This is the sequence the original
#: ``Motor.write_to_motor`` used, preserved because the ladder logic may depend
#: on it and that cannot be checked without the machine.
WRITE_ORDER: List[ParamSpec] = (
    [BY_NAME["Move Type"], BY_NAME["Profile"]]
    + [spec for spec in PARAMS if spec.name not in ("Move Type", "Profile")]
)


def defaults() -> Dict[str, int]:
    """A fresh dictionary of starting values for one piston."""
    return dict((spec.name, spec.default) for spec in PARAMS)


def validate_all(values: Dict[str, int]) -> List[str]:
    """Return every range problem in ``values``, as readable messages.

    Missing parameters are reported too, since a partially filled dictionary
    used to reach the PLC and write whichever values happened to be present.
    """
    problems: List[str] = []
    for spec in PARAMS:
        if spec.name not in values:
            problems.append("{0} is missing".format(spec.name))
            continue
        problem = spec.validate(values[spec.name])
        if problem:
            problems.append(problem)
    return problems


def parse(name: str, text: str) -> int:
    """Turn text typed by the operator into a value for ``name``.

    Raises :class:`ValueError` with a message meant to be shown to the operator.
    """
    spec = BY_NAME.get(name)
    if spec is None:
        raise ValueError("Unknown parameter: {0}".format(name))

    cleaned = text.strip()
    if not cleaned:
        raise ValueError("{0} is empty".format(name))
    try:
        value = int(cleaned)
    except ValueError:
        raise ValueError(
            "{0} must be a whole number ({1}), not {2!r}".format(
                name, spec.describe_range(), text
            )
        ) from None

    problem = spec.validate(value)
    if problem:
        raise ValueError(problem)
    return value
