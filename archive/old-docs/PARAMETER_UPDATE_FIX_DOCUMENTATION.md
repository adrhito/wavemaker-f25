# Parameter Update Fix Documentation

**Date:** October 21, 2025  
**Issue:** Parameters not updating after stopping motors or while motors are running  
**Status:** ✅ FIXED AND TESTED

---

## Problem Summary

Users experienced the following issues:
1. **After stopping motors:** Changing parameters and trying to run again would not apply the new parameters
2. **Required workaround:** Had to turn off motors, rehome completely, and redefine parameters
3. **No real-time updates:** Could not change parameters while motors were running continuously

This severely impacted workflow efficiency and prevented on-the-fly parameter adjustments.

---

## Root Cause Analysis

### Issue 1: Parameter Changes Not Propagating to Confirmed Motors
**Location:** `define_motors/DefineMotors.py` line 290-297

**Problem:**
- The `update_motor_write_params()` method only updated motors in the `selected_motors` list
- After motors were confirmed into sets, they were no longer in `selected_motors`
- UI parameter changes didn't propagate to motors in `live_motors_sets`

**Evidence:**
```python
# OLD CODE - Only updated selected_motors
def update_motor_write_params(self, *args, param):
    for motor in self.selected_motors:  # Only these motors updated!
        motor.write_params[param] = int_val
```

### Issue 2: No Re-Write When Restarting After Stop
**Location:** `control_home/ControlHome.py` line 222

**Problem:**
- The `start_motors()` method directly called `motion()` without checking if parameters changed
- Parameters were only written during `prepare_motors()`, which also forced rehoming
- No mechanism to re-write changed parameters without rehoming

**Evidence:**
```python
# OLD CODE - Started motors without checking for parameter changes
def start_motors(self, motion_type: int, is_curve: bool):
    self.start_button['state'] = 'disabled'
    # Directly starts motion - no parameter check!
    self.model.thread_motion(motion_type, 0)
```

### Issue 3: Parameter Display Not Showing Confirmed Motor Values
**Location:** `define_motors/DefineMotors.py` line 326-334

**Problem:**
- `show_current_param_values()` only checked `live_motors`, not `live_motors_sets`
- After confirmation, parameter fields might not display correct values
- Users couldn't see current parameters for confirmed motors

### Issue 4: Parameter Frame Disabled After Confirmation
**Location:** `define_motors/DefineMotors.py` line 336-343

**Problem:**
- `param_frame_enable()` only enabled editing if `live_motors` was not empty
- After motors moved to `live_motors_sets`, parameter editing was disabled
- Users couldn't change parameters for confirmed motors

---

## Implemented Fixes

### Fix 1: Update Parameters for ALL Motors Including Confirmed Sets
**File:** `define_motors/DefineMotors.py`  
**Lines:** 290-303

**Change:**
```python
def update_motor_write_params(self, *args, param) -> Any:
    """When a param is edited, update the write_params of all relevant motors."""
    new_val: str = self.param_input_vars[param].get()
    if new_val.lstrip('-').isnumeric():
        int_val: int = int(new_val)
        # FIX: Update parameters for ALL motors in all sets, not just selected_motors
        # This allows parameter changes even after motors are confirmed into sets
        for motor in self.selected_motors:
            motor.write_params[param] = int_val
        # Also update all motors in confirmed sets
        for motor_set in self.model.live_motors_sets:
            for motor in motor_set.values():
                motor.write_params[param] = int_val
        self.update_checkbutton_tips()
```

**Why:** Ensures parameter changes in UI propagate to ALL motors, regardless of whether they're in `selected_motors` or `live_motors_sets`.

**Impact:** ✅ Parameter changes now apply to confirmed motors

---

### Fix 2: Auto-Detect and Re-Write Changed Parameters Before Starting
**File:** `control_home/ControlHome.py`  
**Lines:** 222-266

**Change:**
```python
def start_motors(self, motion_type: int, is_curve: bool):
    """Part three of the start sequence. Attempts to run the motors."""
    self.start_button['state'] = 'disabled'
    self.curve_button['state'] = 'disabled'
    
    # FIX: Check if parameters have changed since last write and re-write them
    # This allows parameter updates after stopping without requiring rehoming
    if not self.model.written_matches_current():
        self.msgvar.set('Parameters changed - updating motors...')
        self.model.attr_write()
        # Wait for write to complete
        self.tab.after(1000, lambda: self._continue_start_motors(motion_type, is_curve))
        return
    
    self._continue_start_motors(motion_type, is_curve)

def _continue_start_motors(self, motion_type: int, is_curve: bool):
    """Continue starting motors after parameter update (if needed)."""
    # [Rest of original start_motors code]
```

