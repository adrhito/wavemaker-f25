"""The filming test plan stays inside the pistons, ranges and coverage asked for."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import vision_plan  # noqa: E402


def parse(text):
    return [int(p) for p in text.split(",")]


def ids(text):
    return frozenset(parse(text))


@pytest.fixture(scope="module")
def plan():
    return vision_plan.build_plan(1)


def singles_standard(plan, pistons, stroke=250, speed=400):
    want = frozenset(pistons)
    return [t for t in plan if t["kind"] == "single" and len(t["groups"]) == 1
            and ids(t["groups"][0]["pistons"]) == want
            and t["groups"][0]["stroke"] == stroke
            and t["groups"][0]["speed"] == speed]


def all12_single(plan, **want):
    out = []
    for t in plan:
        if t["kind"] != "single" or len(t["groups"]) != 1:
            continue
        g = t["groups"][0]
        if ids(g["pistons"]) == frozenset(range(1, 13)) and all(
                g[k] == v for k, v in want.items()):
            out.append(t)
    return out


def test_names_unique_and_size(plan):
    names = [t["name"] for t in plan]
    assert len(names) == len(set(names))
    assert 130 <= len(plan) <= 170


def test_pistons_and_ranges(plan):
    for t in plan:
        if t["kind"] == "pulse_down":
            pistons = parse(t["pistons"])
            for key, (lo, hi) in vision_plan.PULSE.items():
                assert lo <= t[key] <= hi, (t["name"], key)
        else:
            assert t["kind"] in ("single", "continuous")
            limits = (vision_plan.SINGLE if t["kind"] == "single"
                      else vision_plan.CONTINUOUS)
            if t["kind"] == "continuous":
                assert 8 <= t["seconds"] <= 15
            assert 1 <= len(t["groups"]) <= 4
            pistons = []
            for g in t["groups"]:
                for key, (lo, hi) in limits.items():
                    assert isinstance(g[key], int)
                    assert lo <= g[key] <= hi, (t["name"], key, g[key])
                pistons += parse(g["pistons"])
            assert len(pistons) == len(set(pistons)), t["name"]
        assert pistons
        assert all(1 <= p <= 18 for p in pistons), t["name"]
        assert 26 not in pistons


def test_core_first_and_balanced(plan):
    flags = [t["core"] for t in plan]
    n = flags.count(True)
    assert flags[:n] == [True] * n
    kinds = {t["kind"] for t in plan[:n]}
    assert kinds == {"single", "continuous", "pulse_down"}


def test_each_water_piston_alone_twice_apart(plan):
    for p in range(1, 13):
        hits = singles_standard(plan, [p])
        assert len(hits) == 2, p
        a, b = (plan.index(t) for t in hits)
        assert abs(a - b) > 1


def test_rows_lanes_pairs_all12(plan):
    for n in range(1, 7):
        assert singles_standard(plan, range(3 * n - 2, 3 * n + 1)), n
    for lane in ([1, 4, 7, 10], [2, 5, 8, 11], [3, 6, 9, 12]):
        assert singles_standard(plan, lane)
    for a, b in ((1, 2), (1, 4), (2, 3), (3, 4)):
        pistons = list(range(3 * a - 2, 3 * a + 1)) + list(
            range(3 * b - 2, 3 * b + 1))
        assert singles_standard(plan, pistons), (a, b)
    assert singles_standard(plan, range(1, 13))


def test_linearity_and_speed_sweep(plan):
    for stroke in (50, 100, 200, 300):
        assert all12_single(plan, stroke=stroke, speed=400), stroke
    for speed in (100, 200, 400, 600):
        assert all12_single(plan, stroke=200, speed=speed), speed


def test_shape_extremes(plan):
    assert all12_single(plan, accel=300, decel=300, jerk=600)
    assert all12_single(plan, accel=20000, decel=20000, jerk=7500)
    assert len([t for t in plan if t["family"] == "shape"]) >= 5


def test_random_mixes(plan):
    mixes = [t for t in plan if t["family"] == "mix"]
    assert 35 <= len(mixes) <= 50
    assert all(2 <= len(t["groups"]) <= 4 for t in mixes)
    deep = [t for t in mixes
            if any(p > 12 for g in t["groups"] for p in parse(g["pistons"]))]
    assert deep
    # 1-12 dominate the random choices.
    counts = [0] * 19
    for t in mixes:
        for g in t["groups"]:
            for p in parse(g["pistons"]):
                counts[p] += 1
    assert sum(counts[1:13]) / 12.0 > 1.5 * sum(counts[13:19]) / 6.0


def test_pulses(plan):
    pulses = [t for t in plan if t["kind"] == "pulse_down"]
    assert 12 <= len(pulses) <= 18
    assert any(t["accel"] == 30 and t["decel"] == 30 and t["jerk"] == 100
               and ids(t["pistons"]) == frozenset(range(1, 13))
               for t in pulses)
    assert any(ids(t["pistons"]) != frozenset(range(1, 13)) for t in pulses)
    assert len({t["lift"] for t in pulses}) >= 4
    assert len({t["decel"] for t in pulses}) >= 3


def test_continuous(plan):
    conts = [t for t in plan if t["kind"] == "continuous"]
    assert 8 <= len(conts) <= 12
    periods = {(t["groups"][0]["stroke"], t["groups"][0]["speed"])
               for t in conts}
    assert len(periods) >= 8


def test_some_tests_reach_rows_5_6(plan):
    assert any(p > 12 for t in plan
               for p in (parse(t["pistons"]) if t["kind"] == "pulse_down"
                         else [q for g in t["groups"]
                               for q in parse(g["pistons"])]))


def test_seed_repeatable_and_writes(tmp_path):
    assert vision_plan.build_plan(3) == vision_plan.build_plan(3)
    assert vision_plan.build_plan(3) != vision_plan.build_plan(4)
    out = tmp_path / "plans" / "p.json"
    vision_plan.main(["--out", str(out), "--seed", "2"])
    assert json.loads(out.read_text()) == vision_plan.build_plan(2)
