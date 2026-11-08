# How each number was obtained

Every value is in `geometry.json` with its range and a one-line source. This file gives
the working, how sure each number is, and what to measure next. Lengths are in metres
unless marked. The frame: x runs along the channel axis (0 at the channel mouth, plus
toward the reservoir), y across it, z up from the inside of the tank floor.

## Measure these next

In order of how much each would tighten the model:

| # | What | How | What it changes |
|---|---|---|---|
| 1 | **Does air vent on every stroke?** (not a tape measurement, but the biggest unknown) | 0.30 m of water, rows 1-3, 300 mm at 100 mm/s (jerk 2000): watch the bottom three outlets in the block's face at the channel mouth. A big burst of air on every downstroke means `leaky`; a small puff at most means `tight` (the swinging duct water can push the inner level the last 12 mm down to row 1's duct ceiling) | Up to 2.7x. That slow run: 63 mm first wave at 5 m (tight) against 23 mm (leaky) |
| 2 | **The ringing**: one row, one stroke, then film the water at the mouth for 10 s | Camera at x = 1 m. The model expects the duct water to swing at 0.9 Hz (row 1) for several seconds after the disc stops | Its period checks the duct mass, its decay fits `duct_zeta`. It shapes everything after the first wave, and the motor force |
| 3 | **The bore** | Tape round one cylinder: about 688 mm for the Sch 10S stainless on the stencil | Narrows the bore's -0.7/+2.4% band, so the volume per mm of travel |
| 4 | **Disc height at 0 mm** above the tank floor | Tank drained, piston at 0 mm: tape up the inside of a pipe from its bottom to the disc face, then add that pipe's bottom height | The length of trapped air, so the air spring and the ringing frequency (a few per cent). Now estimated from the LinMot geometry at 1.35 m |
| 5 | **Row 1's bottom** to the mm | Tape from the tank floor to the bottom of a row-1 cylinder (the top of its flange plate) | Every row's height, since the steps (3.02 in) are drawn. William's 5 in is "roughly"; the pads under the rails, about 1.6 in, are not in a readable file |
| 6 | **Channel window width** (one window, end to end, as for the head's 39.25 in) | Tape | The channel length (17.8 m now; the IIHR plan gives about 18.0 m between gate posts): when the gate's reflection returns. Capacity ±300 gal |

Also record the still-water depth of every run. It decides which rows reach the water,
and the model takes it as an input; it is never a constant.

## The piston bank

**The bank as drawn (BLUEPRINTS.md).** The design files show the piston bank as a block of
sheet-metal ducts with the cylinders standing on it, and IMG_2459 shows the same thing: the
"stacked plates" under the cylinders are the duct layers. Each cylinder sits on a flange
plate with an 8.25 in hole into its own elbow box. The elbow turns the water horizontal, and
straight duct runs it forward to the block's front face, at the channel mouth. There the 30
outlets open into the channel, 3 lanes by 10 layers.

* **Ducts:** 304 stainless, 0.059 in (0.060 nominal) sheet, 9.8 in wide and 3.000 in high
  outside, with a 0.020 in plate between layers. Inside they are 9.68 x 2.94 in (246 x 75
  mm, 184 cm²), about half the bore's area. So the water in a duct moves 1.91 times as
  fast as the disc. From the Mar 2015 STEP files and prints.
* **Pitch:** 9.800 in both ways: the ducts' width, the flange plate's hole centres, and the
  rails' bolt holes along the tank. The block is 3 x 9.8 = 29.4 in wide. It is 11 pitches
  long (2.738 m): the front row stands one pitch back from the face, because every duct
  starts in a two-pitch elbow.
* **Duct runs:** row n's duct runs (11.5 - n) pitches from its cylinder's axis to the
  face: 2.61 m for row 1 (the bottom layer) down to 0.37 m for row 10 (the top layer).
  From the duct-parts count: each row further back adds one pitch of straight duct.
* **Outlets:** row n's outlet spans from 3.0 in below its pipe bottom up to the top of its
  duct. Row 1's is 51-126 mm above the floor, row 10's 741-816 mm.

**Pipe and bore: from the stencil and the standard.** IMG_2458 shows the pipe stencil:
"FELKER USA WELD 8" SCH 10 S TP 304/304L ASTM A-312/ASME SA-312". ASME B36.19M gives
NPS 8 Sch 10S as OD 8.625 in (219.1 mm) and wall 0.148 in (3.76 mm), so the bore is
8.329 in (211.6 mm). William confirmed that a disc with a rubber seal moves inside the
pipe and the pipe does not move. The disc face is therefore the bore: 351.5 cm², or
0.0351 L per mm of travel. ASTM A999 tolerances (OD +1.6/-0.8 mm, wall no more than
12.5% under) allow a bore of 210.8-214.1 mm, so the area is good to -0.7%/+2.4%.

The design archive drew the cylinders in PVC (8 in DWV pipe, then a Grind All prototype
with an 8.050 in bore, Jan 2015). That design is outdated: William, 7 Nov, the built
cylinders are metal, stainless as far as he knows, as the stencil says. Nothing about the
cylinders, their flanges, lengths or seals is taken from it (BLUEPRINTS.md lists those
files). The seal is a rubber seal on the disc (William); its type is not known.

**Pipe tops and stepped bottoms.** Row 1's pipe is about 51 in, with its bottom about 5 in
above the floor, and the tops are level at 56 in (1422 mm) (William, 26 Oct, "roughly").
The step is one layer of the stainless duct block, 3.02 in (drawn), against William's
rounded 3 in. So the bottoms are at 5 + 3.02(n - 1) in, and each pipe runs from there up
to 56 in:

| row | pistons | pipe (in) | bottom (in) | bottom (mm) | outlet (mm) | duct to the face (m) |
|---|---|---|---|---|---|---|
| 1 | 1-3 | 51.0 | 5.00 | 127 | 51-126 | 2.61 |
| 2 | 4-6 | 48.0 | 8.02 | 204 | 128-202 | 2.37 |
| 3 | 7-9 | 45.0 | 11.04 | 280 | 204-279 | 2.12 |
| 4 | 10-12 | 41.9 | 14.06 | 357 | 281-356 | 1.87 |
| 5 | 13-15 | 38.9 | 17.08 | 434 | 358-432 | 1.62 |
| 6 | 16-18 | 35.9 | 20.10 | 511 | 434-509 | 1.37 |
| 7 | 19-21 | 32.9 | 23.12 | 587 | 511-586 | 1.12 |
| 8 | 22-24 | 29.9 | 26.14 | 664 | 588-662 | 0.87 |
| 9 | 25-27 | 26.8 | 29.16 | 741 | 664-739 | 0.62 |
| 10 | 28-30 | 23.8 | 32.18 | 817 | 741-816 | 0.37 |

Under the block the drawings have a 0.059 in base plate on two offset-tee rails (6 in
flange, 4 in web, 0.25 in thick, 104 in long), which stand on support pads. Only the pads'
thickness is missing from a readable file (`Frame/SupportPads.dwb`), and William's 5 in
for row 1 implies about 1.6 in. "Roughly" is carried as `bottom_offset`, ±40 mm on every
bottom; calibration can fit it once there are runs at several depths. Row n is taken to
be display pistons 3n-2..3n, the application's column n (not confirmed).

**Which end is which: William, 28 Oct, and the drawings.** The longest pipes (row 1) stand
at the head end and the shortest (row 10) at the channel mouth. The drawings require it:
the outlets are on the block's tall end, and row 10's elbow is the top layer at the face.

**The block walls off the channel.** Below each step the block is solid boxes, and its
front unit and row 10 stand 817 mm high across the channel's whole width. In water shallower
than that, nothing passes between the channel and the head except through the ducts
themselves. The pistons' water all goes down the channel, which roughly doubled the
predicted first wave. Over a step that is under water, a layer of water runs between the
cylinders (39% of the footprint is open on the 9.8 in pitch). The thin gap under the base
plate (about 2 in, between the rails) is left out.

**Disc position: from the control software and the motor.** wavemaker-f25 `app/params.py`:
positions run from -20 (top) to 370 mm (bottom), and down pushes water out. The drive's own
limits are -57 to 453 mm. That is the LinMot's 510 mm extended stroke, which runs from 25
mm inside the stator's front face to 485 mm out of it, shifted 32 mm. `Model.py`
`PARK_POSITION = 370`: after every stop the pistons rest at the bottom of their travel, so
a run lifts them to Position 1 before it strokes. The stators stand on the square plates on the
pipe tops with the slider coming out downward. At 0 mm the slider's end is then about
1.39 m above the floor, and the disc face a few cm below that. The model takes **1.35 m**
(range 1.28-1.40; 1.30 m was assumed before 7 Nov). It is still an estimate: tape it.

## Trapped air (model, not measured)

With a sealed disc, the space between the disc and the water is closed air.
`wavemodel/air.py` balances the air pressure against the water at the inner surface:
p = p0 + rho g (h - z_in). Air was trapped at atmospheric pressure when the rising
water passed the pipe bottom (the duct's ceiling) with the disc parked. During a run p L^1.4
is held fixed, except when the inner water reaches the pipe bottom (venting, along the duct
and out of the outlet) or, with a leaky seal, when the air falls below atmospheric (air
leaks in). Consequences, at 0.30 m with the discs parked:

| row | under water | air below disc | water in pipe | water follows disc (slowly) | water mass (pipe + duct) | rings at |
|---|---|---|---|---|---|---|
| 1 | 173 mm | 840 mm | 13 mm | 94.6% | 183 kg | 0.94 Hz |
| 2 | 96 mm | 770 mm | 7 mm | 95.0% | 166 kg | 1.02 Hz |
| 3 | 20 mm | 698 mm | 1 mm | 95.4% | 149 kg | 1.13 Hz |

The water mass is the water in the pipe plus the whole duct, referred to the inner
surface: rho A^2 (duct + 0.1 m) / A_duct, about 185 kg for row 1. With the air as the
spring, k = A (n p / L + rho g), the pair rings at sqrt(k / M) / 2 pi. Rows 1-3 ring near
1 Hz, and rows further forward faster (row 8 at 2.5 Hz at 0.70 m), because their ducts are
shorter and their air columns shorter. That is inside the band the strokes drive, so
`predict` integrates the swing in time (`air.pipes_dynamic`). Its damping comes from the
duct's losses (exit, the elbow's turn, friction: K about 2.8), the waves the outlets
radiate, and an extra damping ratio `duct_zeta` = 0.05 (estimated; fittable).

`wm.py volumes --depth <m>` prints the first table for any depth, `wm.py pistons --depth <m>`
the second.

## Head, channel and reservoir

| dimension | value | range | how |
|---|---|---|---|
| channel width | 0.74295 | 0.740-0.746 | **tape**, William 28 Oct: 29.25 in inside, wall to wall where the water is. Replaces the 26.5 in in wavemaker-f25 `app/solitons.py` |
| channel length | 17.774 | 17.55-18.10 | **count and beams from William (28 Oct), window width assumed.** Each side has 16 windows. The 15 beams between them alternate 4, 5, 4, ... 4 in (67 in), starting with 4 in at the head-end corner and ending at the corner with the reservoir. The window width is taken as the head's measured 39.25 in. The photos' post spacing (1.07 m, range 1.00-1.15) agrees with the 1.11 m that implies. Corners read like the head's: 16 x 39.25 + 67 + 2 x 2 3/8 = 699.75 in. Replaces the perspective fit of IMG_2468 (15.4 m), whose far end was always the least sure part. The IIHR lab plan (sheet 1), scaled by its own dimension chain, gives about 711 in (18.06 m) between the gate posts at either end |
| channel rim (inside depth) | 1.1525 | 1.13-1.17 | taken equal to the head's drawn rim (IMG_2460/2462 show one rim line round the tank; only the wide basin's sections are in the archive). Before 7 Nov: the head's 42.5 in window height (1.08 m), and before 28 Oct the photo estimate 1.00 m |
| frame module (post spacing) | 1.07 | 1.00-1.15 | **photogrammetry**, IMG_2461/2465, from the posts' floor contact points. Superseded by the measured beams; kept as the check on the channel's assumed window width |
| lid panel | 0.753 | 0.72-0.78 | **photogrammetry**, IMG_2468, the spacing of the glass lid joints |
| head width (across) | 3.0416 | 3.035-3.048 | **tape**, William 28 Oct. The back face (the wall behind the pistons, opposite the channel) is 125 in end to end over the corner beams. Each steel beam's face is 5 in: a 2 3/8 in flange either side of a 1/4 in web. Inside, at the water: 125 - 2 x 2 3/8 - 2 x 1/4 = 119.75 in |
| back wall | 3 windows, 4 beams | | **William, 28 Oct**: four beams including the corners, three windows between them. Equal windows: (125 - 4 x 5) / 3 = 35 in clear, so the bay is 40 in |
| head length (along the axis) | 8.909 | 8.79-8.93 | **tape**, William 28 Oct. Each long side has 8 windows, 39.25 in wide end to end, with 7 beams between them alternating 5, 4, 5, 4, 5, 4, 5 in (32 in). That is 346 in between the corner beams. The corners are not measured yet; taking them like the back wall's (the inner 2 3/8 in flange inside the water face) gives 350.75 in. The photos had suggested 5 bays (5.07 m) because IMG_2461 shows only half of the side |
| head rim (inside depth) | 1.1525 | 1.13-1.17 | **IIHR sheet 4** (typical wide and shallow basin section): 5'-3 7/8" from the lab floor to the "Max Depth" line, the rim 5" above it, the floor 1'-11 1/2" up: 45.4 in. The 42.5 in William taped is the glass window inside the frame |
| head maximum water depth | 1.0255 | | **IIHR sheet 4**: "Max Depth", 40.4 in above the floor, 5 in below the rim |
| reservoir length | 5.6 | 4.6-6.8 | **photogrammetry**, IMG_2466: face length / height above rim = 3.12, with the height from IMG_2468 |
| reservoir width | 0.75 | 0.67-0.95 | **photogrammetry**, IMG_2468: the end face is about 1.0 m outside the frame; inside assumed close to the channel |
| reservoir height | 2.9 | 2.5-3.4 | IMG_2468: top 1.8 m above the channel rim. UNC Research quotes a "three-meter water column" |
| tank floor above lab floor | 0.597 | 0.59-0.61 | **IIHR sheets 4, 13, 16**: 1'-11 1/2". Confirms the 0.60 m photogrammetry of IMG_2465 |

