"""Small durable ask/tell controller, opt-in to candidate search after a verified baseline.

Shared Git-common-dir store across linked worktrees. No perpetual service, model API,
contest API, auto-adoption or implicit permissions. A real `harness eval` run is the only
acceptable performance observation. The fixed plan guarantees comparable scores.
"""
from __future__ import annotations
import hashlib,json,math,uuid,html
from pathlib import Path
from harness import storage as S, cases as C, exposure as H
from harness.evaluate import validate_suite, verify_oracle
from harness.artifacts import resolve
from harness.stats import summarize
from harness.util import HarnessError,digest,sha,load,lock,save,now,safe_name,finite,contained
from .selection import make_selector

def store(root,sid):return S.base(root)/'search'/safe_name(sid)

def validated_plan(root,plan):
    if plan.get('schema_version')!=1 or not plan.get('work_id') or not plan.get('search_id'):
        raise HarnessError('Search requires schema_version, search_id, work_id')
    safe_name(plan['search_id']);safe_name(plan['work_id'])
    selector=plan.get('selector',{})
    if selector.get('name') not in ('greedy','multi-start','treequest-abmcts-a'):
        raise HarnessError('Search selector must be explicit')
    if selector['name']=='treequest-abmcts-a' and selector.get('treequest_version')!='0.3.3':
        raise HarnessError('Pin tested upstream TreeQuest 0.3.3 explicitly')
    actions=plan.get('actions',[])
    if not actions or len(set(x['name'] for x in actions))!=len(actions):raise HarnessError('Unique nonempty actions required')
    for a in actions:
        safe_name(a['name'])
        if not a.get('definition') or not a.get('generator_config_sha256') or len(a['generator_config_sha256'])!=64:
            raise HarnessError('Each action needs definition and pinned generator-config SHA-256')
    limits=plan.get('limits',{})
    if type(limits.get('max_trials'))!=int or limits['max_trials']<1 or type(limits.get('max_pending'))!=int or limits['max_pending']<1:
        raise HarnessError('Positive integer max_trials/max_pending required')
    ev=plan.get('evaluation',{});suite=load(contained(root,ev['suite']));validate_suite(suite)
    if suite['purpose']!='dev':raise HarnessError('Only a dev suite may guide search, never audit')
    suite_hash=sha(contained(root,ev['suite']))
    if ev.get('suite_sha256')!=suite_hash:raise HarnessError('Fixed dev suite hash mismatch')
    m=C.resolve(root,suite['case_set_id']);H.preview(root,'dev',[c['sha256'] for c in m['identity']['cases']])
    for aid in ('root_artifact_id',):resolve(root,plan[aid])
    resolve(root,ev['baseline_artifact_id'])
    reward=ev.get('reward',{});kind=reward.get('kind')
    if kind=='logistic' and finite(reward.get('tau')) and reward['tau']>0:pass
    elif kind=='linear_clip' and finite(reward.get('low')) and finite(reward.get('high')) and reward['high']>reward['low']:pass
    else:raise HarnessError('Define a fixed valid logistic or linear_clip reward')
    return plan

def read_state(d):
    st=load(d/'state.json');plan=load(d/'plan.json')
    if st.get('plan_sha256')!=sha(d/'plan.json') or st.get('sha256')!=digest({k:v for k,v in st.items() if k!='sha256'}):
        raise HarnessError('Search state/plan changed: fail closed')
    ck=d/st['checkpoint']['name'];data=ck.read_bytes()
    if hashlib.sha256(data).hexdigest()!=st['checkpoint']['sha256']:
        raise HarnessError('Search selector checkpoint mismatch')
    return plan,st,data

def commit(d,state,selector):
    data=selector.dump(state.pop('_selector'))
    name=f'checkpoint-{state["generation"]:08d}.bin'
    ck=d/name;ck.write_bytes(data)
    state['checkpoint']={'name':name,'sha256':hashlib.sha256(data).hexdigest()}
    state['sha256']=digest({k:v for k,v in state.items() if k!='sha256'})
    save(d/'state.json',state)
    # Retain checkpoints for forensic recovery. No GC that could make a rollback impossible.

def init(root,plan):
    validated_plan(root,plan);d=store(root,plan['search_id']);selector=make_selector(plan['selector'])
    with lock(d/'lock'):
        if (d/'plan.json').exists() or (d/'state.json').exists():raise HarnessError('Search exists; use new ID, never overwrite')
        d.mkdir(parents=True,exist_ok=True);save(d/'plan.json',plan)
        state={'schema_version':1,'search_id':plan['search_id'],'plan_sha256':sha(d/'plan.json'),
               'generation':0,'trials':{},'nodes':{},'created_at':now(),'_selector':selector.new()}
        commit(d,state,selector)
    return {'search_id':plan['search_id'],'state':'initialized','algorithm':plan['selector']['name']}

