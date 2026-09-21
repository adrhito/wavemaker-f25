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

What the water does with that motion is worked out too, rather than assumed.
The strip used to draw the piston's displacement *as* the surface, which is
three separate mistakes in one line: the wave is not as tall as the stroke, it
has a length of its own that the stroke has nothing to do with, and it travels
away from the pistons instead of sitting on top of them. So a wave now gets its
length from the period and the depth through the dispersion relation, its
height from the stroke through a wavemaker transfer function, its shape from a
second-order Stokes correction -- sharp crests, long flat troughs -- and its
travel from the fact that a point further along the chamber is showing what the
array did a moment ago, one moment per wave length covered. The "the water"
section below has the whole of it, including which constants are estimates.

It is still a sketch and says so. The eased ramp is an approximation of the
drive's real accel/decel/jerk curve rather than a replay of it, the wave theory
is linear theory with one nonlinear correction bolted on, and nothing here
knows the shape of the tank. It is meant to answer "roughly what will the water
do" at a glance, not to stand in for a measurement.
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
#: Whitecaps, and the surface line once the wave is past breaking.
FOAM = "#eaf6ff"
#: Still-water level, drawn faintly so the wave has a datum.
STILL_WATER = "#2f3a44"
#: The piston bars along the bottom.
PISTON = "#30d158"
LABEL_DIM = "#6e6e73"

#: Frames a second, near enough. Slow enough to be cheap on the lab PC.
FRAME_MS = 60

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


# -- the water ----------------------------------------------------------------
#
# Everything above turns the parameters into the motion of a PISTON. The water
# is a separate question, and drawing the piston's displacement as if it were
# the surface -- which is what this strip used to do -- gets it wrong in three
# ways at once: the wave is not as tall as the stroke, it has a length of its
# own that the stroke has nothing to do with, and it travels away from the
# pistons rather than sitting on top of them.
#
# So the surface is worked out from the piston motion rather than copied from
# it, using textbook linear wave theory:
#
#   * the length comes from the period and the depth, through the dispersion
#     relation, so a slow wave is a long one;
#   * the height comes from the stroke, through a wavemaker transfer function,
#     so a 350 mm stroke does not mean a 350 mm wave;
#   * the pistons underneath say when the water at a point is driven, so a
#     staggered design reads as a wave running along the chamber, and the water
#     itself adds a lag of one cycle per wave length covered, so even an array
#     moving in unison makes a surface with crests and troughs along it rather
#     than one that rises and falls like a lift;
#   * real waves are not sinusoids -- crests are sharp and troughs are long and
#     flat -- so a second-order Stokes correction shapes the result, by an
#     amount that grows with steepness.
#
# It is still a sketch, and the numbers below are estimates the lab should
# replace with measured ones. WATER_DEPTH_MM and CHAMBER_LENGTH_MM in
# particular are recorded nowhere else in this codebase; they are the two worth
# checking first if the picture looks wrong against the tank.

#: Acceleration due to gravity, in the millimetres the rest of the app uses.
GRAVITY_MM_S2 = 9810.0

#: Still-water depth. ESTIMATE -- set this to the tank's working depth.
WATER_DEPTH_MM = 300.0

#: How much of the chamber the strip shows, end to end. ESTIMATE.
CHAMBER_LENGTH_MM = 3000.0

#: Efficiency of this array relative to the ideal paddle the transfer function
#: below describes. ESTIMATE, and the first number to reach for if the picture
#: is consistently too big or too small against the tank.
#:
#: 1.0 would be the textbook value for a paddle that translates bodily through
#: the depth. A bed of vertical plungers is nothing like as efficient at making
#: a propagating wave -- most of what it displaces goes straight up and comes
#: straight back down -- and at 1.0 every preset in ``Presets/`` bar the
#: gentlest came out past the breaking limit, which is not what the lab sees.
#: 0.18 puts the shipped presets on a scale where the gentle ones read gentle,
#: the big demos read strong, and only the very short, very fast ones are past
#: breaking. It is a calibration, not a measurement: one measured wave height
#: would replace it properly.
PADDLE_GAIN = 0.18

#: Wave height that fills the strip vertically.
FULL_WAVE_MM = 260.0

#: Most crest-sharpening allowed, however steep the wave gets. Past this the
#: drawn surface stops looking like water and starts looking like a sawtooth.
MAX_SHARPNESS = 0.45

#: Steepness, as a fraction of the breaking limit, at which whitecaps appear.
FOAM_AT = 0.75


