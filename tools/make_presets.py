"""Generate a starting library of presets.

    py -3 tools/make_presets.py

An important caveat, and it is written into the presets themselves: **none of
these are calibrated.** The lab has no measured relationship between piston
parameters and the wave that results -- as the professor put it, "we have
basically just gone in and fiddled around and got these numbers as big as we
could". So these are named for *what the pistons do*, not for the wave they
supposedly produce, and they are starting points to be measured, not recipes.

Each one is built from the machine's own limits (app/params.py), so nothing here
can ask for something the application would refuse.
"""

from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import params, patterns, paths  # noqa: E402

TOP = params.BY_NAME["Position 1"].minimum          # -20, top of travel
BOTTOM = params.BY_NAME["Position 2"].maximum       # 370, bottom of travel
MAX_SPEED = params.BY_NAME["Speed 1"].maximum       # 900
MAX_ACCEL = params.BY_NAME["Accel 1"].maximum       # 20000


def base(**overrides):
    values = params.defaults()
    values.update(overrides)
    return values


def uniform(values, axes=range(30)):
    """The same parameters on every listed piston."""
    return dict((axis, dict(values)) for axis in axes)


def staggered(values, step, axes=range(30), param="Curve Offset"):
    """A front-to-back stagger, for a wave that travels along the chamber."""
    rows = uniform(values, axes)
    result = patterns.build(list(axes), param, patterns.STAGGER, start=0, step=step)
    for axis, value in result.values.items():
        rows[axis][param] = value
    return rows


def shaped(values, param, pattern, start, end, axes=range(30)):
    rows = uniform(values, axes)
    result = patterns.build(list(axes), param, pattern, start=start, end=end)
    for axis, value in result.values.items():
        rows[axis][param] = value
    return rows


#: name -> (rows, what it does, why these numbers)
PRESETS = {}


def add(name, rows, note):
    PRESETS[name] = (rows, note)


# --- Amplitude, from smallest to largest -------------------------------------

add(
    "Gentle - short slow stroke",
    uniform(base(**{
        "Position 1": 200, "Position 2": 320,
        "Speed 1": 200, "Speed 2": 200,
        "Accel 1": 4000, "Accel 2": 4000, "Decel 1": 4000, "Decel 2": 4000,
        "Jerk 1": 2000, "Jerk 2": 2000,
    })),
    "A 120 mm stroke low in the travel, moving slowly. The smallest "
    "disturbance the array can make while still moving cleanly -- a starting "
    "point for calibration runs, and gentle on the drives.",
)

add(
    "Moderate - half stroke",
    uniform(base(**{
        "Position 1": 100, "Position 2": 350,
        "Speed 1": 450, "Speed 2": 450,
        "Accel 1": 10000, "Accel 2": 10000, "Decel 1": 10000, "Decel 2": 10000,
        "Jerk 1": 4000, "Jerk 2": 4000,
    })),
    "A 250 mm stroke at middling speed. Roughly the machine's comfortable "
    "middle, and a sensible place to start when trying something new.",
)

add(
    "Full stroke - fast",
    uniform(base(**{
        "Position 1": TOP, "Position 2": BOTTOM,
        "Speed 1": 750, "Speed 2": 750,
        "Accel 1": 16000, "Accel 2": 16000, "Decel 1": 16000, "Decel 2": 16000,
        "Jerk 1": 6000, "Jerk 2": 6000,
    })),
    "The whole travel, quickly. Close to the largest single-frequency "
    "disturbance the array can produce.",
)

add(
    "Maximum - everything at the limit",
    uniform(base(**{
        "Position 1": TOP, "Position 2": BOTTOM,
        "Speed 1": MAX_SPEED, "Speed 2": MAX_SPEED,
        "Accel 1": MAX_ACCEL, "Accel 2": MAX_ACCEL,
        "Decel 1": MAX_ACCEL, "Decel 2": MAX_ACCEL,
        "Jerk 1": 7500, "Jerk 2": 7500,
    })),
    "Every value at the limit the application allows. The hardest the machine "
    "is asked to work, and the case where pistons are most likely to fall "
    "behind their demanded position -- watch the array for stragglers.",
)

# --- Shape in time -----------------------------------------------------------

add(
    "Long period - slow with a pause",
    uniform(base(**{
        "Position 1": 60, "Position 2": 360,
        "Speed 1": 180, "Speed 2": 180,
        "Accel 1": 5000, "Accel 2": 5000, "Decel 1": 5000, "Decel 2": 5000,
        "Jerk 1": 2500, "Jerk 2": 2500,
        "Time 1": 500, "Time 2": 500,
        "Profile": 3,
    })),
    "A long slow stroke with a dwell at each end and a sine profile, for the "
    "longest period the array can drive. Time 1 and Time 2 are the pause; if "
    "the units turn out not to be milliseconds, that is the number to adjust.",
)

add(
    "Sharp - hard acceleration, short stroke",
    uniform(base(**{
        "Position 1": 240, "Position 2": 370,
        "Speed 1": MAX_SPEED, "Speed 2": MAX_SPEED,
        "Accel 1": MAX_ACCEL, "Accel 2": MAX_ACCEL,
        "Decel 1": MAX_ACCEL, "Decel 2": MAX_ACCEL,
        "Jerk 1": 7500, "Jerk 2": 7500,
        "Profile": 0,
    })),
    "A short stroke driven as hard as possible, on a trapezoidal profile. Puts "
    "the energy into a brief sharp push rather than a long smooth one.",
)

