"""Read-only live survey. Does not write parameters, clear faults, or move drives."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import drive_status, params, tags
from app.plc import PlcClient


def survey(client):
    commands = {tag: client.read(tag) for tag in (
        tags.RUN_SINGLE, tags.RUN_CONTINUOUS, tags.RUN_CURVE,
        tags.HOME_BUTTON, tags.MOTOR_BOOT, tags.CLEAR_MOTOR_ERROR)}
    rows = []
    for shown in range(1, 13):
        axis = tags.axis_from_display(shown)
        status = int(client.read(tags.axis_field(axis, tags.STATUS_WORD)))
        warning = int(client.read(tags.axis_field(axis, tags.WARN_WORD)))
        rows.append({
            "piston": shown, "axis": axis, "status": status, "warn": warning,
            "position_mm": params.to_mm(client.read(tags.axis_field(axis, tags.ACTUAL_POSITION))),
            "homed": drive_status.is_homed(status), "enabled": bool(status & 1),
            "problems": drive_status.problems(warning, status),
        })
    return {"time": time.time(), "commands": commands, "pistons": rows}


def main():
    client = PlcClient("192.168.1.1", 1)
    records = []
    try:
        if not client.connect():
            raise RuntimeError("Controller not reachable; no writes attempted")
        for index in range(3):
            record = survey(client)
            records.append(record)
            print(json.dumps(record), flush=True)
            if index < 2:
                time.sleep(2)
    finally:
        client.close()
        folder = Path(__file__).resolve().parent.parent / "logs" / "verification"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / ("survey-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        output.write_text(json.dumps(records, indent=2), encoding="utf-8")
        print("Saved " + str(output), flush=True)


if __name__ == "__main__":
    main()
