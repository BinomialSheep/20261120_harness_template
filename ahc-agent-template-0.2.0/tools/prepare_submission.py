#!/usr/bin/env python3
"""Produce a locally checked source package. Never contacts or submits to a contest service."""
import argparse,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from harness.config import root as find_root
from harness import storage as S
from harness.artifacts import resolve
from harness.evaluate import verify_oracle
from harness.util import HarnessError,load,save,sha,now,contained

def prepare(root,artifact,run_id,out):
    root=Path(root);S.require_integrator(root,'prepare submission');a=resolve(root,artifact);identity=a['identity']
    if any(f.startswith(('-D','-U')) for f in identity['flags']):raise HarnessError('Freeze a macro-free deployment artifact, then evaluate it')
    if set(identity['files'])!={identity['source']}:raise HarnessError('Use pack, build the packed source, and evaluate that deployment artifact first')
    rd=contained(S.evidence(root,'runs'),run_id);plan=load(rd/'plan.json');verify_oracle(rd,plan);s=load(rd/'summary.json')
    if load(rd/'state.json')['state']!='complete' or s['decision']=='invalid':raise HarnessError('Completed valid run required')
    if plan['artifacts']['candidate']['artifact_id']!=a['artifact_id']:raise HarnessError('Candidate differs from deployment artifact')
    if plan.get('purpose')!='smoke':raise HarnessError('Use a deployment smoke suite')
    if plan['variants']['candidate'].get('args') or plan['variants']['candidate'].get('env'):
        raise HarnessError('Deployment smoke must run without local-only parameters; embed chosen parameters and re-evaluate')
    c=plan['contract_data']
    if not c.get('verified') or not c.get('evidence'):raise HarnessError('Validate the supplied problem tooling first')
    if plan['oracle_files_resolved'].get(plan['contract'])!=sha(contained(root,plan['contract'])):raise HarnessError('Current contract differs from smoke evaluation')
    tests=root/'reports/local-tests.json'
    from tools.test import fingerprint
    if not tests.exists() or not load(tests).get('ok') or load(tests).get('skipped',1):raise HarnessError('Run make test first, without skips')
    if load(tests)['source_fingerprint']!=fingerprint(root):raise HarnessError('Regression test record is stale')
    out=contained(root,out,exists=False)
    if out.exists():raise HarnessError('Output exists; do not overwrite a previous deployment package')
    out.mkdir(parents=True);src=Path(a['directory'])/'snapshot'/identity['source'];shutil.copy2(src,out/'main.cpp')
    receipt={'at':now(),'artifact_id':a['artifact_id'],'source_sha256':sha(src),'smoke_run':run_id,
             'test_fingerprint':fingerprint(root),'seed_control':c.get('seed_control'),
             'meaning':'Local technical checks only. No platform submission, policy certification or server-time equivalence.',
             'external_operations':0}
    save(out/'manifest.json',receipt)
    (out/'README.md').write_text('# Local deployment package\n\nmain.cpp is the evaluated source. Submit manually only under your project instructions.\nNo upload was performed. Review runtime defaults, local seed handling, compiler, limits and external data dependencies.\n')
    return {'directory':str(out),'receipt':receipt,'network_calls':0}
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('artifact');ap.add_argument('--run',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    try:print(json.dumps(prepare(find_root(),a.artifact,a.run,a.out),indent=2))
    except (HarnessError,OSError,ValueError,KeyError) as e:print(str(e),file=sys.stderr);raise SystemExit(2)
