"""A preset: saved motion parameters, loaded from or written to a CSV file.

File format
-----------
One header row naming ``Motor`` followed by the parameter names, then a row per
piston, then a final row whose Motor column is ``All``::

    Motor,Position 1,Position 2,Speed 1,...
    0,0,350,500,...
    ...
    29,0,0,0,...
    All,0,350,500,...

How a row is chosen for a piston
--------------------------------
When the application saves a preset it writes real values for the pistons that
were in a set and a row of zeroes for every other piston, then repeats the last
real row as ``All``.  So a zero row does not mean "hold this piston at zero", it
means "this piston was not part of the preset".

:meth:`Preset.values_for` therefore uses a piston's own row only when that row
actually commands motion, and otherwise falls back to the ``All`` row.  The
previous code always used the piston's own row, so applying a saved preset to a
set containing any piston that had not been live when the preset was saved
quietly wrote zeroes to it -- the piston simply would not move, with no message.
"""

from __future__ import annotations

from typing import Dict, List, Optional


class Preset:
    """Parameters for some or all pistons, plus a fallback row."""

    def __init__(
        self,
        rows: Dict[int, Dict[str, int]],
        all_row: Optional[Dict[str, int]],
        name: str = "",
        warnings: Optional[List[str]] = None,
    ) -> None:
        #: Per-piston parameters, keyed by axis. Only pistons present in the file.
        self.rows = rows
        #: The ``All`` row, if the file had one.
        self.all_row = all_row
        self.name = name
        #: Problems found while reading that did not stop the load.
        self.warnings = warnings or []

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Preset({0!r}, motors={1}, all_row={2})".format(
            self.name, sorted(self.rows), self.all_row is not None
        )

    @staticmethod
    def _commands_motion(row: Dict[str, int]) -> bool:
        """Whether a row asks a piston to move.

        A piston with both speeds at zero cannot move, so such a row carries no
        instruction and is treated as a placeholder.
        """
        return bool(row.get("Speed 1", 0)) or bool(row.get("Speed 2", 0))

    def values_for(self, axis: int) -> Optional[Dict[str, int]]:
        """The parameters this preset gives piston ``axis``, or ``None``."""
        row = self.rows.get(axis)
        if row is not None and self._commands_motion(row):
            return dict(row)
        if self.all_row is not None:
            return dict(self.all_row)
        return dict(row) if row is not None else None

    def preview(self) -> Dict[str, int]:
        """The values shown in the preview panel: the ``All`` row if there is
        one, otherwise the first piston row that commands motion."""
        if self.all_row is not None:
            return dict(self.all_row)
        for _axis, row in sorted(self.rows.items()):
            if self._commands_motion(row):
                return dict(row)
        return dict(next(iter(sorted(self.rows.items())), (0, {}))[1])

    def describe(self) -> str:
        moving = sorted(
            axis for axis, row in self.rows.items() if self._commands_motion(row)
        )
        if not moving:
            return "{0}: no piston rows with motion; the All row will be used.".format(
                self.name or "Preset"
            )
        return "{0}: motion defined for motors {1}{2}".format(
            self.name or "Preset",
            ", ".join(str(a) for a in moving),
            "; other motors use the All row." if self.all_row is not None else "",
        )
