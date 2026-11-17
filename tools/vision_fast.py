"""Run a test plan as fast as the water allows: fire as soon as the camera sees it still.

``tools/vision_rig.py`` writes ``analytics/rig/live.json`` twice a second
with ``moving_mm``, the largest peak-to-peak surface movement over ten
stations in the last three seconds.  Before each test this waits until that
falls below ``--still-mm`` (or ``--max-wait`` passes), then fires.  Kinds:

- ``single``: one stroke, settings per group (``run --single --sample``);
- ``continuous``: a timed run (``run --seconds N --sample --wait``);
- ``pulse_down``: raise the pistons slowly (``rest=up``, ``park``), wait for
  stillness, push them down (``rest=down``, ``park``) with ``wm watch``
  recording, which is how this machine makes an elevation pulse.

Every test is appended to ``analytics/rig/runs.jsonl`` with how still the
water was when it fired.  Only wavectl is used; this never touches the PLC.
"Controller Hot" alone is passed (``--ignore-warnings`` / ``--ignore-hot``)
on the operator's judgement that the sensor is unreliable; any other drive
problem stops the plan.

    python tools/vision_fast.py analytics/rig/plans/fast1.json
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

from vision_campaign import LOG, _batched, _set_args  # noqa: E402
from vision_continuous import _baseline, _problems  # noqa: E402
from vision_probe import WM, wm  # noqa: E402

LIVE = ROOT / "analytics" / "rig" / "live.json"
REST_DOWN_MM = 370


def wait_still(threshold: float, max_wait: float, min_wait: float) -> dict:
    """Block until the surface is still; return the reading that allowed it."""
    start = time.time()
    last = {}
    while True:
        try:
            last = json.loads(LIVE.read_text())
        except (OSError, ValueError):
            last = {}
        fresh = last and time.time() - last.get("t", 0) < 3.0
        waited = time.time() - start
        if waited >= min_wait and fresh and last.get("moving_mm", 99) < threshold:
            last["waited_s"] = waited
            return last
        if waited >= max_wait:
            last["waited_s"] = waited
            last["timed_out"] = True
            return last
        time.sleep(0.3)


def _check(reply, what):
    if not reply.get("ok"):
        raise SystemExit("{0} refused: {1}".format(what, reply.get("text")))
    return reply


def _setup(groups):
    groups = _batched(groups)
    _check(wm("select", ",".join(g["pistons"] for g in groups)), "select")
    for g in groups:
        _check(wm("set", *_set_args(g), "--pistons", g["pistons"]), "set")


def _watched(seconds, action):
    """Run ``action`` while ``wm watch`` records; return (action reply, samples reply)."""
    watch = subprocess.Popen(["sh", str(WM), "watch", "--seconds", str(int(seconds)),
                              "--hz", "30", "--json"], cwd=str(ROOT),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    fire = time.time()
    reply = action()
    out, err = watch.communicate(timeout=seconds + 60)
    try:
        samples = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        samples = {"ok": False, "text": out + err}
    data = samples.get("data") or {}
    if "file" in data:
        samples["data"] = {"samples": data}
    return fire, reply, samples


def run_one(item, args, still):
    kind = item["kind"]
    entry = {"run": item, "kind": kind, "still": still, "baseline": _baseline()}
    hot = ["--ignore-warnings"]
    if kind == "single":
        _setup(item["groups"])
        trouble = _problems(wm("status", "-p"))
        if trouble:
            raise SystemExit("drive errors: {0}".format(trouble))
        entry["fire_sent"] = time.time()
        reply = wm("run", "--single", "--sample", "30", *hot, timeout=120)
        entry["reply"] = reply
    elif kind == "continuous":
        _setup(item["groups"])
        trouble = _problems(wm("status", "-p"))
        if trouble:
            raise SystemExit("drive errors: {0}".format(trouble))
        entry["fire_sent"] = time.time()
        reply = wm("run", "--seconds", str(int(item["seconds"])), "--sample", "30",
                   "--wait", *hot, timeout=item["seconds"] + 120)
        entry["reply"] = reply
    elif kind == "pulse_down":
        gentle = ["accel={0}".format(int(item["accel"])), "decel={0}".format(int(item["decel"])),
                  "jerk={0}".format(int(item["jerk"]))]
        _check(wm("select", item["pistons"]), "select")
        _check(wm("set", "p1={0}".format(REST_DOWN_MM - int(item["lift"])),
                  "p2={0}".format(REST_DOWN_MM), *gentle, "rest=up"), "set up")
        entry["rise"] = _check(wm("park", timeout=180), "rise").get("text")
        entry["still_after_rise"] = wait_still(args.still_mm, args.max_wait, 3.0)
        _check(wm("set", *gentle, "rest=down"), "set down")
        fire, push, samples = _watched(20, lambda: wm("park", timeout=180))
        entry.update(fire_sent=fire, push=push.get("text"), reply=samples)
        reply = samples
    else:
        raise SystemExit("unknown kind {0}".format(kind))
    with open(LOG, "a") as handle:
        handle.write(json.dumps(entry) + "\n")
    return reply


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--still-mm", type=float, default=1.2,
                    help="largest peak-to-peak movement over 3 s that counts as still")
    ap.add_argument("--min-wait", type=float, default=4.0)
    ap.add_argument("--max-wait", type=float, default=90.0)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many tests")
    a = ap.parse_args()
    done = set()
    if LOG.exists():
        done = {json.loads(l)["run"].get("name") for l in LOG.read_text().splitlines()}
    count = 0
    for item in json.loads(Path(a.plan).read_text()):
        if item["name"] in done:
            continue
        still = wait_still(a.still_mm, a.max_wait, a.min_wait)
        reply = run_one(item, a, still)
        count += 1
        print("{0} {1} {2} (waited {3:.0f} s, moving {4:.2f} mm{5}): {6}".format(
            "ok" if reply.get("ok") else "FAILED", item["kind"], item["name"],
            still.get("waited_s", 0), still.get("moving_mm", float("nan")),
            ", timed out" if still.get("timed_out") else "",
            reply.get("text", "")[:90]), flush=True)
        if not reply.get("ok"):
            raise SystemExit("test failed")
        if a.limit and count >= a.limit:
            break


if __name__ == "__main__":
    main()
