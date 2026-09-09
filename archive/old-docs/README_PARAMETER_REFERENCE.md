# Motor Parameter Complete Reference

## All 18 Parameters - Units, Defaults, Validation, and PLC Tags

---

## MOTION PARAMETERS

### Position 1 & Position 2

**What It Controls**: Target positions for the piston stroke

**Units**: Millimeters (mm)

**Default Values**:
- Position 1: `0 mm`
- Position 2: `350 mm`

**Set In**: `Motor.py` line 75

**Valid Range**: `-20 mm` to `368 mm` (after homing)

**Validation Code**: `Motor.py` lines 259-268
```
Position 1 validation:
    if value > 368 or value < -20:
        raise Exception('Position 1 out of stroke range')

Position 2 validation:
    if value > 368 or value < -20:
        raise Exception('Position 2 out of stroke range')
```

**PLC Tags**:
- `Program:Wave_Control.Motor_{ID}.Pos_1`
- `Program:Wave_Control.Motor_{ID}.Pos_2`

**Physical Meaning**:
- `0 mm` = Home position (piston fully retracted)
- `368 mm` = Maximum extension (physical limit)
- `-20 mm` = Allowed retraction beyond home

**Move Type Impact**:
- **Absolute (0)**: Positions measured from home (0mm)
- **Incremental (1)**: Positions measured from current location

**Tooltip Text**: `Model.py` lines 20-21
"Position limits after homing are 370mm and -20 mm"

---

### Speed 1 & Speed 2

**What It Controls**: Maximum velocity during each stroke

**Units**: Millimeters per second (mm/sec)

**Default Values**:
- Speed 1: `500 mm/sec`
- Speed 2: `500 mm/sec`

**Set In**: `Motor.py` line 75

**Valid Range**: `0 mm/sec` to `900 mm/sec`

**Validation Code**: `Motor.py` lines 270-280
```
Speed 1 validation:
    if value > 900 or value < 0:
        raise Exception('Speed 1 is outside the bounds of the speed limits')

Speed 2 validation:
    if value > 900 or value < 0:
        raise Exception('Speed 2 is outside the bounds of the speed limits')
```

**PLC Tags**:
- `Program:Wave_Control.Motor_{ID}.Spd_1`
- `Program:Wave_Control.Motor_{ID}.Spd_2`

**Physical Meaning**:
- Speed 1: Velocity from Position 1 to Position 2 (forward stroke)
- Speed 2: Velocity from Position 2 to Position 1 (return stroke)
- Motor will accelerate to this speed, then decelerate to stop

**Note**: Actual maximum speed depends on:
- Motor current draw
- Load on piston
- Power supply capacity
- 900 mm/sec is approximate limit under normal conditions

**Tooltip Text**: `Model.py` lines 22-23
"Velocity limits are 0 and approx. 900 mm/sec. Dependant on Current(A) usage"

---

### Accel 1 & Accel 2

**What It Controls**: Rate of velocity increase

**Units**: Millimeters per second squared (mm/s²)

**Default Values**:
- Accel 1: `10,000 mm/s²`
- Accel 2: `10,000 mm/s²`

**Set In**: `Motor.py` line 75

**Valid Range**: `0 mm/s²` to `20,000 mm/s²`

**Validation Code**: `Motor.py` lines 282-292
```
Accel 1 validation:
    if value > 20000 or value < 0:
        raise Exception('Accel 1 is outside the bounds of the acceleration limit')

Accel 2 validation:
    if value > 20000 or value < 0:
        raise Exception('Accel 2 is outside the bounds of the acceleration limit')
```

**PLC Tags**:
- `Program:Wave_Control.Motor_{ID}.Accel_1`
- `Program:Wave_Control.Motor_{ID}.Accel_2`

**Physical Meaning**:
- Accel 1: How quickly motor reaches Speed 1 (forward stroke)
- Accel 2: How quickly motor reaches Speed 2 (return stroke)
- Higher values = faster acceleration = jerkier motion
- Lower values = slower acceleration = smoother motion

