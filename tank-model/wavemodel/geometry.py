"""The tank and the piston bank, from geometry.json.

Everything is in metres (positions) and cubic metres (volumes). Piston
numbering follows the wavemaker-f25 application: display pistons 1..30,
three per column. Column n is William's "row n": the group of three whose
pipe is 21 + 3n inches long, so row 1 has the shortest pipes and the highest
bottoms, row 10 the longest and the lowest. Which end of the bank row 10 is
at comes from geometry.json (piston_bank.row_10_end).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
GEOMETRY_FILE = ROOT / "geometry.json"

GALLONS_PER_M3 = 264.172


def _v(node):
    """A dimension's value, whether stored bare or as {"value": ...}."""
    return node["value"] if isinstance(node, dict) else node


@dataclass
class Piston:
    number: int          # display number, 1..30
    column: int          # 1..10, along the axis (William's row)
    row: int             # 1..3, across the axis (lane)
    x: float             # centre, m
    y: float             # centre, m
    bottom: float        # pipe bottom above the tank floor, m


@dataclass
class Geometry:
    channel_length: float
    channel_width: float
    channel_depth: float
    head_length: float
    head_width: float
    head_depth: float
    reservoir_length: float
    reservoir_width: float
    reservoir_height: float
    floor_above_lab: float
    rows: int
    columns: int
    pitch_across: float
    pitch_along: float
    bank_near_x: float
    pipe_top: float
    row_bottoms: List[float]
    bottom_offset: float
    row_10_end: str
    duct_side_walls: bool
    between_pipes_open: bool
    cylinder_od: float
    cylinder_bore: float
    disc_zero: float
    park_mm: float
    stroke_min_mm: float
    stroke_max_mm: float
    speed_max: float
    accel_max: float
    bestehorn_jerk_factor: float
    published: Dict = field(default_factory=dict)
    raw: Dict = field(default_factory=dict)

    # ---- derived ---------------------------------------------------------

    @property
    def bore_area(self) -> float:
        """Water pushed per metre of piston travel, m^2."""
        return math.pi * self.cylinder_bore ** 2 / 4.0

    @property
    def pipe_area(self) -> float:
        """Plan area of one pipe to its outside, m^2."""
        return math.pi * self.cylinder_od ** 2 / 4.0

    @property
    def void_fraction(self) -> float:
        """Share of the bank's footprint between the round pipes that is open water."""
        if not self.between_pipes_open:
            return 0.0
        return max(0.0, 1.0 - self.pipe_area / (self.pitch_along * self.pitch_across))

    @property
    def bank_length(self) -> float:
        return self.columns * self.pitch_along

    @property
    def bank_width(self) -> float:
        return self.rows * self.pitch_across

    @property
    def bank_x_range(self) -> Tuple[float, float]:
        return (self.bank_near_x - self.bank_length, self.bank_near_x)

    @property
    def column_1_end(self) -> str:
        return "head" if self.row_10_end == "channel" else "channel"

    def row_bottom(self, column: int) -> float:
        """Pipe bottom of William's row `column` (1..10) above the tank floor, m."""
        return self.row_bottoms[column - 1] + self.bottom_offset

    def pipe_length(self, column: int) -> float:
        return self.pipe_top - self.row_bottom(column)

    def wet_columns(self, depth: float) -> List[int]:
        """Rows whose pipe bottoms are under still water `depth`: the only ones that move water."""
        return [c for c in range(1, self.columns + 1) if self.row_bottom(c) < depth]

    def column_x(self, column: int) -> float:
        """x of the centre of a column (row of three)."""
        k = self.columns - column if self.row_10_end == "channel" else column - 1
        return self.bank_near_x - (k + 0.5) * self.pitch_along

    def columns_toward_channel(self, column: int) -> List[int]:
        """This column, then its neighbours in order toward the channel mouth."""
        step = 1 if self.row_10_end == "channel" else -1
        out, c = [], column
        while 1 <= c <= self.columns:
            out.append(c)
            c += step
        return out

    def lip_height(self, column: int, depth: float) -> Optional[float]:
        """Roof height under which a wet row's water leaves the bank toward the channel.

        Water pushed out of a pipe runs along under the bottoms of the wet
        pipes between it and the channel, and comes out under the last of
        them. None if this row is dry.
        """
        z = None
        for c in self.columns_toward_channel(column):
            if self.row_bottom(c) >= depth:
                break
            z = self.row_bottom(c)
        return z

    def disc_height(self, position_mm) -> float:
        """Disc face above the tank floor with the piston at `position_mm` (down = larger)."""
        return self.disc_zero - position_mm / 1000.0

    def pistons(self) -> List[Piston]:
        out = []
        for n in range(1, self.rows * self.columns + 1):
            col = (n - 1) // self.rows + 1
            row = (n - 1) % self.rows + 1
            y = (row - (self.rows + 1) / 2) * self.pitch_across
            out.append(Piston(n, col, row, self.column_x(col), y, self.row_bottom(col)))
        return out

    # ---- volumes -----------------------------------------------------------

    def plan_area_main(self, depth: float) -> float:
        """Free-surface area of head + channel at still-water `depth`, m^2.

        The pipes of wet rows cut through the surface; dry rows hang above it.
        """
        cut = sum(self.rows * self.pipe_area for c in self.wet_columns(depth))
        return self.head_length * self.head_width + self.channel_length * self.channel_width - cut

    def volume_main(self, depth: float) -> float:
        """Water in head + channel at still-water depth `depth` (m), m^3, outside the pipes.

        The wet pipes displace their outside area from their bottom up to the
        surface. The water drawn up inside them is `cylinder_water`.
        """
        d = max(0.0, min(depth, max(self.head_depth, self.channel_depth)))
        head = self.head_length * self.head_width * min(d, self.head_depth)
        chan = self.channel_length * self.channel_width * min(d, self.channel_depth)
        pipes = sum(self.rows * self.pipe_area * (min(d, self.head_depth) - self.row_bottom(c))
                    for c in self.wet_columns(min(d, self.head_depth)))
        return head + chan - pipes

    def air_table(self, depth: float, piston_mm: Optional[float] = None) -> List[Dict]:
        """Per row: pipe bottom, how deep it sits, and the trapped air and water inside
        with the discs parked (or at `piston_mm`). See wavemodel/air.py."""
        from . import air
        pos = self.park_mm if piston_mm is None else piston_mm
        out = []
        for c in range(1, self.columns + 1):
            zb = self.row_bottom(c)
            st = air.rest_state(self.disc_height(self.park_mm), self.disc_height(pos), zb, depth)
            out.append({"row": c, "pistons": "%d-%d" % (3 * c - 2, 3 * c), "bottom_m": zb,
                        "pipe_length_m": self.pipe_length(c), "submergence_m": depth - zb,
                        "wet": zb < depth, **st})
        return out

    def cylinder_water(self, depth: float, piston_mm: Optional[float] = None) -> float:
        """Water held inside the 30 pipes, m^3, with the discs at `piston_mm` (parked by default).

        A pipe holds water only above its bottom and only if that bottom is
        under the still water; the rest is the air trapped below the disc.
        """
        return sum(self.rows * self.bore_area * r["water_column_m"] for r in self.air_table(depth, piston_mm))

    def volume_reservoir(self, level: float = None) -> float:
        h = self.reservoir_height if level is None else max(0.0, min(level, self.reservoir_height))
        return self.reservoir_length * self.reservoir_width * h

    def capacity_table(self) -> Dict[str, float]:
        """Volumes filled to the brim, m^3 and gallons, against the published 13,500 gal."""
        main = self.volume_main(self.channel_depth)
        cyl = self.cylinder_water(self.channel_depth)
        res = self.volume_reservoir()
        total = main + cyl + res
        return {
            "head_and_channel_m3": main,
            "cylinders_m3": cyl,
            "reservoir_m3": res,
            "total_m3": total,
            "total_gal": total * GALLONS_PER_M3,
            "published_gal": self.published.get("capacity_gal"),
            "ratio_to_published": total * GALLONS_PER_M3 / self.published["capacity_gal"]
            if self.published.get("capacity_gal") else None,
        }

    def depth_for_volume(self, volume_m3: float) -> float:
        """Still-water depth in head + channel that holds `volume_m3` (bisection)."""
        lo, hi = 0.0, self.channel_depth
        for _ in range(60):
            mid = (lo + hi) / 2
            if self.volume_main(mid) < volume_m3:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    @property
    def total_length(self) -> float:
        return self.head_length + self.channel_length + self.reservoir_length


