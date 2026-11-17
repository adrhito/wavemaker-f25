"""Write a test plan for filming which piston combinations make which waves.

The plan is a JSON list. Each item is one test with a unique ``name``, a
``kind`` (``single``, ``continuous`` or ``pulse_down``) and a ``family`` saying
which part of the coverage it belongs to. A balanced "core" comes first; the
rest is shuffled with ``--seed``.

Only display pistons 1-18 (rows 1-6) are used. Pistons 1-12 currently reach
the water, so they are weighted heavily. Nothing here contacts the machine.

    python tools/camera/vision_plan.py --out analytics/rig/plans/fast1.json --seed 1
"""

import argparse
import json
import math
import os
import random

MAX_PISTON = 18
WATER_PISTONS = list(range(1, 13))
ALL_PISTONS = list(range(1, MAX_PISTON + 1))
FORBIDDEN = {26}
MAX_GROUPS = 4

SINGLE = {"stroke": (30, 350), "speed": (50, 600), "accel": (200, 20000),
          "decel": (200, 20000), "jerk": (300, 7500)}
CONTINUOUS = dict(SINGLE, stroke=(60, 350), speed=(150, 500))
CONTINUOUS_SECONDS = (8, 15)
PULSE = {"lift": (40, 200), "accel": (30, 400), "decel": (30, 400),
         "jerk": (100, 1500)}
PULSE_SPEED = 200  # fixed by the machine; recorded for reference only

STANDARD = {"stroke": 250, "speed": 400, "accel": 10000, "decel": 10000,
            "jerk": 2000}


def row(n):
    return [3 * n - 2, 3 * n - 1, 3 * n]


def lane(k):
    """Lane k (1..3) across rows 1-4: pistons k, k+3, k+6, k+9."""
    return [k + 3 * i for i in range(4)]


def pistons_text(pistons):
    return ",".join(str(p) for p in sorted(set(pistons)))


def group(pistons, **settings):
    values = dict(STANDARD)
    values.update(settings)
    item = {"pistons": pistons_text(pistons)}
    for key in ("stroke", "speed", "accel", "decel", "jerk"):
        item[key] = int(values[key])
    return item


def single(name, family, groups):
    return {"name": name, "kind": "single", "family": family, "groups": groups}


def continuous(name, family, seconds, groups):
    return {"name": name, "kind": "continuous", "family": family,
            "seconds": int(seconds), "groups": groups}


def pulse(name, family, pistons, lift, accel, decel, jerk):
    return {"name": name, "kind": "pulse_down", "family": family,
            "pistons": pistons_text(pistons), "lift": int(lift),
            "accel": int(accel), "decel": int(decel), "jerk": int(jerk),
            "speed": PULSE_SPEED}


def log_uniform(rng, lo, hi):
    return int(round(math.exp(rng.uniform(math.log(lo), math.log(hi)))))


def random_settings(rng, limits):
    return {
        "stroke": rng.randint(*limits["stroke"]),
        "speed": rng.randint(*limits["speed"]),
        "accel": log_uniform(rng, *limits["accel"]),
        "decel": log_uniform(rng, *limits["decel"]),
        "jerk": log_uniform(rng, *limits["jerk"]),
    }


def random_pistons(rng, count, deep_share):
    """Pick ``count`` distinct pistons, 1-12 weighted over 13-18."""
    chosen = []
    pool = list(ALL_PISTONS)
    while len(chosen) < count:
        weights = [1.0 if p > 12 else 3.0 for p in pool]
        if deep_share == 0:
            weights = [0.0 if p > 12 else w for p, w in zip(pool, weights)]
        p = rng.choices(pool, weights=weights)[0]
        chosen.append(p)
        pool.remove(p)
    return chosen


def split(rng, pistons, k):
    pistons = list(pistons)
    rng.shuffle(pistons)
    cuts = sorted(rng.sample(range(1, len(pistons)), k - 1))
    parts, start = [], 0
    for cut in cuts + [len(pistons)]:
        parts.append(pistons[start:cut])
        start = cut
    return parts


def core_tests():
    tests = []
    for p in WATER_PISTONS:
        tests.append(single("solo_p%02d_a" % p, "solo", [group([p])]))
    for n in range(1, 7):
        tests.append(single("row%d" % n, "row", [group(row(n))]))
    for k in range(1, 4):
        tests.append(single("lane%d" % k, "lane", [group(lane(k))]))
    for a, b in ((1, 2), (1, 4), (2, 3), (3, 4)):
        tests.append(single("rows%d+%d" % (a, b), "row_pair",
                            [group(row(a) + row(b))]))
    tests.append(single("all12", "all12", [group(WATER_PISTONS)]))
    tests.append(pulse("pulse_gentlest_all12", "pulse", WATER_PISTONS,
                       100, 30, 30, 100))
    tests.append(continuous("cont_all12_s200_v300", "continuous", 12,
                            [group(WATER_PISTONS, stroke=200, speed=300)]))
    return tests


