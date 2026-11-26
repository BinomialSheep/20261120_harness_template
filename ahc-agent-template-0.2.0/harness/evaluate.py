"""Single-solver AHC evaluation with frozen inputs, scorer and program variants."""
from __future__ import annotations
import concurrent.futures as cf
import csv, json, os, platform, random, re, shutil, sys, time, uuid
from pathlib import Path
from . import __version__, storage as S, cases as C, exposure as H
from .artifacts import resolve
from .ahc_contract import validate as contract_validate,validate_result
from .config import read
from .process import run
from .research import operation,register_child
from .resources import reserve as machine_reserve
from .stats import summarize
from .util import HarnessError,contained,digest,finite,load,lock,now,save,sha,safe_name,canonical


def validate_variant(v):
    if not isinstance(v,dict) or set(v)-{'args','env','note'}: raise HarnessError('Variant fields are args/env/note')
    if not isinstance(v.get('args',[]),list) or any(not isinstance(x,str) for x in v.get('args',[])):raise HarnessError('Variant args must be argv')
    env=v.get('env',{})
    if not isinstance(env,dict) or any(not re.fullmatch('[A-Z][A-Z0-9_]*',k) or not isinstance(x,str) for k,x in env.items()):raise HarnessError('Environment must be uppercase string keys/values')
    for k in env:
        if k in ('PATH','HOME','PYTHONPATH','LD_PRELOAD','LD_LIBRARY_PATH','AHC_SOLVER_SEED') or any(x in k for x in ('TOKEN','SECRET','PASSWORD','API_KEY')):
            raise HarnessError('Unsafe/reserved variant environment: '+k)


def validate_suite(s):
    allowed={'schema_version','mode','purpose','contract','case_set_id','case_ids','solver_seeds','metric','variants',
             'minimum_blocks','minimum_effect','bootstrap_seed','order_seed','timing_sensitive','run_budget_s','trial_timeout_s','description'}
    if set(s)-allowed:raise HarnessError('Unknown suite fields: '+str(sorted(set(s)-allowed)))
    if s.get('schema_version')!=1 or s.get('mode')!='ahc': raise HarnessError('AHC suite schema_version=1, mode=ahc required')
    if s.get('purpose') not in ('smoke','dev','diagnostic','audit','stress'):raise HarnessError('Unknown suite purpose')
    if not s.get('contract') or not re.fullmatch('[0-9a-f]{64}',s.get('case_set_id','')):raise HarnessError('Contract and frozen case_set_id required')
    seeds=s.get('solver_seeds',[0])
    if not seeds or any(type(x)!=int for x in seeds) or len(seeds)!=len(set(seeds)):raise HarnessError('Unique integer solver_seeds required')
    ids=s.get('case_ids')
    if ids is not None and (not ids or len(ids)!=len(set(ids))):raise HarnessError('case_ids must be a nonempty unique selection')
    metric=s.get('metric',{})
    if set(metric)-{'name','direction','comparison','aggregation'}:raise HarnessError('Unknown metric field')
    if metric.get('direction') not in ('maximize','minimize') or metric.get('comparison') not in ('difference','log_ratio'):
        raise HarnessError('Declare direction and comparison explicitly')
    if metric.get('aggregation','case_mean') not in ('case_mean','block_mean'):raise HarnessError('Choose case_mean or block_mean')
    for k in ('run_budget_s','trial_timeout_s'):
        if k in s and (not finite(s[k]) or s[k]<=0):raise HarnessError('Invalid '+k)
    if type(s.get('minimum_blocks',20)) is not int or s.get('minimum_blocks',20)<2:raise HarnessError('minimum_blocks >= 2 required')
    if not finite(s.get('minimum_effect',0)) or s.get('minimum_effect',0)<0:raise HarnessError('Invalid minimum_effect')
    vs=s.get('variants',{})
    if not isinstance(vs,dict) or set(vs)-{'candidate','baseline'}:raise HarnessError('Unknown variant role')
    for v in vs.values():validate_variant(v)


