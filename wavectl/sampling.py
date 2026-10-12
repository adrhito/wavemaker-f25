"""Recording where the pistons are while something happens.

The answer to "did the array do what it was told" is in the positions the
drives publish, and the one-line answer a command gives cannot carry a few
thousand of them. So they are written to a file, and the answer names the
file and says how far each piston was seen to travel.

Reads only. Sampling shares the session's one connection with the model, a
read at a time, so a Stop is never held up by more than a single read.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app import params, tags
from app.plc import PlcError
from wavectl import pistons, places
from wavectl.timing import SAMPLE_HZ

#: The fastest rate that may be asked for. What is achieved depends on how
#: many pistons are read: every one is a round trip to the controller.
MAX_HZ = 50.0
DEFAULT_HZ = SAMPLE_HZ
#: The longest a stand-alone recording may last.
MAX_SECONDS = 900.0


class Sampler:
    """Reads a set of pistons at a steady rate until told to stop."""

    def __init__(self, transport, axes: Iterable[int], hz: float = DEFAULT_HZ,
                 note: str = "") -> None:
        self.transport = transport
        #: In the order an operator counts them, which is the column order.
        self.axes: List[int] = sorted(set(axes), key=tags.display_number)
        self.hz = min(max(float(hz), 0.1), MAX_HZ)
        self.note = note
        self.times: List[float] = []
        self.rows: List[List[Optional[float]]] = []
        self.began_utc: Optional[str] = None
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "Sampler":
        self.began_utc = datetime.now(timezone.utc).isoformat()
        self._thread = threading.Thread(target=self._loop, name="wavectl sampler",
                                        daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        period = 1.0 / self.hz
        began = time.monotonic()
        due = began
        while not self._halt.is_set():
            row: List[Optional[float]] = []
            for axis in self.axes:
                try:
                    row.append(params.to_mm(self.transport.read(
                        tags.axis_field(axis, tags.ACTUAL_POSITION))))
                except (PlcError, TypeError, ValueError):
                    row.append(None)
            # Stamped after the reads, which is when the last of them was true.
            self.times.append(time.monotonic() - began)
            self.rows.append(row)
            due += period
            # If the reads took longer than a period the rate asked for is
            # not achievable; carry on at what is, rather than fall behind.
            due = max(due, time.monotonic())
            self._halt.wait(due - time.monotonic())

    def stop(self) -> None:
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    # -- what was seen -----------------------------------------------------------

    def travel(self) -> Dict[int, float]:
        """How far each piston was seen to go, by axis."""
        seen: Dict[int, float] = {}
        for column, axis in enumerate(self.axes):
            values = [row[column] for row in self.rows if row[column] is not None]
            if values:
                seen[axis] = max(values) - min(values)
        return seen

    def achieved_hz(self) -> float:
        if len(self.times) < 2 or self.times[-1] <= 0:
            return 0.0
        return (len(self.times) - 1) / self.times[-1]

    def save(self, path: Optional[Path] = None) -> Path:
        """Write the samples as CSV: seconds, then one column per piston.

        Lines beginning with ``#`` carry what the numbers are; ``numpy`` skips
        them unasked and ``pandas`` does with ``comment="#"``.
        """
        if path is None:
            path = places.output_dir() / "samples-{0}.csv".format(
                datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3])
        shown = [tags.display_number(axis) for axis in self.axes]
        lines = [
            "# wavectl piston positions, millimetres (smaller is higher)",
            "# began_utc {0}".format(self.began_utc),
            "# asked_hz {0:g} achieved_hz {1:.2f}".format(self.hz, self.achieved_hz()),
        ]
        if self.note:
            lines.append("# {0}".format(" ".join(self.note.split())))
        lines.append("t_s," + ",".join("p{0}".format(number) for number in shown))
        for when, row in zip(self.times, self.rows):
            lines.append("{0:.4f},".format(when) + ",".join(
                "" if value is None else "{0:.3f}".format(value) for value in row))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def summary(self, path: Path) -> str:
        travel = self.travel()
        parts = ["{0} samples of pistons {1} over {2:.1f} s at {3:.1f} Hz".format(
            len(self.rows), pistons.describe(self.axes),
            self.times[-1] if self.times else 0.0, self.achieved_hz())]
        if travel:
            low, high = min(travel.values()), max(travel.values())
            parts.append("travel {0:.1f} mm".format(high) if high - low < 0.05
                         else "travel {0:.1f} to {1:.1f} mm".format(low, high))
        missing = [axis for axis in self.axes if axis not in travel]
        if missing:
            parts.append("pistons {0} unreadable".format(pistons.describe(missing)))
        parts.append(places.shown(path))
        return " | ".join(parts)

    def record(self, path: Path) -> dict:
        return {
            "file": str(path),
            "samples": len(self.rows),
            "seconds": round(self.times[-1], 3) if self.times else 0.0,
            "achieved_hz": round(self.achieved_hz(), 2),
            "travel_mm": dict((str(tags.display_number(axis)), round(distance, 2))
                              for axis, distance in self.travel().items()),
        }
