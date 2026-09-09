import sys
# from pytest import *
import pytest

import tkinter as tk
sys.path.insert(0,'C:/Users/admin/Desktop/new_GUI')
from Model import Model
from Motor import Motor 

# Authors / Changes Made: TEAM D, COMP523 Fall 23

# Create a temporary window to be able to use IntVar
test = tk.Tk()
test.geometry("300x100") 
int1 = tk.IntVar()

#Create Model
model = Model()

#Check to see if model is writing to the motors
def test_written():
    assert(model.written_matches_current() == True)

def test_write_success_True():
    model = Model()
    assert model.write_success() == True
def test_write_success_False():
    model = Model()
    model.write_success = False
    assert model.write_success == False

#Test model and motor interactions 
#makedict() tests sets motor to be activated or deactivated

#Test field motdict to see if makedict recognizes motor and sets it to the assigned status
def test_makedict_active():
    #Set IO parameter to 1 to be activated
    int1.set(1)
    model.makedict(2,int1)
    assert model.motdict[2] == 1

#Test field motdict to see if makedict recognizes motor and sets it to not active
def test_makedict_not_active():
    #Set IO parameter to 1 to be activated
    int1.set(0)
    model.makedict(2,int1)
    assert model.motdict[2] == 0

#Test motor define method 
#If motor in motdict is set to 1, it will be added to the live motors
def test_active_motor_make_dict():
    int1.set(1)
    model.makedict(3, int1)
    model.makedict(4, int1)
    model.motor_define()
    motor4 = Motor(3, True)
    motor5 = Motor(4, True)
    assert model.live_motors[3].motor_ID == motor4.motor_ID
    assert model.live_motors[4].motor_ID == motor5.motor_ID

def test_not_active_motor_make_dict():
    int1.set(0)
    model.makedict(3, int1)
    model.makedict(4, int1)
    model.motor_define()
    motor4 = Motor(3, True)
    motor5 = Motor(4, True)
    assert model.live_motors == {}

#Test motor define with active and not active motors
def test_combination_make_dict():
    int1.set(1)
    model.makedict(4, int1)
    model.makedict(5, int1)
    model.makedict(6, int1)
    motor5 = Motor(4, True)
    motor6 = Motor(5, True)
    motor7 = Motor(6, True)
    int1.set(0)
    model.makedict(4, int1)
    model.makedict(5, int1)
    model.motor_define()
    assert len(model.live_motors) == 1


#Test model live motors mock reset 
def test_mock_live_reset():
    int1.set(0)
    model.makedict(6, int1)
    model.live_motor_reset_mock()
    assert model.live_motors == {}
    
#Test reset with multiple motors
def test_mock_live_multiple_reset():
    int1.set(1)
    model.makedict(8, int1)
    model.makedict(18, int1)
    model.makedict(28, int1)
    model.makedict(29, int1)
    model.motor_define()
    model.live_motor_reset_mock()
    assert model.live_motors == {}

def test_mock_not_live_multiple_reset():
    int1.set(0)
    model.makedict(8, int1)
    model.makedict(18, int1)
    model.makedict(28, int1)
    model.makedict(29, int1)
    model.live_motor_reset_mock()
    assert model.live_motors == {}

#Test model and motor interactions 
#Test interactions between multiple methods in the model class that interact together
#Test if activated motors get recognized in correct rows
def test_rows():
    int1.set(1)
    print(int1.get)
    model.makedict(0,int1)
    model.makedict(3,int1)
    model.makedict(5,int1)
    model.motor_define()
    assert(model.get_rows() == [1, 3])

#Test if motors turn off and new ones are processed
def test_model_reset():
    model.live_motor_reset_mock()
    int1.set(0)
    model.makedict(0,int1)
    model.makedict(3,int1)
    model.makedict(5,int1)
    assert(model.get_rows() == [])

#Test if activated motors get recognized in correct rows after changes
def test_rows_change():
    int1.set(1)
    model.makedict(26, int1)
    model.makedict(13, int1)
    model.motor_define()
    assert(model.get_rows() == [2, 3])

#Test if activated motors get recognized to corresponding columns
def test_columns():
    int1.set(1)
    model.makedict(0, int1)
    model.makedict(1, int1)
    model.makedict(2, int1)
    model.makedict(5, int1)
    model.makedict(10, int1)
    model.makedict(11, int1)
    model.makedict(12, int1)
    model.motor_define()
    assert model.get_columns() == [1, 2, 4, 5, 9]

#Test if activated motors get recognized to corresponding columns 
#Testing motors that have been deactivated and still activated
def test_cols_change():
    int1.set(0)
    model.mock_live_motor_reset()
    model.makedict(0, int1)
    model.makedict(1, int1)
    model.makedict(2, int1)
    model.makedict(5, int1)
    model.makedict(10, int1)
    model.makedict(11, int1)
    model.makedict(12, int1)
    model.makedict(13, int1)
    model.motor_define()
    assert model.get_columns() == [9]

#Test get column motors that returns list of motors activated
def test_col_motor():
    motor27 = Motor(26, True)
    assert model.get_column(9)[0].motor_ID == motor27.motor_ID

#Test get column motors that returns list of multiple motors activated
def test_col_multiple_motors():
    int1.set(1)
    model.makedict(24, int1)
    model.makedict(25, int1)
    model.motor_define()
    motor25 = Motor(24, True)
    motor26 = Motor(25, True)
    motor27 = Motor(26, True)
    result = []
    for motor in model.get_column(9):
        result.append(motor.motor_ID)
    assert result == [motor27.motor_ID, motor25.motor_ID, motor26.motor_ID]

#Test get row motors that returns list of motors activated
def test_row_motors():
    motor25 = Motor(24, True) 
    assert model.get_row(1)[0].motor_ID == 25

#Test get column motors that returns list of multiple motors activated
def test_row_mumotors():
    assert model.get_row(1)[0].motor_ID == 25
    int1.set(1)
    model.makedict(2, int1)
    model.makedict(5, int1)
    model.makedict(8, int1)
    model.motor_define()
    motor3 = Motor(2, True)
    motor6 = Motor(5, True)
    motor9 = Motor(8, True)
    motore  = {
        3: motor3, 
        6: motor6,
        9: motor9
    }
    result = []
    for motor in model.get_row(3):
        assert motor.__eq__(motore.get(motor.motor_ID))
