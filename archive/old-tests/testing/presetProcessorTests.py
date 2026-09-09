
import sys
# from pytest import *
# import pytest

import tkinter as tk
sys.path.insert(0,"C:/Users/admin/Desktop/new_GUI")
from Model import Model
from Motor import Motor 
from preset_options.Preset import Preset
from preset_options.PresetProcessor import PresetProcessor

# Authors / Changes Made: TEAM D, COMP523 Fall 23

model = Model()
presetProcessor = PresetProcessor(model)

#Test if preset csv loads into method and returns preset

#Testing if preset columns field has loaded in values from csv, testing few cols
#Testing with accel 1 column
def test_process_preset_accel1Columns():
    assert presetProcessor.processPreset("Preset 2.csv").columns.get("Accel 1") == [10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000,10000 ]

#Testing with motor column
def test_process_preset_motorColumns():
    assert presetProcessor.processPreset("Preset 2.csv").columns.get("Motor") == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,19,20,21,22,23,24,25,26,27,28,29]

#Test with Jerk 2
def test_process_preset_jerk2Columns():
    assert presetProcessor.processPreset("Preset 2.csv").columns.get("Jerk 2") == [1500, 1500, 1500, 1500, 1500, 1500, 1500,1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500, 1500,1500,1500,1500,1500,1500,1500,1500,1500,1500,1500,1500]

#Testing if rows from csv have been loaded for each motor
def test_process_preset_rows():
    assert len(presetProcessor.processPreset("Preset 2.csv").rows) == 30

#Testing if values from motor 1 row such as position 1 has been loaded in 
def test_process_preset_rows1_position1():
    assert presetProcessor.processPreset("Preset 2.csv").rows[0]["Position 1"] == -10

#Testing if values from motor 15 row such as position 2 has been loaded in 
def test_process_preset_row15_position2():
    assert presetProcessor.processPreset("Preset 2.csv").rows[16]["Position 2"] == 300

#Test if all row field has the same preset value for each column that can be applied to all motors at once
#18 columns excluding motor, each column has one value for all
def test_process_preset_allrow():
    assert len(presetProcessor.processPreset("Preset 2.csv").all_row) == 18

#Test all row speed 1 key 
def test_process_preset_allrow_speed1():
    assert presetProcessor.processPreset("Preset 2.csv").all_row["Speed 1"] == '450'

#Test all row Accel 2 key 
def test_process_preset_allrow_Accel2():
    assert presetProcessor.processPreset("Preset 2.csv").all_row["Accel 2"] == '10000'

#Test all row profile key 
def test_process_preset_allrow_profile():
    assert presetProcessor.processPreset("Preset 2.csv").all_row["Profile"] == '1'
