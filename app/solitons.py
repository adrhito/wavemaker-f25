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


GRAVITY_MM_S2 = 9810.0
_HALF_HEIGHT_ARGUMENT = math.acosh(math.sqrt(2.0))


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
