"""Solitary waves: what the array has to do to make one.

A solitary wave is not one of the Wave tab's shapes with the repeat turned off.
Everything on that tab is periodic -- a stroke and a period, run continuously --
and a soliton is a single monotone push whose *shape in time* is the whole
point. `docs/CALIBRATION_NOTES.md` says the same thing: "solitary waves need
their own calibration and control path rather than being treated as one of the
existing periodic Wave-tab shapes". Hence a separate module and a separate tab.

The wave
--------
The classical KdV solitary wave on still water of depth ``h`` with crest rise
``a`` above it:

    eta(x, t) = a * sech^2( kappa * (x - c*t) )
    kappa     = sqrt( 3a / (4 h^3) )          the inverse width
    c         = sqrt( g (h + a) )             the celerity

Two consequences the operator can act on. It travels faster when it is taller,
so the firing delays between columns depend on the amplitude asked for. And it
is *wide*: 1/kappa for a 50 mm wave on 250 mm of water is about 645 mm, which
is comparable with the length of the piston bank rather than small against it.
That is why the columns cannot simply fire together -- see `column_delays`.

What the pistons have to do
---------------------------
The bank hangs above the water at one end and pushes down, so it is a plunger:
it makes a wave by displacing a volume, not by sweeping a column of water like
a horizontal paddle. Per unit width the wave carries

    V = integral of eta dx = 2a / kappa

and a bank pushing down by ``S`` over water of depth ``h`` displaces ``S * h``
per unit width, which gives ``S = 2a / (kappa * h)``.

Goring and Raichlen (1980) give the horizontal-piston stroke as ``a/(kappa*h)``
for the same wave, which is half of this. The factor is not a typo on either
side: a horizontal piston occupying the full depth and a plunger entering from
above do not displace the same volume for the same stroke, and the published
result also assumes the paddle is short compared with the wave. Neither
assumption holds here.

**So treat the stroke this module returns as a starting recipe, not a
prediction.** Nothing in this repository has ever measured water height against
piston motion -- there is no wave gauge and no camera tracker -- so
:func:`design` takes a ``gain`` the operator can turn once they have seen what
the tank actually does. That is exactly the calibration the notes ask for, and
the honest thing to put in front of somebody is a number they can correct.

Units are millimetres and seconds throughout, matching app/params.py.
"""

from __future__ import annotations

import math
from typing import Dict, List, NamedTuple, Optional

from app import params, tags

#: Gravity in mm/s^2, because every length in this application is millimetres.
GRAVITY = 9810.0

#: Still-water depth bounds offered. The pistons travel 0..370 mm, so a depth
#: far outside that cannot be what is in the tank.
MIN_DEPTH = 40
MAX_DEPTH = 400
DEFAULT_DEPTH = 250

#: Crest rise above still water. Small amplitudes are the well-behaved end of
#: the theory: the KdV derivation assumes a/h is small, and by a/h = 0.7 the
#: wave is close to breaking and nothing here describes it.
MIN_AMPLITUDE = 5
MAX_AMPLITUDE = 200
DEFAULT_AMPLITUDE = 40

#: Above this ratio of amplitude to depth the wave breaks rather than
#: propagating, and the sech^2 description stops meaning anything. The usual
#: figure quoted for the limiting solitary wave is 0.78.
BREAKING_RATIO = 0.78
#: Past this the shallow-water assumptions are strained and the design is worth
#: a warning even though it will still run.
STEEP_RATIO = 0.5

#: Spacing between piston columns along the tank, in mm. A guess until somebody
#: measures the bank; it only scales the firing delays, and the tab lets the
#: operator set it.
DEFAULT_COLUMN_PITCH = 150

#: How far out along the sech^2 tail the push is taken before it is called
#: finished. tanh(3.8) is 0.999, so this captures all but a thousandth of the
#: wave; taking it further only adds dead time at the ends of the stroke.
TAIL_REACH = 3.8

#: Curve Offset is an integer in hundredths of a second.
OFFSET_TICKS_PER_SECOND = 100.0


def clamp_depth(depth) -> int:
    try:
        return int(max(MIN_DEPTH, min(float(depth), MAX_DEPTH)))
    except (TypeError, ValueError):
        return DEFAULT_DEPTH


def clamp_amplitude(amplitude) -> int:
    try:
        return int(max(MIN_AMPLITUDE, min(float(amplitude), MAX_AMPLITUDE)))
    except (TypeError, ValueError):
        return DEFAULT_AMPLITUDE


# -- the wave itself ----------------------------------------------------------

def celerity(depth, amplitude) -> float:
    """How fast the soliton travels, in mm/s.

    A taller soliton is a faster one, which is why the firing delays between
    columns cannot be fixed once and reused for every amplitude.
    """
    h = clamp_depth(depth)
    a = clamp_amplitude(amplitude)
    return math.sqrt(GRAVITY * (h + a))


