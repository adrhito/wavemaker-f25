"""Continuous runs, one after another, while vision_rig.py records.

Each item is a preset (``{"name": ..., "preset": "Preset 1-Big Wave Demo"}``)
or groups like vision_campaign.py's.  It is run for ``seconds`` with
position sampling, then the drives are checked and the array is left
``rest`` seconds before the next item, so the record also holds the waves
dying away.  A drive reporting a problem pauses the plan until it clears.
Everything is logged to ``analytics/rig/runs.jsonl`` like the single
strokes.  Only wavectl is used.

    python tools/vision_continuous.py analytics/rig/plans/continuous1.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from vision_campaign import LOG, _batched, _set_args  # noqa: E402
from vision_probe import wm  # noqa: E402


#: Pistons left out of every plan because their drives are locked off.
EXCLUDED = {1, 2, 3, 8, 9, 19, 26}


def _problems(status: dict) -> list:
    """Drive errors on the pistons in use.

    "Controller Hot" alone does not pause the plan: on 16 Nov 2026 it stayed
    on a dozen parked drives for over ten minutes, and the operator judged
    the controllers were not actually hot.
    """
    drives = status.get("data", {}).get("drives", [])
    out = []
    for d in drives:
        real = [p for p in d.get("problems", []) if "Controller Hot" not in p]
        if real and d["piston"] not in EXCLUDED:
            out.append((d["piston"], real))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan")
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--rest", type=float, default=40.0)
    parser.add_argument("--ignore-hot", action="store_true",
                        help="run through 'Controller Hot' (only that) on the operator's word")
    args = parser.parse_args()
    done = set()
    if LOG.exists():
        done = {json.loads(l)["run"]["name"] for l in LOG.read_text().splitlines()}
    for item in json.loads(Path(args.plan).read_text()):
        if item["name"] in done:
            continue
        seconds = item.get("seconds", args.seconds)
        if "preset" in item:
            replies = [wm("preset", item["preset"], "--select")]
        else:
            groups = _batched(item["groups"])
            replies = [wm("select", ",".join(g["pistons"] for g in groups))]
            for g in groups:
                replies.append(wm("set", *_set_args(g), "--pistons", g["pistons"]))
        bad = [r for r in replies if not r.get("ok")]
        if bad:
            raise SystemExit("wavectl refused for {0}: {1}".format(item["name"], bad[0].get("text")))
        params = wm("params")
        extra = []
        if args.ignore_hot:
            trouble = _problems(wm("status", "-p"))
            if trouble:
                raise SystemExit("drive errors, not running: {0}".format(trouble))
            # Only "Controller Hot" is left, which the operator has judged
            # spurious; wavectl refuses on it unless told.
            extra = ["--ignore-warnings"]
        fire = time.time()
        reply = wm("run", "--seconds", str(int(seconds)), "--sample", "30", "--wait",
                   *extra, timeout=seconds + 120)
        entry = {"run": item, "kind": "continuous", "fire_sent": fire,
                 "fire_answered": time.time(), "params": params.get("text"),
                 "reply": reply}
        with open(LOG, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
        print("{0} {1}: {2}".format("ok" if reply.get("ok") else "FAILED", item["name"],
                                    reply.get("text", "")[:110]), flush=True)
        if not reply.get("ok"):
            raise SystemExit("run failed")
        time.sleep(args.rest)
        for _ in range(20):  # up to ten more minutes for a hot drive
            trouble = _problems(wm("status", "-p"))
            if not trouble:
                break
            print("waiting for drives: {0}".format(trouble), flush=True)
            time.sleep(30)
        else:
            raise SystemExit("drives did not clear")


if __name__ == "__main__":
    main()
