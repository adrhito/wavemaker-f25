# Water-height calibration: discovery notes

These notes record the starting point for a feature that accepts a desired water-wave height and, after a trial, records the observed height. The intended first measurement workflow is manual: a person supplies the observed height. The lab has cameras, but no camera tracker or wave-height sensor is installed yet. The wavemaker runs on an offline Windows 7 computer; the development Mac cannot drive or measure it.

## What the software does today

- The Wave tab's **Height** value is piston stroke in millimetres, not measured water-surface height. `app/waves.py` converts it to motor positions, and converts period to motor speed. `wave/WaveDesigner.py` sends those parameters to selected pistons.
- `Model.py` can record commanded and actual *motor positions* during a run. That log does not contain water height.
- `Presets/*.csv` store commanded motor positions, speeds, acceleration, deceleration, and curve settings. Their values are starting recipes, not measured relationships between piston motion and water height. `tools/make_presets.py` says this explicitly.
- The current PLC interface sets fixed motion parameters and a `Curve ID`, then can assert `Run_Curve`. The application does not upload an arbitrary time-varying piston trajectory. Before designing soliton control, inspect the controller's stored curves and confirm what trajectory interface the lab PLC actually supports. `tools/live_curve_probe.py` is a read-only inspection aid for the lab computer.

## Historical-data search

The current repository and all its reachable Git history contain no Excel workbook (`.xls`, `.xlsx`, `.xlsm`, or `.ods`) and no soliton-labelled file. Two older repositories linked by its archived material, [PavanAkkineni/wavemaker](https://github.com/PavanAkkineni/wavemaker) and [ggabbylopez/WaveMakerFA23](https://github.com/ggabbylopez/WaveMakerFA23), likewise contain no such workbook in their reachable histories.

The six files under `analytics/` from 2022 record motor demand and actual positions, not directly measured acceleration or water height. The older CSV presets contain commanded `Accel 1/2` and `Decel 1/2` values; they do not identify a soliton trial or its measured wave. The sought spreadsheet must be supplied from outside these repositories before it can be assessed or imported.

## First calibration record

Record desired and observed water heights as distinct values. The lab wants to record both observed crest rise above still water and observed crest-to-trough height. Still-water depth can change, so record it for every trial. Also retain the measurement location, wave type, all selected pistons and their parameter values, run mode, time, and a reference to raw observation notes or images. Keep units explicit and convert to one internal unit. Repeated trials under the same conditions will reveal how repeatable a setting is.

Until measured trials establish a usable range, a desired water height is a *target*, not a guarantee. The UI should not present a piston stroke as the resulting water-wave height or silently extrapolate beyond measured conditions. Solitary waves need their own calibration and control path rather than being treated as one of the existing periodic Wave-tab shapes.

## What the stored PLC curves actually are

Answers part of "confirm which stored PLC curves or trajectory commands are
available for a one-off soliton pulse", below, without needing the machine.
Read out of the archived drive configuration on 26 September 2026, from
`wavemaker all files/LinMot Drive Config/c1250_Drive_1_Config.lmc` (dated
August 2018). **Unverified against the drives as they stand today** --
`tools/live_curve_probe.py` is what confirms it, and it has never been run.

Drive 1's curve memory holds exactly two curves:

| Curve ID | Name | What it is |
|---|---|---|
| 1 | `scurve out` | A smooth monotone position ramp, 30.0 mm to 100.0 mm, S-curve wizard, `XLength 100000` |
| 2 | `scurve in` | The same reversed, 100.0 mm back to 30.0 mm |

Two things follow, and both matter for a solitary wave.

- **A monotone single push is exactly the right primitive.** A solitary wave
  from a piston wavemaker wants one smooth one-way stroke whose velocity
  history is a single bell -- not an oscillation. Curve 1 is that stroke. The
  drive's own `Curve Offset` is a real per-piston delay in hundredths of a
  second during a curve run, which is the **only** per-piston timing this
  machine has; a continuous run ignores it entirely and the application fakes
  phase with staged starting positions instead.
- **The amplitude is fixed at 70 mm** before `Amplitude Scale` is applied,
  against a 390 mm envelope. How `Amplitude Scale` maps onto that span, and
  what it will accept, is not documented here and needs reading at the drive.

`c1250_Drive_2_Config.lmc` from the same folder has **no curves at all**. Curve
memory is per-drive, so there is no reason to assume all thirty drives are
loaded, and every reason to check before a design depends on it. Loading a
curve is a LinMot-Talk operation, drive by drive; this application cannot do it.

### The lab already tried this once

`wavemaker all files/WaveMaker Programs/Python for Wavemaker/Wavemaker_KdVSoliton.csv`
sets, identically on all thirty pistons: `Curve ID 1`, `Time Scale 150`,
`Amplitude Scale 100`, `Profile 1`, `Position 1 -20`, `Position 2 350`,
`Speed 1 200`, `Speed 2 0`, and **`Curve Offset 0`**.

So it played `scurve out` time-stretched to 150% on every piston at once. The
shape was right and the sequencing was simply never done: with `Curve Offset 0`
the whole bank fires simultaneously, which releases a hump rather than driving a
travelling disturbance, and a released hump fissions into a train of solitons
instead of producing one. It also drove all thirty pistons, including the ten
columns that ride clear of a low surface.

## What the Soliton tab now does

Built 26 September 2026. It takes a still-water depth and a desired crest rise,
derives the celerity, width, push and per-column stagger from the KdV solitary
wave, and writes them to the submerged pistons as a curve run. It answers the
note above about not presenting piston stroke as water height: the tab shows
both numbers separately, calls the height a target, and carries a Calibration
multiplier for the operator to turn once a trial has been watched.

It does **not** close the calibration question. No trial has been run and no
water height has been measured, so the multiplier starts at 1.0 with nothing
behind it. The first measured trial is still the thing that turns these numbers
from a recipe into a prediction.

## Decisions still needed

- Decide whether the requested target is crest rise, crest-to-trough height, or two separate targets. Mark a repeatable measurement location along the tank.
- Obtain and inspect the old soliton spreadsheet, including units and how its acceleration values were measured.
- Confirm which stored PLC curves or trajectory commands are available for a one-off soliton pulse.
- Decide how the operator will extract observed height from the camera or other manual measurement before later automating the process.
