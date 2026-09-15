"""Bounded live stroke update test for warning-free pistons 1..9."""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Model import Model, NullBridge, RunMode, MachineState
from app import params, tags, drive_status
from app.plc import PlcClient

DISPLAY = tuple(range(1, 10))
def snapshot(c):
    out=[]
    for shown in DISPLAY:
        axis=tags.axis_from_display(shown)
        status=int(c.read(tags.axis_field(axis,tags.STATUS_WORD)))
        warn=int(c.read(tags.axis_field(axis,tags.WARN_WORD)))
        pos=params.to_mm(c.read(tags.axis_field(axis,tags.ACTUAL_POSITION)))
        out.append({'piston':shown,'axis':axis,'status':status,'warn':warn,'position_mm':pos,'problems':drive_status.problems(warn,status)})
    return out

c=PlcClient('192.168.1.1',1)
evidence={'before':None,'queued':None,'after':None,'commands':None}
try:
    if not c.connect(): raise RuntimeError('controller not reachable')
    evidence['before']=snapshot(c)
    if any(c.read(t) for t in (tags.RUN_SINGLE,tags.RUN_CONTINUOUS,tags.RUN_CURVE)):
        raise RuntimeError('run command already active')
    m=Model(transport=c,is_live=True); m.register_bridge(NullBridge())
    for row in evidence['before']:
        axis=row['axis']; m.toggle(axis,True)
    m.create_set('Live stroke stress')
    for motor,row in zip(m.all_motors,evidence['before']):
        low=max(-20,min(300,int(row['position_mm'])-20))
        motor.set_param('Position 1',low); motor.set_param('Position 2',low+40)
        motor.set_param('Speed 1',100); motor.set_param('Speed 2',100)
    m._homed_axes={motor.axis for motor in m.all_motors}; m._set_state(MachineState.HOMED); m.rest_position='hold'; m._mark_live_motors(); m.start_monitoring()
    if not m.start(RunMode.CONTINUOUS): raise RuntimeError('could not start continuous')
    time.sleep(1.0)
    m.change_stroke_live(60)
    evidence['queued']={'pending':m._pending_live_stroke,'positions':snapshot(c)}
    time.sleep(4.0)
    evidence['after']=snapshot(c)
    m.stop(immediate=True,park=False)
    time.sleep(.4)
    evidence['commands']={name:bool(c.read(tag)) for name,tag in [('Run_1',tags.RUN_SINGLE),('Run_2',tags.RUN_CONTINUOUS),('Run_Curve',tags.RUN_CURVE)]}
finally:
    try: c.write(tags.RUN_SINGLE,0); c.write(tags.RUN_CONTINUOUS,0); c.write(tags.RUN_CURVE,0)
    except Exception: pass
    c.close()
    out=Path('logs/verification')/('live-stroke-stress-'+time.strftime('%Y%m%d-%H%M%S')+'.json'); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(evidence,indent=2)); print(json.dumps(evidence,indent=2)); print('Saved',out)