def wave_number(period_s: float, depth_mm: float = WATER_DEPTH_MM) -> float:
    """Solve the linear dispersion relation for k.

    omega squared = g k tanh(k h). There is no closed form, so it is solved by
    Newton from a deep-water first guess -- a handful of iterations, once per
    redraw, which is nothing next to the drawing itself.
    """
    period = max(float(period_s), 1e-3)
    depth = max(float(depth_mm), 1.0)
    omega = 2.0 * math.pi / period
    target = omega * omega

    k = target / GRAVITY_MM_S2
    if k * depth < 1e-3:
        k = omega / math.sqrt(GRAVITY_MM_S2 * depth)  # shallow-water guess
    for _ in range(40):
        kh = k * depth
        t = math.tanh(kh)
        f = GRAVITY_MM_S2 * k * t - target
        slope = GRAVITY_MM_S2 * (t + kh * (1.0 - t * t))
        if abs(slope) < 1e-15:
            break
        following = k - f / slope
        if following <= 0.0:
            following = k / 2.0
        if abs(following - k) < 1e-12:
            k = following
            break
        k = following
    return max(k, 1e-9)


def wavelength(period_s: float, depth_mm: float = WATER_DEPTH_MM) -> float:
    """Wave length in mm for this period at this depth."""
    return 2.0 * math.pi / wave_number(period_s, depth_mm)


def piston_transfer(period_s: float, depth_mm: float = WATER_DEPTH_MM) -> float:
    """Wave height per mm of stroke, for a piston-type wavemaker.

    The standard result, H/S = 2 (cosh 2kh - 1) / (sinh 2kh + 2kh): a paddle
    that translates bodily through the depth. This array is a bed of vertical
    plungers rather than one translating paddle, so this is an estimate of the
    right order rather than the exact transfer function for this machine --
    PADDLE_GAIN is the knob for correcting it against a measured wave.
    """
    kh = wave_number(period_s, depth_mm) * max(float(depth_mm), 1.0)
    if kh > 20.0:
        return 2.0  # deep water: the ratio tends to 2, and cosh would overflow
    return 2.0 * (math.cosh(2.0 * kh) - 1.0) / (math.sinh(2.0 * kh) + 2.0 * kh)


def wave_height(stroke_mm: float, period_s: float,
                depth_mm: float = WATER_DEPTH_MM) -> float:
    """Peak-to-trough wave height, in mm, for this stroke and period."""
    return (max(float(stroke_mm), 0.0)
            * piston_transfer(period_s, depth_mm) * PADDLE_GAIN)


def breaking_height(period_s: float, depth_mm: float = WATER_DEPTH_MM) -> float:
    """Height at which a wave of this length breaks: H/L = 0.142 tanh kh."""
    length = wavelength(period_s, depth_mm)
    kh = wave_number(period_s, depth_mm) * max(float(depth_mm), 1.0)
    return 0.142 * length * math.tanh(kh)


def intensity(stroke_mm: float, period_s: float,
              depth_mm: float = WATER_DEPTH_MM) -> float:
    """Steepness as a fraction of the breaking limit.

    0 is flat calm; 1 is as steep as a wave of this length can get before it
    spills. It is the one number that says how hard the water is being worked,
    and it is what the strip labels and colours itself from.
    """
    limit = breaking_height(period_s, depth_mm)
    if limit <= 0.0:
        return 0.0
    return wave_height(stroke_mm, period_s, depth_mm) / limit


def intensity_word(value: float) -> str:
    """A word for an intensity, for the label."""
    if value < 0.15:
        return "barely a ripple"
    if value < 0.35:
        return "gentle"
    if value < 0.6:
        return "moderate"
    if value < FOAM_AT + 0.1:
        return "strong"
    if value < 1.0:
        return "steep, close to breaking"
    return "past breaking, expect it to spill"


def sharpness(stroke_mm: float, period_s: float,
              depth_mm: float = WATER_DEPTH_MM) -> float:
    """How much second-order crest sharpening this wave has earned.

    From the Stokes second-order term: with the surface written as
    a cos(theta) + B cos(2 theta), this is B/a, which works out as
    (k a / 4) cosh(kh) (2 + cosh 2kh) / sinh(kh) cubed. Sharp crests over long
    flat troughs are the most recognisable thing about real water, and the
    thing a sine misses entirely, so it is worth the arithmetic.
    """
    depth = max(float(depth_mm), 1.0)
    k = wave_number(period_s, depth)
    kh = k * depth
    if kh > 12.0:
        shape = 1.0  # deep water: the ratio tends to 1
    else:
        sinh = math.sinh(kh)
        if sinh < 1e-9:
            return MAX_SHARPNESS
        shape = math.cosh(kh) * (2.0 + math.cosh(2.0 * kh)) / (sinh ** 3)
    amplitude = wave_height(stroke_mm, period_s, depth) / 2.0
    return max(0.0, min(k * amplitude / 4.0 * shape, MAX_SHARPNESS))


