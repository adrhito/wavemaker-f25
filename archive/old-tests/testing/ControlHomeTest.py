from pytest import *
import pytest
import sys
from tkinter import Canvas, ttk
sys.path.insert(0,"C:/Users/admin/Desktop/new_GUI/control_home")
sys.path.append("C:/Users/admin/Desktop/new_GUI")
from Model import Model
from ControlHome import ControlHome

# Authors / Changes Made: TEAM D, COMP523 Fall 23

notebook = ttk.Notebook()
model = Model()
control = ControlHome(notebook, model)
#Test control home on select method to see if motors are activated 
def test_onSelect_diabled():
    assert(control.msgvar.get() == "Visit the define motors frame to activate motors.")
#Test control home to see if message is updated
def test_update_msg():
    control.msgvar.set("Hi")
    assert (control.msgvar.get() == "Hi")
#Test control home to see if motors are preparing 
def test_prepare_motors():
    control.prepare_motors(2,False)
    assert control.msgvar.get() == 'Motors already written .. skipping to homing.'
#Test control home to see if motors are homing 
def test_home_motors():
    control.home_motors(2)
    assert control.msgvar.get() == 'Homing motors...'
#Test control home to see if motors are homing 
def test_start_motors():
    control.start_motors(2,True)
    assert control.msgvar.get() == 'Starting curve...'
#Test control home to see if motors are stopping 
def test_stop_motors():
    control.stop_motors(2)
    assert control.msgvar.get() == 'Motors stopped'
#Test control home to see if motors have been reset
def test_off_and_reset():
    control.off_and_reset()
    assert control.msgvar.get() == 'Visit the define motors frame to activate motors.'
