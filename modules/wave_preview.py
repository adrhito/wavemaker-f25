"""A small moving picture of the wave the current settings would make.

Numbers in boxes -- 200 mm, 400 mm/s, profile 3 -- do not tell an operator what
the water will do. This draws it: the height comes from the stroke, the speed
of the animation from the cycle time, the shape from the motion profile, and
the lean of the crests from the Curve Offset stagger that makes a wave travel
down the chamber rather than every piston slapping at once.

It is a sketch and says so. It does not model water.
"""

from __future__ import annotations

import math
from tkinter import Canvas
from typing import Dict, List, Optional

from app import waves

SKY = "#141416"
WATER_TOP = "#2a6f97"
WATER_BODY = "#14466b"
CREST = "#8ecae6"
LABEL_DIM = "#6e6e73"

#: Frames a second, near enough. Slow enough to be cheap on the lab PC.
FRAME_MS = 60
#: How much of the height one full stroke uses.
FULL_STROKE_MM = 370.0


def _sampler_for(profile: int):
    """The shape function for a controller motion profile."""
    for shape in waves.SHAPES:
        if shape.profile == profile:
            return shape.sample
    return waves.SHAPES[0].sample


class WavePreview:
    """A strip of animated water, driven by the parameters in force."""

    def __init__(self, parent, height: int = 74, columns: int = 10) -> None:
        self.columns = columns
        self.canvas = Canvas(
            parent, height=height, background=SKY,
            highlightthickness=0, bd=0,
        )
        self._phase = 0.0
        self._stroke = 0.0
        self._period = 1.0
        self._sample = waves.SHAPES[0].sample
        self._offsets: Dict[int, float] = {}
        self._note = "Select some pistons to see the wave they would make."
        self._running = False
        self._job = None

        self.canvas.bind("<Configure>", lambda _e: self._draw())
        self.canvas.bind("<Destroy>", lambda _e: self.stop())

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)
        return self

    # -- what to draw ---------------------------------------------------------

    def show(self, stroke_mm: float, period_s: float, profile: int = 3,
             offsets: Optional[Dict[int, float]] = None,
             note: str = "") -> None:
        """Set the wave being previewed. Safe to call on every change."""
        self._stroke = max(float(stroke_mm), 0.0)
        self._period = max(float(period_s), 0.05)
        self._sample = _sampler_for(profile)
        self._offsets = dict(offsets or {})
        self._note = note
        # Redraw now rather than waiting for the next frame, so dragging a
        # stroke bar moves the picture with the drag.
        self._draw()
        self.start()

    def clear(self, note: str = "") -> None:
        self._stroke = 0.0
        self._note = note or "Select some pistons to see the wave they would make."
        self._draw()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._job is not None:
            try:
                self.canvas.after_cancel(self._job)
            except Exception:  # noqa: BLE001 - the widget may already be gone
                pass
            self._job = None

    # -- drawing --------------------------------------------------------------

    def _tick(self) -> None:
        if not self._running:
            return
        # One full cycle of the wave per period, so a slow wave looks slow.
        self._phase = (self._phase + (FRAME_MS / 1000.0) / self._period) % 1.0
        self._draw()
        try:
            self._job = self.canvas.after(FRAME_MS, self._tick)
        except Exception:  # noqa: BLE001 - window closing
            self._running = False

    def _column_phase(self, column: int) -> float:
        """Where this column sits in the cycle, including any stagger."""
        offset = self._offsets.get(column)
        if offset is None:
            return self._phase
        # Curve Offset is a delay: a column further back starts later, which is
        # what makes the crest travel instead of the whole array moving as one.
        return self._phase - (offset / self._period)

    def _surface(self, width: float, mid: float, amplitude: float) -> List[float]:
        points: List[float] = []
        steps = max(int(width / 6), 24)
        for i in range(steps + 1):
            t = i / float(steps)
            column = t * (self.columns - 1)
            low = int(math.floor(column))
            high = min(low + 1, self.columns - 1)
            blend = column - low
            phase = (self._column_phase(low) * (1 - blend)
                     + self._column_phase(high) * blend)
            points.append(mid - self._sample(phase) * amplitude)
        return points

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        width = max(c.winfo_width(), 200)
        height = max(c.winfo_height(), 40)

        if self._stroke <= 0:
            c.create_text(
                width / 2.0, height / 2.0, text=self._note,
                fill=LABEL_DIM, font=("Segoe UI", 8),
            )
            return

        mid = height * 0.52
        amplitude = min(self._stroke / FULL_STROKE_MM, 1.0) * (height * 0.34)
        surface = self._surface(width, mid, amplitude)
        steps = len(surface) - 1

        # The body of the water, as one filled polygon under the surface.
        polygon = []
        for i, y in enumerate(surface):
            polygon.extend((width * i / float(steps), y))
        polygon.extend((width, height, 0, height))
        c.create_polygon(polygon, fill=WATER_BODY, outline="")

        # The surface line itself, brighter, so the shape reads.
        line = []
        for i, y in enumerate(surface):
            line.extend((width * i / float(steps), y))
        c.create_line(line, fill=WATER_TOP, width=2, smooth=True)

        # A crest marker per column, so the stagger is visible as a travelling
        # wave rather than a wobble.
        for column in range(self.columns):
            x = width * (column + 0.5) / float(self.columns)
            y = mid - self._sample(self._column_phase(column)) * amplitude
            c.create_oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5,
                          fill=CREST, outline="")

        if self._note:
            c.create_text(
                6, height - 8, text=self._note, anchor="w",
                fill=LABEL_DIM, font=("Segoe UI", 8),
            )
