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
   distance from the moving floor. For width from video timing, mark a second
   station downstream and measure the distance between the marks. Put a visible
   vertical scale at the first station and keep the camera position fixed
   between repeated runs.
3. Rehearse **Stage floor**, **Fire one pulse**, **Stop**, and **Record observed
   wave** with the mock launcher. The mock has no water physics. Use the real
   launcher only with the lab operator present.
4. Begin with a small lift and low speed. The current caps are 120 mm lift,
   200 mm/s speed, and 4,000 mm/s² acceleration/deceleration. These software
   limits are starting bounds, not certified mechanical ratings.
5. Choose one marked longitudinal station and enter its distance from the
   floor array in the Soliton tab. Use that same mark for every repeated run.

## At the tank

1. Select the approved floor sections. Enter target crest rise, target width,
   measured depth, and the chosen floor lift.
2. Stage the floor. Confirm the selected sections have reached 370 mm and let
   the water settle before firing. Fire refuses a section that reads more than
   2 mm from its staged start. Before each stage or pulse, the app also checks
   that the PLC's selected pistons match the trial. A mismatch means another
   session may have changed the shared selection; stop that session and stage
   again. This preflight does not provide exclusive control of the PLC.
3. Start video before firing. The controller makes one upward move, slows into
   its endpoint, and holds the floor raised. Use **Stop** or the physical stop
   if motion is abnormal. Do not increase limits to work around an abnormality.
4. At the marked station, measure crest rise above still water, crest-to-trough
   height, and longitudinal full width at half the crest rise if visible. If
   width cannot be measured directly from a scaled image, note when the same
   crest passes each station and how long the surface stays above half the
   crest rise at the first station. Enter the station spacing and both times
   in the observation dialog; the app calculates width as spacing times
   half-height duration divided by crest travel time and saves the raw values.
   Use timings from one video or synchronized cameras. A breaking or reflected
   crest, or a crest that changes substantially between marks, makes this
   estimate unreliable; document that instead of treating it as calibration.
   Repeat the same settings at least three times to assess repeatability.
5. Transfer `analytics/soliton-trials/*.json`, relevant logs, and original
   videos or images from the offline computer. Each JSON record separates the
   requested wave, exact motor parameters, pulse outcome, start and final
   motor readback, application-side Run_1 assertion, endpoint confirmation and
   run-bit cleanup times, and
   manually observed heights, including interrupted runs. It contains no
   water-height sensor or motor-velocity trace. The timing window is measured
   by the app, not the PLC; a separate Stop may clear the run bit sooner than
   the pulse worker's final cleanup. Align video with a visible event or a
   synchronized clock before comparing times.

## Review before changing the motion

- Compare commanded and actual motor positions, following-error warnings,
  settling, rebound, flex, and impact. Inspect the assembly after trials.
- Compare observed height and width across repeats at the same depth and
  station. First-order solitary-wave theory ties crest rise to width at a
  fixed depth; an arbitrary pair is a target, not an exact theoretical wave.
- The **Apply measured lift** suggestion appears only after three consistent
  measured runs at each of two lifts with the same setup and a width near the
  target. It interpolates between their observed heights. Review the source
  trial files and test any suggested lift as a new experimental run.
- Agree on a machine-specific deceleration and jerk limit with the lab
  operator before editing `app/solitons.py`. A drive software limit is not a
  structural rating for the floor assembly.
- On the offline computer, inspect stored PLC curves with the read-only
  `tools/live_curve_probe.py`. A calculated time-varying floor trajectory
  requires a verified controller interface. Do not stream sample-by-sample
  position changes over the network as a substitute for controller motion.
