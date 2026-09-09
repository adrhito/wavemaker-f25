# Wavemaker System Control

Control software for the UNC Fluids Lab wavemaker: thirty LinMot pistons driven
by an Allen-Bradley ControlLogix PLC, in three rows of ten.

- **Operators** — read [docs/OPERATING.md](docs/OPERATING.md).
- **Developers** — read [docs/DEVELOPING.md](docs/DEVELOPING.md).

## Running it

Double-click **`Open_Wavemaker_GUI.cmd`**. It opens the Studio 5000 project,
waits while you go online and switch to Rem Run, then starts the interface.

From a terminal:

```
py -3 main.py                # normal use: connect to the PLC
py -3 main.py --simulate     # training/development: never touches the machine
py -3 main.py --ip 10.0.0.5  # a different PLC address
```

If the PLC does not answer, the application says so in the banner at the top of
Control Home and runs in simulation instead of failing to start. **Nothing moves
in simulation.**

## What you need

- Windows with Python 3.8 or newer (`py -3 --version` should print a version).
- `tkinter`, which comes with the standard Windows Python installer.
- Optional: `pip install -r requirements.txt` for MongoDB analytics storage.
  Without it, analytics are still written to `analytics/<date>.txt`.

The PLC library (pylogix 0.2.0) is vendored in `modules/`; there is nothing to
install for it.

## How the interface is laid out

| Tab | What it is for |
|---|---|
| **Control Home** | Prepare, start, stop. Shows which pistons are in which set, and whether you are connected to the real machine. |
| **Define Motors** | Tick pistons, group them into **sets**, and give each set its own parameters. |
| **Preset Options** | Load saved parameters from `Presets/*.csv` onto chosen sets, or save the sets you have built. |
| **Feedback** | Everything the application has done. Also written to `logs/<date>.log`. |

### Motor sets

A **set** is a group of pistons that run together with the same parameters.
Different sets can have different parameters and run at the same time — that is
the point of them. A piston belongs to at most one set; delete the set to free it.

## The three steps

1. **Define Motors** — tick pistons, press *Create Set from Selection*, then fill
   in that set's parameters. Repeat for as many sets as you need.
2. **Control Home** — press *Prepare Motor(s)*. This writes your parameters to the
   PLC and homes every piston. Wait for "Motors homed and ready to run."
3. **Control Home** — choose *One stroke* or *Continuous* and press
   *Start Motor(s)*, or press *Start Curve*.

*Stop Motor(s)* works at any time, including while the machine is preparing.

## Folders

```
main.py             entry point
Model.py            application state and every machine command
Motor.py            one piston: its parameters and drive state
View.py             the window; routes worker callbacks onto the Tk thread
app/                paths, PLC tag names, PLC transport, parameter definitions
control_home/       Control Home tab
define_motors/      Define Motors tab
preset_options/     Preset Options tab and the preset file reader/writer
feedback/           Feedback tab
modules/            vendored pylogix, logging, tooltips
tests/              test suite; runs with no hardware
Presets/            saved parameter files
logs/  analytics/   written at run time
archive/            superseded documentation and the old test scripts
```

## Licence

See `LICENSE`. Built on pylogix by Burt Peterson, maintained by Dustin Roeder.