**Important Note**: 
- Tooltip says limit is 50,000 mm/s² (`Model.py` line 24)
- **Code enforces 20,000 mm/s²** (safety limit)
- This discrepancy exists in the codebase

**Tooltip Text**: `Model.py` lines 24-25
"Acceleration and Deceleration are limited at 50,000 mm/s^2"

---

### Decel 1 & Decel 2

**What It Controls**: Rate of velocity decrease

**Units**: Millimeters per second squared (mm/s²)

**Default Values**:
- Decel 1: `10,000 mm/s²`
- Decel 2: `10,000 mm/s²`

**Set In**: `Motor.py` line 76

**Valid Range**: `0 mm/s²` to `20,000 mm/s²`

**Validation Code**: `Motor.py` lines 294-304
```
Decel 1 validation:
    if value > 20000 or value < 0:
        raise Exception('Decel 1 is outside the bounds of the deceleration limit')

Decel 2 validation:
    if value > 20000 or value < 0:
        raise Exception('Decel 2 is outside the bounds of the deceleration limit')
```

**PLC Tags**:
- `Program:Wave_Control.Motor_{ID}.Decel_1`
- `Program:Wave_Control.Motor_{ID}.Decel_2`

**Physical Meaning**:
- Decel 1: How quickly motor stops when reaching Position 2
- Decel 2: How quickly motor stops when reaching Position 1
- Higher values = faster deceleration = harder stop
- Lower values = slower deceleration = smoother stop

**Relationship to Acceleration**:
- Typically set equal to acceleration values
- Can be different for asymmetric motion profiles

**Tooltip Text**: `Model.py` lines 26-27
"Acceleration and Deceleration are limited at 50,000 mm/s^2"

---

### Jerk 1 & Jerk 2

**What It Controls**: Rate of change of acceleration (smoothness)

**Units**: Millimeters per second cubed (mm/s³)

**Default Values**:
- Jerk 1: `2,000 mm/s³`
- Jerk 2: `2,000 mm/s³`

**Set In**: `Motor.py` line 76

**Valid Range**: No hard limits enforced in code

**Validation Code**: `Motor.py` lines 306-309
```
No validation - any integer value accepted
```

**PLC Tags**:
- `Program:Wave_Control.Motor_{ID}.Jerk_1`
- `Program:Wave_Control.Motor_{ID}.Jerk_2`

**Physical Meaning**:
- Controls how smoothly acceleration/deceleration changes
- Higher jerk = abrupt acceleration changes = mechanical stress
- Lower jerk = gradual acceleration changes = smooth motion
- Most important for S-Curve and Sinusoidal profiles

**Best Practice** (`Model.py` lines 28-29):
"Jerk in general should be larger than the Acceleration and Deceleration mm/s^3"

**Why It Matters**:
- Reduces mechanical wear
- Prevents fluid sloshing (important for wave experiments)
- Minimizes vibration

**Tooltip Text**: `Model.py` lines 28-29
"Jerk in general should be larger than the Acceleration and Deceleration mm/s^3"

---

### Time 1 & Time 2

**What It Controls**: Time-based motion duration

**Units**: Seconds (s)

**Default Values**:
- Time 1: `0 s`
- Time 2: `0 s`

**Set In**: `Motor.py` line 77

**Valid Range**: No hard limits enforced

**Validation Code**: `Motor.py` lines 311-314
```
No validation - any integer value accepted
```

**PLC Tags**:
- `Program:Wave_Control.Motor_{ID}.Time1`
- `Program:Wave_Control.Motor_{ID}.Time2`

**Physical Meaning**:
- When set to 0: Position-based motion (move to position, ignore time)
- When set > 0: Time-based motion (reach position in specified time)
- Override speed/accel/decel if time-based

**Usage**:
- Default (0): Most common - motors move at specified speed
- Non-zero: Advanced - synchronize multiple motors by time

**Tooltip Text**: `Model.py` lines 30-31
"Default to 0"

---

## PROFILE PARAMETERS

### Profile

**What It Controls**: Shape of the motion trajectory

**Units**: Enumerated integer (0-3)

**Default Value**: `1` (Bestehorn)

**Set In**: `Motor.py` line 77

