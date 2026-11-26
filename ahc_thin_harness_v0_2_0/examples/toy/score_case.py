#!/usr/bin/env python3
"""Judge of the toy problem (score-result-v1). Real problems use adapters/score_case.py with the official tools."""
import argparse
import json
import random
import subprocess
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--binary', required=True)
p.add_argument('--seed', type=int, required=True)
p.add_argument('--result', required=True)
p.add_argument('--time-limit', type=float, default=1.0)
a = p.parse_args()
r = random.Random(a.seed)
n, k = 24, r.randint(3, 8)
v = [r.randint(1, 1000) for _ in range(n)]
info = {'features': {'K': k}}
t = time.perf_counter()
try:
    x = subprocess.run([a.binary], input=f'{n} {k}\n' + ' '.join(map(str, v)) + '\n', text=True, capture_output=True,
                       timeout=a.time_limit)
    info['elapsed_sec'] = round(time.perf_counter() - t, 4)
    ids = [int(s) for s in x.stdout.split()]
    if x.returncode:
        out = {'status': 'invalid', 'valid': False, 'score': None, 'reason': 'RE'}
    elif len(ids) != k or len(set(ids)) != k or not all(0 <= i < n for i in ids):
        out = {'status': 'invalid', 'valid': False, 'score': None, 'reason': 'WA: 添字が不正'}
    else:
        out = {'status': 'ok', 'valid': True, 'score': sum(v[i] for i in ids)}
except ValueError:
    out = {'status': 'invalid', 'valid': False, 'score': None, 'reason': 'WA: 数でない出力'}
except subprocess.TimeoutExpired:
    out = {'status': 'invalid', 'valid': False, 'score': None, 'reason': f'TLE: > {a.time_limit}s'}
Path(a.result).write_text(json.dumps({**out, **info}))
