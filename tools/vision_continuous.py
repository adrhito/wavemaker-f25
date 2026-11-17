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


def _problems(status: dict) -> list:
    drives = status.get("data", {}).get("drives", [])
    return [(d["piston"], d["problems"]) for d in drives
            if d.get("problems") and d["piston"] != 26]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan")
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--rest", type=float, default=40.0)
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
        fire = time.time()
        reply = wm("run", "--seconds", str(int(seconds)), "--sample", "30", "--wait",
                   timeout=seconds + 120)
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
