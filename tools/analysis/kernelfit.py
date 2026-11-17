"""Empirical per-row response kernels from the recorded runs; held-out score."""
import sys, pickle, json, numpy as np
sys.path.insert(0,'tools')
import vision_channel as vc
store=pickle.load(open('analytics/rig/cases.pkl','rb'))
entries=vc.usable_runs()
names=[e['run']['name'] for e in entries if e['run']['name'] in store]
kinds={e['run']['name']:e.get('kind','single') for e in entries}
cases=[store[n] for n in names]
vc.attach_next_fire(cases,names)
DT=0.1; TAPS=400; ROWS=4; S=5
def design(c):
    t=c['t']; grid=np.arange(t[0]-TAPS*DT, t[-1]+DT/2, DT)
    q=np.array([np.interp(grid, c['tq'], c['Qrows'][r], left=0, right=0) for r in range(ROWS)])
    v=np.diff(q,axis=1,prepend=0)*0+q   # flux itself is the input
    n0=TAPS; X=np.zeros((len(t), ROWS*TAPS+1))
    for r in range(ROWS):
        for k in range(TAPS):
            X[:, r*TAPS+k]=v[r, n0-k:n0-k+len(t)]
    X[:,-1]=1.0
    return X
def target(c):
    pre=(c['t']>=-1)&(c['t']<0)
    with np.errstate(all='ignore'):
        base=np.nanmean(c['meas'][pre],axis=0)
    m=c['meas']-np.where(np.isnan(base),np.nanmedian(c['meas'],axis=0),base)
    ok=vc.window(c)[:,None]&vc.valid(c)&~np.isnan(m)
    return m, ok
k=int(len(cases)*0.7)
n=ROWS*TAPS+1
XtX=[np.zeros((n,n)) for _ in range(S)]; XtY=[np.zeros(n) for _ in range(S)]
for c in cases[:k]:
    X=design(c); m,ok=target(c)
    for s in range(S):
        sel=ok[:,s]; Xs=X[sel]; XtX[s]+=Xs.T@Xs; XtY[s]+=Xs.T@m[sel,s]
D=np.zeros((TAPS-2,TAPS))
for i in range(TAPS-2): D[i,i:i+3]=(1,-2,1)
P=np.zeros((n,n))
for r in range(ROWS): P[r*TAPS:(r+1)*TAPS, r*TAPS:(r+1)*TAPS]=D.T@D
best=None
for lam in (1e2,1e3,1e4,1e5):
    W=[np.linalg.solve(XtX[s]+lam*np.mean(np.diag(XtX[s]))*1e-6*P+1e-6*np.eye(n), XtY[s]) for s in range(S)]
    errs=[]
    for c in cases[k:]:
        X=design(c); m,ok=target(c)
        pred=np.array([X@W[s] for s in range(S)]).T
        mm=m[ok]; pp=pred[ok]
        if len(mm)<50: continue
        errs.append(np.sqrt(np.mean((pp-mm)**2))/np.sqrt(np.mean(mm**2)))
    med=np.median(errs); print('smoothing %g: held-out median %.1f%%  mean %.1f%%  (n=%d)'%(lam,100*med,100*np.mean(errs),len(errs)),flush=True)
    if best is None or med<best[0]: best=(med,lam,W)
med,lam,W=best
rows=[]
for c,nme in zip(cases[k:],names[k:]):
    X=design(c); m,ok=target(c); pred=np.array([X@W[s] for s in range(S)]).T
    mm=m[ok]; pp=pred[ok]
    if len(mm)<50: continue
    rows.append((nme,kinds[nme],np.sqrt(np.mean((pp-mm)**2))/np.sqrt(np.mean(mm**2)),np.sqrt(np.mean(mm**2))))
for kd in sorted(set(r[1] for r in rows)):
    v=[r[2] for r in rows if r[1]==kd]; print('  %-12s n=%2d median %.1f%%'%(kd,len(v),100*np.median(v)))
big=[r[2] for r in rows if r[3]>5]; print('  waves rms>5 mm: n=%d median %.1f%%'%(len(big),100*np.median(big)))
np.savez('analytics/rig/kernels.npz', W=np.array(W), taps=TAPS, rows=ROWS, dt=DT, smoothing=lam)
