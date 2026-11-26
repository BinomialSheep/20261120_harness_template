#!/usr/bin/env python3
"""Invoke an optional supplied visualizer for a frozen batch trial. No browser/API automation."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from harness.config import root as find_root
from harness import storage as S,cases as C
from harness.evaluate import verify_oracle
from harness.process import run
from harness.util import HarnessError,contained,load,save,sha
from adapters.ahc.run_case import command

def visualize(root,run_id,trial,execute=False):
    rd=contained(S.evidence(root,'runs'),run_id);plan=load(rd/'plan.json');verify_oracle(rd,plan)
    q=load(contained(rd,'trials/'+trial+'/request.json'));c=plan['contract_data']
    if c['mode']!='batch':raise HarnessError('Interactive visualization is problem-specific; use a supplied transcript visualizer explicitly')
    recipe=c.get('visualizer_command')
    if not recipe:raise HarnessError('No visualizer command in the frozen problem contract')
    m=C.resolve(root,plan['case_set_id']);case=next(x for x in plan['cases'] if x['id']==q['trial']['case_id'])
    out=rd/'visualizations'/trial;out.mkdir(parents=True,exist_ok=True)
    argv=command(recipe,{'python':sys.executable,'oracle':rd/'oracle_snapshot','input':Path(m['directory'])/case['file'],
                       'output':rd/'trials'/trial/'solver.out','visualization':out/'view.svg','work':out})
    report={'argv':argv,'network':'none requested','executed':False,'directory':str(out)}
    if execute:
        report['process']=run(argv,cwd=out,timeout=30,max_output=1048576);report['executed']=True
        report['ok']=report['process']['status']=='ok'
        verify_oracle(rd,plan);save(out/'receipt.json',report)
    return report
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('run_id');ap.add_argument('--trial',required=True);ap.add_argument('--run',action='store_true');a=ap.parse_args()
    try:print(json.dumps(visualize(find_root(),a.run_id,a.trial,a.run),indent=2,ensure_ascii=False))
    except (HarnessError,OSError,ValueError) as e:print(str(e),file=sys.stderr);raise SystemExit(2)