**Valid Values**:
- `0` = Trapezoidal
- `1` = Bestehorn
- `2` = S-Curve
- `3` = Sinusoidal

**Validation Code**: `Motor.py` lines 250-257
```
if value >= 0 and value <= 3:
    # Valid - write to PLC
else:
    raise Exception('The argument for Profile must be an integer 0,1,2,3. 
                     Trapazoidal(0) Bestehorn(1) S-Curve(2) Sin(3)')
```

**PLC Tag**: `Program:Wave_Control.Motor_{ID}.Profile`

**Profile Descriptions**:

**Trapezoidal (0)**:
- Velocity vs Time: Trapezoid shape
- Constant acceleration → Constant velocity → Constant deceleration
- Simplest, most efficient
- Abrupt acceleration changes (high jerk)

**Bestehorn (1)**:
- Specialized profile for wave generation
- Smooth transitions
- Good for fluid dynamics experiments

**S-Curve (2)**:
- Velocity vs Time: S-shaped curve
- Gradual acceleration → Constant velocity → Gradual deceleration
- Uses Jerk parameter for smoothness
- Reduces mechanical stress

**Sinusoidal (3)**:
- Velocity vs Time: Sine wave shape
- Smoothest profile
- Good for gentle wave patterns
- Slowest to reach peak velocity

**Tooltip Text**: `Model.py` line 32
"Trapazoidal(0) Bestehorn(1) S-Curve(2) Sin(3)"

---

### Move Type

**What It Controls**: Position reference frame

**Units**: Enumerated integer (0-1)

**Default Value**: `0` (Absolute)

**Set In**: `Motor.py` line 77

**Valid Values**:
- `0` = Absolute positioning
- `1` = Incremental positioning

**Validation Code**: `Motor.py` lines 241-248
```
if value == 0 or value == 1:
    # Valid - write to PLC
else:
    raise Exception('The MoveType argument must be a 1 or a 0. 
                     0 for absolute 1 for Incremental')
```

**PLC Tag**: `Program:Wave_Control.Motor_{ID}.MoveType`

**Physical Meaning**:

**Absolute (0)**:
- Positions measured from home position (0 mm)
- Position 1 = 0, Position 2 = 350 means:
  - Move to 0mm from home
  - Move to 350mm from home
- Most common mode

**Incremental (1)**:
- Positions measured from current location
- Position 1 = 0, Position 2 = 350 means:
  - Stay at current position
  - Move 350mm from wherever you are
- Useful for relative movements

**Example**:
```
Current position: 100mm from home

Absolute Mode:
  Position 2 = 350 → Motor moves to 350mm from home (moves 250mm forward)

Incremental Mode:
  Position 2 = 350 → Motor moves 350mm from current (ends at 450mm from home)
```

**Tooltip Text**: `Model.py` line 33
"Absolute(0): Based on defined axis. Incremental(1): Moves the amount specified in position argument"

---

## CURVE PARAMETERS (Advanced)

### Curve ID

**What It Controls**: Identifier for pre-programmed motion curve

**Units**: Integer identifier

**Default Value**: `0`

**Set In**: `Motor.py` line 77

**Valid Range**: No validation enforced

**PLC Tag**: `Program:Wave_Control.Curve_{ID}.Curve_ID`

**Physical Meaning**:
- References pre-programmed complex motion patterns
- Stored in PLC memory
- Allows playback of recorded or calculated trajectories

**Usage**: Advanced feature for custom wave patterns

**Tooltip Text**: `Model.py` line 34
"Used for curves."

---

### Time Scale

**What It Controls**: Time axis scaling for curves

**Units**: Scalar multiplier

**Default Value**: `0`

**Set In**: `Motor.py` line 77

**Valid Range**: No validation enforced

**PLC Tag**: `Program:Wave_Control.Curve_{ID}.TimeScale`

**Physical Meaning**:
- Multiplier for curve time axis
- Value > 1: Slows down curve playback
- Value < 1: Speeds up curve playback
- Value = 0: Typically disabled/ignored

**Tooltip Text**: `Model.py` line 35
"Used for curves."

---

### Amplitude Scale

**What It Controls**: Amplitude scaling for curves

