# WaveMaker System - Complete Documentation Index

## Documentation Overview

This folder contains 4 comprehensive README files explaining how motor parameters flow from user input to physical hardware execution.

---

## 📚 AVAILABLE DOCUMENTATION

### 1. **README_PARAMETER_FLOW.md**
**Purpose**: Explains how values travel from GUI to hardware

**Topics Covered**:
- Default value initialization
- User input handling
- Pre-write validation
- PLC transmission process
- Write verification
- System-level confirmation
- Evidence that values reach hardware

**Read This If**: You want to understand the complete journey of a parameter value

**Key Finding**: Values are transmitted through 6 stages with multiple verification points ensuring successful delivery to hardware.

---

### 2. **README_COMPLETE_PIPELINE.md**
**Purpose**: Step-by-step walkthrough from startup to motor operation

**Topics Covered**:
- Application startup and PLC connection
- Motor selection process
- Parameter configuration
- Motor activation sequence
- Write and homing procedures
- Single stroke vs continuous operation
- Analytics recording
- Shutdown and reset

**Read This If**: You want a chronological understanding of the entire system workflow

**Key Finding**: The system follows an 8-phase pipeline with clear state transitions and verification at each stage.

---

### 3. **README_PARAMETER_REFERENCE.md**
**Purpose**: Complete reference for all 18 motor parameters

**Topics Covered**:
- Position, Speed, Accel, Decel, Jerk, Time parameters
- Profile and Move Type settings
- Curve parameters (advanced)
- Units for each parameter
- Default values
- Validation rules
- PLC tag mappings
- Code locations

**Read This If**: You need detailed information about a specific parameter

**Key Finding**: Every parameter has clearly defined units, defaults, validation rules, and PLC tags. No parameter is transmitted without validation.

---

### 4. **README_VERIFICATION_SYSTEM.md**
**Purpose**: Explains how the system ensures parameters reach hardware correctly

**Topics Covered**:
- 4-layer verification architecture
- Input validation
- Pre-write range checking
- Write confirmation process
- System-wide verification
- Evidence of hardware communication
- Failure handling

**Read This If**: You need proof that values are properly transmitted to physical motors

**Key Finding**: The system uses 4 layers of verification with multiple evidence sources proving parameters successfully control physical hardware.

---

## 🎯 QUICK ANSWERS TO KEY QUESTIONS

### Q: Are default values being set?
**A: YES** - See `README_PARAMETER_FLOW.md` Section 1

Every motor is initialized with default values in `Motor.py` lines 75-78:
- Position 2: 350mm
- Speed: 500mm/s
- Accel/Decel: 10000mm/s²
- Jerk: 2000mm/s³
- Profile: Bestehorn (1)
- All others

---

### Q: Are these values being carried to hardware?
**A: YES** - See `README_VERIFICATION_SYSTEM.md` Section "Evidence That Values Reach Hardware"

**Proof**:
1. EtherNet/IP protocol with acknowledgment
2. Synchronous write operations
3. current_params tracking
4. Analytics show actual motor positions matching commanded
5. Successful homing proves bidirectional communication

---

### Q: What are the parameter units?
**A: See `README_PARAMETER_REFERENCE.md`**

**Summary**:
- Position: mm (millimeters)
- Speed: mm/sec
- Acceleration: mm/s²
- Deceleration: mm/s²
- Jerk: mm/s³
- Time: seconds
- Profile: Integer code (0-3)
- Move Type: Integer code (0-1)

---

### Q: Are parameters being validated?
**A: YES** - See `README_VERIFICATION_SYSTEM.md` Layers 1-2

**Validation Stages**:
1. Input validation (numeric check)
2. Range validation (position: -20 to 368mm, speed: 0-900mm/s, etc.)
3. Type validation (profile: 0-3, move type: 0-1)
4. Post-write verification (current == write check)

---

### Q: What's the complete workflow from enabling motors to operation?
**A: See `README_COMPLETE_PIPELINE.md`**

**8-Phase Process**:
1. Application startup (PLC connection test)
2. Motor selection (checkboxes)
3. Parameter configuration (defaults + user input)
4. Motor activation (create objects, notify PLC, boot motors)
5. Prepare for operation (write parameters, verify)
6. Homing sequence (move to home, verify position)
7. Motor operation (single stroke or continuous)
8. Shutdown and reset

---

### Q: How do I verify values were written correctly?
**A: See `README_VERIFICATION_SYSTEM.md` Layers 3-4**

**System Checks**:
1. **Motor Level**: `motor.write_success` flag (`Motor.py` line 219)
2. **System Level**: `model.write_success()` function (`Model.py` line 119)
3. **Parameter Match**: `model.written_matches_current()` (`Model.py` line 126)
4. **Before Operation**: All checks run before motors allowed to start

---

## 🔍 CODE NAVIGATION GUIDE

### To Find Default Values:
- **File**: `Motor.py`
- **Lines**: 75-78
- **Variable**: `self.write_params` dictionary

### To Find Parameter Validation:
- **File**: `Motor.py`
- **Lines**: 241-321
- **Functions**: `write_position()`, `write_speed()`, `write_accel()`, etc.

### To Find Write Verification:
- **File**: `Motor.py`
- **Lines**: 214-220
- **Variable**: `self.write_success`

