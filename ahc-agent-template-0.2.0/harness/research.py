"""Bounded missions and work reservations; tasks themselves belong to Beads."""
from __future__ import annotations
import os, signal, socket, subprocess, time, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from . import storage as S
from .util import HarnessError, nonnegative, now, safe_name

OPS={'evaluation':'solver_runs','case_generation':'generated_cases','rescore':'judge_runs',
     'training':'training_runs','issues_write':'issue_writes','issues_comment':'issue_comments'}
LOCAL={'local_code','local_commit','local_adopt','issues_read'}
KINDS={'question','idea','observation','strategic_challenge','directive','constraint_change','pause','stop'}
BINDING={'directive','constraint_change','pause','stop'}

def input_record(root,text,kind='idea',source='human'):
    if kind not in KINDS or source not in ('human','ai','review') or not text: raise HarnessError('Invalid input')
    with S.transaction(root) as p:
        return S.event(p,'input',input_id='input-'+uuid.uuid4().hex[:12],text=text,input_kind=kind,source=source,
                       authority='binding' if source=='human' and kind in BINDING else 'advisory')

def authority(p,iid):
    e=next((e for e in p['events'] if e.get('input_id')==iid and e['kind']=='input'),None)
    if not e or e['source']!='human' or e['input_kind'] not in ('directive','constraint_change'):
        raise HarnessError('An explicit human directive/constraint change is required; an issue or review is not authority')
    return e

def validate_spec(spec):
    for k in ('goal','scope','stop_conditions','limits','permissions'):
        if k not in spec: raise HarnessError('Mission needs '+k)
    if not spec['goal'] or not spec['scope'] or not spec['stop_conditions']: raise HarnessError('Mission must be bounded by purpose and stopping conditions')
    if not isinstance(spec['limits'],dict) or any(not nonnegative(v) for v in spec['limits'].values()): raise HarnessError('Limits must be finite nonnegative numbers')
    unknown=set(spec['permissions'])-set(OPS)-LOCAL
    if unknown: raise HarnessError('Unsupported operations: '+str(sorted(unknown)))
    if any(type(v) is not bool for v in spec['permissions'].values()): raise HarnessError('Permissions are booleans')
    if spec.get('mode','bounded_campaign') not in ('bounded_campaign','single_experiment'): raise HarnessError('Unknown mission mode')
    if spec.get('deadline'): datetime.fromisoformat(spec['deadline'])

def mission_new(root,spec,iid):
    S.require_integrator(root,'mission creation'); validate_spec(spec)
    with S.transaction(root) as p:
        authority(p,iid)
        if p['mission'] and p['mission']['status'] in ('active','paused'): raise HarnessError('An active/paused mission exists; do not replace another worker\'s authority')
        if p['mission']: p['missions'].append(p['mission'])
        m={**spec,'id':'mission-'+uuid.uuid4().hex[:12],'revision':1,'mode':spec.get('mode','bounded_campaign'),
           'status':'active','source_input':iid,'created_at':now()}
        p['mission']=m; S.event(p,'mission_started',mission=m['id'],source_input=iid); return m

def active(p):
    m=p['mission']
    if not m or m['status']!='active': raise HarnessError('No active mission; questions are fine, budgeted work needs delegation')
    if m.get('deadline') and datetime.now(timezone.utc)>=datetime.fromisoformat(m['deadline']): raise HarnessError('Mission deadline exceeded')
    return m

def budget(p):
    m=p['mission']
    if not m: return {}
    rows=[r for r in p['reservations'].values() if r['mission']==m['id']]
    out={}
    for unit,cap in m['limits'].items():
        used=sum(r.get('charged',{}).get(unit,0) if r['status']=='settled' else r['used'].get(unit,0) for r in rows)
        held=sum(max(0,r['estimate'].get(unit,0)-r['used'].get(unit,0)) for r in rows if r['status']=='held')
        out[unit]={'limit':cap,'used':used,'held':held,'remaining':cap-used-held}
    return out

