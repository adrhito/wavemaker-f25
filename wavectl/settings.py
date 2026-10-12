"""Motion parameters as they are typed at a terminal.

The eighteen parameters are named and bounded once, in ``app.params``. This
module adds only the short spellings a command line wants -- ``speed=300``
for both directions at once, ``stroke=120`` for a length rather than an end
position -- and works out what one piston's parameters become. It decides
nothing about limits: every value is checked against ``app.params``.
"""

from __future__ import annotations

from typing import Dict, Mapping, NamedTuple, Optional, Tuple

from app import params

#: One word for the pair of parameters that are nearly always set together.
PAIRS: Dict[str, Tuple[str, ...]] = {
    "speed": ("Speed 1", "Speed 2"),
    "accel": ("Accel 1", "Accel 2"),
    "decel": ("Decel 1", "Decel 2"),
    "jerk": ("Jerk 1", "Jerk 2"),
    "dwell": ("Time 1", "Time 2"),
    "p1": ("Position 1",),
    "pos1": ("Position 1",),
    "p2": ("Position 2",),
    "pos2": ("Position 2",),
    "offset": ("Curve Offset",),
}

#: Profile is a number on the controller; these are what the numbers mean.
PROFILES = {"trapezoid": 0, "bestehorn": 1, "scurve": 2, "sine": 3}

#: Where the pistons come to rest after a stop (``Model.rest_position``).
RESTS = ("down", "up", "hold")


def _plain(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


_BY_PLAIN: Dict[str, Tuple[str, ...]] = dict(
    (_plain(spec.name), (spec.name,)) for spec in params.PARAMS)
_BY_PLAIN.update(PAIRS)


class Edit(NamedTuple):
    """What a ``set`` command asked for, before it is applied to any piston."""

    #: Parameter name -> value, for the ones given outright.
    values: Dict[str, int]
    #: Stroke length in mm, if given: Position 2 becomes Position 1 plus this.
    stroke: Optional[int]
    #: Resting position, if given. Belongs to the session, not to a piston.
    rest: Optional[str]


def _whole(key: str, text: str) -> int:
    try:
        return int(str(text).strip())
    except ValueError:
        raise ValueError("{0} must be a whole number, not {1!r}.".format(
            key, text)) from None


def read(pairs: Mapping[str, str]) -> Edit:
    """Turn ``{"stroke": "120", "speed": "150"}`` into an :class:`Edit`."""
    values: Dict[str, int] = {}
    stroke = None
    rest = None
    for typed, text in pairs.items():
        key = _plain(typed)
        if key == "stroke":
            stroke = _whole("stroke", text)
            if stroke <= 0:
                raise ValueError("stroke must be greater than zero.")
        elif key == "rest":
            rest = str(text).strip().lower()
            if rest not in RESTS:
                raise ValueError("rest must be one of: {0}.".format(", ".join(RESTS)))
        elif key in _BY_PLAIN:
            if key == "profile" and _plain(text) in PROFILES:
                number = PROFILES[_plain(text)]
            else:
                number = _whole(typed, text)
            for name in _BY_PLAIN[key]:
                values[name] = number
        else:
            raise ValueError(
                '"{0}" is not a parameter. Use stroke, speed, accel, decel, '
                "jerk, dwell, p1, p2, profile, offset, rest, or a full name "
                "such as position1, speed2, movetype, curveid, timescale, "
                "amplitudescale.".format(typed))
    return Edit(values, stroke, rest)


def changed(current: Mapping[str, int], edit: Edit) -> Dict[str, int]:
    """The parameters one piston would be given, as name -> value.

    Raises ``ValueError`` naming every value outside the machine's limits.
    A stroke is measured from this piston's own Position 1, after any new
    Position 1 in the same command, which is how the window's stroke control
    and ``Model.change_stroke_live`` both read it.
    """
    new = dict(edit.values)
    if edit.stroke is not None:
        new["Position 2"] = new.get("Position 1", current["Position 1"]) + edit.stroke
    problems = [problem for problem in (
        params.BY_NAME[name].validate(value) for name, value in new.items())
        if problem]
    if problems:
        raise ValueError("; ".join(problems))
    return new


def summary(values: Mapping[str, Optional[int]]) -> str:
    """Every parameter on one line, in the order the presets store them."""
    return ", ".join("{0} {1}".format(
        spec.name, "differs" if values.get(spec.name) is None else values[spec.name])
        for spec in params.PARAMS)
