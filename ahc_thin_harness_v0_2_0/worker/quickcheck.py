#!/usr/bin/env python3
"""Compile main.cpp and score one allowed seed with the official judge. Prints score, time and remaining minutes.

    python3 quickcheck.py --seed 0
"""
import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--seed', type=int, default=0)
a = p.parse_args()
w = Path(__file__).resolve().parent
meta = json.loads((w / 'worker_config.json').read_text())
if a.seed not in meta['allowed_seeds']:
    sys.exit(f'seed {a.seed} は許可されていない（許可: {meta["allowed_seeds"]}）')
fmt = {'source': str(w / 'main.cpp'), 'binary': str(w / '_quick_bin'), 'workspace': str(w), 'python': sys.executable,
       'problem': str(w / 'problem'), 'tools': str(w / 'problem' / 'tools'), 'seed': str(a.seed),
       'result': str(w / '_quick_result.json'), 'workdir': str(w / 'judge_last')}


def remaining():
    d = meta.get('deadline_epoch')
    return {} if d is None else {'remaining_min': max(0.0, round((d - time.time()) / 60, 1))}


try:
    (w / 'judge_last').mkdir(exist_ok=True)
    build = subprocess.run([x.format_map(fmt) for x in meta['build']], cwd=w, text=True, capture_output=True, timeout=180)
    if build.returncode:
        raise RuntimeError('コンパイルに失敗: ' + build.stderr[-1500:])
    judge = subprocess.run([x.format_map(fmt) for x in meta['judge']], cwd=w, text=True, capture_output=True,
                           timeout=meta['judge_timeout'])
    if judge.returncode:
        raise RuntimeError('判定が失敗: ' + judge.stderr[-1500:])
    r = json.loads(Path(fmt['result']).read_text())
    score = r.get('score')
    if r.get('status') != 'ok' or r.get('valid') is not True or type(score) not in (int, float) or not math.isfinite(score):
        raise RuntimeError('無効な出力: ' + json.dumps(r, ensure_ascii=False)[:600])
    extra = {k: r[k] for k in ('elapsed_sec', 'features') if k in r}
    print(json.dumps({'seed': a.seed, 'valid': True, 'score': score, **extra, **remaining()}, ensure_ascii=False))
except (OSError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
    print('quickcheck failed:', e, json.dumps(remaining()), file=sys.stderr)
    sys.exit(2)
