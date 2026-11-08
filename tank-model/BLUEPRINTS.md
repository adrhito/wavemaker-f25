# The design archive: what is in it and what the model took from it

On 7 Nov 2026 the lab's shared Google Drive folder "Wavemaker" (shared by mahaney@cs.unc.edu)
was copied to `Wave_maker/Wavemaker_Drive/WaveMaker_Leah_versions/`, next to this repository
rather than in it: 273 files, 127 MB. It holds the 2013-2015 design of the wavemaker by Leandra
Vicci (UNC Computer Science) under the NSF MRI grant DMS-1229471, and the 2009 construction
drawings of the tank itself by IIHR (University of Iowa). Paths below are relative to
`WaveMaker_Leah_versions/`.

Parts of the design were built and parts were not, so each file needs judging. IMG_2459 shows
what the duct and drive drawings show: LinMot stators on square plates on top of 30 metal
cylinders, standing on a stack of thin layers. Those layers are the ducts below. The design
changed in at least two ways before it was built:

* **Actuator.** The 2013 concept used Kollmorgen EC2 electric cylinders with AKD drives. The
  Aug 2014 quote switched to LinMot linear motors, which is what wavemaker-f25 drives.
* **Cylinders.** The design drew the cylinders in PVC: 8 in DWV pipe at first, then a PVC
  prototype from Grind All with an 8.050 in bore (Jan 2015), molded PVC flanges, and seals
  sized for them. **That design is outdated.** William (7 Nov): it was changed, and the built
  cylinders are metal, stainless as far as he knows. The stencil on the machine (IMG_2458)
  reads Felker 8 in Sch 10S 304 stainless. So the model takes nothing about the cylinders,
  their flanges or their seals from the archive. Bore, lengths and tops stay as measured:
  8.329 in from the stencil, and William's 51 to 24 in on a level 56 in top.

## What the drawings changed in the model

1. **The bank is a duct block, not open pipes.** Each cylinder stands on its own elbow box. The
   box turns its water horizontal and runs it in a 9.8 x 3.0 in stainless duct to the block's
   front face, where all 30 outlets open into the channel: 3 lanes by 10 layers. Row 10's duct
   is the top layer and 0.37 m long. Row 1's is the bottom layer and 2.61 m long. The block is
   solid below each step, and its front stands 0.82 m high across the channel's whole width.
   In any water shallower than that, it walls the channel off from the head. The pistons' water
   no longer splits between the channel and the head: it all goes down the channel. The model's
   first wave roughly doubled (`results/drawings_effect_*.png`).
2. **The pitch is 9.8 in both ways**, not 9.75 in, and the block is 11 pitches long (2.738 m).
   The front row stands one pitch back from the face.
3. **The pipe bottoms step one duct layer, 3.02 in, per row**: the duct block's layer pitch,
   from the stainless duct and rail drawings. William's 3 in agrees to 0.02 in a row. The
   cylinders' lengths and tops stay William's (row 1 51 in, tops level at 56 in): the
   archive's cylinder lengths belong to the PVC design.
4. **The water in each duct is a large mass on the air spring.** Referred to the disc it is
   about 185 kg for row 1. With the trapped air it rings at 0.9-1.1 Hz in rows 1-3 and up to
   2.5 Hz in row 8. That is inside the band the strokes drive, not "3-5 Hz, well above it" as
   the model had assumed. Predictions now include it (`wavemodel/drive.py`).
5. **The motors are known:** LinMot P01-48x360F with the 800 mm slider. They give 1020 N peak
   force, 200 N continuous (340 N with a fan), 2 m/s, and 510 mm of travel. That travel is
   exactly the drive's configured -57 to 453 mm. `wm.py pistons` and `predict` now check every
   run against these limits.
6. **The tank's heights** are from the IIHR sections:
   * the floor is 23.5 in above the lab floor;
   * the rim is 45.4 in above the tank floor;
   * the maximum water depth is 40.4 in.

   The model had used the 42.5 in window height as the rim. The IIHR plan also checks the head
   (4 modules, about 9.1 m over the posts) and the channel (about 18.0 m between the gate
   posts).

## Every file, by how useful it is

### Used: the model now rests on these

