"""Re-execute a frozen single-solver trial, never continue a stale interactive transcript."""
from pathlib import Path
import sys,uuid
from . import storage as S,cases as C
from .artifacts import resolve
from .research import operation,register_child
from .resources import reserve as machine_reserve
from .process import run
from .evaluate import verify_oracle
from .ahc_contract import validate_result
from .util import contained,load,save,HarnessError


def replay(root,run_id,trial_id,work=None):
    rd=contained(S.evidence(root,'runs'),run_id);td=contained(rd,'trials/'+trial_id)
    plan=load(rd/'plan.json');verify_oracle(rd,plan);manifest=C.resolve(root,plan['case_set_id'])
    q=load(td/'request.json');art=plan['artifacts'][q['trial']['role']]['artifact_id']
    case=next(c for c in plan['cases'] if c['id']==q['trial']['case_id'])
    out=rd/'replays'/f'{trial_id}_{uuid.uuid4().hex[:8]}';out.mkdir(parents=True)
    q.update(work_dir=str(out),oracle_root=str(rd/'oracle_snapshot'),solver_command=resolve(root,art)['command'],
             input_path=str(contained(Path(manifest['directory']),case['file'])),input_sha256=case['sha256'])
    save(out/'request.json',q)
    limits=plan['contract_data']['limits']
    timeout=plan.get('trial_timeout_s',sum(limits[k] for k in ('solver_wall_s','judge_wall_s','tester_wall_s'))+10)
    with operation(root,'evaluation',{'solver_runs':1},work) as auth, machine_reserve('exclusive' if plan.get('timing_sensitive') else 'shared','rerun'):
        p=run([sys.executable,'-S',str(rd/'oracle_snapshot/adapters/ahc/run_case.py'),str(out/'request.json'),str(out/'response.json')],
              cwd=out,timeout=timeout,env={'PYTHONPATH':str(rd/'oracle_snapshot')},on_start=lambda pid:register_child(root,auth,pid))
    (out/'driver.stdout').write_text(p['stdout']);(out/'driver.stderr').write_text(p['stderr'])
    if p['status']!='ok':raise HarnessError('Rerun driver failed: '+p['status'])
    old=load(td/'response.json');new=validate_result(load(out/'response.json'));verify_oracle(rd,plan)
    comparison={'same_outcome':all(old.get(k)==new.get(k) for k in ('status','raw_score','valid')),
       'output_sha_equal':old.get('output_sha256')==new.get('output_sha256') if old.get('output_sha256') else None,
       'transcript_sha_equal':old.get('transcript_sha256')==new.get('transcript_sha256') if old.get('transcript_sha256') else None,
       'path':str(out),'caution':'Same inputs, program, parameters and seed. Wall-time search may diverge; seed support is solver-specific.'}
    save(out/'comparison.json',comparison);return comparison
