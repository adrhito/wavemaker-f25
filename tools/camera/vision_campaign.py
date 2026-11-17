"""Fire a plan of single strokes back to back while vision_rig.py records.

The water is not left to settle: the model adds up every stroke's wave, so
strokes only have to be logged.  Each run selects its pistons, sets them
(pistons with identical settings in one call), and fires one stroke with
position sampling, starting ``--interval`` seconds after the last.  Every run
is appended to ``analytics/rig/runs.jsonl`` with wavectl's reply, which
names the position samples.  Only wavectl is used; the PLC is never touched
here.

    python tools/camera/vision_campaign.py analytics/rig/plans/batch2.json --interval 35
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "camera"))

from vision_probe import REST_MM, wm  # noqa: E402

LOG = ROOT / "analytics" / "rig" / "runs.jsonl"
KEYS = ("stroke", "speed", "accel", "decel", "jerk", "top")


def _batched(groups):
    """Merge groups whose settings are identical, so one call sets them."""
    merged = {}
    for g in groups:
        key = tuple(g.get(k) for k in KEYS)
        merged.setdefault(key, []).append(str(g["pistons"]))
    out = []
    for key, pistons in merged.items():
        g = {k: v for k, v in zip(KEYS, key) if v is not None}
        g["pistons"] = ",".join(pistons)
        out.append(g)
    return out


def _set_args(g):
    stroke = int(g["stroke"])
    args = ["p1={0}".format(int(g.get("top", REST_MM - stroke))),
            "stroke={0}".format(stroke), "speed={0}".format(int(g["speed"]))]
    for key in ("accel", "decel", "jerk"):
        if key in g:
            args.append("{0}={1}".format(key, int(g[key])))
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan")
    parser.add_argument("--interval", type=float, default=35.0)
    args = parser.parse_args()
    runs = json.loads(Path(args.plan).read_text())
    done = set()
    if LOG.exists():
        for line in LOG.read_text().splitlines():
            done.add(json.loads(line)["run"]["name"])
    last_fire = 0.0
    for run in runs:
        if run["name"] in done:
            continue
        groups = _batched(run["groups"])
        everyone = ",".join(g["pistons"] for g in groups)
        replies = [wm("select", everyone)]
        for g in groups:
            replies.append(wm("set", *_set_args(g), "--pistons", g["pistons"]))
        bad = [r for r in replies if not r.get("ok")]
        if bad:
            raise SystemExit("wavectl refused for {0}: {1}".format(
                run["name"], bad[0].get("text")))
        wait = last_fire + args.interval - time.time()
        if wait > 0:
            time.sleep(wait)
        fire = time.time()
        reply = wm("run", "--single", "--sample", "30")
        last_fire = fire
        entry = {"run": run, "fire_sent": fire, "fire_answered": time.time(),
                 "reply": reply}
        with open(LOG, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
        print("{0} {1}: {2}".format("ok" if reply.get("ok") else "FAILED",
                                    run["name"], reply.get("text", "")[:100]),
              flush=True)
        if not reply.get("ok"):
            raise SystemExit("stroke failed")


if __name__ == "__main__":
    main()
