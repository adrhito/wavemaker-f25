# Complete Motor Operation Pipeline

## End-to-End Process: From GUI Click to Motor Movement

This document walks through every step from application startup to physical motor operation.

---

## PHASE 1: APPLICATION STARTUP

### Step 1.1: Launch Application
**File**: `main.py` lines 24-25
**User Action**: Run `python main.py`

**What Happens**:
1. Creates Model instance
2. Model attempts PLC connection at 192.168.1.1
3. If connection succeeds: `CONNECTED = True`
4. If connection fails: `CONNECTED = False` (mock mode)
5. Creates GUI window with 4 tabs

**PLC Connection Test**: `Model.py` lines 98-102
```
try:
    self.live_motor_reset()  # Writes zeros to all PLC Live_Motors tags
    self.CONNECTED = True     # Success - PLC is accessible
except:
    self.CONNECTED = False    # Failed - Running in mock mode
```

**Result**: Application window opens, "Control Home" tab visible

---

## PHASE 2: MOTOR SELECTION

### Step 2.1: Navigate to "Define Motors" Tab
**File**: `DefineMotors.py`
**User Action**: Click "Define Motors" tab

**What You See**: 30 checkboxes (Motor 0 - Motor 29) in 3 rows × 10 columns

**Physical Layout**:
- Row 1: Motors 0, 3, 6, 9, 12, 15, 18, 21, 24, 27
- Row 2: Motors 1, 4, 7, 10, 13, 16, 19, 22, 25, 28
- Row 3: Motors 2, 5, 8, 11, 14, 17, 20, 23, 26, 29

### Step 2.2: Select Motors
**User Action**: Check one or more motor checkboxes

**What Happens Internally**:
1. Checkbox triggers `model.onCheck(motor_number, checkbox_state)`
2. Updates `model.motdict[motor_number] = 1` (1 = selected)
3. No PLC communication yet - just tracking selection

**File**: `Model.py` lines 105-107

**Result**: Motors marked as selected in internal dictionary

---

## PHASE 3: PARAMETER CONFIGURATION

### Step 3.1: View Default Parameters
**What Happens**: Selected motors already have default values from `Motor.py` lines 75-78

**Defaults Applied**:
- Position 1: 0 mm, Position 2: 350 mm
- Speed 1: 500 mm/s, Speed 2: 500 mm/s  
- Accel 1: 10000 mm/s², Accel 2: 10000 mm/s²
- Decel 1: 10000 mm/s², Decel 2: 10000 mm/s²
- Jerk 1: 2000 mm/s³, Jerk 2: 2000 mm/s³
- Time 1: 0 s, Time 2: 0 s
- Profile: 1 (Bestehorn), Move Type: 0 (Absolute)

### Step 3.2: Modify Parameters (Optional)
**User Action**: Type new values in parameter fields

**Example**: Change "Speed 1" from 500 to 700

**What Happens**: `DefineMotors.py` lines 290-297
1. Input field detects change
2. Validates input is numeric integer
3. Updates `motor.write_params['Speed 1'] = 700`
4. Tooltip shows parameter limits

**Parameter Field Tooltips Show**:
- Position: "Position limits after homing are 370mm and -20mm"
- Speed: "Velocity limits are 0 and approx. 900 mm/sec"
- Accel/Decel: "Limited at 50,000 mm/s²"
- Profile: "Trapezoidal(0) Bestehorn(1) S-Curve(2) Sin(3)"

**Result**: Parameters stored in memory, NOT yet sent to PLC

---

## PHASE 4: MOTOR ACTIVATION

### Step 4.1: Add Motors to Set
**User Action**: Click "Add Selected Motors to Set" button

**What Happens**: `Model.py` lines 267-325

**Step 4.1a - Create Motor Objects**:
```
For each selected motor (where motdict[motor] == 1):
    1. Create Motor(motor_number, CONNECTED) instance
    2. Store in live_motors dictionary
    3. Motor gets row/column position (calculated in Motor.py lines 108-117)
```

**Step 4.1b - Notify PLC Which Motors Are Active**:
```
If CONNECTED:
    For each motor:
        Write to PLC tag: Program:Wave_Control.Live_Motors.{motor_number} = 1
```
This tells the PLC "these specific motors should be controlled"

**Step 4.1c - Boot Motors**:
Calls `motor_on()` which does:
```
Write: Program:Wave_Control.Motor_Boot = 1
Wait: 5 seconds
Write: Program:Wave_Control.Motor_Boot = 0
```
This triggers PLC routine to power on and enable the drives

**Result**: 
- Motors powered on (if CONNECTED)
- Green checkboxes indicate active motors
- RUN_ENABLE = True (allows next steps)

### Step 4.2: Confirm Set
**User Action**: Click "Confirm Set" button

