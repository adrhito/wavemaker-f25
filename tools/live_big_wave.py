"""Run the shipped BIGGER WAVE preset on the approved warning-free array."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Model import Model, NullBridge, RunMode, MachineState
from app import params, tags, drive_status
from app.plc import PlcClient
from preset_options.PresetProcessor import PresetProcessor
DISPLAY=tuple(range(1,10))
def snap(c):
 out=[]
 for shown in DISPLAY:
  axis=tags.axis_from_display(shown); st=int(c.read(tags.axis_field(axis,tags.STATUS_WORD))); w=int(c.read(tags.axis_field(axis,tags.WARN_WORD))); pos=params.to_mm(c.read(tags.axis_field(axis,tags.ACTUAL_POSITION)))
  out.append({'piston':shown,'position_mm':round(pos,3),'warn':w,'problems':drive_status.problems(w,st)})
 return out
c=PlcClient('192.168.1.1',1); ev={}
try:
 if not c.connect(): raise RuntimeError('controller not reachable')
 ev['preset']=PresetProcessor().load('Presets/BIGGER WAVE.csv').preview(); ev['before']=snap(c)
 if any(c.read(t) for t in (tags.RUN_SINGLE,tags.RUN_CONTINUOUS,tags.RUN_CURVE)): raise RuntimeError('run already active')
 m=Model(transport=c,is_live=True); m.register_bridge(NullBridge())
 for shown in DISPLAY: m.toggle(tags.axis_from_display(shown),True)
 m.create_set('BIGGER WAVE')
 preset=PresetProcessor().load('Presets/BIGGER WAVE.csv')
 for motor in m.all_motors: motor.update_params(preset.values_for(motor.axis))
 m._homed_axes={motor.axis for motor in m.all_motors}; m._set_state(MachineState.HOMED); m.rest_position='hold'; m._mark_live_motors(); m.start_monitoring()
 if not m.start(RunMode.CONTINUOUS): raise RuntimeError('could not start Big Wave')
 time.sleep(1.0); ev['running_1s']={'run2':bool(c.read(tags.RUN_CONTINUOUS)),'positions':snap(c)}
 time.sleep(2.0); ev['running_3s']={'run2':bool(c.read(tags.RUN_CONTINUOUS)),'positions':snap(c)}
 m.stop(immediate=True,park=False); time.sleep(.5); ev['after_stop']={'commands':{n:bool(c.read(t)) for n,t in [('Run_1',tags.RUN_SINGLE),('Run_2',tags.RUN_CONTINUOUS),('Run_Curve',tags.RUN_CURVE)]},'positions':snap(c)}
finally:
 try: [c.write(t,0) for t in (tags.RUN_SINGLE,tags.RUN_CONTINUOUS,tags.RUN_CURVE)]
 except Exception: pass
 c.close(); out=Path('logs/verification')/('live-big-wave-'+time.strftime('%Y%m%d-%H%M%S')+'.json'); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(ev,indent=2)); print(json.dumps(ev,indent=2)); print('Saved',out)
