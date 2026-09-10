# Wavemaker System Control

Control software for the UNC Fluids Lab wavemaker: thirty LinMot pistons driven
by an Allen-Bradley ControlLogix PLC, in three rows of ten.

- **Operators** — read [docs/OPERATING.md](docs/OPERATING.md).
- **Developers** — read [docs/DEVELOPING.md](docs/DEVELOPING.md).

## Running it

Double-click **`Open Wavemaker.cmd`**. That is the whole procedure.

It does not open Studio 5000 and does not wait for a keypress. The application
opens its own EtherNet/IP session to the controller; Studio 5000 "Go Online"
connects *Studio 5000* to the controller, not this application.

The one real prerequisite is that the controller is in **Run**, because the
ladder logic has to be scanning. That is persistent state -- it stays in Run
until somebody changes it -- so it is not a per-launch step. If the application
cannot reach the controller it says so at the top of Control Home and offers
**Reconnect** and **Open Studio 5000** buttons.

## Trying it without the machine

Double-click **`Mock Wavemaker (no machine).cmd`**. It runs a simulated
wavemaker: no PLC is contacted and nothing physical can move, so it is safe on
any laptop, anywhere.

The mock is not an empty shell. Its pistons really stroke between Position 1 and
Position 2 at the speeds you set, homing takes a moment, and a Curve Offset
staggered front to back really does produce a wave that travels along the
chamber, which you can watch in the live view. Use it to learn the interface and
to build presets before going near the tank.

It does **not** predict how the real machine behaves. It moves rectangles.

From a terminal:

```
py -3 main.py                # normal use: connect to the PLC
py -3 main.py --mock         # the simulated wavemaker
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
| **Operate** | Everything needed to run: choose pistons on the tank, set stroke and speed, press Start. Shows the pistons moving live while the machine runs. |
| **Preset Options** | Load saved parameters onto groups, or save the groups you have built. |
| **Feedback** | Everything the application has done. Also written to `logs/<date>.log`. |

### Groups

Selected pistons are simply "the pistons that will run" -- there is no step to
confirm them. If you need two lots of pistons running with different parameters
at the same time, press **Add group**; only then does the idea of a group appear
at all.

## Running it

1. **Click or drag on the tank** to choose pistons.
2. Set **stroke** and **speed**. Everything else is behind *All parameters*.
3. Press **Start**.

Start does whatever is needed: it writes any changed parameters, homes the
pistons if they are not homed, then runs. Homing physically moves every piston
and takes about a minute, so the first run asks before doing it. After that
Start is immediate.

**Stop** is always available, on every tab, and `Escape` does the same. After a
stop the pistons return to the bottom of their stroke; pressing Stop again while
they are moving there leaves them where they are.

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