def rest_tests(rng):
    tests = []
    for p in WATER_PISTONS:
        tests.append(single("solo_p%02d_b" % p, "solo", [group([p])]))

    for stroke in (50, 100, 200, 300):
        tests.append(single("lin_stroke%03d" % stroke, "linearity",
                            [group(WATER_PISTONS, stroke=stroke, speed=400)]))
    for speed in (100, 200, 400, 600):
        tests.append(single("lin_speed%03d" % speed, "speed_sweep",
                            [group(WATER_PISTONS, stroke=200, speed=speed)]))

    shapes = [(300, 300, 600), (1000, 1000, 1000), (3000, 3000, 2000),
              (10000, 10000, 5000), (20000, 20000, 7500), (20000, 300, 7500),
              (300, 20000, 600), (5000, 5000, 300)]
    for acc, dec, jerk in shapes:
        tests.append(single("shape_a%d_d%d_j%d" % (acc, dec, jerk), "shape",
                            [group(WATER_PISTONS, stroke=200, speed=400,
                                   accel=acc, decel=dec, jerk=jerk)]))

    # Deeper-water rows alongside the water rows.
    tests.append(single("rows1-6", "deep", [group(ALL_PISTONS)]))
    tests.append(single("rows5+6", "deep", [group(row(5) + row(6))]))
    tests.append(single("rows1-4_vs_5-6", "deep",
                        [group(WATER_PISTONS, stroke=200),
                         group(row(5) + row(6), stroke=300)]))

    # Random subsets of 1-12 at standard settings with a random stroke.
    for i in range(20):
        count = rng.randint(2, 9)
        pistons = random_pistons(rng, count, deep_share=0)
        stroke = rng.choice((100, 150, 200, 250, 300))
        tests.append(single("subset%02d" % (i + 1), "subset",
                            [group(pistons, stroke=stroke)]))

    # Random mixes of 1-18, 2-4 settings groups each.
    for i in range(42):
        k = rng.randint(2, MAX_GROUPS)
        count = rng.randint(max(k, 3), 14)
        pistons = random_pistons(rng, count, deep_share=1)
        groups = [group(part, **random_settings(rng, SINGLE))
                  for part in split(rng, pistons, k)]
        tests.append(single("mix%02d" % (i + 1), "mix", groups))

    pulses = [
        ("all12", WATER_PISTONS, 40, 100, 100, 300),
        ("all12", WATER_PISTONS, 80, 100, 100, 300),
        ("all12", WATER_PISTONS, 120, 100, 100, 300),
        ("all12", WATER_PISTONS, 160, 100, 100, 300),
        ("all12", WATER_PISTONS, 200, 100, 100, 300),
        ("all12", WATER_PISTONS, 120, 30, 30, 100),
        ("all12", WATER_PISTONS, 200, 30, 30, 100),
        ("all12", WATER_PISTONS, 120, 200, 200, 600),
        ("all12", WATER_PISTONS, 120, 400, 400, 1500),
        ("all12", WATER_PISTONS, 120, 100, 400, 1500),
        ("row1", row(1), 120, 100, 100, 300),
        ("rows1-2", row(1) + row(2), 120, 100, 100, 300),
        ("rows3-4", row(3) + row(4), 120, 100, 100, 300),
        ("rows1-6", ALL_PISTONS, 120, 100, 100, 300),
    ]
    for label, pistons, lift, acc, dec, jerk in pulses:
        name = "pulse_%s_l%d_a%d_d%d_j%d" % (label, lift, acc, dec, jerk)
        tests.append(pulse(name, "pulse", pistons, lift, acc, dec, jerk))

    conts = [(60, 300), (100, 300), (150, 300), (250, 400), (300, 400),
             (350, 500), (200, 150), (120, 500)]
    for stroke, speed in conts:
        tests.append(continuous("cont_all12_s%d_v%d" % (stroke, speed),
                                "continuous", rng.randint(*CONTINUOUS_SECONDS),
                                [group(WATER_PISTONS, stroke=stroke,
                                       speed=speed)]))
    tests.append(continuous("cont_rows12_vs_34", "continuous", 12,
                            [group(row(1) + row(2), stroke=150, speed=300),
                             group(row(3) + row(4), stroke=300, speed=300)]))
    tests.append(continuous("cont_rows1-6_s200_v300", "continuous", 12,
                            [group(ALL_PISTONS, stroke=200, speed=300)]))
    return tests


def build_plan(seed):
    rng = random.Random(seed)
    core = core_tests()
    rng.shuffle(core)
    rest = rest_tests(rng)
    rng.shuffle(rest)
    for item in core:
        item["core"] = True
    for item in rest:
        item["core"] = False
    return core + rest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="plan JSON path")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    plan = build_plan(args.seed)
    folder = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(args.out, "w") as handle:
        json.dump(plan, handle, indent=1)
        handle.write("\n")
    counts = {}
    for item in plan:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    print("%d tests -> %s  %s" % (len(plan), args.out, json.dumps(counts)))


if __name__ == "__main__":
    main()