**What Happens**: `DefineMotors.py` lines 250-271
1. Shows confirmation dialog
2. Adds motors to `live_motors_sets` list
3. Changes checkbox colors to pink (locked)
4. Motors can no longer be modified without reset

**Why Sets?**: Allows multiple groups of motors with different parameters to run simultaneously

---

## PHASE 5: PREPARE FOR OPERATION

### Step 5.1: Navigate to Control Home
**User Action**: Click "Control Home" tab

**What You See**:
- 30 circles representing motors (white = off, green = active)
- Prepare Motor(s) button
- Start/Stop buttons (disabled until prepared)
- Motion type: One Stroke / Continuous radio buttons

### Step 5.2: Write Parameters to PLC
**User Action**: Click "Prepare Motor(s)" button

**What Happens**: `ControlHome.py` lines 181-200

**Step 5.2a - Check If Already Written**:
```
If write_success() AND written_matches_current():
    Skip to homing (parameters already in PLC)
Else:
    Write parameters now
```

**Step 5.2b - Write All Parameters** (`Model.py` line 586):
```
For each motor set:
    For each motor in set:
        Call motor.write_to_motor(IP_ADDRESS, PROCESSOR_SLOT)
```

**Step 5.2c - Individual Motor Write** (`Motor.py` lines 198-220):
```
For each parameter category:
    1. write_movetype()   → Program:Wave_Control.Motor_{ID}.MoveType
    2. write_profile()    → Program:Wave_Control.Motor_{ID}.Profile
    3. write_position()   → Program:Wave_Control.Motor_{ID}.Pos_1 & Pos_2
    4. write_speed()      → Program:Wave_Control.Motor_{ID}.Spd_1 & Spd_2
    5. write_accel()      → Program:Wave_Control.Motor_{ID}.Accel_1 & Accel_2
    6. write_decel()      → Program:Wave_Control.Motor_{ID}.Decel_1 & Decel_2
    7. write_jerk()       → Program:Wave_Control.Motor_{ID}.Jerk_1 & Jerk_2
    8. write_time()       → Program:Wave_Control.Motor_{ID}.Time1 & Time2
    9. write_curve()      → Program:Wave_Control.Curve_{ID}.[CurveParams]
```

**Each Write Function Does**:
1. **Validate** parameter is within limits
2. **Open PLC connection** via EtherNet/IP
3. **Write to specific tag** using pylogix library
4. **Store in current_params** for verification
5. **Close PLC connection**

**Example - Writing Speed 1 for Motor 15**:
```
Validation: Check 500 is between 0-900 ✓
PLC Tag: Program:Wave_Control.Motor_16.Spd_1
Value: 500
Protocol: EtherNet/IP to 192.168.1.1:44818
Confirmation: current_params['Speed 1'] = 500
```

**Step 5.2d - Verify Write Success**:
After all writes complete:
```
If current_params == write_params for ALL parameters:
    motor.write_success = True
Else:
    motor.write_success = False
    Log error
```

**Result**: All parameters now in PLC memory, ready to execute

---

## PHASE 6: HOMING SEQUENCE

### Step 6.1: Automatic Homing
**Triggered Automatically After**: Successful parameter write (2 second delay)

**What Happens**: `Model.py` lines 196-265

**Step 6.1a - Initiate Homing** (runs twice for accuracy):
```
Write: Program:Wave_Control.Home_Button = 0
Wait: 5 seconds
Read: Program:Wave_Control.Home_Button (should be 0)
Write: Program:Wave_Control.Home_Button = 1 (start homing)
```

**Step 6.1b - Poll Motor Status**:
```
Every 5 seconds (up to 40 seconds total):
    For each motor:
        Read: Program:Wave_Control.Axis[{motor}].StatusWord
        Check: Bit 12 of StatusWord (home indicator)
        If bit 12 == 1: Motor is homed
    
    If ALL motors homed:
        Write: Program:Wave_Control.Home_Button = 0
        Set model.state = 1 (HOMED STATE)
        Break loop
    
    If timeout (40 seconds):
        Log error "Unable to Home Motors"
        Break loop
```

**Step 6.1c - Status Word Check** (`Motor.py` lines 175-196):
```
Read StatusWord as binary
Convert to binary string
Check position of bit 12 from right
If bit == 1: self.home = True
```

**Physical Process**:
- Each piston moves to its home position
- Hall effect sensor or limit switch detects home
- PLC sets StatusWord bit 12 when homed
- Process takes 5-20 seconds per motor

**Result**: 
- All motors at home position
- model.state = 1 (HOMED)
- Start button becomes enabled
- Message: "Motor(s) Homed"

---

## PHASE 7: MOTOR OPERATION