# --- Shape across the array --------------------------------------------------

add(
    "Travelling wave - front to back",
    staggered(base(**{
        "Position 1": 40, "Position 2": 350,
        "Speed 1": 550, "Speed 2": 550,
        "Accel 1": 12000, "Accel 2": 12000, "Decel 1": 12000, "Decel 2": 12000,
        "Jerk 1": 5000, "Jerk 2": 5000,
        "Curve ID": 1, "Time Scale": 100, "Amplitude Scale": 100,
    }), step=20),
    "Each column starts a little after the one in front of it, so the "
    "disturbance runs along the chamber instead of the whole array rising "
    "together. Curve Offset goes 0 at the front to 180 at the back. Run this "
    "with Start Curve; the stagger is what makes it a travelling wave rather "
    "than a standing one.",
)

add(
    "Travelling wave - fast march",
    staggered(base(**{
        "Position 1": 40, "Position 2": 350,
        "Speed 1": 700, "Speed 2": 700,
        "Accel 1": 15000, "Accel 2": 15000, "Decel 1": 15000, "Decel 2": 15000,
        "Jerk 1": 6000, "Jerk 2": 6000,
        "Curve ID": 1, "Time Scale": 100, "Amplitude Scale": 100,
    }), step=8),
    "The same idea with a much smaller delay between columns, so the wave "
    "crosses the chamber quickly -- a shorter apparent wavelength than "
    "'front to back'.",
)

add(
    "Focused - strongest in the middle",
    shaped(base(**{
        "Position 1": 40, "Position 2": 350,
        "Speed 1": 600, "Speed 2": 600,
        "Accel 1": 14000, "Accel 2": 14000, "Decel 1": 14000, "Decel 2": 14000,
        "Jerk 1": 5000, "Jerk 2": 5000,
    }), "Position 2", patterns.MIRROR, start=350, end=150),
    "Full stroke through the middle of the chamber, tapering towards the front "
    "and back. Concentrates the disturbance rather than spreading it evenly.",
)

add(
    "Tapered - largest at the back",
    shaped(base(**{
        "Position 1": 40, "Position 2": 350,
        "Speed 1": 600, "Speed 2": 600,
        "Accel 1": 14000, "Accel 2": 14000, "Decel 1": 14000, "Decel 2": 14000,
        "Jerk 1": 5000, "Jerk 2": 5000,
    }), "Position 2", patterns.RAMP, start=150, end=370),
    "Stroke grows steadily from the front of the chamber to the back, so the "
    "array pushes progressively harder along its length.",
)

# --- Depth ------------------------------------------------------------------

add(
    "Shallow water - front four columns",
    uniform(base(**{
        "Position 1": 140, "Position 2": 370,
        "Speed 1": 400, "Speed 2": 400,
        "Accel 1": 9000, "Accel 2": 9000, "Decel 1": 9000, "Decel 2": 9000,
        "Jerk 1": 3500, "Jerk 2": 3500,
    }), axes=range(12)),
    "Values for the front four columns only, low in the travel, for use when "
    "the tank is shallow. Select the front four columns before applying it.",
)


def write(name, rows, note):
    paths.ensure_directories()
    target = paths.PRESET_DIR / (name + ".csv")
    columns = ["Motor"] + params.PARAM_NAMES

    blank = dict((n, 0) for n in params.PARAM_NAMES)
    last_real = None

    with open(str(target), "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for axis in range(30):
            row = rows.get(axis)
            if row is None:
                writer.writerow([axis] + [blank[n] for n in params.PARAM_NAMES])
            else:
                last_real = row
                writer.writerow([axis] + [row[n] for n in params.PARAM_NAMES])
        fallback = last_real or blank
        writer.writerow(["All"] + [fallback[n] for n in params.PARAM_NAMES])
    return target, note


def main():
    written = []
    for name, (rows, note) in PRESETS.items():
        for row in rows.values():
            problems = params.validate_all(row)
            if problems:
                raise SystemExit("{0}: {1}".format(name, "; ".join(problems)))
        target, note = write(name, rows, note)
        written.append((name, note))
        print("wrote {0}".format(target.name))

    index = paths.PRESET_DIR / "ABOUT THESE PRESETS.txt"
    with open(str(index), "w", encoding="utf-8") as handle:
        handle.write(
            "Presets\n"
            "=======\n\n"
            "These are starting points, not calibrated recipes. There is no\n"
            "measured relationship yet between piston parameters and the wave\n"
            "that results, so each one is named for what the PISTONS do rather\n"
            "than for the wave it supposedly makes. Measure before you trust a\n"
            "name.\n\n"
            "Every value sits inside the limits in app/params.py, so the\n"
            "application will accept all of them.\n\n"
            "A row of zeroes for a piston means 'this piston was not part of\n"
            "the preset'; it falls back to the All row when applied.\n\n"
        )
        for name, note in written:
            handle.write("{0}\n{1}\n\n".format(name, "-" * len(name)))
            handle.write("  " + note.replace(". ", ".\n  ") + "\n\n")
    print("wrote {0}".format(index.name))


if __name__ == "__main__":
    main()
