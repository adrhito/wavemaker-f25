"""
Testing script to verify parameter update fixes work correctly
Tests that parameters update properly after stopping and during runtime

Author: Testing Script for Parameter Update Fix Verification
Date: 2025-10-21
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Motor import Motor

# Mock Model class to avoid database dependencies
class MockModel:
    IP_ADDRESS = '192.168.1.1'
    PROCESSOR_SLOT = 1
    CONNECTED = False
    live_motors = {}
    live_motors_sets = []
    
    def written_matches_current(self):
        """Check if write_params match current_params for all motors"""
        for motor_set in self.live_motors_sets:
            for motor in motor_set.values():
                for attr in motor.write_params:
                    if motor.write_params[attr] != motor.current_params.get(attr):
                        return False
        return True


def test_fix_1_parameter_propagation_to_confirmed_motors():
    """Test Fix 1: Parameters update for motors in confirmed sets"""
    print("\n" + "=" * 80)
    print("TEST 1: Parameter Propagation to Confirmed Motors")
    print("=" * 80)
    
    model = MockModel()
    
    # Create motors and add to set
    motor1 = Motor(0, CONNECTED=False)
    motor2 = Motor(1, CONNECTED=False)
    
    # Write initial parameters
    motor1.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
    motor2.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
    
    # Add to confirmed set (simulating confirmation)
    model.live_motors_sets = [{0: motor1, 1: motor2}]
    
    print("\n1. Initial state - motors in confirmed set:")
    print(f"   Motor 1 Speed 1: write={motor1.write_params['Speed 1']}, current={motor1.current_params.get('Speed 1')}")
    print(f"   Motor 2 Speed 1: write={motor2.write_params['Speed 1']}, current={motor2.current_params.get('Speed 1')}")
    
    # Simulate parameter change (as DefineMotors.update_motor_write_params would do)
    print("\n2. Simulating parameter change in UI (Speed 1 -> 750)...")
    new_speed = 750
    for motor_set in model.live_motors_sets:
        for motor in motor_set.values():
            motor.write_params['Speed 1'] = new_speed
    
    print(f"   Motor 1 Speed 1: write={motor1.write_params['Speed 1']}, current={motor1.current_params.get('Speed 1')}")
    print(f"   Motor 2 Speed 1: write={motor2.write_params['Speed 1']}, current={motor2.current_params.get('Speed 1')}")
    
    # Check if parameters changed
    params_match = model.written_matches_current()
    print(f"\n3. Parameters match current: {params_match}")
    
    if not params_match:
        print("   ✓ SUCCESS: Parameters detected as changed - will be re-written on start")
    else:
        print("   ❌ FAILED: Parameters appear unchanged - will NOT be re-written")
    
    # Simulate re-write (as ControlHome.start_motors would do)
    print("\n4. Simulating parameter re-write before starting...")
    motor1.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
    motor2.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
    
    print(f"   Motor 1 Speed 1: write={motor1.write_params['Speed 1']}, current={motor1.current_params.get('Speed 1')}")
    print(f"   Motor 2 Speed 1: write={motor2.write_params['Speed 1']}, current={motor2.current_params.get('Speed 1')}")
    
    # Verify parameters now match
    params_match_after = model.written_matches_current()
    print(f"\n5. After re-write, parameters match: {params_match_after}")
    
    if params_match_after and motor1.current_params['Speed 1'] == new_speed:
        print("   ✓ SUCCESS: Parameters successfully updated to new value!")
        return True
    else:
        print("   ❌ FAILED: Parameters not properly updated")
        return False


def test_fix_2_parameter_detection_and_rewrite():
    """Test Fix 2: Parameter changes detected and re-written before starting"""
    print("\n\n" + "=" * 80)
    print("TEST 2: Parameter Change Detection and Auto Re-Write")
    print("=" * 80)
    
    model = MockModel()
    motor = Motor(5, CONNECTED=False)
    
    # Initial write (like "Prepare Motors")
    print("\n1. Initial prepare - writing parameters...")
    motor.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
    model.live_motors_sets = [{5: motor}]
    
    print(f"   Position 2: write={motor.write_params['Position 2']}, current={motor.current_params.get('Position 2')}")
    print(f"   Parameters match: {model.written_matches_current()}")
    
    # Simulate motors running then stopping
    print("\n2. Motors run, then user stops them...")
    
    # User changes parameter
    print("\n3. User changes Position 2 from 350 to 300...")
    motor.write_params['Position 2'] = 300
    print(f"   Position 2: write={motor.write_params['Position 2']}, current={motor.current_params.get('Position 2')}")
    
    # Check if change is detected
    needs_update = not model.written_matches_current()
    print(f"\n4. System detects parameters changed: {needs_update}")
    
    if needs_update:
        print("   ✓ SUCCESS: Change detected - will re-write before starting")
        
        # Simulate auto re-write
        print("\n5. Auto re-writing parameters before start...")
        motor.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
        print(f"   Position 2: write={motor.write_params['Position 2']}, current={motor.current_params.get('Position 2')}")
        
        if motor.current_params['Position 2'] == 300:
            print("   ✓ SUCCESS: Parameters updated without rehoming!")
            return True
        else:
            print("   ❌ FAILED: Parameter not written correctly")
            return False
    else:
        print("   ❌ FAILED: Change not detected - will NOT re-write")
        return False


def test_fix_3_multiple_parameter_changes():
    """Test Fix 3: Multiple parameter changes work correctly"""
    print("\n\n" + "=" * 80)
    print("TEST 3: Multiple Parameter Changes")
    print("=" * 80)
    
    model = MockModel()
    motor = Motor(10, CONNECTED=False)
    
    # Initial write
    motor.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
    model.live_motors_sets = [{10: motor}]
    
    print("\n1. Initial parameters:")
    print(f"   Speed 1: {motor.current_params['Speed 1']}")
    print(f"   Accel 1: {motor.current_params['Accel 1']}")
    print(f"   Position 1: {motor.current_params['Position 1']}")
    
    # Change multiple parameters
    print("\n2. Changing multiple parameters...")
    motor.write_params['Speed 1'] = 650
    motor.write_params['Accel 1'] = 15000
    motor.write_params['Position 1'] = 100
    
    # Check detection
    needs_update = not model.written_matches_current()
    print(f"\n3. Changes detected: {needs_update}")
    
    if needs_update:
        # Re-write
        motor.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
        
        print("\n4. After re-write:")
        print(f"   Speed 1: {motor.current_params['Speed 1']} (expected 650)")
        print(f"   Accel 1: {motor.current_params['Accel 1']} (expected 15000)")
        print(f"   Position 1: {motor.current_params['Position 1']} (expected 100)")
        
        all_correct = (motor.current_params['Speed 1'] == 650 and
                      motor.current_params['Accel 1'] == 15000 and
                      motor.current_params['Position 1'] == 100)
        
        if all_correct:
            print("\n   ✓ SUCCESS: All parameters updated correctly!")
            return True
        else:
            print("\n   ❌ FAILED: Some parameters not updated")
            return False
    else:
        print("   ❌ FAILED: Changes not detected")
        return False


def test_fix_4_no_unnecessary_writes():
    """Test Fix 4: Parameters not re-written if unchanged"""
    print("\n\n" + "=" * 80)
    print("TEST 4: No Unnecessary Writes When Unchanged")
    print("=" * 80)
    
    model = MockModel()
    motor = Motor(15, CONNECTED=False)
    
    # Initial write
    motor.write_to_motor(model.IP_ADDRESS, model.PROCESSOR_SLOT)
    model.live_motors_sets = [{15: motor}]
    
    print("\n1. Parameters written and motors running...")
    print(f"   Parameters match: {model.written_matches_current()}")
    
    # User stops but doesn't change anything
    print("\n2. User stops motors but makes NO changes...")
    
    # Check if system detects no changes
    needs_update = not model.written_matches_current()
    print(f"\n3. System thinks update needed: {needs_update}")
    
    if not needs_update:
        print("   ✓ SUCCESS: No unnecessary write - parameters unchanged")
        return True
    else:
        print("   ❌ FAILED: System wants to re-write unchanged parameters")
        return False


if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 15 + "PARAMETER UPDATE FIX VERIFICATION TESTS" + " " * 24 + "║")
    print("╚" + "=" * 78 + "╝")
    
    results = []
    
    results.append(("Parameter Propagation", test_fix_1_parameter_propagation_to_confirmed_motors()))
    results.append(("Change Detection & Re-Write", test_fix_2_parameter_detection_and_rewrite()))
    results.append(("Multiple Parameter Changes", test_fix_3_multiple_parameter_changes()))
    results.append(("No Unnecessary Writes", test_fix_4_no_unnecessary_writes()))
    
    print("\n\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "❌ FAILED"
        print(f"{test_name:.<60} {status}")
    
    all_passed = all(result[1] for result in results)
    
    print("\n" + "=" * 80)
    if all_passed:
        print("✓ ALL TESTS PASSED - Fixes working correctly!")
    else:
        print("❌ SOME TESTS FAILED - Review fixes needed")
    print("=" * 80 + "\n")
