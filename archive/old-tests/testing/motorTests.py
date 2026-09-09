
from pytest import *
import pytest
import sys
sys.path.insert(0,"C:/Users/admin/Desktop/new_GUI")
from Motor import Motor

#We want to see if the motors are being activated and getting recognized

# Authors / Changes Made: TEAM D, COMP523 Fall 23

#Test if motor 0 is activated
def test_write_params_0():
    motor = Motor(0, True)
    
    assert(motor.write_params['Move Type'] == 0)

def test_write_params_1():
    motor = Motor(1,True)
    motor.write_params['Move Type'] = 3
    with pytest.raises(Exception) as excinfo:
        motor.write_movetype("?", 1)
    assert str(excinfo.value) == "The MoveType argument must be a 1 or a 0. 0 for absolute 1 for Incremental"
def test_write_dict_true():
    motor = Motor(5, True)
    assert motor.valid_write_dict() == True
def test_write_dict_false():
    motor = Motor(5, True)
    motor.write_params = {'accel': ''}
    assert motor.valid_write_dict() == False

#Test if motors get recognized in proper column 
def test_activated_motor_col():
    motor3 = Motor(3, True)
    motor3.motor_sort()
    assert motor3.column == 2


#Test if motors get recognized in proper rows
def test_activated_motor_row():
    motor3 = Motor(3, True)
    motor3.motor_sort()
    assert motor3.row == 1

