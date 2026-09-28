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

# Until the lab establishes a safe stopping limit, this trial uses no more
# than the speed and acceleration of the shipped gentle piston preset.  These
# are conservative software bounds, not a certification of mechanical safety.
MAX_TRIAL_LIFT_MM = 120
MAX_TRIAL_SPEED_MM_S = 200
TRIAL_ACCEL_MM_S2 = 4000
TRIAL_DECEL_MM_S2 = 4000
TRIAL_JERK_MM_S3 = 2000
BOTTOM_MM = params.BY_NAME["Position 2"].maximum
TRIAL_POSITION_TOLERANCE_MM = 2.0


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


@dataclass(frozen=True)
class SolitonTrial:
    """A bounded one-way floor lift to try against a solitary-wave target.

    The independent ``floor_lift_mm`` is deliberate: no measured transfer
    function maps a requested water crest to floor travel yet.  Width is used
    only to estimate a traversal speed from shallow-water phase speed.  A
    point-to-point S-curve cannot reproduce the ideal sech² velocity pulse.
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
    def requested_speed_mm_s(self) -> float:
        return self.floor_lift_mm / self.target.half_height_seconds

    @property
    def speed_mm_s(self) -> int:
        return max(1, min(int(round(self.requested_speed_mm_s)),
                          MAX_TRIAL_SPEED_MM_S))

    @property
    def speed_limited(self) -> bool:
        return self.requested_speed_mm_s > MAX_TRIAL_SPEED_MM_S

    @property
    def nominal_travel_seconds(self) -> float:
        # Actual travel is longer when acceleration and jerk are active.
        return self.floor_lift_mm / float(self.speed_mm_s)

    @staticmethod
    def _motion_values(destination_mm: int, speed_mm_s: int):
        values = params.defaults()
        values.update({
            "Position 1": destination_mm,
            "Position 2": destination_mm,
            "Speed 1": speed_mm_s,
            "Speed 2": speed_mm_s,
            "Accel 1": TRIAL_ACCEL_MM_S2,
            "Accel 2": TRIAL_ACCEL_MM_S2,
            "Decel 1": TRIAL_DECEL_MM_S2,
            "Decel 2": TRIAL_DECEL_MM_S2,
            "Jerk 1": TRIAL_JERK_MM_S3,
            "Jerk 2": TRIAL_JERK_MM_S3,
            "Profile": 2,  # S-curve: controlled deceleration into the endpoint.
            "Move Type": 0,
        })
        return values

    def stage_parameters(self):
        """Slowly lower the selected floor sections before a separate fire."""
        return self._motion_values(BOTTOM_MM, MAX_TRIAL_SPEED_MM_S)

    def pulse_parameters(self):
        """One controlled upward move; no immediate return wave."""
        return self._motion_values(self.top_mm, self.speed_mm_s)
