"""Synthetic scorer: validate exactly k different indices, score their sum."""
import json,sys
from pathlib import Path
inp,out,res=map(Path,sys.argv[1:4])
try:
    x=list(map(int,inp.read_text().split()));n,k,salt=x[:3];a=x[3:]
    if len(a)!=n:raise RuntimeError('broken input fixture')
except Exception as e:raise SystemExit('judge input error: '+str(e))
try:
    p=list(map(int,out.read_text().split()))
    if len(p)!=k or len(set(p))!=k or any(i<0 or i>=n for i in p):raise ValueError('wrong number / duplicate / out-of-range index')
    r={'schema_version':1,'status':'ok','valid':True,'raw_score':sum(a[i] for i in p)}
except (ValueError,UnicodeError) as e:
    r={'schema_version':1,'status':'solver_invalid','valid':False,'raw_score':None,'reason':str(e)}
res.write_text(json.dumps(r)+'\n')
