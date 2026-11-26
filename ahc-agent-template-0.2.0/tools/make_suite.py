#!/usr/bin/env python3
"""Create an explicit case-comparison suite. No default objective direction."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.util import save,HarnessError
from harness.config import root as find_root
from harness.cases import resolve
from harness.evaluate import validate_suite
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case-set',required=True);p.add_argument('--contract',default='problem/contract.json')
p.add_argument('--direction',required=True,choices=['maximize','minimize']);p.add_argument('--comparison',default='difference',choices=['difference','log_ratio'])
p.add_argument('--purpose',default='dev',choices=['smoke','dev','diagnostic','audit','stress']);p.add_argument('--solver-seed',type=int,action='append')
p.add_argument('--timing-sensitive',action='store_true');p.add_argument('--out',required=True)
if __name__=='__main__':
    a=p.parse_args();root=find_root();resolve(root,a.case_set);dest=root/a.out
    if dest.exists():raise SystemExit('Output exists: keep past evaluation definitions intact')
    s={'schema_version':1,'mode':'ahc','purpose':a.purpose,'case_set_id':a.case_set,'contract':a.contract,
       'metric':{'name':'raw_score','direction':a.direction,'comparison':a.comparison,'aggregation':'case_mean'},
       'solver_seeds':a.solver_seed or [0],'minimum_blocks':20,'run_budget_s':3600,'timing_sensitive':a.timing_sensitive}
    validate_suite(s);save(dest,s);print(json.dumps({'suite':str(dest)},indent=2))