def wave_number(depth, amplitude) -> float:
    """``kappa``: the inverse width of the sech^2 profile, in 1/mm."""
    h = float(clamp_depth(depth))
    a = float(clamp_amplitude(amplitude))
    return math.sqrt(3.0 * a / (4.0 * h ** 3))


def width(depth, amplitude) -> float:
    """``1/kappa`` in mm -- the length scale of the wave.

    Worth showing the operator next to the length of the piston bank: when the
    two are comparable, firing every column at once is not a solitary wave, it
    is a wall of water leaving all at the same moment.
    """
    kappa = wave_number(depth, amplitude)
    if kappa <= 0.0:
        return 0.0
    return 1.0 / kappa


def duration(depth, amplitude) -> float:
    """How long the push lasts, in seconds.

    The sech^2 tail is infinite, so "how long" is a choice: the push runs from
    one end of the tail to the other (:data:`TAIL_REACH` each way) at the
    wave's own speed, plus the time the piston itself is moving.
    """
    kappa = wave_number(depth, amplitude)
    speed = celerity(depth, amplitude)
    if kappa <= 0.0 or speed <= 0.0:
        return 0.0
    tail = 2.0 * TAIL_REACH / kappa
    return (tail + stroke(depth, amplitude)) / speed


# -- what the pistons do ------------------------------------------------------

def stroke(depth, amplitude, gain: float = 1.0) -> float:
    """How far the bank pushes down, in mm, from displaced volume.

    ``S = 2a / (kappa * h)``: see the module docstring for where this comes
    from and why it is a recipe rather than a prediction. ``gain`` is the
    operator's calibration handle once the tank has been watched.
    """
    h = float(clamp_depth(depth))
    a = float(clamp_amplitude(amplitude))
    kappa = wave_number(h, a)
    if kappa <= 0.0 or h <= 0.0:
        return 0.0
    return (2.0 * a / (kappa * h)) * float(gain)


def peak_speed(depth, amplitude, gain: float = 1.0) -> float:
    """The fastest the piston has to move, in mm/s.

    A sech^2 velocity pulse of total travel ``S`` over time ``T`` peaks at
    ``2S/T`` rather than the mean ``S/T``: half the travel happens in the
    middle fifth of the time. Checking the mean against the drive's 900 mm/s
    limit would pass designs the machine then clips the top off, turning the
    velocity bell into a flat-topped push and the soliton into something else.
    """
    seconds = duration(depth, amplitude)
    if seconds <= 0.0:
        return 0.0
    return 2.0 * stroke(depth, amplitude, gain) / seconds


def column_delays(depth, amplitude, columns: int,
                  pitch: float = DEFAULT_COLUMN_PITCH) -> Dict[int, float]:
    """When each column fires, in seconds after the first, keyed by column 0..n.

    The bank is long compared with the wave -- see :func:`width` -- so the
    columns are not one plunger. Each sits a little further along the tank than
    the one before it, and to be part of the same solitary wave it has to push
    when the wave arrives under it, not when its neighbour does.

    So the disturbance is walked along the bank at the wave's own celerity.
    This is the difference between a soliton and a released hump: the lab's
    2022 attempt (`Wavemaker_KdVSoliton.csv`) fired all thirty pistons together
    with Curve Offset 0, which is a hump, and a released hump fissions into a
    train of solitons instead of making one.
    """
    speed = celerity(depth, amplitude)
    if speed <= 0.0 or columns <= 0:
        return {}
    step = float(pitch) / speed
    return dict((column, column * step) for column in range(int(columns)))


def curve_offsets(depth, amplitude, columns: int,
                  pitch: float = DEFAULT_COLUMN_PITCH) -> Dict[int, int]:
    """:func:`column_delays` as Curve Offset values, in hundredths of a second.

    Curve Offset is the only real per-piston timing this machine has, and the
    controller reads it during a curve run only. A continuous run ignores it,
    which is why a soliton cannot be a continuous-run design however the
    parameters are arranged.
    """
    return dict(
        (column, int(round(seconds * OFFSET_TICKS_PER_SECOND)))
        for column, seconds in column_delays(depth, amplitude, columns, pitch).items()
    )


# -- the whole design ---------------------------------------------------------

