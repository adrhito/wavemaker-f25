"""Read-only: is there anything for Start Curve to run?

Curve mode writes Curve_ID, Time Scale and Amplitude Scale to the controller
and sets Run_Curve. All three are documented "Used by Start Curve only", and
the application's own failure message admits the likeliest outcome -- "no curve
is loaded on the controller for the Curve ID you set, so there is nothing to
run". Nothing in this application ever loads a curve, so the question is
whether somebody loaded them in Studio 5000.

This answers it without moving anything: it reads the Curve_<n> structure for
every piston and reports what is there. It writes nothing and sets no run bit.

    python tools/live_curve_probe.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import params, tags
from app.plc import PlcClient

#: The fields the application knows about on a Curve_<n> structure, plus the
#: ones a stored curve would plausibly need. Reading an absent tag simply
#: fails, which is itself the answer.
CURVE_FIELDS = ("Curve_ID", "TimeScale", "AmplitudeScale", "Offset",
                "Curve_Type", "NumPoints", "Active")


def read_or_none(client, tag):
    try:
        return client.read(tag)
    except Exception as exc:  # noqa: BLE001 - "no such tag" is a result
        return "<{0}>".format(type(exc).__name__)


def main() -> int:
    client = PlcClient("192.168.1.1", 1)
    found = {"time": time.time(), "pistons": {}, "run_curve": None}
    try:
        if not client.connect():
            print("Controller not reachable; nothing read.")
            return 2

        found["run_curve"] = client.read(tags.RUN_CURVE)
        print("Run_Curve is currently: {0}\n".format(found["run_curve"]))

        # What the application itself writes, read back for a few pistons.
        print("Curve parameters the application writes, as held by the drive:")
        for shown in (1, 5, 9):
            axis = tags.axis_from_display(shown)
            row = {}
            for name in ("Curve ID", "Time Scale", "Amplitude Scale",
                         "Curve Offset"):
                spec = params.BY_NAME[name]
                row[name] = read_or_none(client, spec.tag(axis))
            found["pistons"][shown] = row
            print("  piston {0}: {1}".format(shown, row))

        print("\nEverything on the Curve_<n> structure for piston 1:")
        axis = tags.axis_from_display(1)
        extra = {}
        for field in CURVE_FIELDS:
            extra[field] = read_or_none(client, tags.curve_field(axis, field))
            print("  {0:<16} {1}".format(field, extra[field]))
        found["curve_struct_piston_1"] = extra

        print("\nReading is all this does. A Curve_ID of 0 everywhere, or a "
              "tag that does not exist, means Start Curve has nothing to run.")
    finally:
        client.close()
        folder = Path(__file__).resolve().parent.parent / "logs" / "verification"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("curve-probe-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        target.write_text(json.dumps(found, indent=2, default=str),
                          encoding="utf-8")
        print("Saved {0}".format(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
