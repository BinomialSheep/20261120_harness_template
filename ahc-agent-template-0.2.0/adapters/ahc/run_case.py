#!/usr/bin/env python3
"""Canonical batch / interactive / output-only trial adapter.
The supplied problem wrapper emits result.json. Solvers never receive hidden input paths here.
"""
from __future__ import annotations
import json, os, shlex, shutil, sys
from pathlib import Path
from harness.ahc_contract import validate,validate_result
from harness.execution import run_files
from harness.util import HarnessError,load,save,sha


def command(template,values):
    out=list(template)
    for k,v in values.items():out=[s.replace('{'+k+'}',str(v)) for s in out]
    return out


def execute(q):
    wd=Path(q['work_dir']);oracle=Path(q['oracle_root']);c=validate(q['contract']);lim=c['limits']
    case=Path(q['input_path'])
    if sha(case)!=q['input_sha256']:raise HarnessError('Case input hash mismatch')
    result_path=wd/'judge-result.json';output=wd/'solver.out'
    vals={'python':sys.executable,'oracle':oracle,'input':case,'output':output,
          'result':result_path,'work':wd,'solver_seed':q['solver_seed']}
    env={k:v.replace('{solver_seed}',str(q['solver_seed'])) for k,v in q['variant'].get('env',{}).items()}
    env['AHC_SOLVER_SEED']=str(q['solver_seed'])
    proc_meta={}
    if q.get('precomputed_output'):
        shutil.copyfile(q['precomputed_output'],output)
        if sha(output)!=q['precomputed_sha256']:raise HarnessError('Output hash changed')
        if output.stat().st_size>lim['max_output_bytes']:
            return {'schema_version':1,'status':'solver_output_limit','valid':False,'raw_score':None,'reason':'Saved output exceeds cap'}
        mode='output_only'
    else:mode=c['mode']
    if mode=='output_only' and not q.get('precomputed_output'):raise HarnessError('output_only requires saved output; use judge, not eval')
    if mode in ('batch','interactive'):
        argv=q['solver_command']+command(q['variant'].get('args',[]),{'solver_seed':q['solver_seed']})
        constrained=[sys.executable,'-S',str(oracle/'adapters/ahc/limit_exec.py'),
                     json.dumps({'memory_mb':lim.get('solver_memory_mb'),'cpu_s':lim.get('solver_cpu_s')}),*argv]
        if not lim.get('solver_memory_mb') and not lim.get('solver_cpu_s'):constrained=argv
        save(wd/'solver-invocation.json',{'argv':argv,'env':env,'seed_control':c.get('seed_control',{})})
    if mode=='batch':
        p=run_files(constrained,cwd=wd,stdin_path=case,stdout_path=output,stderr_path=wd/'solver.err',
                    timeout=lim['solver_wall_s'],max_output=lim['max_output_bytes'],max_stderr=lim['max_stderr_bytes'],env=env)
        proc_meta['solver']=p
        if p['status']!='ok':
            status={'timeout':'solver_timeout','crash':'solver_crash','output_limit':'solver_output_limit'}.get(p['status'],'infrastructure_error')
            # RLIMIT_CPU signal is reported as timeout; unknown memory-related crashes remain crashes.
            if p.get('returncode')==-24:status='solver_timeout'
            return {'schema_version':1,'status':status,'valid':False,'raw_score':None,'metrics':proc_meta,'reason':p['status']}
    if mode=='interactive':
        launch=wd/'solver-launch'
        # Secrets were stripped from the driver environment; variants cannot override HOME/PATH.
        launcher_args=['env',*[f'{k}={v}' for k,v in env.items()],*constrained]
        launch.write_text('#!/bin/sh\nexec '+shlex.join(launcher_args)+'\n');launch.chmod(0o700)
        vals['solver']=launch
        argv=command(c['tester_command'],vals);timeout=lim['tester_wall_s']
    else:
        argv=command(c['judge_command'],vals);timeout=lim['judge_wall_s']
    p=run_files(argv,cwd=wd,stdin_path=None,stdout_path=wd/'judge.stdout',stderr_path=wd/'judge.stderr',
                timeout=timeout,max_output=lim['max_stderr_bytes'],max_stderr=lim['max_stderr_bytes'])
    proc_meta['tester' if mode=='interactive' else 'judge']=p
    if p['status']!='ok':
        # Only the tester can distinguish solver timeout/invalid from tester failure.
        return {'schema_version':1,'status':'infrastructure_error','valid':False,'raw_score':None,
                'reason':'Judge/tester process '+p['status'],'metrics':proc_meta}
    if not result_path.is_file() or result_path.stat().st_size>1048576: raise HarnessError('Missing/oversized judge JSON')
    r=validate_result(load(result_path));r['metrics']={**r.get('metrics',{}),'processes':proc_meta}
    if output.is_file():r['output_sha256']=sha(output)
    trace=wd/'transcript.jsonl'
    if trace.is_file():r['transcript_sha256']=sha(trace)
    r['score_origin']='supplied_judge_adapter';return r


def main():
    out=Path(sys.argv[2])
    try:r=execute(load(Path(sys.argv[1])))
    except Exception as e:r={'schema_version':1,'status':'infrastructure_error','valid':False,'raw_score':None,'reason':f'{type(e).__name__}: {e}'}
    save(out,r)
if __name__=='__main__':main()