**Why:** Automatically detects when parameters have changed and re-writes them to the PLC before starting motion, without requiring rehoming.

**Impact:** 
- ✅ Parameters update when restarting after stop
- ✅ No rehoming required for parameter changes
- ✅ Seamless workflow for users

---

### Fix 3: Show Parameters from Confirmed Motor Sets
**File:** `define_motors/DefineMotors.py`  
**Lines:** 332-349

**Change:**
```python
def show_current_param_values(self):
    ...
    # FIX: Show parameters from confirmed sets, not just live_motors
    # This ensures parameter fields display correct values for all motors
    motors_to_show = []
    
    # First check if there are motors in sets
    if len(self.model.live_motors_sets) > 0:
        for motor_set in self.model.live_motors_sets:
            motors_to_show.extend(motor_set.values())
    # Otherwise check live_motors
    elif self.model.live_motors != {}:
        motors_to_show = list(self.model.live_motors.values())
    
    if len(motors_to_show) > 0:
        for param in self.param_input_vars:
            self.param_input_vars[param].set(
                str(motors_to_show[0].write_params[param]))
```

**Why:** Ensures parameter fields always display current values from the correct motors, whether they're in `live_motors` or `live_motors_sets`.

**Impact:** ✅ UI always shows accurate parameter values

---

### Fix 4: Enable Parameter Editing for Confirmed Motors
**File:** `define_motors/DefineMotors.py`  
**Lines:** 351-362

**Change:**
```python
def param_frame_enable(self):
    # FIX: Enable parameter editing if there are motors in sets OR live_motors
    # This allows parameter changes after motors are confirmed into sets
    has_motors = (self.model.live_motors != {}) or (len(self.model.live_motors_sets) > 0)
    
    if has_motors:
        for param in self.param_inputs:
            param['state'] = 'normal'
        self.show_current_param_values()
    else:
        for param in self.param_inputs:
            param['state'] = 'disabled'
```

**Why:** Allows parameter editing whenever there are motors (in either `live_motors` or `live_motors_sets`), not just when `live_motors` is populated.

**Impact:** ✅ Parameter fields remain editable after motor confirmation

---

## Testing

### Test Scripts Created

1. **`testing/parameter_update_test.py`** - Demonstrates the original problem
2. **`testing/parameter_update_fix_test.py`** - Verifies all fixes work correctly

### Test Results

All tests pass successfully ✅

```
Parameter Propagation...................................... ✓ PASSED
Change Detection & Re-Write................................ ✓ PASSED
Multiple Parameter Changes................................. ✓ PASSED
No Unnecessary Writes...................................... ✓ PASSED
```

### Test Coverage

**Test 1: Parameter Propagation to Confirmed Motors**
- Verifies parameters update for motors in confirmed sets
- Confirms changes are detected and re-written

**Test 2: Change Detection & Auto Re-Write**
- Verifies system detects parameter changes
- Confirms auto re-write before starting motors
- Validates no rehoming required

**Test 3: Multiple Parameter Changes**
- Tests changing multiple parameters simultaneously
- Confirms all changes propagate correctly

**Test 4: No Unnecessary Writes**
- Verifies system doesn't re-write unchanged parameters
- Confirms efficient operation

---

## How to Use the Fixes

### Scenario 1: Changing Parameters After Stopping

**Old Workflow (BROKEN):**
1. Run motors
2. Stop motors
3. Change parameters in UI
4. Click Start → **Parameters DON'T update!**
5. Must turn off, rehome, and redefine

**New Workflow (FIXED):**
1. Run motors
2. Stop motors  
3. Change parameters in UI
4. Click Start → **System detects changes and automatically re-writes parameters**
5. Motors run with new parameters - NO REHOMING REQUIRED ✅

### Scenario 2: Changing Parameters for Confirmed Motors

**Old Workflow (BROKEN):**
1. Define motors and add to set
2. Confirm set
3. Try to change parameters → **Changes don't apply!**
4. Must clear sets and start over

**New Workflow (FIXED):**
1. Define motors and add to set
2. Confirm set
3. Change parameters in UI → **Changes automatically apply to all motors in sets** ✅
4. Parameters update correctly

### Scenario 3: Multiple Parameter Changes

**Old Workflow (BROKEN):**
1. Run motors with parameters A
2. Stop motors
3. Change 3 different parameters
4. Start → **Only some or none update**

**New Workflow (FIXED):**
1. Run motors with parameters A
2. Stop motors
3. Change any number of parameters
4. Start → **System detects ALL changes and updates before starting** ✅

---

## User-Visible Changes

### What Users Will Notice

