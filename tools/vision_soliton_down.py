"""Elevation pulses: raise the discs slowly, let the water settle, push them down.

Raising a disc draws water up into its pipe, so the soliton tool's upward
pulse makes a trough on this machine (16 Nov 2026, camera and tank model
agree).  A crest needs the discs to go *down*.  This uses only existing
wavectl commands: ``park`` moves the selection to its resting place at
200 mm/s with whatever acceleration, deceleration and jerk the drives hold,
and ``rest=up`` puts that place at each piston's Position 1.  So:

1. set gentle accel/decel/jerk, Position 1 = the raised height, rest=up; park
   (the slow rise, which makes a small trough);
2. wait for the water;
3. set rest=down and park again, with ``wm watch`` recording: one smooth
   push down at 200 mm/s, stopping as gently as the deceleration allows.

Each pulse is logged to ``analytics/rig/runs.jsonl`` with the watch's samples.

    python tools/vision_soliton_down.py --pistons 1-12 --lifts 60 120 --decel 150
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from vision_campaign import LOG  # noqa: E402
from vision_probe import WM, wm  # noqa: E402

REST_MM = 370  # where park puts the pistons with rest=down


def _ok(reply, what):
    if not reply.get("ok"):
        raise SystemExit("{0} refused: {1}".format(what, reply.get("text")))
    return reply


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pistons", required=True)
    ap.add_argument("--lifts", type=int, nargs="+", required=True, help="mm above rest")
    ap.add_argument("--accel", type=int, default=150, help="mm/s^2")
    ap.add_argument("--decel", type=int, default=150, help="mm/s^2")
    ap.add_argument("--jerk", type=int, default=500, help="mm/s^3")
    ap.add_argument("--settle", type=float, default=60.0)
    ap.add_argument("--record", type=float, default=60.0, help="s after the push")
    ap.add_argument("--rest", type=float, default=20.0)
    a = ap.parse_args()
    for lift in a.lifts:
        top = REST_MM - lift
        name = "SD_lift{0}_a{1}_d{2}_j{3}".format(lift, a.accel, a.decel, a.jerk)
        _ok(wm("select", a.pistons), "select")
        gentle = ["accel={0}".format(a.accel), "decel={0}".format(a.decel),
                  "jerk={0}".format(a.jerk)]
        _ok(wm("set", "p1={0}".format(top), "p2={0}".format(REST_MM), *gentle, "rest=up"), "set up")
        rise = _ok(wm("park", timeout=180), "rise")
        time.sleep(a.settle)
        _ok(wm("set", *gentle, "rest=down"), "set down")
        total = 5 + 15 + a.record
        watch = subprocess.Popen(["sh", str(WM), "watch", "--seconds", str(int(total)),
                                  "--hz", "30", "--json"], cwd=str(ROOT),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        started = time.time()
        time.sleep(3)
        fire = time.time()
        push = wm("park", timeout=180)
        out, err = watch.communicate(timeout=total + 60)
        try:
            reply = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            reply = {"ok": False, "text": out + err}
        data = reply.get("data") or {}
        if "file" in data:
            reply["data"] = {"samples": data}
        entry = {"run": {"name": name, "pistons": a.pistons, "lift_mm": lift,
                         "accel": a.accel, "decel": a.decel, "jerk": a.jerk, "speed": 200},
                 "kind": "soliton_down", "fire_sent": fire, "watch_started": started,
                 "rise": rise.get("text"), "push": push.get("text"), "reply": reply}
        with open(LOG, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
        print("{0}: rise {1} | push {2} | watch {3}".format(
            name, rise.get("text", "")[:70], push.get("text", "")[:70],
            reply.get("text", "")[:70]), flush=True)
        time.sleep(a.rest)


if __name__ == "__main__":
    main()