The channel leaves from the exact middle of the head's channel-side wall, on the bank's
axis (William). The bank ends at the channel mouth (operator's description).

## Capacity check against the published figure

UNC publishes 13,500 gal, 118-120 ft overall, and 70,000 lb. At the values above:

* filled to the drawn maximum depth (1.026 m) with the reservoir full: head + channel
  40.7 m³ + reservoir 12.2 m³ = 52.9 m³ = **14,000 gal, 1.03 of the published figure**;
* filled to the brim (1.153 m): 57.9 m³ = 15,300 gal, 1.13 of it.

So the published capacity is most likely the tank at its maximum depth, reservoir
included. The reservoir (photo-only) and the channel's assumed window width are the
likeliest to be a little large. Drawing every ranged dimension uniformly across its range
gives 14,600-16,800 gal to the brim (5th to 95th percentile). The water in the cylinders
is almost nothing, since they hold air. The duct block's interior is water wherever it is
submerged, so it barely changes the volume.

Before 7 Nov the rim was taken as the 42.5 in window height, and the brim total was
14,500 gal.

The 36 m published length against the model's 32.3 m (head + channel + reservoir)
probably includes the platforms and stairs at both ends (IMG_2460, IMG_2462). The IIHR plan
agrees: the whole installation is about 122 ft over its end structures. The model does
not use it.

## Piston motion

**The motors: LinMot P01-48x360F/360x510** (stator PS01-48x360F-C, slider PL01-28x800/720;
Scott Equipment quote 10033082, Aug 2014; LinMot data sheet 0185-1005):

| | |
|---|---|
| stroke | 360 mm at full force (50-410 mm out of the stator), 510 mm in all, falling to 79% force at the ends; the drive's -57..453 mm is that 510 mm |
| force | 1020 N peak at 72 V (905 N at 48 V); 200 N continuous without cooling, 340 N with the HV01 fan; thermal time constant 1000 s (340 s with fan) |
| speed | 2.0 m/s at 72 V (1.3 m/s at 48 V); the application allows 0.9 m/s |
| current | 32 N/A; 31.9 A peak, 6.2 A continuous (11 A with fan) |
| parts | stator 48 mm x 410 mm, 2.88 kg; slider 28 mm x 800 mm, 3.56 kg; repeatability ±0.05 mm |
| sizing | the 2014 sizing sheet asked for 440 N at 1 Hz on a 300 mm stroke: 1100 W peak, 480 W mean per motor |

The drives' supply voltage on the machine today is not recorded here; the quote was for
an E1200 drive on a 72 V supply.

**Profiles, from wavemaker-f25 `app/params.py`.** Speed up to 900 mm/s and acceleration
up to 20,000 mm/s². The profiles are 0 Trapezoidal, 1 Bestehorn, 2 S-curve and 3 Sine.
The lab's fast settings are accel 20,000 and jerk 7,500 (`app/solitons.py`). The
Bestehorn profile's effective jerk is the set jerk times 1.249, fitted so the model
reproduces the 3.834 s cycle measured in `BIGGER WAVE.csv` (0-350 mm, 500 mm/s,
10,000 mm/s², jerk 2,000). With jerk 7,500 the peak speed is jerk-limited: 155, 246, 323,
391, 453, 512 and 567 mm/s for strokes of 50 to 350 mm, whatever higher speed is
commanded. The peak acceleration is then about 1.8 m/s², so the 20,000 mm/s² setting never
binds.

**What a stroke asks of a motor** (`wm.py pistons --stroke`, and every `predict`):
F = m a - m g + A (p - p0). The moving mass is about 6.5 kg (est.: the slider's 3.56 kg and a
disc of unknown mass), so holding a disc takes
64 N and a dry row could accelerate at 147 m/s². For a wet row, the air pressure is what
counts. A fast stroke builds up to 15-22 kPa under the disc as the duct water lags, and
that is 500-750 N. Across the sweeps (0.30-0.70 m, strokes 50-350 mm, 100-900 mm/s) no
run needs more than 742 N, within the 1020 N. But a fast run of 200 mm or more averages
about 200-290 N rms per wet row while moving, above the 200 N continuous rating without a
fan, so long runs at those settings will heat the motors. Short fast strokes near the
rows' ring frequency do it too: 50 mm at 0.70 m averages 211 N. Dry rows need only about
75 N.

## Photos

`photos/originals/IMG_2458-2468.heic` (exported from Photos, 26 Oct). `photos/jpg/` has
JPEG copies. What each one gave:

* IMG_2458: pipe stencil.
* IMG_2459: the bank, side plates, level pipe tops.
* IMG_2460: head, platform, overall layout.
* IMG_2461: head long side, frame modules.
* IMG_2462: bank through the glass, stairs.
* IMG_2463: a corner of the head and its fill/drain plumbing (no dimension taken).
* IMG_2464-2465: the channel's side, rim and floor height.
* IMG_2466: the reservoir's face.
* IMG_2467: looking up at the overhead platform (no dimension taken).
* IMG_2468: down the channel: its length, the lid joints, the reservoir height.