| File | What it is | What it gave |
|---|---|---|
| `Design/Mechanicals/Frame/FramedDucts.png` | render of the whole duct block on its rails | the bank's structure: stepped ducts, all outlets on the tall end |
| `Design/Mechanicals/Ducts/RapidQuote/Mar2015/*.stp` (Elbow, Extend, Extend2, FlangePlate, ExPlate, ExPlate2, ExPlate3, BasPlate2, BasPlate3) and the matching `.pdf` prints | the duct parts as ordered from Rapid Sheet Metal, Mar 2015 | ducts 9.8 x 3.000 in outside, 0.059 in 304 sheet, 9.68 x 2.94 in inside (184 cm²); elbows 19.6 in (two pitches); flange plates with three 8.25 in holes on 9.8 in centres; base plates 29.4 in wide |
| `Design/Mechanicals/Ducts/RapidQuote/21Oct2014/DuctPartsCount.png`, `Mar2015/PartsList` | hand count of duct parts per layer | each row further back adds one pitch of straight duct: the duct lengths, and that there are exactly 30 ducts |
| `Design/Mechanicals/Ducts/ElbowConstruction/ElbowConstruction~`, `v2/ElbowConstruction,v2` | how the elbow box is built | its lip, slits, folds and fastener spacing, and that its top is open where the cylinder sits |
| `Design/Mechanicals/Frame/VerticalRails.pdf`, `VRailDetail.pdf` | the front corner angles, 2 x 2 x 1/4 in, 30.2 in | the layer pitch, 3.020 in (bolt holes); that the block bolts to the tank's gate frame |
| `Design/Mechanicals/Frame/HorizontalRails.pdf`, `HRailDetail.pdf`, `Gussets.pdf` | the two offset-tee rails (6 x 4 x 1/4 in, 104 in) and gussets | how the block sits on the tank floor; hole pitch 9.800 in along the tank |
| `Design/Mechanicals/Frame/FrameDesignNotes~`, `WaveTankInterface~`, `Fasteners/FastenerList`, `Frame3D.png` | design notes on mounting | supported on the floor by the rails and held at the front by the gate frame |
| `Construction/BOM_Account.ods` | the build order and its status, Nov 2014 to Apr 2015 | that the frame rails were ordered (Stainless Structurals, Apr 2015). Its cylinder, flange and seal lines are the PVC design: see below |
| `AcquisitionAndServices/ScottEquipment/Qte10033082_1.pdf`, `UNC quote 48 300mm stroke rev3.xlsx` | LinMot quote and sizing sheet, Aug 2014 | the motors: PS01-48x360F-C stator, PL01-28x800/720 slider, E1200-GP-UC drive (72 V, 32 A), T01-72/900 supply, fan cooler; sized for 440 N at 1 Hz (1100 W peak, 480 W mean) |
| LinMot data sheet 0185-1005 (P01-48x360F, edition 24), fetched from shop.linmot.com | not in the archive | force, speed, stroke, current and thermal ratings for that stator and slider; position zero against the stator face |
| `Design/Mechanicals/CadFiles/MotorMount.stp`, `AcquisitionAndServices/PhysicsShop/MotorMount`, `AcquisitionAndServices/RapidQuote/rapid machining quote 20381.pdf` | the plate on each cylinder top (Nov 2013 design) | only the 3D model's plates: 9.6 in square, 0.25 in aluminium. Drawn for the PVC cylinders; the machine has square plates there (IMG_2459), size not measured |
| `Design/Mechanicals/CadFiles/Assemblies/Pump.png` | render of one actuator, plate, cylinder and rod | how a piston is put together, in outline (the cylinder in it is the PVC design) |
| `Design/Infrastructure/WaveTankDrawings/Sheet1, 4, 13, 16.png` | IIHR construction drawings of the tank, Jul 2009 | floor 23.5 in above the lab floor, rim 45.4 in, max depth 40.4 in; the head is 4 modules long and the gate is at the head/channel junction; the plan, scaled, checks the head and channel lengths |
| `Design/Mechanicals/Mass~` | design mass | 1031 kg, centre of gravity 913 mm up, for the 2015 design with its PVC cylinders: not the machine's |

### Outdated: the PVC cylinder design, not used

William (7 Nov): the PVC cylinder design was changed before the machine was built, and the
cylinders are metal. These files describe the outdated cylinders and the parts that went
with them. Take no figure from them:

| File | What it is |
|---|---|
| `Construction/BOM_Account.ods`, the "Pistons and Cylinders" lines | Grind All cylinders 24.005-51.185 in long, 3.020 in apart; 60 molded PVC flanges ("Flange6"); 30 custom 8 in double-acting piston cups (DAPC-800-SP1, Smith Seal) |
| `AcquisitionAndServices/Grindall/Proto2-Jan2015/*` | PVC prototype cylinder, 51.185 in long, bore 8.050 in |
| `AcquisitionAndServices/SmithSeal/*`, `Construction/SoleSource/DAPC.png` | the double-acting piston cup, sized for the PVC bore |
| `Design/Mechanicals/CadFiles/Cylinder.dwb`, `Piston.dwb`, `Flange.dwb`, `FlangeMod.dwb`, `FlPlate*.dwb`, their `*,v` histories | the PVC cylinder, its disc and flanges (VariCAD) |