def amend(root,changes,reason,iid=None,expected_revision=None):
    S.require_integrator(root,'mission amendment')
    if not reason: raise HarnessError('Explain the amendment')
    with S.transaction(root) as p:
        m=p['mission']
        if not m or m['status'] not in ('active','paused'): raise HarnessError('No amendable mission')
        if expected_revision is None or expected_revision!=m['revision']: raise HarnessError('Supply the current expected revision')
        if set(changes)-{'limits','permissions','deadline','goal','scope','stop_conditions'}: raise HarnessError('Unknown amendment fields')
        # Any change of mission boundaries requires human authority. Workers still select hypotheses freely.
        authority(p,iid)
        proposal={**m,**changes,'limits':{**m['limits'],**changes.get('limits',{})},'permissions':{**m['permissions'],**changes.get('permissions',{})}}
        validate_spec(proposal); proposal['revision']=m['revision']+1; p['mission']=proposal
        S.event(p,'mission_amended',mission=m['id'],reason=reason,source_input=iid,changes=changes)
        return proposal

def proc_start(pid):
    try: return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError,ValueError): return None

def process_record(pid): return {'pid':pid,'host':socket.gethostname(),'start':proc_start(pid),'at':now()}
def alive(q):
    if q.get('ended_at'): return False
    if q.get('host')!=socket.gethostname(): return True # unknown host cannot be treated as dead
    try: os.kill(int(q['pid']),0)
    except ProcessLookupError:
        if q.get('pgid'):
            try: os.killpg(q['pgid'],0); return True
            except ProcessLookupError: pass
        return False
    except (PermissionError,ValueError,KeyError): return True
    if q.get('start') and proc_start(q['pid'])!=q['start']: return False
    try:
        if Path(f'/proc/{int(q["pid"])}/stat').read_text().rsplit(')',1)[1].split()[0]=='Z': return False
    except OSError: pass
    return True

def mission_state(root,status,reason):
    if status not in ('paused','stopped','completed','active') or not reason: raise HarnessError('State and reason required')
    S.require_integrator(root,'mission state change')
    with S.transaction(root) as p:
        m=p['mission']
        if not m: raise HarnessError('No mission')
        if status=='active' and m['status']!='paused': raise HarnessError('Only a paused mission can resume')
        held=[r for r in p['reservations'].values() if r['mission']==m['id'] and r['status']=='held']
        if status=='completed' and held: raise HarnessError('Settle all work before completing a mission')
        m.update(status=status,reason=reason,revision=m['revision']+1)
        S.event(p,'mission_state',mission=m['id'],status=status,reason=reason)
    return {'status':status,'running_not_killed':True}

def reserve(root,work,ops,estimate,experiment=None):
    safe_name(work)
    if not ops or set(ops)-set(OPS): raise HarnessError('Declare budgeted operations')
    if not estimate or any(not nonnegative(x) for x in estimate.values()): raise HarnessError('Finite nonnegative estimates required')
    with S.transaction(root) as p:
        m=active(p); b=budget(p)
        if work in p['reservations'] and p['reservations'][work]['status']=='held': raise HarnessError('This work already has a reservation')
        for op in ops:
            if not m['permissions'].get(op): raise HarnessError('Mission does not permit '+op)
            if estimate.get(OPS[op],0)<=0: raise HarnessError('Reserve a positive '+OPS[op])
        if m['mode']=='single_experiment' and (not experiment or experiment!=m.get('experiment')): raise HarnessError('Outside the delegated single experiment')
        for unit,val in estimate.items():
            if unit not in b or val>b[unit]['remaining']: raise HarnessError('Insufficient/unassigned budget: '+unit)
        if work in p['reservations']:
            old=p['reservations'].pop(work); p['reservations'][work+'-archived-'+uuid.uuid4().hex[:8]]=old
        r={'id':'reservation-'+uuid.uuid4().hex,'work':work,'mission':m['id'],'operations':ops,'estimate':estimate,'used':{},'status':'held',
           'experiment':experiment,'workspace':str(Path(root).resolve()),'opened_at':now(),'processes':[],'calls':[]}
        p['reservations'][work]=r; S.event(p,'reserved',work=work,estimate=estimate); return r

def check_permission(root,op,artifact=None,target=None):
    p=S.read(root)['payload']; m=active(p)
    if not m['permissions'].get(op): raise HarnessError('Mission does not permit '+op)
    if op in ('submit','issues_write','issues_comment'):
        S.require_integrator(root,op)
        key='submission_artifacts' if op=='submit' else 'github_items'
        item=artifact if op=='submit' else target
        if not item or item not in m.get(key,[]): raise HarnessError('Operation target is not explicitly authorized by this mission')
    return m

