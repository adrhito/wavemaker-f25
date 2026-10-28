"""A requested solitary-wave shape, separate from any piston command.

The first-order, constant-depth surface profile is H sech²(kx), with
k = sqrt(3H / (4h³)).  This describes a target in the water, not a transfer
function for this machine's vertically moving floor.  The latter requires
geometry and measured runs that the repository does not yet contain.

Reference: https://www.mdpi.com/2077-1312/11/1/35, equations (2)–(5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app import params


GRAVITY_MM_S2 = 9810.0
_HALF_HEIGHT_ARGUMENT = math.acosh(math.sqrt(2.0))

BOTTOM_MM = params.BY_NAME["Position 2"].maximum
TRIAL_POSITION_TOLERANCE_MM = 2.0

# The pulse was first held to the shipped gentle preset (120 mm, 200 mm/s,
# 4,000 mm/s², jerk 2,000 mm/s³), and the waves it made were too small to
# judge, mostly because 120 mm of travel is far less water than a soliton in
# this tank carries.  The pulse may now use the full Position range every
# other tab accepts.  Its speed, acceleration and jerk are worked out for each
# pulse from the wave itself (see SolitonTrial), and never exceed the
# strongest motion the shipped presets already run on this array (Maximum,
# Lil wave demo, Big Wave Demo).  These are software bounds, not a measured
# safe stopping limit.
MAX_TRIAL_LIFT_MM = BOTTOM_MM - params.BY_NAME["Position 1"].minimum
MAX_TRIAL_SPEED_MM_S = params.BY_NAME["Speed 1"].maximum
MAX_TRIAL_ACCEL_MM_S2 = 20000
MAX_TRIAL_JERK_MM_S3 = 7500

# The tank, for working out how far the plates must move to carry one
# soliton's worth of water.  The channel's inside width was taped at 29.25 in
# on 28 October 2026; the 26.5 in used before was out of date.  Three plates
# fill that width, 9.75 in each.  Their length along the tank has NOT been
# measured; the base plates look square in the photos (tank-model/), so it is
# taken equal until a plate is taped.  The lift scales directly with
# PLATE_LENGTH_MM, so measure a plate and correct it here.
TANK_WIDTH_MM = 29.25 * 25.4
PLATES_ACROSS = 3
PLATE_WIDTH_MM = TANK_WIDTH_MM / PLATES_ACROSS
PLATE_LENGTH_MM = PLATE_WIDTH_MM

# Staging only moves the floor into place before the water settles, so it
# keeps the gentle preset's motion: a hard staging move would make the very
# wave the operator is about to wait out.
STAGE_SPEED_MM_S = 200
STAGE_ACCEL_MM_S2 = 4000
STAGE_DECEL_MM_S2 = 4000
STAGE_JERK_MM_S3 = 2000


@dataclass(frozen=True)
class SolitaryTarget:
    """An operator's desired crest rise and longitudinal full width at half height.

    Width and height are linked by first-order solitary-wave theory for a
    given depth.  An independently chosen width is still useful as an
    experimental *target*, but it is not an exact solitary-wave solution when
    it differs from :attr:`theoretical_width_mm`.
    """

    crest_height_mm: float
    width_mm: float
    water_depth_mm: float

    def __post_init__(self) -> None:
        for name in ("crest_height_mm", "width_mm", "water_depth_mm"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("{0} must be a positive finite number".format(name))

    @property
    def phase_speed_mm_s(self) -> float:
        """First-order shallow-water estimate including wave amplitude."""
        return math.sqrt(GRAVITY_MM_S2 *
                         (self.water_depth_mm + self.crest_height_mm))

    @property
    def theoretical_width_mm(self) -> float:
        """FWHM of the first-order soliton at the entered water depth."""
        k = math.sqrt(3.0 * self.crest_height_mm /
                      (4.0 * self.water_depth_mm ** 3))
        return 2.0 * _HALF_HEIGHT_ARGUMENT / k

    @property
    def half_height_seconds(self) -> float:
        """Time for the target's half-height width to pass a fixed point."""
        return self.width_mm / self.phase_speed_mm_s

    @property
    def width_matches_depth(self) -> bool:
        """Whether the operator's width is within 10% of the theory value."""
        return abs(self.width_mm / self.theoretical_width_mm - 1.0) <= 0.1

    def elevation_mm(self, x_mm: float) -> float:
        """Requested crest elevation at distance ``x_mm`` from its center."""
        k = 2.0 * _HALF_HEIGHT_ARGUMENT / self.width_mm
        # Avoid overflow in cosh for distant preview samples.
        argument = min(abs(k * x_mm), 350.0)
        return self.crest_height_mm / math.cosh(argument) ** 2


def plate_footprint_mm(piston_count: int) -> float:
    """Length of tank the selected plates cover, averaged across its width.

    Water carried per millimetre of tank width is plate area times travel
    divided by the tank width, so nine plates three-across act like one plate
    three plates long spanning the whole width.
    """
    return (max(int(piston_count), 0) * PLATE_LENGTH_MM * PLATE_WIDTH_MM /
            TANK_WIDTH_MM)


def volume_lift_mm(target: SolitaryTarget, piston_count: int) -> float:
    """Plate travel that moves one soliton's worth of water, in mm.

    A soliton H sech²(kx) carries 2H/k of water per unit tank width.  Plates
    that nearly fill the tank move their footprint times their travel, so the
    travel is 2H / (k * footprint).  This assumes every plate passes its full
    area of water into the wave; plates with water above and below them
    probably pass less, so the real crest is expected to fall short and the
    operator may need to ask for more.  Returns 0 with nothing selected.
    """
    footprint = plate_footprint_mm(piston_count)
    if footprint <= 0:
        return 0.0
    k = math.sqrt(3.0 * target.crest_height_mm /
                  (4.0 * target.water_depth_mm ** 3))
    return 2.0 * target.crest_height_mm / (k * footprint)


