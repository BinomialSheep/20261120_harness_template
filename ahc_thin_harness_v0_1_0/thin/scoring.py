"""Candidate scoring on common fresh per-round seeds. Minimal resumable on-disk CSV.

All cases are evaluated by the problem-specific official tester adapter; never guess scoring
rules from stdout. Workload = 10*1000 (+1000 baseline) in A, 1000 (+baseline) in B by default.
"""
from __future__ import annotations
import concurrent.futures,csv,hashlib,math,os,shutil,statistics,sys,tempfile
from pathlib import Path
from .config import context
from .core import locked,tool_hash,read_best
from .process import execute
from .util import ThinError,read_json,write_json,now,digest,format_argv,safe_id

FIELDS=['seed','status','score','seconds','reason']

def _compile(root,rd,source,jid,config):
    source=Path(source);work=rd/'evaluation'/jid;work.mkdir(parents=True,exist_ok=True)
    binary=work/'solver';stamp=work/'compile.json';h=digest(source)
    if stamp.is_file():
        prior=read_json(stamp)
        if prior['source_sha256']!=h:raise ThinError(f'Frozen source {jid} mutated after scoring started')
        if prior['status']!='ok':return None,prior
        if binary.is_file() and digest(binary)==prior.get('binary_sha256'):return binary,prior
        raise ThinError(f'Frozen compiled binary missing/modified for {jid}')
    cmd=format_argv(config['solver']['build'],{'source':str(source.resolve()),'binary':str(binary.resolve()),
        'workspace':str(work.resolve()),'python':sys.executable})
    record=execute(cmd,work,120,stdout_file=work/'compiler.stdout',stderr_file=work/'compiler.stderr')
    obj={'source_sha256':h,'status':record['status'],'seconds':record['seconds']}
    if record['status']=='ok' and binary.exists():obj['binary_sha256']=digest(binary)
    else:obj.update(status='compile_error',reason=(work/'compiler.stderr').read_text(errors='replace')[-1200:])
    write_json(stamp,obj)
    return (binary if obj['status']=='ok' else None),obj

def _case(root,rd,binary,seed,config,jid):
    work=rd/'evaluation'/jid
    with tempfile.TemporaryDirectory(dir=work,prefix='case-') as t:
        t=Path(t);result=t/'result.json';mapping=context(root,config,rd,binary,seed,result,t)
        mapping['tools']=str((rd/'judge_tools').resolve())
        cmd=format_argv(config['judge']['command'],mapping)
        proc=execute(cmd,t,config['judge']['timeout_sec'],stdout_file=t/'judge.stdout',stderr_file=t/'judge.stderr')
        reason='';score=None;status=proc['status']
        if proc['status']=='ok':
            try:
                record=read_json(result);v=record.get('score')
                if record.get('status')=='ok' and record.get('valid') is True and type(v) in (int,float) and math.isfinite(v):
                    score=float(v);status='ok'
                elif record.get('valid') is False and record.get('status')!='ok' and record.get('score') is None:
                    status='invalid';reason=str(record.get('reason','invalid candidate'))[:400]
                else:status='adapter_error';reason='Invalid score-result-v1 JSON'
            except (ThinError,TypeError,ValueError) as e:status='adapter_error';reason=str(e)[:400]
        else:
            reason=(t/'judge.stderr').read_text(errors='replace')[-400:] if (t/'judge.stderr').exists() else str(proc)
        return {'seed':seed,'status':status,'score':score,'seconds':proc['seconds'],'reason':reason}

