"""Dimensioned plan and side drawings of the tank and the piston bank, from geometry.json."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .geometry import Geometry

INK = "#0b0b0b"
INK_2 = "#52514e"
FAINT = "#c3c2b7"
WATER = "#cde2fb"
WATER_EDGE = "#2a78d6"
STEEL = "#d9d8d2"
WET = "#2a78d6"
DRY = "#eb6834"
RES = "#86b6ef"
PAPER = "#fcfcfb"


def _setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "figure.facecolor": PAPER, "axes.facecolor": PAPER,
                         "savefig.facecolor": PAPER, "text.color": INK})
    return plt


def _dim(ax, p, q, text, off=(0.0, 0.0), side=1, color=INK_2, fs=7.5, ext=True, rot=None):
    """A dimension line from p to q, drawn `off` away from them, with extension lines and a label."""
    (x1, y1), (x2, y2) = p, q
    ox, oy = off
    a, b = (x1 + ox, y1 + oy), (x2 + ox, y2 + oy)
    if ext and (ox or oy):
        ax.plot([x1, a[0]], [y1, a[1]], color=FAINT, lw=0.6)
        ax.plot([x2, b[0]], [y2, b[1]], color=FAINT, lw=0.6)
    ax.annotate("", xy=a, xytext=b, arrowprops=dict(arrowstyle="<|-|>", color=color, lw=0.7,
                                                    shrinkA=0, shrinkB=0, mutation_scale=7))
    if not text:
        return
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    vertical = abs(x2 - x1) < abs(y2 - y1)
    if rot is None:
        rot = 90 if vertical else 0
    ax.text(mx, my, text, ha="center", va="center", rotation=rot, fontsize=fs, color=INK,
            bbox=dict(boxstyle="square,pad=0.15", fc=PAPER, ec="none"))


def _rect(ax, x0, y0, w, h, **kw):
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((x0, y0), w, h, **kw))


def _mm(v):
    return "%.0f" % (1000 * v)


def plan(g: Geometry, depth: float, out: Path) -> List[Path]:
    """Plan of the whole T, and an enlarged plan of the head and the bank."""
    plt = _setup()
    from matplotlib.patches import Circle
    L, b = g.channel_length, g.channel_width
    HL, HW = g.head_length, g.head_width
    RL, RW = g.reservoir_length, g.reservoir_width
    wet = set(g.wet_columns(depth))
    paths = []

    # --- whole tank -----------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(16, 4.6))
    _rect(ax, -HL, -HW / 2, HL, HW, fc=WATER, ec=INK, lw=1.2)
    _rect(ax, 0, -b / 2, L, b, fc=WATER, ec=INK, lw=1.2)
    ax.plot([0, 0], [-b / 2, b / 2], color=WATER, lw=1.6)           # the mouth is open
    _rect(ax, L, -RW / 2, RL, RW, fc=RES, ec=INK, lw=1.2)
    ax.plot([L, L], [-RW / 2, RW / 2], color=INK, lw=2.4)            # gate
    x0, x1 = g.bank_x_range
    _rect(ax, x0, -g.bank_width / 2, g.bank_length, g.bank_width, fc=STEEL, ec=INK_2, lw=0.8)
    ax.text(-HL / 2, HW / 2 - 0.35, "HEAD", ha="center", fontsize=9, weight="bold")
    ax.text(L / 2, b / 2 + 0.25, "CHANNEL", ha="center", fontsize=9, weight="bold")
    ax.text(L + RL / 2, RW / 2 + 0.25, "RESERVOIR (dam-break column)", ha="center", fontsize=9, weight="bold")
    ax.text(L + 0.1, -RW / 2 - 0.35, "gate", fontsize=7.5, color=INK_2)
    ax.text((x0 + x1) / 2, -g.bank_width / 2 - 0.3, "piston bank", ha="center", fontsize=7.5, color=INK_2)
    _dim(ax, (-HL, -HW / 2), (0, -HW / 2), "head %.2f m" % HL, off=(0, -0.45))
    _dim(ax, (-HL, -HW / 2), (-HL, HW / 2), "head %.2f m" % HW, off=(-0.45, 0))
    _dim(ax, (0, -b / 2), (L, -b / 2), "channel %.2f m (photogrammetry; tape it)" % L, off=(0, -1.0))
    _dim(ax, (L, -RW / 2), (L + RL, -RW / 2), "%.1f m" % RL, off=(0, -1.0))
    _dim(ax, (-HL, HW / 2), (L + RL, HW / 2), "overall %.1f m (published 118-120 ft = 36 m incl. platforms)"
         % (HL + L + RL), off=(0, 0.45))
    _dim(ax, (L * 0.55, -b / 2), (L * 0.55, b / 2), "", off=(0, 0), ext=False)
    ax.text(L * 0.55 + 0.1, 0, "width %s mm (tape)" % _mm(b), va="center", fontsize=7.5, color=INK_2)
    ax.set_aspect("equal")
    ax.set_xlim(-HL - 1.2, L + RL + 0.6)
    ax.set_ylim(-HW / 2 - 1.3, HW / 2 + 1.0)
    ax.axis("off")
    ax.set_title("Plan, whole tank (metres). Still water %.2f m: rows %s of the bank are in the water. x = 0 at the channel mouth."
                 % (depth, _span(sorted(wet))), loc="left", fontsize=9)
    fig.tight_layout()
    paths += _save(fig, out / "plan_whole")

    # --- head and bank, enlarged ---------------------------------------------------
    fig, ax = plt.subplots(figsize=(11, 8.6))
    _rect(ax, -HL, -HW / 2, HL, HW, fc=WATER, ec=INK, lw=1.2)
    _rect(ax, 0, -b / 2, 1.6, b, fc=WATER, ec=INK, lw=1.2)
    ax.plot([0, 0], [-b / 2, b / 2], color=WATER, lw=2)
    ax.text(1.65, 0, "to the channel ->", va="center", fontsize=8, color=INK_2)
    if g.duct_side_walls:
        for s in (-1, 1):
            ax.plot([x0, x1], [s * g.bank_width / 2] * 2, color=INK, lw=2.2)
    for p in g.pistons():
        c = WET if p.column in wet else DRY
        ax.add_patch(Circle((p.x, p.y), g.cylinder_od / 2, fc="white", ec=c, lw=1.4))
        ax.add_patch(Circle((p.x, p.y), g.cylinder_bore / 2, fc="none", ec=c, lw=0.5))
        ax.text(p.x, p.y, str(p.number), ha="center", va="center", fontsize=6.5, color=INK)
    for c in range(1, g.columns + 1):
        xc = g.column_x(c)
        ax.text(xc, g.bank_width / 2 + 0.12, "row %d" % c, ha="center", va="bottom", fontsize=7, rotation=90)
        ax.text(xc, -g.bank_width / 2 - 0.12, "%s" % _mm(g.row_bottom(c)), ha="center", va="top", fontsize=6.5,
                rotation=90, color=WET if c in wet else DRY)
    ax.text(x0 - 0.05, -g.bank_width / 2 - 0.55, "pipe bottom above floor, mm", ha="right", fontsize=7, color=INK_2)
    _dim(ax, (x0, g.bank_width / 2), (x1, g.bank_width / 2), "bank %.3f m = 10 x %s mm pitch (pitch along assumed)"
         % (g.bank_length, _mm(g.pitch_along)), off=(0, 0.75))
    _dim(ax, (x0, -g.bank_width / 2), (x0, g.bank_width / 2), "%s" % _mm(g.bank_width), off=(-0.25, 0))
    _dim(ax, (-HL, -HW / 2), (0, -HW / 2), "head %.2f m (estimate, 3.0-5.2)" % HL, off=(0, -0.3))
    _dim(ax, (-HL, -HW / 2), (-HL, HW / 2), "head %.2f m" % HW, off=(-0.3, 0))
    _dim(ax, (x0, -HW / 2), (-HL, -HW / 2), "%.2f m open water behind the bank" % (x0 + HL), off=(0, 0.35))
    ax.text(-HL + 0.15, HW / 2 - 0.25,
            "Pipes: 8 in NPS Sch 10S, OD %s mm, bore %s mm (%.1f cm2 disc face).\n"
            "Blue: bottom under the %.2f m still water, the row moves water.\n"
            "Orange: bottom above it, the row moves only air.\n"
            "Row 10 (shortest pipes) drawn at the %s end - ASSUMED, ask William.\n"
            "Row n = display pistons 3n-2..3n."
            % (_mm(g.cylinder_od), "%.1f" % (1000 * g.cylinder_bore), g.bore_area * 1e4, depth, g.row_10_end),
            va="top", fontsize=7.5, color=INK)
    ax.set_aspect("equal")
    ax.set_xlim(-HL - 0.6, 2.4)
    ax.set_ylim(-HW / 2 - 0.6, HW / 2 + 0.3)
    ax.axis("off")
    ax.set_title("Plan, head and piston bank (metres, pipe bottoms in mm)", loc="left", fontsize=9)
    fig.tight_layout()
    paths += _save(fig, out / "plan_head_bank")
    plt.close("all")
    return paths


def side(g: Geometry, depth: float, out: Path) -> List[Path]:
    """Section along the centreline: the whole tank, and the bank enlarged with its trapped air."""
    plt = _setup()
    L, HL, RL = g.channel_length, g.head_length, g.reservoir_length
    rim = g.channel_depth
    wet = set(g.wet_columns(depth))
    paths = []

    # --- whole tank ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(16, 4.4))
    _rect(ax, -HL, 0, HL + L, depth, fc=WATER, ec="none")
    ax.plot([-HL, L], [depth, depth], color=WATER_EDGE, lw=0.9)
    ax.plot([-HL, -HL, L], [rim, 0, 0], color=INK, lw=1.2)
    ax.plot([-HL, L], [rim, rim], color=INK_2, lw=0.6, ls=(0, (6, 3)))
    _rect(ax, L, 0, RL, g.reservoir_height, fc=RES, ec=INK, lw=1.2)
    ax.plot([L, L], [0, g.reservoir_height], color=INK, lw=2.4)
    for p in g.pistons():
        if p.row != 2:
            continue
        c = WET if p.column in wet else DRY
        _rect(ax, p.x - g.cylinder_od / 2, p.bottom, g.cylinder_od, g.pipe_top - p.bottom, fc=STEEL, ec=c, lw=0.6)
    ax.plot([-HL, L + RL], [-g.floor_above_lab] * 2, color=INK_2, lw=0.8)
    ax.text(L + RL, -g.floor_above_lab - 0.12, "lab floor", ha="right", va="top", fontsize=7, color=INK_2)
    _dim(ax, (L + RL, 0), (L + RL, g.reservoir_height), "%.1f m" % g.reservoir_height, off=(0.4, 0))
    _dim(ax, (-HL, 0), (-HL, rim), "rim %.2f m" % rim, off=(-0.4, 0))
    _dim(ax, (-HL, -g.floor_above_lab), (-HL, 0), "", off=(-0.9, 0))
    ax.text(-HL - 1.0, -g.floor_above_lab / 2, "%.2f m" % g.floor_above_lab, ha="right", va="center", fontsize=7.5)
    _dim(ax, (L * 0.3, 0), (L * 0.3, depth), "", off=(0, 0), ext=False)
    ax.text(L * 0.3 + 0.15, depth + 0.08, "still water %.2f m (an input: set it per run)" % depth, fontsize=7.5,
            color=WATER_EDGE, va="bottom")
    _dim(ax, (-HL, 0), (0, 0), "head %.2f m" % HL, off=(0, -0.3))
    _dim(ax, (0, 0), (L, 0), "channel %.2f m" % L, off=(0, -0.3))
    _dim(ax, (L, 0), (L + RL, 0), "reservoir %.1f m" % RL, off=(0, -0.3))
    ax.text(-HL / 2, rim + 0.6, "piston bank (centre lane):\npipes stand above the rim", ha="center", fontsize=7.5,
            color=INK_2)
    ax.set_aspect("equal")
    ax.set_xlim(-HL - 1.8, L + RL + 1.2)
    ax.set_ylim(-g.floor_above_lab - 0.5, g.reservoir_height + 0.3)
    ax.axis("off")
    ax.set_title("Side section on the centreline (metres). Rim height from photogrammetry: tape it.", loc="left",
                 fontsize=9)
    fig.tight_layout()
    paths += _save(fig, out / "side_whole")

    # --- bank enlarged ---------------------------------------------------------------
    from . import air
    fig, ax = plt.subplots(figsize=(12, 7.0))
    x0, x1 = g.bank_x_range
    xa, xb = x0 - 0.5, x1 + 0.7
    _rect(ax, xa, 0, xb - xa, depth, fc=WATER, ec="none")
    ax.plot([xa, xb], [depth, depth], color=WATER_EDGE, lw=0.9)
    ax.plot([xa, xb], [0, 0], color=INK, lw=1.4)
    ax.plot([xa, xb], [rim, rim], color=INK_2, lw=0.6, ls=(0, (6, 3)))
    ax.text(xb, rim + 0.01, "rim %.2f m (est.)" % rim, ha="right", va="bottom", fontsize=7, color=INK_2)
    ax.text(xb, depth + 0.01, "still water %.0f mm" % (1000 * depth), ha="right", va="bottom", fontsize=7,
            color=WATER_EDGE)
    ax.plot([x1, x1], [0, rim], color=INK_2, lw=0.6)
    ax.text(x1 + 0.03, rim * 0.55, "channel mouth\nx = 0", fontsize=7, color=INK_2)
    zd_park = g.disc_height(g.park_mm)
    for c in range(1, g.columns + 1):
        xc = g.column_x(c)
        zb = g.row_bottom(c)
        r_o, r_i = g.cylinder_od / 2, g.cylinder_bore / 2
        col = WET if c in wet else DRY
        _rect(ax, xc - r_o, zb, 2 * r_o, g.pipe_top - zb, fc="white", ec=col, lw=1.0)
        st = air.rest_state(zd_park, zd_park, zb, depth)
        if st["inner_level_m"] is not None and st["water_column_m"] > 0:
            _rect(ax, xc - r_i, zb, 2 * r_i, st["water_column_m"], fc=WATER, ec="none")
        # disc at the park position and the travel range
        z0, z370 = g.disc_height(g.stroke_min_mm), g.disc_height(g.stroke_max_mm)
        _rect(ax, xc - r_i, z370 - 0.008, 2 * r_i, 0.016, fc=INK, ec="none")
        ax.plot([xc, xc], [z370, z0], color=INK_2, lw=0.5, ls=(0, (2, 2)))
        ax.plot([xc - r_i, xc + r_i], [z0, z0], color=INK_2, lw=0.6)
        ax.text(xc, zb - 0.03, _mm(zb), ha="center", va="top", fontsize=7, color=col)
        ax.text(xc, g.pipe_top + 0.03, "row %d\n%d-%d" % (c, 3 * c - 2, 3 * c), ha="center", va="bottom", fontsize=6.8)
        if c in wet:
            ax.text(xc, (zb + z370) / 2, "air\n%.0f" % (1000 * st["air_length_m"]), ha="center", va="center",
                    fontsize=6.3, color=INK_2)
    xl = min(g.column_x(1), g.column_x(g.columns)) - g.pitch_along / 2
    _dim(ax, (xa + 0.1, 0), (xa + 0.1, g.pipe_top), "pipe tops %s mm (56 in)" % _mm(g.pipe_top), off=(0, 0), ext=False)
    c_lo = min(range(1, g.columns + 1), key=g.row_bottom)
    _dim(ax, (g.column_x(c_lo) + 0.13, 0), (g.column_x(c_lo) + 0.13, g.row_bottom(c_lo)), "5 in", off=(0, 0),
         ext=False, rot=0, fs=6.5)
    ax.text(xl, -0.13,
            "Pipe bottoms (mm above the floor) step 3 in per row: 5 + 3(n - 1) in; pipe length 54 - 3n in.\n"
            "Dark bar: disc parked at %.0f mm; dotted: its travel up to 0 mm (top tick). "
            "Disc height at 0 mm = %s mm, ASSUMED: measure it.\n"
            "Blue inside a pipe: water under the trapped air with the disc parked; 'air' = air column length, mm.\n"
            "Row 10 drawn at the %s end (ASSUMED)."
            % (g.park_mm, _mm(g.disc_zero), g.row_10_end), fontsize=7, color=INK_2, va="top")
    ax.set_aspect("equal")
    ax.set_xlim(xa - 0.25, xb + 0.05)
    ax.set_ylim(-0.45, g.pipe_top + 0.2)
    ax.axis("off")
    ax.set_title("Piston bank, section on the centre lane (metres; labels mm). Still water %.0f mm: rows %s in the water."
                 % (1000 * depth, _span(sorted(wet))), loc="left", fontsize=9)
    fig.tight_layout()
    paths += _save(fig, out / "side_bank")
    plt.close("all")
    return paths


def _span(cols):
    if not cols:
        return "none"
    return "%d-%d" % (cols[0], cols[-1]) if len(cols) > 1 else str(cols[0])


def _save(fig, stem: Path) -> List[Path]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for ext in ("png", "pdf"):
        p = stem.with_suffix("." + ext)
        fig.savefig(p, dpi=170 if ext == "png" else None)
        out.append(p)
    return out


def make_all(g: Geometry, depth: float, out: Path) -> List[Path]:
    return plan(g, depth, out) + side(g, depth, out)