### Step 7.1: Single Stroke Operation
**User Action**: Select "One Stroke", click "Start Motor(s)"

**What Happens**: `Model.py` lines 393-416

```
Write: Program:Wave_Control.Run_1 = 1
Message: "Motor(s) Running"
Wait: 5 seconds (allows motion to complete)
Write: Program:Wave_Control.Run_1 = 0
Message: "Motor(s) Stopped"
Set: model.state = 0 (UNPREPARED)
```

**Physical Motion**:
1. Motors move from Position 1 to Position 2
2. Using Speed 1, Accel 1, Decel 1, Jerk 1
3. Following the selected Profile shape
4. Motion completes when position reached
5. Motors stop automatically

### Step 7.2: Continuous Operation
**User Action**: Select "Continuous", click "Start Motor(s)"

**What Happens**: `Model.py` lines 420-455

```
Write: Program:Wave_Control.Run_2 = 1
Set: model.state = 2 (RUNNING)
Message: "Motor(s) Running"

If RECORD_ANALYTICS enabled:
    Record positions every 0.25 seconds for 10 seconds
    Store in analytics/{today's date}.txt
```

**Physical Motion**:
1. Motors continuously cycle between Position 1 and Position 2
2. Forward stroke: Pos1→Pos2 using Speed1, Accel1, Decel1, Jerk1
3. Return stroke: Pos2→Pos1 using Speed2, Accel2, Decel2, Jerk2
4. Repeats indefinitely until stopped
5. Uses selected Profile for motion shape

### Step 7.3: Analytics Recording (Optional)
**If Enabled**: `Model.py` lines 331-386

```
Every 0.25 seconds:
    For each active motor:
        Read: Program:Wave_Control.Axis[{motor}].ComDemandPosition
        Read: Program:Wave_Control.Axis[{motor}].ComActualPosition
        Calculate: displacement = |demand - actual|
        Write to file: timestamp, demand, actual, displacement
```

**Output File**: `analytics/2025-10-05.txt`

**Data Shows**:
- Commanded position (from parameters)
- Actual position (from motor encoder)
- Difference (tracking error)
- Proves parameters are controlling hardware

### Step 7.4: Stop Continuous Operation
**User Action**: Click "Stop Motor(s)" (only visible during continuous)

**What Happens**: `ControlHome.py` lines 256-265

```
Write: Program:Wave_Control.Run_2 = 0
Set: model.state = 0 (UNPREPARED)
Message: "Motors stopped"
```

**Physical Motion**:
- Motors complete current stroke
- Stop at current position
- Remain powered and ready

---

## PHASE 8: SHUTDOWN & RESET

### Step 8.1: Turn Off Motors
**User Action**: Click "Off and Reset" button

**What Happens**: `Model.py` lines 161-193

```
Write: Program:Wave_Control.Clear_Motor_Error = 1
Wait: 5 seconds
Write: Program:Wave_Control.Clear_Motor_Error = 0

Call motion(2, 1) to stop any running motion:
    Write: Program:Wave_Control.Run_2 = 0
    
Reset all PLC control tags:
    Write: Program:Wave_Control.Run_1 = 0
    Write: Program:Wave_Control.Run_2 = 0
    Write: Program:Wave_Control.Clear_Motor_Error = 0
    Write: Program:Wave_Control.Home_Button = 0
    Write: Program:Wave_Control.Run_Curve = 0

Reset Live_Motors array:
    For motors 0-29:
        Write: Program:Wave_Control.Live_Motors.{motor} = 0

Clear internal state:
    live_motors_sets = []
    live_motors = {}
    RUN_ENABLE = False
```

**Result**: 
- Motors powered down
- All parameters cleared
- PLC ready for new configuration
- GUI returns to initial state

---

## SUMMARY: Value Propagation Through System

```
USER INPUT (or defaults)
    ↓
motor.write_params{} dictionary
    ↓
VALIDATION (range checks)
    ↓
PLC COMMUNICATION (EtherNet/IP)
    ↓
PLC MEMORY (Program:Wave_Control.Motor_X.Param)
    ↓
motor.current_params{} dictionary (confirmation)
    ↓
VERIFICATION (current == write?)
    ↓
PLC EXECUTION (Run_1 or Run_2 trigger)
    ↓
PHYSICAL MOTORS (servos execute motion)
    ↓
ENCODER FEEDBACK (actual position)
    ↓
ANALYTICS (proves hardware response)
```

**Key Verification Points**:
1. After write: `current_params == write_params` check
2. Before operation: `write_success()` check
3. During operation: Analytics compare demand vs actual
4. All checks must pass for system to allow motor movement

**CONCLUSION**: Parameters flow from GUI → PLC → Hardware with multiple verification stages ensuring values are correctly transmitted and executed.