The duct block and its rails are stainless parts from the same design set. They are used,
because IMG_2459 shows the layered block under the cylinders. Where they meet the
cylinders (the flange plates' 8.25 in holes, the motor plates), they were drawn for the PVC
cylinders and may differ on the machine.

### Context: the design's reasoning, not used for numbers

| File | What it is |
|---|---|
| `Design/ConceptJul3013/ConceptSketch.png`, `PrimeMoverSpecs.png`, `Design/Calculations.png` | the June 2013 concept: a piston in an 8 in cylinder feeding the flume through a duct, sized for internal waves 10 times the reach of Gostiaux's generator (Lyon) and for 0.1 m surface waves at 1 Hz; force budget for the Kollmorgen actuator |
| `Admin/ResearchFair2013/ResearchFair/*` | 2013 research-fair material: slides (`WaveMaker.pptx`), Kollmorgen EC2/AKD pictures, layouts, photos |
| `AcquisitionAndServices/FacilitiesServices/*` | the 480 V three-phase feed brought to the lab for the drives (2013); a ceiling hanger location |
| `AcquisitionAndServices/QualifyVendors`, `Contractor`, `RapidQuote/rapid sheet metal quote 55338.pdf`, `Ducts/RapidQuote/Mar2015/PurchaseDocuments/*`, `*/Waiver of Competition*`, `Construction/W-9.pdf` | vendors and purchasing paperwork |
| `Design/Mechanicals/Ducts/RapidQuote/16June2014`, `9July2014`, `21Oct2014`, `v2,Jan2015` (`.stp`, `.dwg`) | earlier revisions of the duct parts; Mar 2015 supersedes them |
| `Design/Mechanicals/CadFiles/ConRod.*`, `EC2.dwb` | the rod and mount for the Kollmorgen actuator, superseded by the LinMot slider |
| `Design/DSP/*.m` | MATLAB experiments with resampling and filtering a signal, presumably for smoothing motion commands |
| `Design/Mechanicals/COTSparts/*`, `Frame/Fasteners/*.STEP`, `Ducts/92196A576.STEP`, `Fasteners/PEMnuts.pdf` | fasteners and their catalogue |
| `Design/Mechanicals/Frame/SCIdesignManual.pdf`, `Section-Properties-T-Shapes.pdf`, `ASTM_A479.pdf`, `Ducts/RSMStdTolerances.pdf`, `StainlessStructurals` | reference standards and vendor tolerances |
| `Design/BillOfMaterials/ElectronicsAndMotors/USBtoRS232/*` | USB-serial cables for configuring the LinMot drives |
| `Admin/*` (except the research fair), `Admin/FabricationAccount/*` | administration and accounts |
| `Construction/Status/Frame/*`, `*~` with no text | empty files |

### Not readable here

The VariCAD files (`*.dwb`, and their RCS histories `*,v`) are VariCAD's own format. Where a
part was also exported to STEP or printed to PDF, that copy was used. Two things exist only as
`.dwb` and would be worth exporting (the cylinder and disc files are the outdated PVC
design):

* `Frame/SupportPads.dwb`: the pads under the rails. Their thickness, now inferred at about
  1.6 in from row 1's 5 in bottom, sets every row's height.
* `Frame/FramedDucts.dwb` (35 MB): the whole assembly.

VariCAD's free viewer opens them, and VariCAD itself can export STEP. The `.dwg` files (AutoCAD)
are 2D prints of parts that are also in STEP. The Aug 2015 IGES files (`Ducts/BasPlate.igs`,
`Ducts/Elbow.igs`) are later re-exports that came through as surfaces only.

## What is still not settled by the archive

* **The bore, to the millimetre.** The cylinders are metal; the stencil says Sch 10S stainless,
  8.329 in, good to -0.7/+2.4% in area by the pipe standard. A tape round a cylinder (about
  688 mm) would confirm it.
* **Where the disc is at 0 mm.** The LinMot geometry puts it about 1.35 m above the floor (now
  the model's value); a tape inside a pipe would settle it. It sets only the trapped air's length.
* **The seal.** The archive's double-acting cups were for the PVC cylinders, so the machine's
  seal is unknown: only bubbles or a measured wave decide `tight` against `leaky`.
* **The disc's mass and thickness.** `Piston.dwb` is the PVC design's disc.
* **The ringing.** Its frequency and decay follow from the duct mass, the air and estimated
  losses. One camera run would show them: a single row, one stroke, then watch the water at
  the mouth for the 1 Hz swing.
