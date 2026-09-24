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

## Decisions still needed

- Decide whether the requested target is crest rise, crest-to-trough height, or two separate targets. Mark a repeatable measurement location along the tank.
- Obtain and inspect the old soliton spreadsheet, including units and how its acceleration values were measured.
- Confirm which stored PLC curves or trajectory commands are available for a one-off soliton pulse.
- Decide how the operator will extract observed height from the camera or other manual measurement before later automating the process.
