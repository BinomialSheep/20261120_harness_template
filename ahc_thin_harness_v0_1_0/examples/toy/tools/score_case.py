#!/usr/bin/env python3
"""Standalone synthetic case runner. Real problems replace it with official tools wrapper."""
import argparse,json,random,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--binary');p.add_argument('--seed',type=int);p.add_argument('--result');a=p.parse_args()
r=random.Random(a.seed);n=24;k=5;v=[r.randint(1,1000) for _ in range(n)]
try:
 x=subprocess.run([a.binary],input=f'{n} {k}\n'+ ' '.join(map(str,v))+'\n',text=True,capture_output=True,timeout=1)
 if x.returncode:out={'status':'invalid','valid':False,'score':None,'reason':'solver crashed'}
 else:
  ids=list(map(int,x.stdout.split()));valid=len(ids)==k and len(set(ids))==k and all(0<=i<n for i in ids)
  out={'status':'ok' if valid else 'invalid','valid':valid,'score':sum(v[i] for i in ids) if valid else None}
except (ValueError,subprocess.TimeoutExpired) as e:out={'status':'invalid','valid':False,'score':None,'reason':type(e).__name__}
Path(a.result).write_text(json.dumps(out))