RANGED = {
    "channel_length": ("channel", "length"), "channel_width": ("channel", "width"),
    "channel_depth": ("channel", "depth_to_rim"), "head_length": ("head", "length_along_axis"),
    "head_width": ("head", "width_across"), "head_depth": ("head", "depth_to_rim"),
    "reservoir_length": ("reservoir", "length_along_axis"), "reservoir_width": ("reservoir", "width"),
    "reservoir_height": ("reservoir", "height_above_channel_floor"),
    "bottom_offset": ("piston_bank", "bottom_offset"), "disc_zero": ("pistons", "disc_face_at_0mm"),
}


def capacity_band(n: int = 20000, seed: int = 1) -> Dict[str, float]:
    """Brim-full capacity, gallons, with every ranged dimension drawn uniformly from its range."""
    import numpy as np
    rng = np.random.default_rng(seed)
    g = load()
    totals = []
    for _ in range(n):
        kw = {}
        for attr, (sec, key) in RANGED.items():
            node = g.raw[sec][key]
            if isinstance(node, dict) and "range" in node:
                lo, hi = node["range"]
                kw[attr] = rng.uniform(lo, hi)
        gi = load(**kw)
        if "head_depth" in kw and "channel_depth" in kw:
            gi.head_depth = gi.channel_depth   # one rim height round the head and channel
        totals.append(gi.capacity_table()["total_gal"])
    t = np.array(totals)
    pub = g.published.get("capacity_gal")
    return {"p5": float(np.percentile(t, 5)), "p50": float(np.percentile(t, 50)), "p95": float(np.percentile(t, 95)),
            "share_above_published": float((t >= pub).mean()) if pub else None}


