#!/usr/bin/env python3
"""Wave tank model: volumes, predictions, sweep tables and charts, drawings,
3D model, calibration.

  .venv/bin/python wm.py volumes --depth 0.30
  .venv/bin/python wm.py predict --pistons all --stroke 300 --speed 500 --depth 0.30 --plot
  .venv/bin/python wm.py sweep --depth 0.30,0.40,0.50,0.60
  .venv/bin/python wm.py chart
  .venv/bin/python wm.py drawings
  .venv/bin/python wm.py model3d --depth 0.30
  .venv/bin/python wm.py template            # observations_template.csv to fill in
  .venv/bin/python wm.py calibrate observations.csv [--compare]

Pistons: 'all', display numbers '1-30' or '1,4,7', William's rows 'row8-10'
(also 'c8-10'), lanes across the tank 'lane2' (also 'r2').
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from wavemodel import calibrate, motion, predict, tank  # noqa: E402
from wavemodel.geometry import GALLONS_PER_M3, capacity_band, load, parse_pistons  # noqa: E402

RESULTS = HERE / "results"


def _geom(a):
    over = {}
    if getattr(a, "row10_end", None):
        over["row_10_end"] = a.row10_end
    if getattr(a, "disc_zero", None) is not None:
        over["disc_zero"] = a.disc_zero
    return load(**over)


def cmd_volumes(a):
    g = _geom(a)
    cap = g.capacity_table()
    print("Tank volumes (geometry.json)")
    print("  head + channel to the rim   %6.1f m3  %7.0f gal" % (cap["head_and_channel_m3"], cap["head_and_channel_m3"] * GALLONS_PER_M3))
    print("  inside the 30 pipes         %6.2f m3  %7.0f gal   (water below the trapped air, discs parked)"
          % (cap["cylinders_m3"], cap["cylinders_m3"] * GALLONS_PER_M3))
    print("  reservoir to the top        %6.1f m3  %7.0f gal" % (cap["reservoir_m3"], cap["reservoir_m3"] * GALLONS_PER_M3))
    print("  everything to the brim      %6.1f m3  %7.0f gal   (published: %s gal, ratio %.2f)"
          % (cap["total_m3"], cap["total_gal"], cap["published_gal"], cap["ratio_to_published"]))
    band = capacity_band(4000)
    print("  with every dimension drawn across its range in geometry.json: 5%%-95%% %.0f-%.0f gal, median %.0f;"
          " %.0f%% of draws reach 13,500" % (band["p5"], band["p95"], band["p50"], 100 * band["share_above_published"]))
    print()
    print("Head + channel at a still-water depth (the reservoir gate shut):")
    print("  depth   volume            per cm of depth   rows in the water")
    for d in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.00):
        v = g.volume_main(d)
        dv = (g.volume_main(d + 0.005) - g.volume_main(d - 0.005))
        wet = g.wet_columns(d)
        print("  %.2f m  %5.2f m3 %6.0f gal   %4.0f L  %3.0f gal   %s"
              % (d, v, v * GALLONS_PER_M3, dv * 1000, dv * GALLONS_PER_M3,
                 ("rows %d-%d" % (wet[0], wet[-1]) if len(wet) > 1 else "row %d" % wet[0]) if wet else "none"))
    print()
    print("The bank at %.3f m still water, discs parked at %.0f mm (row 10 at the %s end):" % (a.depth, g.park_mm, g.row_10_end))
    print("  row  pistons  pipe   bottom  under water   air below disc   water in pipe   air gauge   water follows disc")
    for r in g.air_table(a.depth):
        print("  %3d  %-7s  %4.0f mm  %4.0f mm  %6s       %4.0f mm          %4.0f mm         %5.2f kPa    %s"
              % (r["row"], r["pistons"], 1000 * r["pipe_length_m"], 1000 * r["bottom_m"],
                 ("%4.0f mm" % (1000 * r["submergence_m"])) if r["wet"] else "dry",
                 1000 * r["air_length_m"], 1000 * r["water_column_m"], r["air_gauge_kpa"],
                 ("%.1f%%" % (100 * r["follow"])) if r["wet"] else "-"))
    print()
    print("One piston: %.1f cm2 bore, %.3f L per mm of travel, %.1f L for the full 390 mm."
          % (g.bore_area * 1e4, g.bore_area * 1e-3 * 1e3, g.bore_area * 0.39 * 1e3))
    wet = g.wet_columns(a.depth)
    nw = 3 * len(wet)
    print("At %.2f m, %d wet pistons x 390 mm = %.0f L = %.1f mm on the head + channel surface."
          % (a.depth, nw, nw * g.bore_area * 0.39 * 1e3, nw * g.bore_area * 0.39 / g.plan_area_main(a.depth) * 1e3))


def _run_from_args(a, g):
    ids = parse_pistons(a.pistons, g)
    if a.position1 is not None:
        p1, p2 = a.position1, a.position2
    else:
        p1, p2 = a.bottom - a.stroke, a.bottom
    prm = motion.Params(p1, p2, a.speed, a.speed, a.accel, a.accel, a.accel, a.accel, a.jerk, a.jerk,
                        a.dwell, a.dwell, a.profile)
    delays = {n: ((n - 1) // g.rows) * a.column_delay for n in ids} if a.column_delay else {}
    return predict.Run(ids, prm, a.depth, cycles=a.cycles, delays=delays)


def _hp(a, seal=None):
    return tank.HydroParams(end=a.end, head_walls=a.head_walls, gain=a.gain,
                            head_absorption=a.head_absorption, channel_damping=a.channel_damping,
                            seal=seal or a.seal)


def print_rows(res):
    print("  row  pistons   bottom  under water  disc stroke  water stroke first / steady  air")
    for r in res["rows"]:
        if not r["wet"]:
            print("  %3d  %-8s  %4.0f mm  dry          -            -                            moves only air"
                  % (r["row"], "%d-%d" % (r["pistons"][0], r["pistons"][-1]), r["bottom_mm"]))
            continue
        note = "vents at the bottom" if r["vented"] else "stays in the pipe"
        if r["leaked"]:
            note += ", drawn in past the seal"
        print("  %3d  %-8s  %4.0f mm  %4.0f mm      %4.0f mm      %5.0f / %4.0f mm               %s"
              % (r["row"], "%d-%d" % (r["pistons"][0], r["pistons"][-1]), r["bottom_mm"], r["submergence_mm"],
                 r["disc_down_mm"], r["first_down_mm"], r["last_down_mm"], note))


def cmd_predict(a):
    g = _geom(a)
    if a.preset:
        run = predict.from_preset(a.preset, a.pistons, a.depth, a.cycles, g)
    else:
        run = _run_from_args(a, g)
    res = predict.predict(run, a.stations, g, _hp(a), verbose=True)
    print("pistons %s  stroke %.0f mm (%.0f -> %.0f)  speed %.0f (reached %.0f) mm/s  depth %.3f m  seal %s  row 10 at the %s"
          % (a.pistons, run.params.stroke, run.params.position1, run.params.position2, run.params.speed2,
             res["peak_piston_speed_mm_s"], run.depth, res["seal"], g.row_10_end))
    print("cycle %.3f s   water moved per stroke %.1f L (discs sweep %.1f L)   peak flux %.1f L/s   grid %.3f m, %s cells"
          % (res["cycle_time_s"], 1000 * res["volume_per_stroke_m3"], 1000 * res["volume_disc_m3"],
             1000 * res["peak_flux_m3s"], res["dx"], res["cells"]))
    print_rows(res)
    for w in res["warnings"]:
        print("  ! " + w)
    print("  x(m)  arrive  gate-back  incident H  inc.crest  first H   max crest  min trough  H1/3   T(s)  L(m)  Ursell")
    for m in res["metrics"]:
        print("  %4.1f  %6.2f  %9.2f  %10.1f  %9.1f  %7.1f  %9.1f  %10.1f  %5.1f  %5.2f  %4.1f  %6.1f"
              % (m["x_m"], m["arrival_s"], m["gate_reflection_s"], m["incident_height_mm"], m["incident_crest_mm"],
                 m["first_wave_height_mm"], m["max_crest_mm"], m["min_trough_mm"], m["H_one_third_mm"],
                 m["period_s"], m["wavelength_m"], m["ursell"]))
        for fl in m["flags"]:
            print("        - " + fl)
    if a.plot:
        _plot(res, run, a)


def _plot(res, run, a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    RESULTS.mkdir(exist_ok=True)
    t = res["t"]
    show = t < max(40.0, res["motion_end_s"] + 25)
    fig, axes = plt.subplots(len(res["stations"]) + 1, 1, figsize=(10, 1.6 * (len(res["stations"]) + 1)), sharex=True)
    axes[0].plot(t[show], 1000 * res["net_flux"][show], color="#52514e", lw=1.2)
    axes[0].set_ylabel("flux\nL/s")
    for ax, x, e in zip(axes[1:], res["stations"], res["eta"]):
        ax.plot(t[show], 1000 * e[show], color="#2a78d6", lw=1.4)
        ax.axhline(0, color="#c3c2b7", lw=0.8)
        ax.set_ylabel("x=%.0f m\nmm" % x)
    axes[-1].set_xlabel("time (s)")
    fig.suptitle("pistons %s, %.0f mm at %.0f mm/s, depth %.2f m, %s seal"
                 % (a.pistons, run.params.stroke, run.params.speed2, run.depth, res["seal"]))
    fig.tight_layout()
    tag = Path(a.preset).stem.replace(" ", "_") if a.preset else "%dmm_%dmms" % (run.params.stroke, run.params.speed2)
    name = RESULTS / ("predict_%s_%s_%dcm_%s.png" % (a.pistons.replace(",", "_"), tag, round(run.depth * 100), res["seal"]))
    fig.savefig(name, dpi=130)
    print("plot:", name)


# William's rows (groups of three along the tank). Order sets each one's colour in the chart.
SETS = {
    "all 30": "all",
    "rows 1-3 (pistons 1-9)": "row1-3",
    "rows 4-7 (pistons 10-21)": "row4-7",
    "rows 8-10 (pistons 22-30)": "row8-10",
    "row 1 only (pistons 1-3)": "row1",
    "middle lane (2, 5 ... 29)": "lane2",
}


def cmd_sweep(a):
    depths = [float(d) for d in str(a.depth).split(",")]
    for d in depths:
        _sweep_one(a, d)


def _sweep_one(a, depth):
    g = _geom(a)
    seals = ["tight", "leaky"] if a.seal == "both" else [a.seal]
    strokes = [int(s) for s in a.strokes.split(",")]
    speeds = [int(s) for s in a.speeds.split(",")]
    sets = SETS if a.sets == "default" else dict((s, s) for s in a.sets.split(";"))
    RESULTS.mkdir(exist_ok=True)
    suffix = "" if g.row_10_end == "channel" else "_row10-%s" % g.row_10_end
    out = RESULTS / ("sweep_%dcm%s.csv" % (round(depth * 100), suffix))
    rows = []
    n = len(strokes) * len(speeds) * len(sets) * len(seals)
    i = 0
    print("sweep at %.2f m, row 10 at the %s end, wet rows %s" % (depth, g.row_10_end, g.wet_columns(depth)))
    for seal, (label, spec), stroke, speed in itertools.product(seals, sets.items(), strokes, speeds):
        i += 1
        ids = parse_pistons(spec, g)
        prm = motion.Params.simple(stroke, speed, a.accel, a.jerk, a.profile)
        run = predict.Run(ids, prm, depth, cycles=a.cycles)
        res = predict.predict(run, a.stations, g, _hp(a, seal), verbose=(i == 1))
        wet = [r for r in res["rows"] if r["wet"]]
        for m in res["metrics"]:
            rows.append({
                "pistons": label, "spec": spec, "n_pistons": len(ids), "n_wet": 3 * len(wet),
                "wet_rows": " ".join(str(r["row"]) for r in wet), "seal": seal, "row10_end": g.row_10_end,
                "stroke_mm": stroke,
                "water_stroke_mm": round(sum(r["last_down_mm"] for r in wet) / len(wet), 1) if wet else 0.0,
                "litres_per_stroke": round(1000 * res["volume_per_stroke_m3"], 1),
                "speed_cmd_mm_s": speed, "speed_reached_mm_s": round(res["peak_piston_speed_mm_s"]),
                "accel": a.accel, "jerk": a.jerk, "profile": motion.PROFILE_NAMES[a.profile],
                "depth_m": depth, "cycle_s": round(res["cycle_time_s"], 3), "x_m": m["x_m"],
                "arrival_s": round(m["arrival_s"], 2),
                "incident_H_mm": round(m["incident_height_mm"], 1), "incident_crest_mm": round(m["incident_crest_mm"], 1),
                "first_H_mm": round(m["first_wave_height_mm"], 1), "first_crest_mm": round(m["first_crest_mm"], 1),
                "max_crest_mm": round(m["max_crest_mm"], 1), "H13_mm": round(m["H_one_third_mm"], 1),
                "period_s": round(m["period_s"], 2), "wavelength_m": round(m["wavelength_m"], 2),
                "ursell": round(m["ursell"], 1), "flags": "; ".join(m["flags"] + res["warnings"]),
            })
        if i % 20 == 0 or i == n:
            print("  [%d/%d] %-5s %-28s %3d mm %3d mm/s -> H %.1f mm at x=%.0f m" %
                  (i, n, seal, label, stroke, speed, rows[-len(res["metrics"])]["incident_H_mm"], a.stations[0]),
                  flush=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote", out, "(%d rows)" % len(rows))


def cmd_chart(a):
    from wavemodel import charts
    for p in charts.wave_height_charts(RESULTS, station=a.station, speed=a.speed):
        print("chart:", p)


def cmd_drawings(a):
    from wavemodel import drawings
    for p in drawings.make_all(_geom(a), a.depth, HERE / "drawings"):
        print("drawing:", p)


def cmd_model3d(a):
    from wavemodel import model3d
    for p in model3d.export(_geom(a), a.depth, HERE / "model", disc_mm=a.disc_mm):
        print("3D:", p)


def cmd_template(a):
    path = HERE / "observations_template.csv"
    calibrate.write_template(path)
    print("wrote", path)


def cmd_calibrate(a):
    params = tuple(a.fit.split(","))
    if a.compare:
        res = calibrate.compare(a.csv, params)
    else:
        res = calibrate.fit(a.csv, params, seal=a.seal if a.seal != "both" else "tight", row_10_end=a.row10_end)
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / "calibration.json"
    out.write_text(json.dumps(res, indent=2))
    best = res["best"] if a.compare else res
    print("  run          x(m)  depth  observed  predicted  ratio")
    for r in best["table"]:
        print("  %-12s %5s  %5s  %8.1f  %9.1f  %5.2f" % (r["run_id"], r["station_x_m"], r["depth_m"],
                                                       r["observed_mm"], r["predicted_mm"], r["ratio"]))
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def geom_args(p, depth_default=0.30):
        p.add_argument("--depth", default=depth_default, type=float, help="still-water depth, m")
        p.add_argument("--row10-end", choices=["channel", "head"],
                       help="which end of the bank row 10 (shortest pipes) is at; default from geometry.json")
        p.add_argument("--disc-zero", type=float, help="disc face height above the floor at 0 mm, m")

    p = sub.add_parser("volumes")
    geom_args(p)
    p.set_defaults(fn=cmd_volumes)

    def run_args(p, accel=10000, jerk=2000):
        p.add_argument("--pistons", default="all", help="all | 1-30 | row8-10 | lane2 | 1,4,7")
        p.add_argument("--accel", type=float, default=accel)
        p.add_argument("--jerk", type=float, default=jerk)
        p.add_argument("--profile", type=int, default=1, help="0 trapezoid, 1 Bestehorn, 2 S-curve, 3 sine")
        p.add_argument("--cycles", type=float, default=5)
        p.add_argument("--stations", type=lambda s: [float(v) for v in s.split(",")],
                       default=predict.DEFAULT_STATIONS, help="x along the channel from its mouth, m")
        p.add_argument("--end", default="gate", choices=["gate", "absorbing"])
        p.add_argument("--head-walls", default="reflect", choices=["reflect", "absorb"])
        p.add_argument("--gain", type=float, default=1.0)
        p.add_argument("--head-absorption", type=float, default=0.01)
        p.add_argument("--channel-damping", type=float, default=1.0)
        p.add_argument("--row10-end", choices=["channel", "head"],
                       help="which end of the bank row 10 (shortest pipes) is at; default from geometry.json")
        p.add_argument("--disc-zero", type=float, help="disc face height above the floor at 0 mm, m")

    p = sub.add_parser("predict")
    run_args(p)
    p.add_argument("--depth", type=float, default=0.30, help="still-water depth, m")
    p.add_argument("--seal", default="tight", choices=["tight", "leaky"],
                   help="tight: no air passes the disc; leaky: air leaks in under vacuum (stroke capped at submergence)")
    p.add_argument("--stroke", type=float, default=200)
    p.add_argument("--bottom", type=float, default=370, help="Position 2 (bottom of the stroke), mm")
    p.add_argument("--position1", type=float)
    p.add_argument("--position2", type=float)
    p.add_argument("--speed", type=float, default=500)
    p.add_argument("--dwell", type=float, default=0, help="Time 1 / Time 2, ms")
    p.add_argument("--column-delay", type=float, default=0.0, help="start delay per row of three, s")
    p.add_argument("--preset", help="a wavemaker-f25 preset CSV; overrides stroke/speed/accel/jerk/profile")
    p.add_argument("--plot", action="store_true")
    p.set_defaults(fn=cmd_predict)

    p = sub.add_parser("sweep")
    run_args(p, accel=20000, jerk=7500)
    p.add_argument("--depth", default="0.30,0.40,0.50,0.60", help="still-water depth(s), m, comma-separated")
    p.add_argument("--seal", default="both", choices=["tight", "leaky", "both"])
    p.add_argument("--strokes", default="50,100,150,200,250,300,350")
    p.add_argument("--speeds", default="100,200,300,500,700,900")
    p.add_argument("--sets", default="default", help="'default' or 'all;row8-10;lane2'")
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("chart")
    p.add_argument("--station", type=float, default=5.0, help="x along the channel, m")
    p.add_argument("--speed", type=int, default=900, help="commanded speed to plot, mm/s")
    p.set_defaults(fn=cmd_chart)

    p = sub.add_parser("drawings")
    geom_args(p)
    p.set_defaults(fn=cmd_drawings)

    p = sub.add_parser("model3d")
    geom_args(p)
    p.add_argument("--disc-mm", type=float, default=370, help="piston position to show the discs at, mm")
    p.set_defaults(fn=cmd_model3d)

    sub.add_parser("template").set_defaults(fn=cmd_template)
    p = sub.add_parser("calibrate")
    p.add_argument("csv")
    p.add_argument("--fit", default="gain", help="comma list from gain, channel_damping, bottom_offset, disc_zero, head_absorption")
    p.add_argument("--seal", default="tight", choices=["tight", "leaky", "both"])
    p.add_argument("--row10-end", choices=["channel", "head"])
    p.add_argument("--compare", action="store_true",
                   help="fit both disc seals and rank them by how well they explain the runs")
    p.set_defaults(fn=cmd_calibrate)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
