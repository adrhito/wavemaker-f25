"""Piston lists as an operator types them, and as they are read back.

Everything a person sees is a display number, 1 to 30, counted down each
column from the front of the chamber. Everything the PLC sees is an axis, 0 to
29, numbered the other way round. The conversion lives in ``app.tags``; this
module only handles the text.
"""

from __future__ import annotations

from typing import Iterable, List

from app import tags

_LIMITS = {"": tags.MOTOR_COUNT, "c": tags.COLUMN_COUNT, "r": tags.ROWS_PER_COLUMN}
_KINDS = {"": "piston", "c": "column", "r": "row"}


def parse(text) -> List[int]:
    """The axes named by a list of display numbers.

    ``1-9``, ``1,4,7`` and ``1-3,10-12`` are pistons. ``c1-3`` is whole
    columns counted from the front of the chamber, so it is the same as
    ``1-9``; ``r1`` is a whole row, pistons 1, 4, 7 and so on. ``all`` and
    ``none`` are what they say. Pieces may be mixed: ``c1,r3``.
    """
    words = "".join(str(text).split()).lower()
    if words == "all":
        return list(range(tags.MOTOR_COUNT))
    if words in ("", "none"):
        return []

    shown = set()
    for piece in words.split(","):
        if not piece:
            continue
        kind = piece[0] if piece[0] in "cr" else ""
        low, _, high = piece[len(kind):].partition("-")
        try:
            first = int(low)
            last = int(high) if high else first
        except ValueError:
            raise ValueError(
                '"{0}" is not a piston list. Use numbers and ranges such as '
                "1-9 or 1,4,7; c1-3 for columns, r1 for a row, or all."
                .format(piece)) from None
        limit = _LIMITS[kind]
        if not 1 <= first <= last <= limit:
            raise ValueError(
                '"{0}": {1}s are numbered 1 to {2}, lowest first.'.format(
                    piece, _KINDS[kind], limit))
        for number in range(first, last + 1):
            if kind == "c":
                start = tags.ROWS_PER_COLUMN * (number - 1) + 1
                shown.update(range(start, start + tags.ROWS_PER_COLUMN))
            elif kind == "r":
                shown.update(range(number, tags.MOTOR_COUNT + 1,
                                   tags.ROWS_PER_COLUMN))
            else:
                shown.add(number)
    return sorted(tags.axis_from_display(number) for number in shown)


def shown(axes: Iterable[int]) -> List[int]:
    """Display numbers for these axes, in the order an operator counts."""
    return sorted(tags.display_number(axis) for axis in axes)


def describe(axes: Iterable[int]) -> str:
    """Display numbers as short ranges: ``1-9, 12``.

    ``tags.display_list`` spells every number out, which is right in a
    sentence on screen and far too long when thirty pistons are selected and
    the line is being read by something that pays for every word.
    """
    numbers = shown(axes)
    if not numbers:
        return "none"
    runs = []
    start = previous = numbers[0]
    for number in numbers[1:] + [None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        runs.append(str(start) if start == previous
                    else "{0}-{1}".format(start, previous))
        start = previous = number
    return ", ".join(runs)
