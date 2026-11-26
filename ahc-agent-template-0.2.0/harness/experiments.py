"""Immutable experiment plan + append-only amendments + terminal result."""
import uuid
from . import storage as S
from .util import HarnessError, digest, load, lock, now, save, safe_name

def directory(root,eid): return S.evidence(root,'experiments')/safe_name(eid)
def init(root,plan):
    for k in ('hypothesis','work_id','acceptance','failure','budget'):
        if k not in plan: raise HarnessError('Experiment plan needs '+k)
    eid='exp-'+uuid.uuid4().hex[:16]; d=directory(root,eid); d.mkdir()
    save(d/'plan.json',plan); save(d/'record.json',{'plan_sha256':digest(plan),'created_at':now(),'amendments':[],'result':None})
    return {'id':eid,'plan_sha256':digest(plan),'path':str(d)}
def change(root,eid,amendment=None,result=None):
    d=directory(root,eid)
    with lock(d/'.lock'):
        p=load(d/'plan.json'); r=load(d/'record.json')
        if digest(p)!=r['plan_sha256']: raise HarnessError('Original plan was modified')
        if r['result'] is not None: raise HarnessError('Experiment already closed')
        if amendment is not None:
            if not amendment.get('reason'): raise HarnessError('Amendment needs reason')
            r['amendments'].append({**amendment,'at':now()})
        if result is not None:
            if result.get('decision') not in ('adopted','rejected','hold','inconclusive','abandoned'): raise HarnessError('Invalid experiment decision')
            # Adopt/keep is an engineering choice. A hypothesis may remain unresolved even after a rejection.
            if result.get('hypothesis_status','not_assessed') not in ('supported','refuted','unresolved','not_assessed'):
                raise HarnessError('Invalid independent hypothesis_status')
            if result.get('research_status','not_assessed') not in ('continue','defer','stop','not_assessed'):
                raise HarnessError('Invalid independent research_status')
            r['result']={**result,'hypothesis_status':result.get('hypothesis_status','not_assessed'),
                         'research_status':result.get('research_status','not_assessed'), 'at':now()}

        save(d/'record.json',r)
    return r