def make_plan(root,s,candidate,baseline):
    validate_suite(s);manifest=C.resolve(root,s['case_set_id']);rows=manifest['identity']['cases']
    selected=s.get('case_ids')
    if selected:
        found={r['id']:r for r in rows}
        if set(selected)-set(found):raise HarnessError('Unknown case_ids')
        rows=[found[i] for i in selected]
    c=contract_validate(load(contained(root,s['contract'])))
    if c['mode']=='output_only':raise HarnessError('Use judge for saved outputs; eval requires batch or interactive')
    arts={r:resolve(root,n) for r,n in (('candidate',candidate),('baseline',baseline))}
    oracle={}
    for rel in ['adapters/ahc','harness',s['contract'],*c['files']]:
        path=contained(root,rel)
        for f in (sorted(path.rglob('*')) if path.is_dir() else [path]):
            if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc':
                contained(root,f);oracle[str(f.relative_to(root))]=sha(f)
    trials=[];groups=[];rng=random.Random(s.get('order_seed',0))
    case_order=list(rows);rng.shuffle(case_order)
    for case in case_order:
        for rep,seed in enumerate(s.get('solver_seeds',[0])):
            roles=['candidate','baseline'];rng.shuffle(roles);group=[]
            for role in roles:
                t={'trial_id':f't{len(trials):07d}','case_id':case['id'],'role':role,'repeat':rep,'solver_seed':seed}
                trials.append(t);group.append(t)
            groups.append(group)
    return {**s,'cases':rows,'contract_data':c,'case_manifest':{k:v for k,v in manifest.items() if k!='directory'},
            'artifacts':{role:{k:v for k,v in a.items() if k not in ('directory','command')} for role,a in arts.items()},
            'trials':trials,'groups':groups,'oracle_files_resolved':oracle,'oracle_sha256':digest(oracle),
            'variants':{role:s.get('variants',{}).get(role,{}) for role in arts},'harness_version':__version__}


def verify_oracle(rd,plan):
    if digest({k:v for k,v in plan.items() if k!='plan_sha256'})!=plan.get('plan_sha256'):raise HarnessError('Plan checksum mismatch')
    for rel,h in plan['oracle_files_resolved'].items():
        if sha(contained(rd/'oracle_snapshot',rel))!=h:raise HarnessError('Frozen evaluator changed')


def execute_trial(root,rd,plan,t,auth,timeout=None):
    td=rd/'trials'/t['trial_id'];td.mkdir(parents=True,exist_ok=False)
    manifest={'directory':str(S.evidence(root,'casesets')/plan['case_set_id'])};case=next(c for c in plan['cases'] if c['id']==t['case_id'])
    art=resolve(root,plan['artifacts'][t['role']]['artifact_id'])
    q={'schema_version':1,'trial':t,'work_dir':str(td),'oracle_root':str(rd/'oracle_snapshot'),
       'contract':plan['contract_data'],'input_path':str(contained(Path(manifest['directory']),case['file'])),
       'input_sha256':case['sha256'],'solver_command':art['command'],'solver_seed':t['solver_seed'],
       'variant':plan['variants'][t['role']]}
    save(td/'request.json',q)
    limit=plan['contract_data']['limits']
    outer=plan.get('trial_timeout_s',limit['solver_wall_s']+limit['judge_wall_s']+limit['tester_wall_s']+10)
    if timeout is not None:outer=min(outer,timeout)
    if outer<=0:
        r={'schema_version':1,'status':'not_run','valid':False,'raw_score':None,'reason':'run budget exhausted'}
        save(td/'response.json',r)
    else:
        p=run([sys.executable,'-S',str(rd/'oracle_snapshot/adapters/ahc/run_case.py'),str(td/'request.json'),str(td/'response.json')],
              cwd=td,timeout=outer,env={'PYTHONPATH':str(rd/'oracle_snapshot')},
              on_start=(lambda pid:register_child(root,auth,pid)) if auth else None)
        (td/'driver.stdout').write_text(p['stdout']);(td/'driver.stderr').write_text(p['stderr'])
        try:
            if p['status']!='ok':raise HarnessError('Driver '+p['status']+' (not a proven solver TLE)')
            if (td/'response.json').stat().st_size>1048576:raise HarnessError('Oversized driver result')
            r=validate_result(load(td/'response.json'))
        except (OSError,ValueError,HarnessError) as e:
            r={'schema_version':1,'status':'infrastructure_error','valid':False,'raw_score':None,'reason':str(e)}
            save(td/'response.json',r)
        r['driver_wall_s']=p['wall_s']
    return {**r,'trial_id':t['trial_id'],'case_id':t['case_id'],'role':t['role'],'solver_seed':t['solver_seed']}


