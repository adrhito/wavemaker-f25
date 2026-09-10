"""Wave shapes, and how they become machine parameters.

You pick a shape, a height and a period; this works out the stroke, the speed
and the motion profile that make the array do that.

What the machine can and cannot do
----------------------------------
The pistons are driven between two positions at a set speed on one of four
built-in motion profiles. That is the whole vocabulary. So:

* **Height** becomes the stroke -- how far each piston travels.
* **Period** becomes the speed, since a piston covering the stroke in half a
  period is what sets the frequency.
* **Shape** becomes the profile. The controller's four profiles really are
  different waveforms: sine is smooth, trapezoidal is a hard push with flat
  top, S-curve eases in and out, Bestehorn is gentler still on the mechanism.

* **A travelling wave** -- one that runs along the chamber rather than the whole
  array rising together -- cannot be had from these parameters in a continuous
  run, because there is no per-piston start delay among them. It needs the
  controller's curve feature, where Curve Offset shifts each column in time.
  The application says which of the two a given design needs rather than
  pretending both are available.

Nothing here models fluid. It converts a description of piston motion into
piston parameters. Whether a 200 mm stroke at 1.5 seconds makes the wave you
want is a question for the tank, not for this file.
"""

from __future__ import annotations

import math
from typing import Dict, List, NamedTuple

from app import params

#: The machine's four motion profiles, from app/params.py.
TRAPEZOIDAL, BESTEHORN, S_CURVE, SINE = 0, 1, 2, 3


class WaveShape(NamedTuple):
    """One kind of wave the array can make."""

    key: str
    name: str
    #: One line an operator can act on. No jargon.
    description: str
    #: Which of the controller's motion profiles gives this character.
    profile: int
    #: Suggested starting height and period, so a preset is one click.
    height: int
    period: float
    #: Drawn in the picker: amplitude at a fraction 0..1 through one cycle.
    def sample(self, t: float) -> float:
        return _SAMPLERS[self.key](t)


def _sine(t: float) -> float:
    return math.sin(2 * math.pi * t)


def _trapezoid(t: float) -> float:
    # Constant-speed ramps with flat tops: what a hard trapezoidal push looks
    # like at the water surface.
    x = (t % 1.0) * 4.0
    if x < 1:
        return x
    if x < 2:
        return 1.0
    if x < 3:
        return 1.0 - (x - 2) * 2.0
    return -1.0


def _s_curve(t: float) -> float:
    # A sine with the peaks flattened: eased at both ends.
    raw = math.sin(2 * math.pi * t)
    return math.copysign(abs(raw) ** 0.7, raw)


def _bestehorn(t: float) -> float:
    # Gentler still, with a longer dwell around the turn.
    raw = math.sin(2 * math.pi * t)
    return math.copysign(abs(raw) ** 1.4, raw)


_SAMPLERS = {
    "ripples": _sine,
    "swell": _sine,
    "rolling": _s_curve,
    "choppy": _trapezoid,
    "storm": _trapezoid,
    "long": _bestehorn,
}


#: The shapes offered, gentlest first.
SHAPES: List[WaveShape] = [
    WaveShape(
        "ripples", "Ripples",
        "Small, quick and smooth. The calmest thing the array can make.",
        SINE, 80, 0.8,
    ),
    WaveShape(
        "swell", "Gentle swell",
        "Smooth and even, like open water on a calm day.",
        SINE, 180, 1.6,
    ),
    WaveShape(
        "rolling", "Rolling waves",
        "Bigger and rounder, with a definite rise and fall.",
        S_CURVE, 260, 2.0,
    ),
    WaveShape(
        "choppy", "Choppy",
        "Short and sharp. The pistons push hard and stop flat.",
        TRAPEZOIDAL, 200, 1.0,
    ),
    WaveShape(
        "storm", "Storm",
        "The biggest and hardest the machine will make. Watch the array.",
        TRAPEZOIDAL, 350, 0.9,
    ),
    WaveShape(
        "long", "Long swell",
        "A long, slow, heavy rise and fall.",
        BESTEHORN, 320, 3.2,
    ),
]

BY_KEY: Dict[str, WaveShape] = dict((shape.key, shape) for shape in SHAPES)

# Where a wave design lives within the travel. Pistons rest at the bottom, so a
# wave is built upwards from there: a taller wave reaches further up.
BOTTOM = params.BY_NAME["Position 2"].maximum      # 370
TOP = params.BY_NAME["Position 1"].minimum        # -20

#: How the array is driven.
TOGETHER = "together"       # every piston in step; run with Start
TRAVELLING = "travelling"   # each column delayed; needs Start Curve

MIN_PERIOD = 0.4
MAX_PERIOD = 6.0
MIN_HEIGHT = 20
MAX_HEIGHT = BOTTOM - TOP    # 390


def clamp_height(height: int) -> int:
    return int(max(MIN_HEIGHT, min(height, MAX_HEIGHT)))


def clamp_period(period: float) -> float:
    return float(max(MIN_PERIOD, min(period, MAX_PERIOD)))


def stroke_for(height: int):
    """The two positions a wave of this height travels between."""
    height = clamp_height(height)
    low = BOTTOM - height
    return int(max(TOP, low)), int(BOTTOM)


def speed_for(height: int, period: float) -> int:
    """Speed needed to cover the stroke in half a period.

    Clamped to the machine's limit, which is why a very tall wave cannot also
    be very fast -- :func:`achievable_period` says what you actually get.
    """
    height = clamp_height(height)
    period = clamp_period(period)
    ideal = 2.0 * height / period
    return int(min(round(ideal), params.BY_NAME["Speed 1"].maximum))


def achievable_period(height: int, period: float) -> float:
    """The period actually obtained once the speed limit is applied."""
    speed = speed_for(height, period)
    if speed <= 0:
        return clamp_period(period)
    return 2.0 * clamp_height(height) / float(speed)


def is_limited(height: int, period: float) -> bool:
    """Whether the speed limit stops this design running as fast as asked."""
    return achievable_period(height, period) > clamp_period(period) + 0.05


def to_parameters(shape: WaveShape, height: int, period: float) -> Dict[str, int]:
    """The machine parameters for one piston running this wave."""
    low, high = stroke_for(height)
    speed = speed_for(height, period)
    values = params.defaults()
    values.update({
        "Position 1": low,
        "Position 2": high,
        "Speed 1": speed,
        "Speed 2": speed,
        "Profile": shape.profile,
        "Move Type": 0,
    })
    # Sharper waves want the acceleration to match, or the profile is wasted.
    if shape.profile == TRAPEZOIDAL:
        accel = params.BY_NAME["Accel 1"].maximum
        jerk = 7500
    elif shape.profile == SINE:
        accel = 12000
        jerk = 5000
    else:
        accel = 9000
        jerk = 3500
    values.update({
        "Accel 1": accel, "Accel 2": accel,
        "Decel 1": accel, "Decel 2": accel,
        "Jerk 1": jerk, "Jerk 2": jerk,
    })
    return values


def column_offsets(period: float, columns: int = 10, wavelength: float = 10.0):
    """Curve Offset per column, so the wave runs front to back.

    One wavelength across ``wavelength`` columns. Only meaningful for a curve
    run: Curve Offset is not consulted during an ordinary continuous run, which
    is why a travelling design has to be started with Start Curve.
    """
    offsets = {}
    for column in range(columns):
        fraction = (column % wavelength) / float(wavelength)
        offsets[column] = int(round(fraction * period * 100))
    return offsets
