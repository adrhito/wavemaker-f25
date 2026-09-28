# Soliton trial validation on the offline lab computer

The Soliton tab commands a bounded, one-way lift of selected floor sections. It
does not prove that the water surface is a solitary wave or that the current
4,000 mm/s² deceleration is the fastest safe stop. Use measured trials and a
mechanical review before making either claim.

## Before a water trial

1. Have the lab operator identify which floor sections can move together and
   which selections could load a shared plate unevenly. Use an approved
   selection, keep the physical stop accessible, and ensure no other program
   is controlling the PLC.
2. Measure still-water depth. Mark a fixed station along the tank and its
   distance from the moving floor. Put a visible vertical scale at the station
   and keep the camera position fixed between repeated runs.
3. Rehearse **Stage floor**, **Fire one pulse**, **Stop**, and **Record observed
   wave** with the mock launcher. The mock has no water physics. Use the real
   launcher only with the lab operator present.
4. Begin with a small lift and low speed. The current caps are 120 mm lift,
   200 mm/s speed, and 4,000 mm/s² acceleration/deceleration. These software
   limits are starting bounds, not certified mechanical ratings.

## At the tank

1. Select the approved floor sections. Enter target crest rise, target width,
   measured depth, and the chosen floor lift.
2. Stage the floor. Confirm the selected sections have reached 370 mm and let
   the water settle before firing.
3. Start video before firing. The controller makes one upward move, slows into
   its endpoint, and holds the floor raised. Use **Stop** or the physical stop
   if motion is abnormal. Do not increase limits to work around an abnormality.
4. At the marked station, measure crest rise above still water, crest-to-trough
   height, and longitudinal full width at half the crest rise if visible.
   Record the observed heights in the app and put the width and its units in
   the notes. Repeat the same settings at least three times to assess
   repeatability.
5. Transfer `analytics/soliton-trials/*.json`, relevant logs, and original
   videos or images from the offline computer. Each JSON record separates the
   requested wave, motor command, actual endpoint readback, and manually
   observed heights. It contains no water-height sensor trace.

## Review before changing the motion

- Compare commanded and actual motor positions, following-error warnings,
  settling, rebound, flex, and impact. Inspect the assembly after trials.
- Compare observed height and width across repeats at the same depth and
  station. First-order solitary-wave theory ties crest rise to width at a
  fixed depth; an arbitrary pair is a target, not an exact theoretical wave.
- Agree on a machine-specific deceleration and jerk limit with the lab
  operator before editing `app/solitons.py`. A drive software limit is not a
  structural rating for the floor assembly.
- On the offline computer, inspect stored PLC curves with the read-only
  `tools/live_curve_probe.py`. A calculated time-varying floor trajectory
  requires a verified controller interface. Do not stream sample-by-sample
  position changes over the network as a substitute for controller motion.