def stokes_shape(value: float, sharpness_ratio: float) -> float:
    """Sharpen a -1..1 surface signal into a crest-heavy one.

    ``value + r (2 value squared - 1)`` is the second-order term written in
    terms of the first, since cos(2 theta) = 2 cos squared theta - 1. Dividing
    by (1 + r) holds the crest at 1 so the wave stays inside the strip; the
    trough lifts towards the middle, which is the asymmetry real water has.
    """
    ratio = max(0.0, min(float(sharpness_ratio), MAX_SHARPNESS))
    if ratio <= 1e-6:
        return value
    return (value + ratio * (2.0 * value * value - 1.0)) / (1.0 + ratio)


def wave_amplitude_px(height_mm: float, strip_height: float) -> float:
    """Half the drawn peak-to-trough, in pixels, for a wave this tall."""
    fraction = min(max(float(height_mm), 0.0) / FULL_WAVE_MM, 1.0)
    return fraction * (float(strip_height) * 0.28)


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
        # Per-row leads within each column, for a row cascade. Keyed by column,
        # one entry per row. Empty when the rows of a column move together.
        self._row_leads: Dict[int, List[float]] = {}
        self._note = "Select some pistons to see the wave they would make."
        self._running = False
        self._job = None

        # What the water does with all that. Worked out once per change rather
        # than once per frame: the dispersion relation is solved by iteration
        # and none of it moves between redraws.
        self._wavelength = CHAMBER_LENGTH_MM
        self._height_mm = 0.0
        self._intensity = 0.0
        self._sharpness = 0.0

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
             start_fractions: Optional[Dict[int, float]] = None,
             row_fractions: Optional[Dict[int, List[float]]] = None) -> None:
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

        # A row cascade puts the three pistons of a column at three different
        # points in the stroke. Keyed by column alone -- which is all this
        # strip used to be given -- the three overwrite one another and the
        # last one wins, so a working row cascade drew exactly the same
        # picture as "All together" and looked like it had done nothing. That
        # is what sent the lab looking for a fault in the machine.
        self._row_leads = {}
        if row_fractions:
            for column, fractions in row_fractions.items():
                leads = []
                for fraction in fractions:
                    s = invert_leg_position(float(fraction), self._smoothing)
                    leads.append(s * self._out_frac)
                if leads:
                    self._row_leads[column] = leads

        # What this stroke and period actually do to the water. The period
        # that matters here is the whole cycle, dwells included, which is what
        # leg_fractions has just worked out.
        self._wavelength = wavelength(self._period)
        self._height_mm = wave_height(self._stroke, self._period)
        self._intensity = intensity(self._stroke, self._period)
        self._sharpness = sharpness(self._stroke, self._period)

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

    def _column_phases(self, column: int) -> List[float]:
        """Every row's phase in this column.

        One entry when the rows of a column move together, which is the usual
        case. Three when a row cascade has deliberately put them out of step.
        """
        leads = self._row_leads.get(column)
        if leads:
            return [(self._phase + lead) % 1.0 for lead in leads]
        return [self._column_phase(column)]

    @staticmethod
    def _unwrap(first: float, second: float) -> float:
        """``second`` moved to within half a cycle of ``first``.

        The phases come back modulo one cycle, so a stagger running
        0.9 -> 0.05 across two columns would otherwise read as running
        backwards through 0.5 and put a crease in the water at that one place.
        """
        while second - first > 0.5:
            second -= 1.0
        while first - second > 0.5:
            second += 1.0
        return second

    def _forcing_phases(self, position_mm: float) -> List[float]:
        """The piston phases driving the water here, interpolated by column.

        A list rather than a single value, because the three rows of a column
        are not always doing the same thing -- and when they are not, all three
        are still pushing on the same water.
        """
        place = (position_mm / CHAMBER_LENGTH_MM) * (self.columns - 1)
        low = max(0, min(int(math.floor(place)), self.columns - 1))
        high = min(low + 1, self.columns - 1)
        blend = max(0.0, min(place - low, 1.0))

        near = self._column_phases(low)
        far = self._column_phases(high)
        if len(near) != len(far):
            # Different row counts between neighbours should not happen, but a
            # crease in the water is not worth a crash: use the nearer column.
            return near
        return [first + (self._unwrap(first, second) - first) * blend
                for first, second in zip(near, far)]

    def _elevation(self, position_mm: float) -> float:
        """The surface at this point along the chamber, as -1..1.

        Two things set it. The pistons underneath say *when* the water here is
        driven -- that is the stagger, and it is what makes a cascade read as a
        wave running along the chamber. The water itself says how that driving
        spreads: a disturbance takes time to travel, so a point further along
        is showing what the array did a moment ago, one moment per wave length
        covered. That second term is the one the old picture had no idea about,
        and it is why an array moving in unison still makes a wave with
        crests and troughs along it rather than a surface that rises and falls
        like a lift.

        The result is shaped by :func:`stokes_shape`, which sharpens the crests
        and flattens the troughs by an amount that grows with steepness.
        """
        travel = position_mm / max(self._wavelength, 1e-6)  # cycles of lag
        phases = self._forcing_phases(position_mm)
        # Averaged, not picked: every row of a column displaces the same water,
        # so what the surface sees is their sum. Rows in step add up to the
        # full stroke; rows spread across the leg partly cancel, and the wave
        # really is smaller. That reduction IS the visible difference between
        # "Rows out of step" and "All together" from the side, and drawing one
        # row instead of the mean is what hid it.
        raw = sum(self._sample(phase - travel) for phase in phases) / len(phases)
        return stokes_shape(raw, self._sharpness)

    def _surface(self, width: float, mid: float, amplitude: float) -> List[float]:
        points: List[float] = []
        steps = max(int(width / 6), 24)
        for i in range(steps + 1):
            t = i / float(steps)
            points.append(mid - self._elevation(t * CHAMBER_LENGTH_MM) * amplitude)
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

        # Still-water level sits high enough that a trough has somewhere to go
        # and the piston bar below still fits.
        mid = height * 0.50
        amplitude = wave_amplitude_px(self._height_mm, height)
        surface = self._surface(width, mid, amplitude)
        steps = len(surface) - 1

        # The body of the water, as one filled polygon under the surface.
        polygon = []
        for i, y in enumerate(surface):
            polygon.extend((width * i / float(steps), y))
        polygon.extend((width, height, 0, height))
        c.create_polygon(polygon, fill=WATER_BODY, outline="")

        # Still-water level, so the wave is seen to sit on something and the
        # asymmetry between crest and trough is visible rather than implied.
        # Width 1 deliberately: Tk on Windows only draws a dash pattern on
        # one-pixel lines, and the lab PC is Windows 7. Widen this and the
        # dash silently becomes a solid rule there while still looking dashed
        # on a newer machine.
        c.create_line(0, mid, width, mid, fill=STILL_WATER, width=1, dash=(2, 4))

        # The surface line itself, brighter, so the shape reads. It whitens as
        # the wave steepens, which is the quickest read of intensity there is.
        c.create_line(
            [v for i, y in enumerate(surface)
             for v in (width * i / float(steps), y)],
            fill=self._surface_colour(), width=2, smooth=True,
        )

        # Whitecaps once the wave is steep enough to spill: a short tick on
        # each crest that is within reach of the breaking limit.
        if self._intensity >= FOAM_AT:
            for index in range(1, steps):
                if surface[index] < surface[index - 1] and surface[index] <= surface[index + 1]:
                    x = width * index / float(steps)
                    c.create_line(x - 4, surface[index] - 1,
                                  x + 4, surface[index] - 1,
                                  fill=FOAM, width=2)

        # The pistons, as a row of bars along the bottom. They are what is
        # driving all of this, and seeing them lead the water is the whole
        # point of a staggered design.
        self._draw_pistons(c, width, height)

        # Both captions along the top: the bottom of the strip belongs to
        # the piston bars now.
        if self._note:
            c.create_text(
                6, 9, text=self._note, anchor="w",
                fill=LABEL_DIM, font=("Segoe UI", 8),
            )
        c.create_text(
            width - 6, 9, text=self._wave_label(), anchor="e",
            fill=LABEL_DIM, font=("Segoe UI", 8),
        )

    def _surface_colour(self) -> str:
        """Crest colour, from calm blue to white as the wave steepens."""
        if self._intensity >= 1.0:
            return FOAM
        if self._intensity >= FOAM_AT:
            return CREST
        return WATER_TOP

    def _wave_label(self) -> str:
        """The water in three numbers and a word.

        The stroke and the cycle time are already on the left of the strip;
        what an operator cannot work out from those is what the water does with
        them, which is this.
        """
        return "wave about {0:.0f} mm, {1:.1f} m long - {2}".format(
            self._height_mm, self._wavelength / 1000.0,
            intensity_word(self._intensity),
        )

    def _draw_pistons(self, c: Canvas, width: float, height: float) -> None:
        """A bar per column along the bottom, at its piston's displacement."""
        base = height - 3
        travel = 9.0
        for column in range(self.columns):
            x = width * (column + 0.5) / float(self.columns)
            level = (self._sample(self._column_phase(column)) + 1.0) / 2.0
            top = base - 2.0 - level * travel
            c.create_rectangle(x - 3, top, x + 3, base,
                               fill=PISTON, outline="")