@contextmanager
def operation(root,op,amounts,work=None,artifact=None,target=None):
    """Charge known work before launching. Crashes cannot erase usage; unmeasured reservations remain held."""
    work=work or os.environ.get('ARENA_TASK')
    if not work: raise HarnessError('Set ARENA_TASK or --work to a Beads work ID')
    if op not in OPS or not amounts or any(not nonnegative(v) for v in amounts.values()): raise HarnessError('Invalid operation budget')
    if amounts.get(OPS[op],0)<=0: raise HarnessError('Operation count must be positive')
    check_permission(root,op,artifact,target)
    cid='call-'+uuid.uuid4().hex
    with S.transaction(root) as p:
        m=active(p)
        if not m['permissions'].get(op): raise HarnessError('Permission revoked')
        r=p['reservations'].get(work)
        if not r or r['status']!='held' or r['mission']!=m['id'] or op not in r['operations']: raise HarnessError('No matching work reservation')
        if any(alive(q) for q in r['processes']): raise HarnessError('This work already has a live process')
        for unit,val in amounts.items():
            if val>r['estimate'].get(unit,0)-r['used'].get(unit,0): raise HarnessError('Work budget exhausted: '+unit)
        # A lowered mission cap invalidates existing reservations too.
        if any(row['remaining']<0 for row in budget(p).values()): raise HarnessError('Reservations exceed changed mission limits')
        for unit,val in amounts.items(): r['used'][unit]=r['used'].get(unit,0)+val
        q=process_record(os.getpid()); q['call_id']=cid; r['processes'].append(q)
        r['calls'].append({'id':cid,'op':op,'amounts':amounts,'started':now(),'completed':False})
        S.event(p,'operation_started',work=work,op=op,call_id=cid,amounts=amounts)
    completed=False
    try:
        yield {'work':work,'call_id':cid}; completed=True
    finally:
        with S.transaction(root) as p:
            r=p['reservations'][work]
            for q in r['processes']:
                if q.get('call_id')==cid: q['ended_at']=now()
            for c in r['calls']:
                if c['id']==cid: c.update(completed=completed,ended=now())
            S.event(p,'operation_ended',work=work,call_id=cid,completed=completed)

def settle(root,work,outcome,usage=None):
    if outcome not in ('done','failed','released'): raise HarnessError('Invalid outcome')
    with S.transaction(root) as p:
        r=p['reservations'].get(work)
        if not r or r['status']!='held': raise HarnessError('No held reservation')
        if any(alive(q) for q in r['processes']): raise HarnessError('A registered process is still live')
        # Missing measurements are charged at their reserved maximum, not silently zeroed.
        charged={}; unknown=[]
        for unit,cap in r['estimate'].items():
            v=(usage or {}).get(unit)
            if v is None: v=max(r['used'].get(unit,0),cap); unknown.append(unit)
            if not nonnegative(v) or v<r['used'].get(unit,0): raise HarnessError('Measured usage must not undercut already charged calls')
            charged[unit]=v
        r.update(status='settled',outcome=outcome,charged=charged,unknown=unknown,closed_at=now())
        S.event(p,'settled',work=work,outcome=outcome,charged=charged,unknown=unknown); return r

def run_command(root,work,op,argv,timeout=3600,amounts=None):
    from .resources import reserve as machine_reserve
    from .process import run
    if not argv: raise HarnessError('Command required')
    m=active(S.read(root)['payload'])
    if m.get('deadline'):timeout=min(timeout,max(0.01,(datetime.fromisoformat(m['deadline'])-datetime.now(timezone.utc)).total_seconds()))
    if amounts is None:
        if op=='case_generation': raise HarnessError('Generation requires --amount generated_cases=N before starting')
        amounts={OPS[op]:1}
    with operation(root,op,amounts,work) as auth:
        with machine_reserve('shared','work:'+work):
            # The caller process is visible while it runs. This is NOT a detached job service.
            result=run(argv,cwd=root,timeout=timeout,max_output=1024*1024,on_start=lambda pid:register_child(root,auth,pid))
    return result


def register_child(root,auth,pid):
    with S.transaction(root) as p:
        r=p['reservations'].get(auth['work'])
        if not r or r['status']!='held': raise HarnessError('Work reservation disappeared before process registration')
        q=process_record(pid);q.update(call_id=auth['call_id'],pgid=pid)
        r['processes'].append(q)