class SolitonDesign(NamedTuple):
    """Everything a soliton run needs, and everything wrong with it."""

    depth: int
    amplitude: int
    gain: float
    pitch: float

    celerity: float
    width: float
    duration: float
    stroke: float
    peak_speed: float

    #: Curve Offset per column index, 0 being nearest the end wall.
    offsets: Dict[int, int]
    #: Things the operator should know. Empty when the design is unremarkable.
    warnings: List[str]
    #: Things that stop it running at all.
    problems: List[str]

    @property
    def runnable(self) -> bool:
        return not self.problems

    @property
    def steepness(self) -> float:
        """Amplitude over depth. The number the theory is expanded in."""
        if self.depth <= 0:
            return 0.0
        return float(self.amplitude) / float(self.depth)


def submerged_columns(depth) -> int:
    """How many columns of the bank are actually in the water.

    The bank is not level: each column sits higher than the one before it, so
    pistons 1-15 (columns 1-5) are always fully submerged and 16-30 ride clear
    of a low surface. A piston out of the water reports a perfectly healthy
    stroke and moves no water at all, so a soliton design that includes them is
    quietly weaker than it looks. See the tank section of the wavemaker skill.

    Without a measured height for each column this is the conservative answer
    rather than a calculated one: the five that are always under.
    """
    return 5


def design(depth, amplitude, gain: float = 1.0,
           pitch: float = DEFAULT_COLUMN_PITCH,
           columns: Optional[int] = None) -> SolitonDesign:
    """Work out a whole soliton run, and say what is wrong with it."""
    h = clamp_depth(depth)
    a = clamp_amplitude(amplitude)
    if columns is None:
        columns = submerged_columns(h)
    columns = int(max(1, min(columns, tags.COLUMN_COUNT)))

    travel = stroke(h, a, gain)
    fastest = peak_speed(h, a, gain)
    seconds = duration(h, a)
    offsets = curve_offsets(h, a, columns, pitch)

    warnings: List[str] = []
    problems: List[str] = []

    ratio = float(a) / float(h)
    if ratio >= BREAKING_RATIO:
        problems.append(
            "A {0} mm wave on {1} mm of water is past breaking ({2:.2f} of the "
            "depth, limit {3:.2f}). It will spill instead of travelling. Ask "
            "for less height or fill the tank deeper.".format(
                a, h, ratio, BREAKING_RATIO)
        )
    elif ratio >= STEEP_RATIO:
        warnings.append(
            "At {0:.2f} of the depth this is a steep soliton. The theory "
            "behind these numbers assumes a small fraction, so expect the "
            "wave to differ from the target more than a gentler one would."
            .format(ratio)
        )

    speed_limit = params.BY_NAME["Speed 1"].maximum
    if fastest > speed_limit:
        problems.append(
            "The pistons would have to reach {0:.0f} mm/s and they stop at "
            "{1}. The drive would clip the top off the push, which flattens "
            "the wave rather than shrinking it. Ask for less height, or a "
            "deeper tank.".format(fastest, speed_limit)
        )

    reach = params.BY_NAME["Position 2"].maximum - params.BY_NAME["Position 1"].minimum
    if travel > reach:
        problems.append(
            "The push would be {0:.0f} mm and the pistons travel {1} mm. Ask "
            "for less height.".format(travel, reach)
        )

    # A stagger finer than Curve Offset's own resolution is not a stagger.
    if len(offsets) > 1:
        spread = max(offsets.values()) - min(offsets.values())
        if spread == 0:
            warnings.append(
                "The columns would all fire within one hundredth of a second, "
                "which is the finest Curve Offset can express, so this will "
                "behave as a single push rather than a travelling one. A "
                "larger column spacing or a slower wave would separate them."
            )

    bank = float(pitch) * max(columns - 1, 0)
    wave_width = width(h, a)
    if bank > 0 and wave_width > 0 and bank > 2.0 * wave_width:
        warnings.append(
            "The pistons in use span {0:.0f} mm and the wave is about {1:.0f} "
            "mm wide, so the bank is long compared with the wave. The firing "
            "delays matter more than usual here; check the column spacing is "
            "right.".format(bank, wave_width)
        )

    return SolitonDesign(
        depth=h, amplitude=a, gain=float(gain), pitch=float(pitch),
        celerity=celerity(h, a), width=wave_width, duration=seconds,
        stroke=travel, peak_speed=fastest, offsets=offsets,
        warnings=warnings, problems=problems,
    )


def axes_for(columns: int) -> List[int]:
    """The axes of every piston in the first ``columns`` columns of the bank.

    Column 1 is nearest the end wall and is the deepest, so counting from there
    is counting from the pistons that move the most water. Returned as axes
    because that is what Model wants; everything shown to an operator goes
    through tags.display_number.
    """
    wanted = []
    for column in range(int(max(0, columns))):
        for row in range(tags.ROWS_PER_COLUMN):
            number = column * tags.ROWS_PER_COLUMN + row + 1
            if number <= tags.MOTOR_COUNT:
                wanted.append(tags.axis_from_display(number))
    return sorted(wanted)