def score_source(root,rd,jid,source,config,seeds,tool_digest):
    work=rd/'evaluation'/jid;work.mkdir(parents=True,exist_ok=True)
    manifest=work/'source.json';sha=digest(source)
    identity={'source_sha256':sha,'tool_sha256':tool_digest,'judge_command':config['judge']['command'],
              'build_command':config['solver']['build'],'seeds':[seeds[0],seeds[-1],len(seeds)]}
    if manifest.exists():
        if read_json(manifest)!=identity:raise ThinError(f'Changed evidence identity for {jid}')
    else:write_json(manifest,identity)
    binary,binfo=_compile(root,rd,source,jid,config)
    if binary is None:return {'id':jid,'valid':False,'status':'compile_error','error':binfo.get('reason'),'source_sha256':sha,'n':0}
    # Orchestrator-side seed0 gate: worker self-report alone is not sufficient.
    smoke=work/'seed0.json'
    if smoke.exists():
        check=read_json(smoke)
        if check.get('source_sha256')!=sha or check.get('tools_sha256')!=tool_digest:
            raise ThinError(f'Seed0 check provenance mismatch: {jid}')
    else:
        check=_case(root,rd,binary,0,config,jid)
        check['source_sha256']=sha;check['tools_sha256']=tool_digest
        write_json(smoke,check)
    if check.get('status')!='ok':
        return {'id':jid,'valid':False,'status':'seed0_failed','error':check.get('reason','seed0 failed'),
                'source_sha256':sha,'n':0}
    csvpath=work/'scores.csv';existing=[]
    if csvpath.exists():
        with csvpath.open(newline='') as f:
            rdrows=csv.DictReader(f)
            if rdrows.fieldnames!=FIELDS:raise ThinError('Unexpected score CSV schema '+str(csvpath))
            existing=list(rdrows)
        if len(existing)>len(seeds) or [int(r['seed']) for r in existing]!=seeds[:len(existing)]:
            raise ThinError(f'Score file missing, reordered or duplicate seeds for {jid}')
    bad=[r for r in existing if r['status']!='ok']
    if not bad or not config['evaluation']['early_stop_on_invalid']:
        with csvpath.open('a',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS)
            if not existing:writer.writeheader()
            for seed in seeds[len(existing):]:
                rec=_case(root,rd,binary,seed,config,jid)
                writer.writerow(rec);f.flush()
                existing.append({k:'' if v is None else str(v) for k,v in rec.items()})
                if rec['status']!='ok' and config['evaluation']['early_stop_on_invalid']:break
    bad=[r for r in existing if r['status']!='ok']
    if bad:
        return {'id':jid,'valid':False,'status':bad[0]['status'],'error':bad[0]['reason'],
                'first_bad_seed':int(bad[0]['seed']),'n':len(existing),'source_sha256':sha}
    scores=[float(r['score']) for r in existing]
    return {'id':jid,'valid':len(scores)==len(seeds),'status':'ok' if len(scores)==len(seeds) else 'incomplete',
            'mean_score':statistics.mean(scores) if scores else None,'n':len(scores),'source_sha256':sha}

def _scores(rd,jid):
    with (rd/'evaluation'/jid/'scores.csv').open(newline='') as f:
        return {int(r['seed']):float(r['score']) for r in csv.DictReader(f) if r['status']=='ok'}

def compare(candidate_scores,baseline_scores,config,seeds):
    direction=config['judge']['direction'];metric=config['judge']['metric'];d=[]
    for seed in seeds:
        a=candidate_scores[seed];b=baseline_scores[seed]
        if metric=='paired_log_ratio':
            if a<=0 or b<=0:raise ThinError('paired_log_ratio requires both scores strictly positive')
            delta=math.log(a/b) if direction=='maximize' else math.log(b/a)
        else:delta=(a-b) if direction=='maximize' else (b-a)
        d.append(delta)
    mean=statistics.mean(d);n=len(d);se=statistics.stdev(d)/(n**.5) if n>1 else None
    return {'effect':mean,'standard_error':se,'exploratory_ci95':[mean-1.96*se,mean+1.96*se] if se is not None else None,
            'n':n,'better_cases':sum(x>0 for x in d),'worse_cases':sum(x<0 for x in d),
            'note':'Descriptive normal CI; repeated winner selection among 10 candidates biases promotion evidence'}