### To Find System Verification:
- **File**: `Model.py`
- **Lines**: 119-132
- **Functions**: `write_success()`, `written_matches_current()`

### To Find PLC Communication:
- **File**: `Motor.py`
- **Lines**: 221-239
- **Function**: `write_generic()`

### To Find Parameter Tooltips:
- **File**: `Model.py`
- **Lines**: 20-37
- **Variable**: `ALL_PARAM_TIPS`

---

## 📊 SYSTEM STATE TRACKING

### Motor States:
```
write_params     → What user wants to send
current_params   → What was confirmed sent
write_success    → Boolean: all parameters sent?
home             → Boolean: motor homed?
```

### Model States:
```
-1 = Disconnected (mock mode)
 0 = Unprepared (motors not homed)
 1 = Homed (ready to run)
 2 = Running (in operation)
```

### Connection Modes:
```
CONNECTED = True  → Real PLC communication
CONNECTED = False → Mock mode (simulation)
```

---

## 🛠️ TROUBLESHOOTING GUIDE

### Issue: Parameters not reaching hardware

**Check**:
1. `model.CONNECTED` == True? (See `Model.py` line 100)
2. `motor.write_success` == True? (See `Motor.py` line 219)
3. `model.write_success()` == True? (See `Model.py` line 119)
4. Network connection to 192.168.1.1?
5. PLC program "Wave_Control" running?

**Solution**: See `README_VERIFICATION_SYSTEM.md` Section "Failure Scenarios"

### Issue: Motors won't start

**Check**:
1. Motors homed? (`model.state` == 1)
2. Parameters written? (`write_success()` == True)
3. Start button enabled?

**Solution**: See `README_COMPLETE_PIPELINE.md` Phase 5-6

### Issue: Don't understand a parameter

**Check**: `README_PARAMETER_REFERENCE.md`
- Find parameter name
- Read units, defaults, validation
- Check tooltip text

---

## 📈 VERIFICATION SUMMARY

### ✅ Values ARE Being Set:
- Defaults in `Motor.py` lines 75-78
- User overrides in `DefineMotors.py` lines 290-297
- Preset loading in `PresetOptions.py` lines 102-107

### ✅ Values ARE Being Validated:
- Input validation: `DefineMotors.py` line 293
- Range validation: `Motor.py` lines 241-321
- Type validation: `Motor.py` lines 244, 253

### ✅ Values ARE Being Transmitted:
- PLC writes: `Motor.py` lines 221-239
- EtherNet/IP protocol: `modules/eip.py`
- Synchronous operations with acknowledgment

### ✅ Values ARE Being Verified:
- Motor level: `Motor.py` lines 214-220
- System level: `Model.py` lines 119-132
- Pre-operation: `ControlHome.py` line 190

### ✅ Hardware IS Responding:
- Analytics prove position tracking: `Model.py` lines 355-375
- Homing confirms status reading: `Motor.py` lines 175-196
- Motion execution observable and recordable

---

## 🎓 LEARNING PATH

### Beginner:
1. Read `README_COMPLETE_PIPELINE.md` (understand workflow)
2. Skim `README_PARAMETER_REFERENCE.md` (know what parameters exist)

### Intermediate:
3. Read `README_PARAMETER_FLOW.md` (understand data flow)
4. Review code sections mentioned in documentation

### Advanced:
5. Read `README_VERIFICATION_SYSTEM.md` (understand guarantees)
6. Review PLC communication code in `modules/eip.py`
7. Examine analytics output files

---

## 📁 FILE STRUCTURE REFERENCE

```
WaveMaker/
├── README.md                              # Original project README
├── README_DOCUMENTATION_INDEX.md          # This file
├── README_PARAMETER_FLOW.md               # How values reach hardware
├── README_COMPLETE_PIPELINE.md            # End-to-end workflow
├── README_PARAMETER_REFERENCE.md          # Parameter details
├── README_VERIFICATION_SYSTEM.md          # Verification proof
├── main.py                                # Entry point
├── Model.py                               # Business logic (CRITICAL)
├── Motor.py                               # Motor control (CRITICAL)
├── View.py                                # GUI main window
├── control_home/ControlHome.py            # Operation UI
├── define_motors/DefineMotors.py          # Parameter UI
├── preset_options/PresetOptions.py        # Preset management
└── modules/eip.py                         # PLC communication
```

---

## 🔑 KEY TAKEAWAYS

1. **Default values exist** for all 18 parameters in `Motor.py` lines 75-78

2. **User input validated** at entry and before write

3. **Parameters transmitted** via industrial EtherNet/IP protocol to PLC tags

4. **Writes confirmed** by comparing `current_params` to `write_params`

5. **System verified** before allowing motor operation

6. **Hardware proves execution** through analytics position feedback

7. **Failure handled** with exceptions, logging, and user notification

8. **Complete traceability** from GUI input to physical motor movement

---

## 📞 DOCUMENTATION FEEDBACK

If you need clarification on any topic:
1. Check the specific README file listed above
2. Use code line references to examine actual implementation
3. Review the troubleshooting section
4. Trace through the verification checklist

**These README files do NOT modify code** - they explain existing implementation.