def run_evaluation(root,suite_path,candidate,baseline,label,jobs,audit_approval=None,work=None):
    root=Path(root).resolve();s=load(contained(root,suite_path));validate_suite(s);safe_name(label)
    if not 1<=jobs<=read(root)['resources']['max_jobs']:raise HarnessError('Invalid jobs')
    if s.get('timing_sensitive') and jobs!=1:raise HarnessError('Timing-sensitive validation requires jobs=1')
    if s['purpose']=='audit' and not audit_approval:
        raise HarnessError('Audit exposure requires an approval/reference; this is a research holdout policy, not a contest AI rule')
    plan=make_plan(root,s,candidate,baseline)
    # Advisory preflight before consuming resources; register() rechecks under the shared lock.
    H.preview(root,s['purpose'],[c['sha256'] for c in plan['cases']])
    if len(plan['trials'])>read(root)['resources']['max_trials']:raise HarnessError('Trial cap exceeded')
    # Reserve all planned runs conservatively. Failures and early stop do not silently refund the reservation.
    with operation(root,'evaluation',{'solver_runs':len(plan['trials'])},work) as auth, machine_reserve('exclusive' if s.get('timing_sensitive') else 'shared','eval:'+label):
        run_id=time.strftime('%Y%m%dT%H%M%S',time.gmtime())+'_'+label+'_'+uuid.uuid4().hex[:8]
        rd=S.evidence(root,'runs')/run_id;rd.mkdir()
        for rel,h in plan['oracle_files_resolved'].items():
            out=rd/'oracle_snapshot'/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/rel,out)
            if sha(out)!=h:raise HarnessError('Oracle changed during snapshot')
        plan.update(run_id=run_id,created_at=now(),jobs=jobs,suite_sha256=sha(contained(root,suite_path)),approval=audit_approval,
            host={'platform':platform.platform(),'python':sys.version,'cpu_count':os.cpu_count(),'loadavg_start':list(os.getloadavg())})
        plan['plan_sha256']=digest(plan);save(rd/'plan.json',plan)
        # Protect the actual input bytes across distinct case-set/suite names and worktrees.
        # A failed/aborted audit is still exposed: do not silently refund its freshness.
        H.register(root,run_id,s['purpose'],plan['suite_sha256'],s['case_set_id'],
                   [c['sha256'] for c in plan['cases']],[c['block'] for c in plan['cases']],audit_approval)
        start=time.monotonic();deadline=start+s.get('run_budget_s',3600);results=[]
        save(rd/'state.json',{'state':'running','planned_trials':len(plan['trials']),'started_at':now()})
        def paired(group):return [execute_trial(root,rd,plan,t,auth,deadline-time.monotonic()) for t in group]
        try:
            with (rd/'results.jsonl').open('w') as out,cf.ThreadPoolExecutor(max_workers=jobs) as pool:
                for group in pool.map(paired,plan['groups']):
                    for r in group:results.append(r);out.write(canonical(r).decode()+'\n');out.flush()
            verify_oracle(rd,plan);C.resolve(root,s['case_set_id'])
            for a in plan['artifacts'].values():resolve(root,a['artifact_id'])
            summary=summarize(results,plan)
            summary.update(run_id=run_id,plan_sha256=plan['plan_sha256'],wall_s=time.monotonic()-start,
                           loadavg_end=list(os.getloadavg()))
            save(rd/'summary.json',summary)
            with (rd/'cases.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['case_id','block','tags','valid_pairs','complete','effect','candidate','baseline']);w.writeheader();w.writerows(summary['case_effects'])
            report=render_report(summary);(rd/'report.md').write_text(report)
            save(rd/'state.json',{'state':'complete','decision':summary['decision'],'finished_at':now()})
            return {'run_id':run_id,'summary':summary,'report':str(rd/'report.md')}
        except BaseException:
            save(rd/'state.json',{'state':'aborted','recorded':len(results),'finished_at':now()});raise


def render_report(s):
    return ('# AHC comparison '+s['run_id']+'\n\n'
      +f"Decision: **{s['decision']}**. Positive effect means improvement; no automatic adoption.\n\n"
      +f"Trials: {s['recorded_trials']}/{s['planned_trials']}; independent blocks: {s['n_blocks']}.\n\n"
      +f"Mean effect: {s['mean_effect']}; exploratory 95% interval: {s['ci95']}.\n\n"
      +'Case details: `cases.csv`. Failed trials remain failures, not zero costs or silently excluded wins.\n\n'
      +'```json\n'+json.dumps(s,ensure_ascii=False,indent=2)+'\n```\n')

# Compatibility for callers/tests that validate the common result contract.
validate_response=validate_result