def evaluate(root,rid):
    root=Path(root);rid=safe_id(rid);rd=root/'runs'/rid;plan=read_json(rd/'plan.json')
    state=read_json(rd/'state.json')
    if state['phase']=='closed' and (rd/'result.json').is_file():return read_json(rd/'result.json')
    if state['phase'] not in ('workers_done','evaluated'):
        raise ThinError('All workers must submit or fail, then complete workers before evaluate')
    config=plan['config'];seeds=list(range(plan['seed_start'],plan['seed_start']+plan['seed_count']))
    if tool_hash(rd/'judge_tools')!=plan['tools_sha256'] or digest(rd/'judge_statement.md')!=plan['statement_sha256']:
        raise ThinError('Round judge/statement snapshot changed')
    jobs=[(x['id'],rd/'jobs'/x['id']/'candidate.cpp') for x in plan['ideas']
          if read_json(rd/'jobs'/x['id']/'job.json')['status'] in ('submitted','scored')]
    if not jobs:raise ThinError('All workers failed; cannot evaluate a round with no candidates')
    for jid,src in jobs:
        j=read_json(src.parent/'job.json')
        if not src.is_file() or digest(src)!=j['sha256']:raise ThinError('Candidate changed after submission: '+jid)
    b=plan['best_before'];baseline=None
    if b:
        src=rd/'baseline.cpp'
        if not src.exists() or digest(src)!=b['sha256']:raise ThinError('Frozen previous best was changed')
        baseline=score_source(root,rd,'baseline',src,config,seeds,plan['tools_sha256'])
        if not baseline['valid']:raise ThinError('Current baseline fails evaluation; round comparison unsafe: '+str(baseline))
    groups={}
    for jid,source in jobs:groups.setdefault(digest(source),[]).append((jid,source))
    first=[group[0] for group in groups.values()]
    scored={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=config['evaluation']['parallelism']) as pool:
        pending={pool.submit(score_source,root,rd,jid,src,config,seeds,plan['tools_sha256']):jid for jid,src in first}
        for future in concurrent.futures.as_completed(pending):scored[pending[future]]=future.result()
    results=[]
    baseline_scores=_scores(rd,'baseline') if baseline else None
    for group in groups.values():
        representative=group[0][0];record=scored[representative];score_map=_scores(rd,representative) if record['valid'] else None
        for jid,_ in group:
            row={**record,'id':jid,'reused_from':representative if jid!=representative else None}
            if row['valid']:
                if baseline:
                    try:row.update(compare(score_map,baseline_scores,config,seeds))
                    except ThinError as e:row.update(valid=False,status='metric_error',error=str(e))
                else:
                    row['effect']=row['mean_score']*(1 if config['judge']['direction']=='maximize' else -1)
            results.append(row)
    results.sort(key=lambda row:(not row['valid'],-row.get('effect',float('-inf')) if row['valid'] else 0,row['id']))
    valid=[x for x in results if x['valid']]
    result={'round_id':rid,'mode':plan['mode'],'seed_start':plan['seed_start'],'seed_count':len(seeds),
            'baseline':baseline,'candidates':results,'recommended':valid[0]['id'] if valid else None,
            'selection_note':'Ranking on development cases, not a fresh final holdout; promotion is a separate explicit action',
            'judged_at':now()}
    with locked(root):
        write_json(rd/'result.json',result)
        for row in results:
            jfile=rd/'jobs'/row['id']/'job.json';job=read_json(jfile)
            job['status']='scored' if row['valid'] else 'scoring_failed';write_json(jfile,job)
        write_json(rd/'state.json',{'phase':'evaluated','round_id':rid,'evaluated_at':now()})
    return result

def promote(root,rid,jid):
    root=Path(root);rid=safe_id(rid);jid=safe_id(jid);rd=root/'runs'/rid
    with locked(root):
        plan=read_json(rd/'plan.json');state=read_json(rd/'state.json')
        if state['phase']!='evaluated':raise ThinError('Evaluate before promote')
        res=read_json(rd/'result.json');candidate=next((x for x in res['candidates'] if x['id']==jid),None)
        if not candidate or not candidate['valid']:raise ThinError('Cannot promote absent/invalid candidate')
        prior=read_best(root)
        prevsha=plan['best_before']['sha256'] if plan['best_before'] else None
        if (prior['sha256'] if prior else None)!=prevsha:raise ThinError('Current best changed since round started')
        margin=plan['config']['evaluation']['promotion_margin']
        if prior and candidate['effect']<=margin:
            raise ThinError(f'Candidate effect {candidate["effect"]} does not exceed promotion margin {margin}')
        source=rd/'jobs'/jid/'candidate.cpp'
        if digest(source)!=candidate['source_sha256']:raise ThinError('Candidate mutated after evaluation')
        best=root/'best';best.mkdir(exist_ok=True)
        if prior:
            hist=best/'history';hist.mkdir(exist_ok=True)
            shutil.copyfile(best/'main.cpp',hist/f'{rid}-prior-{prior["sha256"][:12]}.cpp')
        shutil.copyfile(source,best/'main.cpp')
        info={'sha256':digest(best/'main.cpp'),'round':rid,'job':jid,'effect':candidate['effect'],
              'score':candidate['mean_score'],'source':str(source.relative_to(root)),'at':now(),
              'warning':'Winner chosen on dev cases; 10-way winner selection may overfit'}
        write_json(best/'best.json',info)
        res['promoted']=jid;write_json(rd/'result.json',res)
    return info
