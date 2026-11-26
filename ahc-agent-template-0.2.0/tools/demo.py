#!/usr/bin/env python3
"""Runs three synthetic problems in a disposable project; does not configure the user's real project."""
import argparse,json,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.helpers import make_project,mission
from tests.fixtures.setup import prepare
from harness import research as R
from harness.artifacts import build
from harness.evaluate import run_evaluation
from harness.replay import replay
from harness.judge import score_output
from harness import storage as S
from harness.util import save

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out');a=ap.parse_args();result={}
    with tempfile.TemporaryDirectory(prefix='ahc-demo ') as td:
        root=make_project(Path(td)/'copy',full=True)
        mission(root,{'solver_runs':150,'judge_runs':1},{'evaluation':True,'rescore':True})
        for kind in ('max','min','interactive'):
            path,suite=prepare(root,kind,24)
            art=build(root,'tests/fixtures/'+('search.cpp' if kind=='interactive' else 'selector.cpp'),kind)
            R.reserve(root,'demo-'+kind,['evaluation'],{'solver_runs':49})
            out=run_evaluation(root,path,art['artifact_id'],art['artifact_id'],kind,2,work='demo-'+kind)
            again=replay(root,out['run_id'],'t0000000','demo-'+kind)
            result[kind]={'summary':out['summary'],'rerun':{k:v for k,v in again.items() if k!='path'}}
            R.settle(root,'demo-'+kind,'done',{'solver_runs':49})
        # Re-score a saved valid output, without invoking the solver.
        rd=S.evidence(root,'runs')/out['run_id']
        path,suite=prepare(root,'max',1);answer=root/'work/saved.out';answer.write_text('0 1 2\n')
        R.reserve(root,'score',['rescore'],{'judge_runs':1})
        r=score_output(root,suite['case_set_id'],'c0000',answer,suite['contract'],'score')
        result['output_only']=r['result'];R.settle(root,'score','done',{'judge_runs':1})
    result['scope']='Synthetic problems only. No AtCoder API, network, official tool or competition policy used.'
    if a.out:save(Path(a.out),result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
