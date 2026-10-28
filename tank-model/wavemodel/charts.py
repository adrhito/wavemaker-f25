"""Charts of the sweep tables: predicted wave height against stroke."""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Dict, List

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3de"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
MARKERS = ["o", "s", "^", "D", "v", "P"]     # second channel for identity besides colour


def _read(paths: List[Path]) -> List[Dict]:
    rows = []
    for p in paths:
        with open(p, newline="") as fh:
            rows += list(csv.DictReader(fh))
    return rows


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return x


def _style(plt):
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
        "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 9,
        "axes.titlesize": 10, "axes.titleweight": "bold", "legend.frameon": False,
    })


def _height(r):
    """Leading-wave height, mm; a set with no pistons in the water makes no wave."""
    h = _num(r["first_H_mm"])
    if r["n_wet"] == "0":
        return 0.0
    return h


def wave_height_charts(results: Path, station: float = 5.0, speed: int = 900) -> List[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    _style(plt)
    out = []
    main = sorted(p for p in results.glob("sweep_*cm.csv") if re.fullmatch(r"sweep_\d+cm\.csv", p.name))
    if not main:
        raise SystemExit("no sweep tables in %s; run `wm.py sweep` first" % results)
    rows = [r for r in _read(main) if abs(_num(r["x_m"]) - station) < 1e-6 and int(r["speed_cmd_mm_s"]) == speed]
    depths = sorted({_num(r["depth_m"]) for r in rows})
    sets = list(dict.fromkeys(r["pistons"] for r in rows))
    reached = sorted({int(r["speed_reached_mm_s"]) for r in rows})
    accel, jerk = rows[0]["accel"], rows[0]["jerk"]
    breaking = any(_height(r) > 0.6 * 780 * _num(r["depth_m"]) for r in rows)

    fig, axes = plt.subplots(1, len(depths), figsize=(3.0 * len(depths) + 0.6, 4.6), sharey=True)
    axes = list(axes) if len(depths) > 1 else [axes]
    ymax = 0.0
    for ax, d in zip(axes, depths):
        dr = [r for r in rows if _num(r["depth_m"]) == d]
        wet = next((r["wet_rows"] for r in dr if r["pistons"] == "all 30"), "")
        wet_rows = [int(w) for w in wet.split()] if wet else []
        for si, s in enumerate(sets):
            for seal, ls in (("tight", "-"), ("leaky", (0, (4, 2.5)))):
                pts = sorted((_num(r["stroke_mm"]), _height(r)) for r in dr if r["pistons"] == s and r["seal"] == seal)
                pts = [(x, y) for x, y in pts if not math.isnan(y)]
                if not pts:
                    continue
                xs, ys = zip(*pts)
                ymax = max(ymax, max(ys))
                ax.plot(xs, ys, color=SERIES[si % len(SERIES)], lw=2, ls=ls, solid_capstyle="round",
                        marker=MARKERS[si % len(MARKERS)], ms=5 if seal == "tight" else 0,
                        markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
        if breaking:
            # depth-limited breaking height: real waves above it break and come out smaller
            ax.axhline(780 * d, color=INK_2, lw=0.8, zorder=2)
            ax.text(352, 780 * d, "breaks above ~0.78 h", ha="right", va="bottom", fontsize=7.5, color=INK_2)
        ax.set_title("%.2f m deep" % d, loc="left", pad=15)
        same = " (all 30 acts as rows 1-3)" if wet_rows == [1, 2, 3] else ""
        ax.text(0, 1.01, ("rows %d-%d in the water%s" % (wet_rows[0], wet_rows[-1], same) if len(wet_rows) > 1
                          else "row %d in the water" % wet_rows[0] if wet_rows else "no row in the water"),
                transform=ax.transAxes, fontsize=8, color=INK_2, va="bottom", ha="left")
        ax.set_xlabel("disc stroke (mm)")
        ax.set_xticks([50, 150, 250, 350])
    axes[0].set_ylabel("first wave, crest to trough, at x = %g m (mm)" % station)
    axes[0].set_ylim(0, ymax * 1.1)

    handles = [Line2D([], [], color=SERIES[i % len(SERIES)], lw=2, marker=MARKERS[i % len(MARKERS)], ms=5,
                      markeredgecolor=SURFACE, label=s) for i, s in enumerate(sets)]
    seal_h = [Line2D([], [], color=INK_2, lw=2, label="tight seal: no air gets past the disc"),
              Line2D([], [], color=INK_2, lw=2, ls=(0, (4, 2.5)),
                     label="leaky seal: air drawn in on the upstroke, stroke capped at the submergence")]
    fig.legend(handles=handles, loc="upper left", ncol=3, bbox_to_anchor=(0.01, 0.995), fontsize=8.5,
               columnspacing=1.6, handlelength=2.6)
    fig.legend(handles=seal_h, loc="upper right", ncol=1, bbox_to_anchor=(0.995, 0.995), fontsize=8.5,
               handlelength=2.6)
    fig.text(0.01, 0.005,
             "Linear model, row 1 (longest pipes) at the head end, row 10 (shortest) at the channel end. Lab fast settings: Bestehorn, %s mm/s², "
             "jerk %s mm/s³, commanded %d mm/s (the jerk limit holds the real peak to %d-%d mm/s).\n"
             "Pistons park at 370 mm and stroke up from there.%s Tables: results/sweep_*cm.csv."
             % (accel, jerk, speed, reached[0], reached[-1],
                "" if breaking else " Every height is well under the 0.78 h breaking limit."), fontsize=7.5, color=INK_2)
    fig.tight_layout(rect=(0, 0.05, 1, 0.86))
    p = results / ("wave_height_vs_stroke_%dmms.png" % speed)
    fig.savefig(p, dpi=150)
    plt.close(fig)
    out.append(p)

    # which end row 10 is at: the same chart for the two ends, tight seal
    alt = sorted(results.glob("sweep_*cm_row10-head.csv"))
    if alt:
        rows2 = [r for r in _read(main + alt) if abs(_num(r["x_m"]) - station) < 1e-6
                 and int(r["speed_cmd_mm_s"]) == speed and r["seal"] == "tight" and r["spec"] in ("all", "row1-3")]
        depths2 = sorted({_num(r["depth_m"]) for r in rows2 if r["row10_end"] == "head"})
        fig, axes = plt.subplots(1, len(depths2), figsize=(3.0 * len(depths2) + 0.6, 4.0), sharey=True)
        axes = list(axes) if len(depths2) > 1 else [axes]
        labels = {"all": "all 30", "row1-3": "rows 1-3 (pistons 1-9)"}
        ymax = 0.0
        for ax, d in zip(axes, depths2):
            for si, spec in enumerate(("all", "row1-3")):
                for end, ls in (("channel", "-"), ("head", (0, (4, 2.5)))):
                    pts = sorted((_num(r["stroke_mm"]), _height(r)) for r in rows2
                                 if _num(r["depth_m"]) == d and r["spec"] == spec and r["row10_end"] == end)
                    pts = [(x, y) for x, y in pts if not math.isnan(y)]
                    if not pts:
                        continue
                    xs, ys = zip(*pts)
                    ymax = max(ymax, max(ys))
                    ax.plot(xs, ys, color=SERIES[si], lw=2, ls=ls, marker=MARKERS[si], ms=5 if end == "channel" else 0,
                            markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
            ax.set_title("%.2f m deep" % d, loc="left")
            ax.set_xlabel("disc stroke (mm)")
            ax.set_xticks([50, 150, 250, 350])
        axes[0].set_ylabel("first wave, crest to trough, at x = %g m (mm)" % station)
        axes[0].set_ylim(0, ymax * 1.1)
        h1 = [Line2D([], [], color=SERIES[i], lw=2, marker=MARKERS[i], ms=5, markeredgecolor=SURFACE,
                     label=labels[s]) for i, s in enumerate(("all", "row1-3"))]
        h2 = [Line2D([], [], color=INK_2, lw=2, label="row 10 at the channel end, row 1 (longest) at the head end"),
              Line2D([], [], color=INK_2, lw=2, ls=(0, (4, 2.5)), label="row 1 (longest) at the channel end")]
        fig.legend(handles=h1 + h2, loc="upper left", ncol=4, bbox_to_anchor=(0.01, 0.995), fontsize=8.5,
                   handlelength=2.6)
        fig.text(0.01, 0.005, "Tight seal, commanded %d mm/s, same settings as the main chart. Which end the "
                 "longest pipes are at is not known yet; this is how much it matters." % speed, fontsize=7.5, color=INK_2)
        fig.tight_layout(rect=(0, 0.03, 1, 0.9))
        p = results / ("row10_end_effect_%dmms.png" % speed)
        fig.savefig(p, dpi=150)
        plt.close(fig)
        out.append(p)
    return out