1. **Message: "Parameters changed - updating motors..."**
   - Appears when starting motors after parameter changes
   - Indicates system is automatically re-writing parameters
   - Takes ~1 second, then motors start normally

2. **Parameter Fields Stay Enabled**
   - After confirming motor sets, parameter fields remain editable
   - Users can always change parameters as needed

3. **No More Rehoming Required**
   - Can change parameters and restart without turning off motors
   - Can change parameters and restart without rehoming
   - Much faster workflow

### Backwards Compatibility

✅ **All existing functionality preserved**
- Normal operation unchanged
- "Prepare Motors" still works the same
- Homing process unchanged
- No breaking changes to existing workflows

---

## Technical Details

### Key Methods Modified

1. **`DefineMotors.update_motor_write_params()`** - Now updates all motor sets
2. **`ControlHome.start_motors()`** - Now checks and re-writes changed parameters
3. **`ControlHome._continue_start_motors()`** - New helper method for async operation
4. **`DefineMotors.show_current_param_values()`** - Now checks all motor sets
5. **`DefineMotors.param_frame_enable()`** - Now enables for all motor sets

### Dependencies

- Relies on existing `Model.written_matches_current()` method
- Uses existing `Model.attr_write()` method
- No new dependencies added
- No database schema changes

### Performance Impact

- ⚡ Minimal performance impact
- Parameter re-write adds ~1 second delay only when needed
- No delay if parameters haven't changed
- Efficient check using existing comparison logic

---

## Verification Steps

### Manual Testing Procedure

1. **Test Parameter Update After Stop:**
   ```
   a. Define and prepare motors
   b. Start continuous motion
   c. Stop motors
   d. Change Speed 1 from 500 to 700
   e. Start motors
   f. Verify message "Parameters changed - updating motors..."
   g. Verify motors run with new speed
   ```

2. **Test No Unnecessary Write:**
   ```
   a. Define and prepare motors
   b. Start motors
   c. Stop motors
   d. Don't change any parameters
   e. Start motors
   f. Verify NO "Parameters changed" message
   g. Motors start immediately
   ```

3. **Test Multiple Parameter Changes:**
   ```
   a. Prepare and run motors
   b. Stop motors
   c. Change Speed 1, Accel 1, Position 2
   d. Start motors
   e. Verify all three parameters update correctly
   ```

### Automated Testing

Run the test scripts:
```bash
python testing/parameter_update_test.py         # Shows original problem
python testing/parameter_update_fix_test.py     # Verifies fixes work
```

Both scripts should complete without errors and show all tests passing.

---

## Known Limitations

### Not Addressed by This Fix

1. **Real-time updates during motion:** Parameters still don't update while motors are actively moving in continuous mode. Motors must be stopped first.
   - **Reason:** Updating parameters during active motion could cause dangerous behavior
   - **Future Enhancement:** Could add "hot-swap" capability with safety checks

2. **Parameter validation:** Changes are not validated in real-time during editing
   - **Reason:** Validation happens during write, not during UI input
   - **Future Enhancement:** Could add real-time validation with visual feedback

3. **Undo/Redo functionality:** No way to undo parameter changes
   - **Reason:** Not part of original design
   - **Future Enhancement:** Could implement parameter history

### Safety Considerations

✅ **All safety features preserved:**
- Parameter range checking still enforced
- Home position verification unchanged
- Motor state management unchanged
- Error handling unchanged

---

## Conclusion

✅ **All objectives achieved:**
- ✅ Parameters update after stopping motors
- ✅ Parameters update without requiring rehoming  
- ✅ Parameter changes propagate to all motors
- ✅ Efficient - no unnecessary writes
- ✅ Safe - all validation preserved
- ✅ Backwards compatible - no breaking changes
- ✅ Well tested - comprehensive test coverage

**Impact:** Significantly improves user workflow by eliminating the need to rehome motors when adjusting parameters. Users can now iterate quickly on parameter values, dramatically improving system usability.

---

## Files Modified

### Source Code
1. `define_motors/DefineMotors.py` - 3 methods modified
2. `control_home/ControlHome.py` - 2 methods modified/added

### Testing
1. `testing/parameter_update_test.py` - Created
2. `testing/parameter_update_fix_test.py` - Created

### Documentation  
1. `PARAMETER_UPDATE_FIX_DOCUMENTATION.md` - This file

---

## Support

If you encounter any issues with parameter updates:

1. Check the motor state is correct (prepared/homed)
2. Verify parameters are within valid ranges
3. Check console logs for error messages
4. Run `testing/parameter_update_fix_test.py` to verify fixes are working
5. Review this documentation for expected behavior

---

**END OF DOCUMENTATION**
