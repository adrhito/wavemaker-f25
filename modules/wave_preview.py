"""A small moving picture of the wave the current settings would make.

Numbers in boxes -- 200 mm, 400 mm/s, profile 3 -- do not tell an operator what
the water will do. This draws it: the height comes from the stroke, the speed
of the animation from the cycle time, and the shape from the actual motion of
a piston travelling between Position 1 and Position 2.

That last part used to be wrong. The strip drew ``app.waves`` SHAPES -- the
same sine/trapezoid/S-curve/Bestehorn curves used to describe what a whole
*wave* looks like -- as if that were the piston's position over time. It is
not: a piston in a continuous run travels from Position 1 to Position 2 at
Speed 1 and back at Speed 2, with acceleration and deceleration ramps at each
end and optional dwells from Time 1 / Time 2. The controller's "Profile"
parameter only shapes how eased those ramps are; it is not a waveform to
sample. So the preview always drew a smooth, symmetric hump no matter what was
set, could never show Speed 1 and Speed 2 differing (fast up, slow down), and
had no flats for the dwells at all. This module now builds the displacement
curve from those actual pieces -- an eased ramp out, an optional dwell, an
eased ramp back at its own speed, another optional dwell -- matching the shape
of motion :mod:`app.simulator` models in ``SimulatedMachine._advance``, the
best existing account in this codebase of how a piston actually moves. It does
not call into the simulator (that is a live, threaded mock built around a tag
store, not a pure function to sample from a redraw), but it uses the same
:data:`app.simulator.PROFILE_SMOOTHING` easing-per-profile numbers so the two
pictures agree on what each Profile setting looks like.

It is still a sketch and says so. It does not model water, and the eased ramp
below is an approximation of the drive's real accel/decel/jerk curve, not a
replay of it.
"""

from __future__ import annotations

import math
from tkinter import Canvas
from typing import Dict, List, Optional

from app.simulator import PROFILE_SMOOTHING

SKY = "#141416"
WATER_TOP = "#2a6f97"
WATER_BODY = "#14466b"
CREST = "#8ecae6"
LABEL_DIM = "#6e6e73"

#: Frames a second, near enough. Slow enough to be cheap on the lab PC.
FRAME_MS = 60
#: How much of the height one full stroke uses.
FULL_STROKE_MM = 370.0

#: Fallback easing for a profile number PROFILE_SMOOTHING does not know about.
DEFAULT_SMOOTHING = 0.6


# -- pure geometry ------------------------------------------------------------
#
# Kept free of Tkinter and of WavePreview's own state so the shape of the
# motion can be tested directly, without a Canvas or a running animation.

def leg_position(s: float, smoothing: float) -> float:
    """How far along a leg (0..1) a piston has travelled at time-fraction s.

    ``smoothing`` is :data:`app.simulator.PROFILE_SMOOTHING` for the profile in
    force: 0 leaves the whole leg at constant speed (a straight ramp -- what
    "no easing" means for Trapezoidal), 1 removes the constant-speed middle
    entirely so the whole leg is one eased sweep end to end (Sine, "fully
    eased"). Values between blend eased ends onto a linear middle, which is
    what a real trapezoidal *velocity* profile with finite accel/decel looks
    like at the position level. The curve is built so it reaches exactly 0 at
    s=0 and exactly 1 at s=1 regardless of smoothing, so a leg always arrives.
    """
    s = max(0.0, min(1.0, s))
    half = max(0.0, min(1.0, smoothing)) * 0.5
    if half <= 1e-9:
        return s  # constant speed for the whole leg: a straight ramp

    # A raised-cosine ease covers each end; a peak speed high enough that the
    # whole leg (ease-in + cruise + ease-out) still covers exactly 1.0.
    peak_speed = 1.0 / (1.0 - half)

    def eased(u: float) -> float:
        # Distance covered after fraction u (0..1) through one ease region.
        return peak_speed * half / 2.0 * (u - math.sin(math.pi * u) / math.pi)

    if s <= half:
        return eased(s / half)
    if s >= 1.0 - half:
        remaining = (1.0 - s) / half
        return 1.0 - eased(remaining)
    return eased(1.0) + peak_speed * (s - half)