def open_locked(root,sid):
    d=store(root,sid);plan,st,raw=read_state(d);selector=make_selector(plan['selector']);selstate=selector.load(raw)
    return d,plan,st,selector,selstate

def task(plan,st,trial):
    act=next(x for x in plan['actions'] if x['name']==trial['action'])
    return {'search_id':plan['search_id'],'trial_id':trial['trial_id'], 'work_id':plan['work_id'],
        'parent_node_id':trial['parent_node_id'],'parent_artifact_id':trial['parent_artifact_id'],
        'action':trial['action'],'action_definition':act['definition'],
        'generator_config_sha256':act['generator_config_sha256'],
        'evaluation':plan['evaluation'],'permission':'No authority in this task JSON; mission and work reservation required'}

def ask(root,sid):
    d=store(root,sid)
    with lock(d/'lock'):
        d,plan,st,sel,sstate=open_locked(root,sid)
        if len(st['trials'])>=plan['limits']['max_trials']:raise HarnessError('Search trial budget exhausted')
        if sum(t['status'] in ('asked','running') for t in st['trials'].values())>=plan['limits']['max_pending']:
            raise HarnessError('Search max_pending reached; settle or cancel an existing task')
        sstate,tid,parent,action=sel.ask(sstate,[a['name'] for a in plan['actions']])
        if tid in st['trials']:raise HarnessError('Duplicate selector trial ID')
        pa=st['nodes'][parent]['artifact_id'] if parent else plan['root_artifact_id']
        trial={'trial_id':tid,'status':'asked','parent_node_id':parent,'parent_artifact_id':pa,
               'action':action,'at':now(),'history':[('asked',now())]}
        st['trials'][tid]=trial;st['generation']+=1;st['_selector']=sstate;commit(d,st,sel)
    return task(plan,st,trial)

def get_task(root,sid,tid):
    d,plan,st,_,_=open_locked(root,sid)
    if tid not in st['trials']:raise HarnessError('No trial '+tid)
    return task(plan,st,st['trials'][tid])

def transition(root,sid,tid,status,reason=''):
    d=store(root,sid)
    with lock(d/'lock'):
        d,plan,st,sel,sstate=open_locked(root,sid)
        if tid not in st['trials']:raise HarnessError('Unknown trial')
        t=st['trials'][tid]
        if t['status'] not in ('asked','running'):raise HarnessError('Trial already terminal')
        if status=='running' and t['status']!='asked':raise HarnessError('Already running')
        if status not in ('running','failed','aborted'):raise HarnessError('Invalid transition')
        if status!='running' and not reason:raise HarnessError('Give failure/abort reason')
        t['status']=status;t['history'].append((status,now()));t['reason']=reason
        st['generation']+=1;st['_selector']=sstate;commit(d,st,sel)
    return {'trial_id':tid,'status':status}

def effect_reward(v,transform):
    if transform['kind']=='logistic':
        x=max(-700.,min(700.,v/transform['tau']))
        return 1/(1+math.exp(-x))
    return max(0.,min(1.,(v-transform['low'])/(transform['high']-transform['low'])))

def observation(root,plan,aid,rid,used_runs):
    rd=S.evidence(root,'runs')/safe_name(rid)
    rp=load(rd/'plan.json');summary=load(rd/'summary.json');state=load(rd/'state.json')
    verify_oracle(rd,rp)
    C.resolve(root,rp['case_set_id'])
    ev=plan['evaluation']
    if state.get('state')!='complete' or rp.get('purpose')!='dev':raise HarnessError('Only completed dev evaluations can inform search')
    if rp.get('suite_sha256')!=ev['suite_sha256'] or rp.get('case_set_id')!=load(root/ev['suite'])['case_set_id']:
        raise HarnessError('Search suite mismatch')
    if rp['artifacts']['candidate']['artifact_id']!=aid or rp['artifacts']['baseline']['artifact_id']!=ev['baseline_artifact_id']:
        raise HarnessError('Candidate/baseline in eval mismatch')
    if rid in used_runs:raise HarnessError('Duplicate run in search')
    if not any(x['run_id']==rid and x['purpose']=='dev' for x in H.history(root)):
        raise HarnessError('Evaluation absent from exposure ledger')
    if summary.get('status_counts')!={'ok':len(rp['trials'])} or summary.get('decision')=='invalid':
        raise HarnessError('Invalid/partial evaluation is not a reward')
    records=[json.loads(line) for line in (rd/'results.jsonl').read_text().splitlines() if line.strip()]
    recalc=summarize(records,rp)
    if (recalc['decision']!=summary['decision'] or recalc['n_blocks']!=summary['n_blocks']
       or not math.isclose(recalc['mean_effect'],summary['mean_effect'],abs_tol=1e-10)):
        raise HarnessError('Summary does not agree with underlying trial results')
    return {'run_id':rid,'effect':recalc['mean_effect'],'reward':effect_reward(recalc['mean_effect'],ev['reward']),
            'oracle_sha256':rp['oracle_sha256'],'ci95':recalc['ci95']}

