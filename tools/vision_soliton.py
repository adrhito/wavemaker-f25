"""Soliton trials filmed by vision_rig.py: stage, settle, fire, hold, park.

Each trial selects the wet pistons, stages them low (``wm soliton stage``),
waits for the water, fires one held pulse (``wm soliton fire``), waits for
the wave to pass and return, and parks.  A ``wm watch`` runs alongside the
whole time, so every move -- staging, the pulse and the park -- is in one
sample file for the model.  Each trial is logged to
``analytics/rig/runs.jsonl`` like the other runs, with the watch's reply.
Only wavectl is used.

    python tools/vision_soliton.py --pistons 4-7,10-12 --crests 20 40 --depth 406
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pistons", required=True)
    ap.add_argument("--crests", type=float, nargs="+", required=True, help="mm")
    ap.add_argument("--depth", type=float, required=True, help="still water, mm")
    ap.add_argument("--settle", type=float, default=40.0)
    ap.add_argument("--hold", type=float, default=45.0)
    ap.add_argument("--rest", type=float, default=60.0)
    ap.add_argument("--extra", nargs="*", default=[],
                    help="further soliton stage options, e.g. --width 2000")
    ap.add_argument("--ignore-hot", action="store_true")
    a = ap.parse_args()
    for crest in a.crests:
        name = "S_crest{0:g}_d{1:g}".format(crest, a.depth)
        sel = wm("select", a.pistons)
        if not sel.get("ok"):
            raise SystemExit(sel.get("text"))
        plan = wm("soliton", "plan", "--crest", str(crest), "--depth", str(a.depth), *a.extra)
        total = 30 + a.settle + 10 + a.hold + 20
        watch = subprocess.Popen(["sh", str(WM), "watch", "--seconds", str(int(total)),
                                  "--hz", "30", "--json"], cwd=str(ROOT),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        started = time.time()
        stage = wm("soliton", "stage", "--crest", str(crest), "--depth", str(a.depth),
                   *a.extra, *(["--ignore-hot"] if a.ignore_hot else []), timeout=200)
        if not stage.get("ok"):
            watch.terminate()
            raise SystemExit("stage refused: {0}".format(stage.get("text")))
        time.sleep(a.settle)
        fire_sent = time.time()
        fire = wm("soliton", "fire", timeout=120)
        time.sleep(a.hold)
        park = wm("park", timeout=120)
        out, err = watch.communicate(timeout=total + 60)
        try:
            reply = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            reply = {"ok": False, "text": out + err}
        data = reply.get("data") or {}
        if "file" in data:
            # watch answers with the samples themselves; a run nests them.
            reply["data"] = {"samples": data}
        entry = {"run": {"name": name, "pistons": a.pistons, "crest_mm": crest,
                         "depth_mm": a.depth, "extra": a.extra},
                 "kind": "soliton", "fire_sent": fire_sent, "watch_started": started,
                 "plan": plan.get("text"), "stage": stage.get("text"),
                 "fire": fire.get("text"), "park": park.get("text"), "reply": reply}
        with open(LOG, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
        print("{0}: fire {1} | park {2} | watch {3}".format(
            name, fire.get("text", "")[:120], park.get("text", "")[:60],
            reply.get("text", "")[:80]), flush=True)
        time.sleep(a.rest)


if __name__ == "__main__":
    main()