def invert_leg_position(distance: float, smoothing: float) -> float:
    """The time-fraction s at which :func:`leg_position` reaches ``distance``.

    Needed to turn a starting *position* along the stroke (what the machine
    actually stages, see the module docstring) into a starting *time* within
    the leg -- the eased ramp does not cover ground at a constant rate, so
    "a third of the way there" and "a third of the way through" are not the
    same fraction once smoothing is above zero.
    """
    distance = max(0.0, min(1.0, distance))
    if distance <= 0.0:
        return 0.0
    if distance >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(40):  # plenty for a 74px-tall strip; this is not physics
        mid = (lo + hi) / 2.0
        if leg_position(mid, smoothing) < distance:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def leg_fractions(stroke: float, speed_1: Optional[float], speed_2: Optional[float],
                   dwell_1_s: float, dwell_2_s: float,
                   fallback_period: float) -> "tuple[float, float, float, float, float]":
    """Split one cycle into (out, dwell-at-2, back, dwell-at-1, total seconds).

    Speed 1 drives the leg toward Position 2, Speed 2 the leg back toward
    Position 1 -- matching ``SimulatedMachine._move``, where ``out_speed`` is
    Speed 1 and applies while ``piston.outbound`` is true. When the speeds
    are not both known (an older caller not yet passing them) this falls back
    to a symmetric half-and-half split over ``fallback_period`` with no
    dwell, which is what the strip has always assumed.
    """
    if stroke > 0 and speed_1 is not None and speed_2 is not None:
        out_time = stroke / max(float(speed_1), 1e-6)
        back_time = stroke / max(float(speed_2), 1e-6)
        dwell_2 = max(float(dwell_2_s), 0.0)
        dwell_1 = max(float(dwell_1_s), 0.0)
        total = out_time + dwell_2 + back_time + dwell_1
        if total > 1e-6:
            return (out_time / total, dwell_2 / total,
                    back_time / total, dwell_1 / total, total)
    period = max(float(fallback_period), 0.05)
    return (0.5, 0.0, 0.5, 0.0, period)


def cycle_sample(phase: float, out_frac: float, dwell_2_frac: float,
                  back_frac: float, dwell_1_frac: float,
                  smoothing: float) -> float:
    """Displacement at ``phase`` (0..1 through one full cycle).

    -1.0 is Position 1, +1.0 is Position 2, matching the sign the rest of the
    module already draws with (``mid - sample * amplitude``, so +1 draws
    higher up the strip). A dwell holds the value flat at whichever end it
    follows, which is the whole point of Time 1 / Time 2 and the thing the
    old shape-sampler approach could not represent at all.
    """
    phase = phase % 1.0
    if out_frac > 1e-9 and phase < out_frac:
        return 2.0 * leg_position(phase / out_frac, smoothing) - 1.0
    phase -= out_frac
    if dwell_2_frac > 1e-9 and phase < dwell_2_frac:
        return 1.0
    phase -= dwell_2_frac
    if back_frac > 1e-9 and phase < back_frac:
        return 1.0 - 2.0 * leg_position(phase / back_frac, smoothing)
    return -1.0  # the rest is the dwell at Position 1 (or float slop)


