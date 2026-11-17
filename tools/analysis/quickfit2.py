import sys, json, time, numpy as np
sys.path.insert(0,'tools/camera')
import vision_channel as vc
entries=vc.usable_runs()
t=time.time()
for e in entries:
    try: vc.run_flux(e)
    except Exception as ex: print('flux skip', e['run']['name'], ex)
print('flux for %d runs in %.0f s'%(len(entries), time.time()-t), flush=True)
import pickle, os
pk='analytics/rig/cases.pkl'
store=pickle.load(open(pk,'rb')) if os.path.exists(pk) else {}
cases=[];names=[];kinds=[]
for e in entries:
    n=e['run']['name']
    if n not in store:
        try: store[n]=vc.load_case2(n, entries)
        except Exception as ex: print('skip', n, ex); continue
    cases.append(store[n]); names.append(n); kinds.append(e.get('kind','single'))
pickle.dump(store,open(pk,'wb'))
vc.attach_next_fire(cases,names)
print('%d cases'%len(cases), flush=True)
def thin(c):
    d=dict(c); d['meas']=c['meas'][:,::2]; d['x']=c['x'][::2]; return d
k=int(len(cases)*0.7)
train=[thin(c) for c in cases[:k]]
prev=json.load(open('analytics/rig/channel_params.json'))
best=None
for L in (27.5,):
    st=dict(prev); st.update(L=float(L))
    q=vc.fit(train, start=st)
    e=np.median([vc.score(c,q)[0] for c in train])
    print('start L=%g -> L %.2f  fitted median %.1f%%'%(L,q['L'],100*e), flush=True)
    if best is None or e<best[0]: best=(e,q)
p=best[1]
json.dump(p,open('analytics/rig/channel_params.json','w'),indent=1)
print('params', {a:round(float(b),3) for a,b in p.items()}, flush=True)
rows=[]
for c,n,kd in zip(cases[k:],names[k:],kinds[k:]):
    err,rms,peak=vc.score(c,p)
    pre=(c['t']>=-6)&(c['t']<0); bg=float(np.sqrt(np.nanmean((c['meas'][pre]-np.nanmean(c['meas'][pre],axis=0))**2)))
    rows.append((n,kd,err,rms,peak,bg))
    print('  %-30s %-11s %6.1f%%  wave rms %5.1f  peak %5.1f  bg %4.1f'%(n,kd,100*err,rms,peak,bg), flush=True)
e=np.array([r[2] for r in rows]); print('HELD-OUT median %.1f%%  mean %.1f%%'%(100*np.median(e),100*e.mean()))
for kd in sorted(set(r[1] for r in rows)):
    v=[r[2] for r in rows if r[1]==kd]; print('  %-12s n=%2d median %.1f%%'%(kd,len(v),100*np.median(v)))
clean=[r[2] for r in rows if r[5]<1.5 and r[3]>3]; print('clean (background<1.5 mm, wave rms>3 mm): n=%d median %.1f%%'%(len(clean),100*np.median(clean) if clean else float('nan')))
big=[r[2] for r in rows if r[3]>5]; print('held-out with wave rms > 5 mm: n=%d median %.1f%%'%(len(big),100*np.median(big) if big else float('nan')))
