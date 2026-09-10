"""Filling one parameter across a set of pistons in a shape.

Until now every preset gave all its pistons identical values -- the fourteen in
``Presets/`` have at most three distinct rows between them, which is just the
active block plus zero-filler. Anything else meant editing thirty rows of CSV by
hand, so in practice the array only ever moved as one block.

These patterns vary a parameter across the array instead. Staggering the timing
or curve offset column by column is what makes a wave travel along the tank
rather than arriving everywhere at once; ramping the stroke tapers it towards
the walls.

Nothing here knows any wave physics. It fills in machine parameters in a shape
you choose, in the machine's own units, and every value is clamped to the same
limits the entry boxes enforce.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Sequence

from app import params, tags

#: Which way the pattern runs across the array.
ACROSS_COLUMNS = "columns"
ACROSS_ROWS = "rows"
ACROSS_SELECTION = "selection"

AXES_CHOICES = (
    (ACROSS_COLUMNS, "Front to back  (columns 1 - 10)"),
    (ACROSS_ROWS, "Top to bottom  (rows 1 - 3)"),
    (ACROSS_SELECTION, "In motor-number order"),
)

UNIFORM = "uniform"
RAMP = "ramp"
STAGGER = "stagger"
MIRROR = "mirror"

PATTERN_CHOICES = (
    (UNIFORM, "Uniform - the same value everywhere"),
    (RAMP, "Ramp - even steps from a start value to an end value"),
    (STAGGER, "Stagger - a fixed step per position, for a travelling wave"),
    (MIRROR, "Mirror - ramp out from the centre, symmetric about it"),
)


class PatternResult(NamedTuple):
    """What a pattern produced, and anything the operator should know."""

    values: Dict[int, int]
    #: Values that had to be pulled back to the parameter's limits.
    clamped: List[str]

    @property
    def distinct(self) -> int:
        return len(set(self.values.values()))

    def summary(self, param: str) -> str:
        if not self.values:
            return "No motors to apply to."
        ordered = sorted(self.values.items())
        lowest = min(self.values.values())
        highest = max(self.values.values())
        head = "{0}: {1} motor(s), {2} distinct value(s)".format(
            param, len(ordered), self.distinct
        )
        if self.distinct > 1:
            head += ", {0} to {1}".format(lowest, highest)
        if self.clamped:
            head += "\nClamped to the limit: " + ", ".join(self.clamped)
        return head


def _index_of(axis: int, across: str, axes: Sequence[int]) -> int:
    """Where this axis sits along the chosen direction, counting from zero."""
    if across == ACROSS_COLUMNS:
        return axis // 3
    if across == ACROSS_ROWS:
        return axis % 3
    return list(axes).index(axis)


def _span(across: str, axes: Sequence[int]) -> List[int]:
    """The distinct positions present, so a ramp spreads over what is selected
    rather than over the whole array."""
    return sorted(set(_index_of(a, across, axes) for a in axes))


def build(
    axes: Sequence[int],
    param: str,
    pattern: str,
    start: int,
    end: Optional[int] = None,
    step: Optional[int] = None,
    wrap: Optional[int] = None,
    across: str = ACROSS_COLUMNS,
) -> PatternResult:
    """Work out the value of ``param`` for each axis.

    ``start``  the value at the first position (all positions, for uniform).
    ``end``    the value at the last position. Ramp and mirror.
    ``step``   how much to add per position. Stagger.
    ``wrap``   if set, stagger values wrap back round at this value, so a phase
               offset can cycle rather than growing without limit.
    """
    spec = params.BY_NAME.get(param)
    if spec is None:
        raise KeyError("Unknown parameter: {0}".format(param))

    axes = sorted(axes)
    if not axes:
        return PatternResult({}, [])

    positions = _span(across, axes)
    last = len(positions) - 1

    raw: Dict[int, float] = {}
    for axis in axes:
        index = positions.index(_index_of(axis, across, axes))

        if pattern == UNIFORM:
            value = float(start)

        elif pattern == RAMP:
            finish = float(start if end is None else end)
            value = float(start) if last == 0 else (
                start + (finish - start) * index / float(last)
            )

        elif pattern == STAGGER:
            increment = float(step or 0)
            value = start + increment * index
            if wrap:
                value = start + ((value - start) % float(wrap))

        elif pattern == MIRROR:
            finish = float(start if end is None else end)
            centre = last / 2.0
            distance = abs(index - centre)
            value = float(start) if centre == 0 else (
                start + (finish - start) * (distance / centre)
            )

        else:
            raise ValueError("Unknown pattern: {0}".format(pattern))

        raw[axis] = value

    values: Dict[int, int] = {}
    clamped: List[str] = []
    for axis, value in raw.items():
        rounded = int(round(value))
        if spec.minimum is not None and rounded < spec.minimum:
            clamped.append("piston {0} ({1} -> {2})".format(
                tags.display_number(axis), rounded, spec.minimum))
            rounded = spec.minimum
        if spec.maximum is not None and rounded > spec.maximum:
            clamped.append("piston {0} ({1} -> {2})".format(
                tags.display_number(axis), rounded, spec.maximum))
            rounded = spec.maximum
        values[axis] = rounded

    return PatternResult(values, clamped)


def describe(pattern: str) -> str:
    """A sentence explaining what a pattern does, for the panel."""
    return {
        UNIFORM: (
            "Every selected motor gets the same value. This is how the machine "
            "has always been driven."
        ),
        RAMP: (
            "The value moves in even steps from the first position to the last. "
            "Ramping Position 2 tapers the stroke across the array."
        ),
        STAGGER: (
            "Each position gets the value before it plus a fixed step. Front to "
            "back is the direction the chamber runs, so staggering a timing or "
            "curve offset that way makes the wave reach the back of the chamber "
            "after the front, rather than the whole array moving together."
        ),
        MIRROR: (
            "The value ramps outward from the centre, the same on both sides. "
            "Use it for a wave that converges on, or spreads from, the middle."
        ),
    }.get(pattern, "")