**Units**: Scalar multiplier

**Default Value**: `0`

**Set In**: `Motor.py` line 77

**Valid Range**: No validation enforced

**PLC Tag**: `Program:Wave_Control.Curve_{ID}.AmplitudeScale`

**Physical Meaning**:
- Multiplier for curve amplitude
- Value > 1: Increases wave height
- Value < 1: Decreases wave height
- Value = 0: Typically disabled/ignored

**Tooltip Text**: `Model.py` line 36
"Used for curves."

---

### Curve Offset

**What It Controls**: Vertical offset for curves

**Units**: Offset value (mm)

**Default Value**: `0`

**Set In**: `Motor.py` line 78

**Valid Range**: No validation enforced

**PLC Tag**: `Program:Wave_Control.Curve_{ID}.CurveOffset`

**Physical Meaning**:
- Adds constant offset to curve positions
- Positive: Shifts curve upward
- Negative: Shifts curve downward
- Allows same curve pattern at different positions

**Tooltip Text**: `Model.py` line 37
"Used for curves."

---

## PARAMETER UPDATE TRACKING

### write_params vs current_params

**write_params Dictionary**:
- **Location**: `Motor.py` line 75-78
- **Contains**: Values user wants to send to PLC
- **Updated**: When user changes parameter in GUI
- **Updated**: When preset loaded
- **Initialized**: With default values at motor creation

**current_params Dictionary**:
- **Location**: `Motor.py` line 79
- **Contains**: Values confirmed written to PLC
- **Updated**: After successful PLC write operation
- **Initialized**: Empty `{}`

**Verification Process**:
```
Before write:
    write_params = {'Speed 1': 700, 'Speed 2': 500, ...}
    current_params = {}

After write:
    write_params = {'Speed 1': 700, 'Speed 2': 500, ...}
    current_params = {'Speed 1': 700, 'Speed 2': 500, ...}

Verification:
    if write_params == current_params:
        write_success = True  ✓
```

---

## FILES TO REFERENCE FOR EACH PARAMETER

| Parameter | Default | Validation | Write | PLC Tag Format |
|-----------|---------|------------|-------|----------------|
| Position 1 & 2 | Motor.py:75 | Motor.py:262-268 | Motor.py:259-268 | Motor_X.Pos_1/2 |
| Speed 1 & 2 | Motor.py:75 | Motor.py:272-277 | Motor.py:270-280 | Motor_X.Spd_1/2 |
| Accel 1 & 2 | Motor.py:75 | Motor.py:284-289 | Motor.py:282-292 | Motor_X.Accel_1/2 |
| Decel 1 & 2 | Motor.py:76 | Motor.py:296-301 | Motor.py:294-304 | Motor_X.Decel_1/2 |
| Jerk 1 & 2 | Motor.py:76 | None | Motor.py:306-309 | Motor_X.Jerk_1/2 |
| Time 1 & 2 | Motor.py:77 | None | Motor.py:311-314 | Motor_X.Time1/2 |
| Profile | Motor.py:77 | Motor.py:253-257 | Motor.py:250-257 | Motor_X.Profile |
| Move Type | Motor.py:77 | Motor.py:244-248 | Motor.py:241-248 | Motor_X.MoveType |
| Curve ID | Motor.py:77 | None | Motor.py:318 | Curve_X.Curve_ID |
| Time Scale | Motor.py:77 | None | Motor.py:319 | Curve_X.TimeScale |
| Amplitude Scale | Motor.py:77 | None | Motor.py:320 | Curve_X.AmplitudeScale |
| Curve Offset | Motor.py:78 | None | Motor.py:321 | Curve_X.CurveOffset |

---

## QUICK REFERENCE: WHERE TO LOOK

**To see default values**: `Motor.py` lines 75-78

**To see parameter tooltips**: `Model.py` lines 20-37

**To see validation logic**: `Motor.py` lines 241-321

**To see PLC tag names**: `Motor.py` lines 221-321

**To see parameter input UI**: `DefineMotors.py` lines 113-141

**To see write verification**: `Motor.py` lines 214-220 and `Model.py` lines 119-132