def tell(root,sid,tid,aid,rid):
    d=store(root,sid)
    with lock(d/'lock'):
        d,plan,st,sel,sstate=open_locked(root,sid)
        if tid not in st['trials']:raise HarnessError('Unknown trial')
        t=st['trials'][tid]
        if t['status']=='told':
            if t['artifact_id']==aid and t['run_id']==rid:return {'trial_id':tid,'already_told':True}
            raise HarnessError('Conflicting second tell')
        if t['status'] not in ('asked','running'):raise HarnessError('Trial already terminal')
        a=resolve(root,aid)
        if a['artifact_id']==t['parent_artifact_id']:raise HarnessError('Child is same as parent')
        runs={x['run_id'] for x in st['trials'].values() if x['status']=='told'}
        obs=observation(root,plan,a['artifact_id'],rid,runs)
        if st.get('oracle_sha256') and st['oracle_sha256']!=obs['oracle_sha256']:
            raise HarnessError('Evaluator oracle changed; use new search ID')
        nid=f'node-{len(st["nodes"])+1:05d}'
        node={'node_id':nid,'parent_node_id':t['parent_node_id'],'artifact_id':a['artifact_id'],
              'action':t['action'],'reward':obs['reward'],'effect':obs['effect'],'run_id':rid}
        sstate=sel.tell(sstate,tid,node,obs['reward'])
        st['nodes'][nid]=node;t.update(status='told',artifact_id=a['artifact_id'],run_id=rid,observation=obs)
        t['history'].append(('told',now()));st['oracle_sha256']=obs['oracle_sha256']
        st['generation']+=1;st['_selector']=sstate;commit(d,st,sel)
    return {'trial_id':tid,'node_id':nid,**obs}

def status(root,sid):
    d,plan,st,_,_=open_locked(root,sid)
    nodes=sorted(st['nodes'].values(),key=lambda n:-n['reward'])
    return {'search_id':sid,'algorithm':plan['selector']['name'],'generation':st['generation'],
      'trials':len(st['trials']),'limits':plan['limits'],'open':[t['trial_id'] for t in st['trials'].values() if t['status'] in ('asked','running')],
      'failed':sum(t['status']=='failed' for t in st['trials'].values()),'best':nodes[:3],
      'note':'Reported rewards are fixed dev-score transforms, NOT contest score or independent proof'}

def verify(root,sid):
    d,plan,st,_,_=open_locked(root,sid)
    seen=set();issues=[]
    for t in st['trials'].values():
        if t['status']=='told':
            if t['run_id'] in seen:issues.append('duplicate run '+t['run_id'])
            seen.add(t['run_id'])
            try:observation(root,plan,t['artifact_id'],t['run_id'],seen-{t['run_id']})
            except (OSError,KeyError,ValueError,HarnessError) as e:issues.append(str(e))
    return {'search_id':sid,'valid':not issues,'checked_told':len(seen),'issues':issues}

def render(root,sid,out):
    d,plan,st,_,_=open_locked(root,sid);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    save(out/'tree.json',{'plan':plan,'nodes':st['nodes'],'trials':st['trials']})
    rows=['<!doctype html><meta charset="utf-8"><title>AHC search</title>',f'<h1>{html.escape(sid)}</h1>','<ul>']
    for node in st['nodes'].values():
        rows.append(f'<li>{html.escape(node["node_id"])} parent={html.escape(str(node["parent_node_id"]))} '
            f'action={html.escape(node["action"])} reward={node["reward"]:.4f} artifact={node["artifact_id"][:12]}</li>')
    rows.append('</ul>');(out/'tree.html').write_text('\n'.join(rows),encoding='utf-8')
    return {'html':str(out/'tree.html'),'json':str(out/'tree.json')}