def amplitude_for(stroke_mm: float, height: float) -> float:
    """Half the drawn peak-to-trough, in pixels, for a stroke of this size.

    A separate function so the "does the drawn wave get bigger with a bigger
    stroke" property can be tested without a Canvas to measure pixels on.
    """
    fraction = min(max(float(stroke_mm), 0.0) / FULL_STROKE_MM, 1.0)
    return fraction * (float(height) * 0.34)


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
        self._smoothing = DEFAULT_SMOOTHING
        self._out_frac = 0.5
        self._dwell_2_frac = 0.0
        self._back_frac = 0.5
        self._dwell_1_frac = 0.0
        self._offsets: Dict[int, float] = {}
        self._column_leads: Dict[int, float] = {}
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
             offsets: Optional[Dict[int, float]] = None, note: str = "",
             *,
             speed_1: Optional[float] = None,
             speed_2: Optional[float] = None,
             dwell_1_s: float = 0.0,
             dwell_2_s: float = 0.0,
             start_fractions: Optional[Dict[int, float]] = None) -> None:
        """Set the wave being previewed. Safe to call on every change.

        ``stroke_mm``, ``period_s``, ``profile``, ``offsets`` and ``note``
        are the existing call, unchanged, so ``Operate.py`` keeps working as
        it is. Everything after the ``*`` is new and optional:

        * ``speed_1`` / ``speed_2`` (mm/s) let the two legs take different
          times, drawing the fast-up-slow-down asymmetry a single ``period_s``
          cannot express on its own. Leave them out and the strip falls back
          to the old symmetric half-and-half split.
        * ``dwell_1_s`` / ``dwell_2_s`` (seconds, i.e. Time 1 / Time 2 already
          converted from the drive's milliseconds) put a flat section at
          Position 1 / Position 2. Zero by default, matching "ignored" today.
        * ``start_fractions`` is a column -> 0..1 map of how far from
          Position 1 to Position 2 that column is parked before the run
          starts -- what ``waves.cascade_starts`` / ``Model._stage_cascade``
          actually stage for a travelling design now (see the note on
          ``offsets`` below). When given, it takes priority over ``offsets``
          for that column.

        The caller does not have to supply all of the new arguments together;
        anything left out just falls back to the old behaviour for that piece
        of the shape.
        """
        self._stroke = max(float(stroke_mm), 0.0)
        self._smoothing = PROFILE_SMOOTHING.get(int(profile), DEFAULT_SMOOTHING)
        (self._out_frac, self._dwell_2_frac, self._back_frac,
         self._dwell_1_frac, self._period) = leg_fractions(
            self._stroke, speed_1, speed_2, dwell_1_s, dwell_2_s, period_s)

        # NOTE for whoever wires this up in Operate.py: the ``offsets`` this
        # call site has always sent are seconds of Curve Offset, which is a
        # per-leg timing lead the controller only reads during a curve run.
        # An ordinary continuous run -- what Start (not Start Curve) drives --
        # ignores Curve Offset entirely and instead gets its stagger from a
        # different STARTING POSITION per column (waves.cascade_starts,
        # staged by Model._stage_cascade: see app/waves.py's own docstring
        # and the wavemaker skill's "Making the array move as a pattern"
        # section). So for a design run with Start, ``offsets`` no longer
        # describes what the machine does; pass the same columns' fractions
        # from ``waves.cascade_fractions`` as ``start_fractions`` instead, and
        # keep ``offsets`` only for a design that will actually run with
        # Start Curve.
        self._offsets = dict(offsets or {})
        self._column_leads = {}
        if start_fractions:
            for column, fraction in start_fractions.items():
                s = invert_leg_position(float(fraction), self._smoothing)
                self._column_leads[column] = s * self._out_frac

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

    def _sample(self, phase: float) -> float:
        return cycle_sample(
            phase, self._out_frac, self._dwell_2_frac,
            self._back_frac, self._dwell_1_frac, self._smoothing,
        )

    def _column_phase(self, column: int) -> float:
        """Where this column sits in the cycle, including any stagger."""
        lead = self._column_leads.get(column)
        if lead is not None:
            # A head start along the stroke, not a delay in time -- see the
            # note in show() about why a starting position and a starting
            # time are not interchangeable once the ramp is eased.
            return (self._phase + lead) % 1.0
        offset = self._offsets.get(column)
        if offset is None:
            return self._phase
        # Legacy path, for a design meant for Start Curve: Curve Offset is a
        # delay there, so a column further back starts later.
        return (self._phase - (offset / self._period)) % 1.0

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
        amplitude = amplitude_for(self._stroke, height)
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