def load(path: Path = GEOMETRY_FILE, **overrides) -> Geometry:
    raw = json.loads(Path(path).read_text())
    ch, hd, rs, pb, cy, ps = (raw["channel"], raw["head"], raw["reservoir"],
                              raw["piston_bank"], raw["cylinder"], raw["pistons"])
    g = Geometry(
        channel_length=_v(ch["length"]),
        channel_width=_v(ch["width"]),
        channel_depth=_v(ch["depth_to_rim"]),
        head_length=_v(hd["length_along_axis"]),
        head_width=_v(hd["width_across"]),
        head_depth=_v(hd["depth_to_rim"]),
        reservoir_length=_v(rs["length_along_axis"]),
        reservoir_width=_v(rs["width"]),
        reservoir_height=_v(rs["height_above_channel_floor"]),
        floor_above_lab=_v(raw["tank_floor_above_lab_floor"]),
        rows=pb["rows_across"],
        columns=pb["columns_along"],
        pitch_across=_v(pb["pitch_across"]),
        pitch_along=_v(pb["pitch_along"]),
        bank_near_x=_v(pb["near_end_x"]),
        pipe_top=_v(pb["pipe_top_height"]),
        row_bottoms=list(_v(pb["row_bottoms"])),
        bottom_offset=_v(pb["bottom_offset"]),
        row_10_end=_v(pb["row_10_end"]),
        duct_side_walls=bool(_v(pb["duct_side_walls"])),
        between_pipes_open=bool(_v(pb["between_pipes_open"])),
        cylinder_od=_v(cy["outside_diameter"]),
        cylinder_bore=_v(cy["bore"]),
        disc_zero=_v(ps["disc_face_at_0mm"]),
        park_mm=_v(ps["park_mm"]),
        stroke_min_mm=ps["position_mm"]["min"],
        stroke_max_mm=ps["position_mm"]["max"],
        speed_max=ps["speed_max_mm_s"],
        accel_max=ps["accel_max_mm_s2"],
        bestehorn_jerk_factor=_v(ps["bestehorn_jerk_factor"]),
        published=raw.get("published", {}),
        raw=raw,
    )
    for k, val in overrides.items():
        if not hasattr(g, k):
            raise KeyError("unknown geometry field: " + k)
        setattr(g, k, val)
    if g.row_10_end not in ("channel", "head"):
        raise ValueError("row_10_end must be 'channel' or 'head', not %r" % g.row_10_end)
    return g


def parse_pistons(spec: str, g: Geometry = None) -> List[int]:
    """Display numbers from a selection.

    'all'; '1-30' or '1,4,7' (display numbers); 'row8-10' or 'c8-10' (William's
    rows 8 to 10, i.e. pistons 22-30); 'lane2' or 'r2' (the middle piston of
    every row, across the tank).
    """
    g = g or load()
    n_all = g.rows * g.columns
    spec = spec.strip().lower().replace(" ", "")
    if spec in ("all", "*"):
        return list(range(1, n_all + 1))
    out = []
    for part in spec.split(","):
        if not part:
            continue
        if part.startswith("row"):
            kind, body = "c", part[3:]
        elif part.startswith("lane"):
            kind, body = "r", part[4:]
        elif part[0] in "cr":
            kind, body = part[0], part[1:]
        else:
            kind, body = "p", part
        if "-" in body:
            a, b = body.split("-")
            ids = list(range(int(a), int(b) + 1))
        else:
            ids = [int(body)]
        for i in ids:
            if kind == "c":
                out += [(i - 1) * g.rows + r for r in range(1, g.rows + 1)]
            elif kind == "r":
                out += [(c - 1) * g.rows + i for c in range(1, g.columns + 1)]
            else:
                out.append(i)
    bad = [p for p in out if not 1 <= p <= n_all]
    if bad:
        raise ValueError("piston numbers out of range: %s" % bad)
    return sorted(set(out))
