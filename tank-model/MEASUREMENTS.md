# How each number was obtained

Every value is in `geometry.json` with its range and a one-line source. This file gives
the working, how sure each number is, and what to measure next. Lengths are in metres
unless marked. The frame: x runs along the channel axis (0 at the channel mouth, plus
toward the reservoir), y across it, z up from the inside of the tank floor.

## Measure these next

In order of how much each would tighten the model:

| # | What | How | What it changes |
|---|---|---|---|
| 1 | **Which end row 10 is at** (the longest pipes, lowest bottoms). Are pistons 28-30 in it? | Look at the pipe bottoms through the glass, or ask whoever built it | Wave heights by 10-40%. The same stroke gives a 202 mm first wave with row 10 at the channel end and 287 mm with it at the head end (all 30, 350 mm, 0.70 m). `results/row10_end_effect_900mms.png` |
| 2 | **Does air vent on every stroke?** (not a tape measurement, but the biggest unknown) | 0.30 m of water, rows 8-10, 300 mm stroke: watch the row-10 pipe bottoms. Bubbles on every downstroke mean the seal lets air in (`--seal leaky`). None after the first stroke means `tight` | Up to 2.6x. All 30 at 0.30 m with a 350 mm stroke: 118 mm first wave (tight) against 45 mm (leaky) |
| 3 | **Rim height**: inside floor to the top of the glass, in the head and in the channel | Tape. Now taken from the head's 42.5 in window height, and the channel assumed the same | Capacity 12,800-14,000 gal across its range. Also checks that the pipe tops (1422 mm) stand about 0.42 m above the rim, as IMG_2459 suggests |
| 4 | **Channel length**: mouth (inside face of the head's channel-side wall) to the gate | Tape along the rim | When the gate's reflection gets back to each station (15.0 s at 5 m in 0.30 m water, ±1 s across the range): the window for measuring clean incident waves. Capacity only ±120 gal |
| 5 | **Head corners**: the four corner beams of the head (face width, and how much of each sits inside the water face) | Tape, as for the back wall | The head's inside length (8.79-8.93 m between the two ways of reading the corners). Small: under 1% on capacity, nothing on the first wave |
| 6 | **Disc height at 0 mm** above the tank floor | Tank drained, piston at 0 mm: tape up the inside of a pipe from its open bottom to the disc face, then add that pipe's bottom height | Less than 1% on wave height (it only sets the length of trapped air); low priority. It also shows whether the disc can ever sit below the water |

Also record the still-water depth of every run. It decides which rows reach the water,
and the model takes it as an input; it is never a constant.

## The piston bank

**Pipe and bore: from the stencil and the standard.** IMG_2458 shows the pipe stencil:
"FELKER USA WELD 8" SCH 10 S TP 304/304L ASTM A-312/ASME SA-312". ASME B36.19M gives
NPS 8 Sch 10S as OD 8.625 in (219.1 mm) and wall 0.148 in (3.76 mm), so the bore is
8.329 in (211.6 mm). William confirmed that a disc with a rubber seal moves inside the
pipe and the pipe does not move. The disc face is therefore the bore: 351.5 cm², or
0.0351 L per mm of travel. ASTM A999 tolerances (OD +1.6/-0.8 mm, wall no more than
12.5% under) allow a bore of 210.8-214.1 mm, so the area is good to -0.7%/+2.4%. A tape
round the outside reading about 688 mm would confirm it.

**Pipe tops and stepped bottoms: William, 26 Oct ("roughly").**
* Each row of three has the same pipe. Row n is 21 + 3n inches long: row 1 is 24 in,
  row 10 is 51 in.
* The tops are level. Row 10's bottom is about 5 in above the floor, so the tops are
  56 in = 1422 mm up.
* Bottoms are at 5 + 3(10 - n) in:

| row | pistons | pipe (in) | bottom (in) | bottom (mm) |
|---|---|---|---|---|
| 1 | 1-3 | 24 | 32 | 813 |
| 2 | 4-6 | 27 | 29 | 737 |
| 3 | 7-9 | 30 | 26 | 660 |
| 4 | 10-12 | 33 | 23 | 584 |
| 5 | 13-15 | 36 | 20 | 508 |
| 6 | 16-18 | 39 | 17 | 432 |
| 7 | 19-21 | 42 | 14 | 356 |
| 8 | 22-24 | 45 | 11 | 279 |
| 9 | 25-27 | 48 | 8 | 203 |
| 10 | 28-30 | 51 | 5 | 127 |

IMG_2459 agrees with the level tops: about two pipe diameters (0.42 m) of pipe stand
above the deck plates at the rim. "Roughly" is carried as `bottom_offset`, ±40 mm on
every bottom; calibration can fit it once there are runs at several depths. Row n is
taken to be display pistons 3n-2..3n, the application's column n. **Which end row 10 is
at is assumed**: the channel end, so the bottoms form a ramp falling toward the channel.
It is a parameter until William confirms it.

**Pitch: inferred.** Across the tank, 673/3 = 224.3 mm: the three pipes fill the
channel width, and the square top plates in IMG_2459 tile the bank. Along the tank the
pitch is assumed to be the same (square plates), which makes the bank 2.243 m long. Round
pipes on that square pitch leave 25% of the bank's footprint as gaps; the model treats
the gaps as open water with a free surface.

**Side plates: IMG_2459.** Stacked steel plates close the long sides of the bank, so
water under it leaves only through its ends: toward the channel and back into the head.
This can be switched off with `duct_side_walls`.

**Disc position: from the control software.** wavemaker-f25 `app/params.py`: positions
run from -20 (top) to 370 mm (bottom), and down pushes water out. The drive's own limits
are -57 to 453 mm. `Model.py` `PARK_POSITION = 370`: after every stop the pistons rest at
the bottom of their travel, so a run lifts them to Position 1 before it strokes. The
disc's height at 0 mm is **assumed** to be 1.30 m above the floor. It must lie between
about 1.25 m (at 453 mm the disc is still inside row 1's 813 mm-bottom pipe) and 1.36 m
(at -57 mm it is still below the 1422 mm top).

## Trapped air (model, not measured)

With a sealed disc, the space between the disc and the water is closed air.
`wavemodel/air.py` balances the air pressure against the water at the inner surface:
p = p0 + rho g (h - z_in). Air was trapped at atmospheric pressure when the rising
water passed the pipe bottom with the disc parked. During a run p L^1.4 is held fixed,
except when the inner water reaches the pipe bottom (venting) or, with a leaky seal,
when the air falls below atmospheric (air leaks in). Consequences, at 0.30 m with the
discs parked:

| row | under water | air below disc | water in pipe | water follows disc |
|---|---|---|---|---|
| 8 | 21 mm | 649 mm | 1 mm | 95.7% |
| 9 | 97 mm | 720 mm | 6 mm | 95.3% |
| 10 | 173 mm | 791 mm | 12 mm | 94.9% |

`wm.py volumes --depth <m>` prints this table for any depth.

## Head, channel and reservoir

| dimension | value | range | how |
|---|---|---|---|
| channel width | 0.673 | 0.665-0.680 | **tape**: 26.5 in, measured by the operator (wavemaker-f25 `app/solitons.py`) |
| channel length | 15.4 | 14.8-16.2 | **photogrammetry**, IMG_2468. The rim width (known: 673 mm + glass) was fitted along the perspective (horizon at row 498, camera pitch 43°) from the platform edge to the reservoir face. The glass lid joints come out evenly spaced at 0.75 m, which checks the fit |
| channel rim (inside depth) | 1.0795 | 0.98-1.18 | assumed equal to the head's 42.5 in window height, since IMG_2460/2462 show one rim line round the tank. The photo estimate before 28 Oct was 1.00 m (IMG_2465/2464: the glass between the rails is 0.68 of the camera height) |
| frame module (post spacing) | 1.07 | 1.00-1.15 | **photogrammetry**, IMG_2461/2465, from the posts' floor contact points |
| lid panel | 0.753 | 0.72-0.78 | **photogrammetry**, IMG_2468, the spacing of the glass lid joints |
| head width (across) | 3.0416 | 3.035-3.048 | **tape**, William 28 Oct. The back face (the wall behind the pistons, opposite the channel) is 125 in end to end over the corner beams. Each steel beam's face is 5 in: a 2 3/8 in flange either side of a 1/4 in web. Inside, at the water: 125 - 2 x 2 3/8 - 2 x 1/4 = 119.75 in |
| back wall | 3 windows, 4 beams | | **William, 28 Oct**: four beams including the corners, three windows between them. Equal windows: (125 - 4 x 5) / 3 = 35 in clear, so the bay is 40 in |
| head length (along the axis) | 8.909 | 8.79-8.93 | **tape**, William 28 Oct. Each long side has 8 windows, 39.25 in wide end to end, with 7 beams between them alternating 5, 4, 5, 4, 5, 4, 5 in (32 in). That is 346 in between the corner beams. The corners are not measured yet; taking them like the back wall's (the inner 2 3/8 in flange inside the water face) gives 350.75 in. The photos had suggested 5 bays (5.07 m) because IMG_2461 shows only half of the side |
| head rim (inside depth) | 1.0795 | 1.04-1.12 | the head's **window height**, 42.5 in (William 28 Oct), taken as the depth from the inside floor to the rim. An inference: tape it |
| reservoir length | 5.6 | 4.6-6.8 | **photogrammetry**, IMG_2466: face length / height above rim = 3.12, with the height from IMG_2468 |
| reservoir width | 0.75 | 0.67-0.95 | **photogrammetry**, IMG_2468: the end face is about 1.0 m outside the frame; inside assumed close to the channel |
| reservoir height | 2.9 | 2.5-3.4 | IMG_2468: top 1.8 m above the channel rim. UNC Research quotes a "three-meter water column" |
| tank floor above lab floor | 0.60 | 0.50-0.70 | **photogrammetry**, IMG_2465, assuming a standing camera height of 1.45 m |

The channel leaves from the exact middle of the head's channel-side wall, on the bank's
axis (William). The bank ends at the channel mouth (operator's description).

## Capacity check against the published figure

UNC publishes 13,500 gal, 118-120 ft overall, and 70,000 lb. At the values above, filled
to the brim, the model holds:

* head + channel: 39.8 m³
* water inside the pipes: almost none (it is air)
* reservoir: 12.2 m³
* total: 52.0 m³ = 13,700 gal, or 1.02 of the published figure.

Drawing every ranged dimension uniformly across its range gives 12,700-15,500 gal
(5th to 95th percentile, median 14,100); 73% of draws reach 13,500. With the head
measured (8.91 m by 3.04 m, 1.08 m deep), the published 13,500 gal is matched to 2%, so
it is most likely the tank filled to the brim, reservoir included. The photo-based
head (3.8 m by 5.2 m) and 1.00 m rim had given 11,000 gal. The reservoir is now the
largest remaining uncertainty in the capacity (12,800-14,600 gal across its ranges). Before the trapped air and the stepped pipes were modelled, the pipes
counted as full of water (265 gal) and the bank's whole footprint was dry above a
300 mm duct. Now the pipes hold almost nothing and the gaps between them hold water.
Net, the brim total fell from 11,160 to 11,020 gal.

The 36 m published length against the model's 29.9 m (head + channel + reservoir) probably
includes the platforms and stairs at both ends (IMG_2460, IMG_2462), so the model does not
use it.

## Piston motion

From wavemaker-f25 `app/params.py`: speed up to 900 mm/s and acceleration up to
20,000 mm/s². The profiles are 0 Trapezoidal, 1 Bestehorn, 2 S-curve and 3 Sine. The
lab's fast settings are accel 20,000 and jerk 7,500 (`app/solitons.py`). The Bestehorn
profile's effective jerk is the set jerk times 1.249, fitted so the model reproduces the
3.834 s cycle measured in `BIGGER WAVE.csv` (0-350 mm, 500 mm/s, 10,000 mm/s², jerk 2,000).
With jerk 7,500 the peak speed is jerk-limited: 155, 246, 323, 391, 453, 512 and 567 mm/s
for strokes of 50 to 350 mm, whatever higher speed is commanded.

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