def _s_curve_seconds(distance: float, speed: float, accel: float,
                     jerk: float) -> float:
    """Duration of a symmetric jerk-limited point-to-point move.

    The drive honours jerk in mm/s³ (the measured BIGGER WAVE cycle only fits
    that reading), so lift/speed badly underestimates a gentle move.
    """
    def ramp(peak):
        # Time and distance to reach ``peak`` speed from rest.
        if peak >= accel * accel / jerk:
            seconds = peak / accel + accel / jerk
        else:
            seconds = 2.0 * math.sqrt(peak / jerk)
        return seconds, peak * seconds / 2.0

    seconds, covered = ramp(speed)
    if 2.0 * covered <= distance:
        return 2.0 * seconds + (distance - 2.0 * covered) / speed
    low, high = 0.0, speed  # Too short to reach full speed: find the peak.
    for _ in range(60):
        middle = (low + high) / 2.0
        if 2.0 * ramp(middle)[1] < distance:
            low = middle
        else:
            high = middle
    return 2.0 * ramp(high)[0]


@dataclass(frozen=True)
class SolitonTrial:
    """A bounded one-way floor lift to try against a solitary-wave target.

    The floor's travel follows the ideal soliton-making trajectory
    X(t) = (S/2)(1 + tanh(wt)), whose time scale w is the time for the
    target's width to pass at its phase speed.  That fixes the peak speed
    (Sw/2), peak acceleration (0.385 S w²) and peak jerk (S w³) the S-curve
    is given, so a long, low soliton in deep water gets the long, gentle
    push it needs instead of a hard knock that only makes ripples.  A
    point-to-point S-curve is still only an approximation of that tanh.
    """

    target: SolitaryTarget
    floor_lift_mm: int

    def __post_init__(self) -> None:
        if isinstance(self.floor_lift_mm, bool) or not isinstance(self.floor_lift_mm, int):
            raise ValueError("floor lift must be a whole number of millimetres")
        if not 1 <= self.floor_lift_mm <= MAX_TRIAL_LIFT_MM:
            raise ValueError("floor lift must be between 1 and {0} mm".format(
                MAX_TRIAL_LIFT_MM))

    @property
    def top_mm(self) -> int:
        return BOTTOM_MM - self.floor_lift_mm

    @property
    def _rate(self) -> float:
        """The trajectory's time scale w, in 1/s."""
        return (2.0 * _HALF_HEIGHT_ARGUMENT * self.target.phase_speed_mm_s /
                self.target.width_mm)

    @property
    def requested_speed_mm_s(self) -> float:
        return self.floor_lift_mm * self._rate / 2.0

    @property
    def requested_accel_mm_s2(self) -> float:
        return self.floor_lift_mm * self._rate ** 2 * 2.0 / (3.0 * math.sqrt(3.0))

    @property
    def requested_jerk_mm_s3(self) -> float:
        return self.floor_lift_mm * self._rate ** 3

    @property
    def speed_mm_s(self) -> int:
        return max(1, min(int(math.ceil(self.requested_speed_mm_s)),
                          MAX_TRIAL_SPEED_MM_S))

    @property
    def accel_mm_s2(self) -> int:
        """Used for deceleration too: the ideal push is symmetric."""
        return max(1, min(int(math.ceil(self.requested_accel_mm_s2)),
                          MAX_TRIAL_ACCEL_MM_S2))

    @property
    def jerk_mm_s3(self) -> int:
        return max(1, min(int(math.ceil(self.requested_jerk_mm_s3)),
                          MAX_TRIAL_JERK_MM_S3))

    @property
    def speed_limited(self) -> bool:
        """True when any cap stops the drive following the ideal push."""
        return (self.requested_speed_mm_s > MAX_TRIAL_SPEED_MM_S or
                self.requested_accel_mm_s2 > MAX_TRIAL_ACCEL_MM_S2 or
                self.requested_jerk_mm_s3 > MAX_TRIAL_JERK_MM_S3)

    @property
    def nominal_travel_seconds(self) -> float:
        """Expected duration of the pulse, including its S-curve ramps."""
        return _s_curve_seconds(self.floor_lift_mm, self.speed_mm_s,
                                self.accel_mm_s2, self.jerk_mm_s3)

    @staticmethod
    def _motion_values(destination_mm: int, speed_mm_s: int,
                       accel: int, decel: int, jerk: int):
        values = params.defaults()
        values.update({
            "Position 1": destination_mm,
            "Position 2": destination_mm,
            "Speed 1": speed_mm_s,
            "Speed 2": speed_mm_s,
            "Accel 1": accel,
            "Accel 2": accel,
            "Decel 1": decel,
            "Decel 2": decel,
            "Jerk 1": jerk,
            "Jerk 2": jerk,
            "Profile": 2,  # S-curve: controlled deceleration into the endpoint.
            "Move Type": 0,
        })
        return values

    def stage_parameters(self):
        """Slowly lower the selected floor sections before a separate fire."""
        return self._motion_values(BOTTOM_MM, STAGE_SPEED_MM_S,
                                   STAGE_ACCEL_MM_S2, STAGE_DECEL_MM_S2,
                                   STAGE_JERK_MM_S3)

    def pulse_parameters(self):
        """One controlled upward move; no immediate return wave."""
        return self._motion_values(self.top_mm, self.speed_mm_s,
                                   self.accel_mm_s2, self.accel_mm_s2,
                                   self.jerk_mm_s3)
