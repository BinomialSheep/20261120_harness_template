#!/usr/bin/env python3
"""Score many allowed seeds in parallel, optionally against a baseline on the same seeds.

    python3 batchcheck.py --seeds 1000000-1000199
    python3 batchcheck.py --seeds 1000000-1000999 --baseline parent_1.cpp     # paired: mean difference, SE, by band
    python3 batchcheck.py --seeds 1000000-1000119 --time-scale 8              # the solver sees a clock 8x slower
    python3 batchcheck.py --seeds ... --env AHC_PARAM_X=3 --out result.json

--time-scale K loads slowclock.so into the solver only, so it runs about K times longer without source changes
(works for std::chrono / clock_gettime; not for code that reads rdtsc directly).
"""
import argparse
import concurrent.futures
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('--seeds', required=True, help='例 1000000-1000199 または 0,1,2（範囲は両端を含む）')
p.add_argument('--binary', help='ビルド済みのソルバー（既定は main.cpp をビルド）')
p.add_argument('--baseline', help='比べる相手のソース（.cpp）かバイナリ。同じ seed で対にして比べる')
p.add_argument('--env', action='append', default=[], metavar='KEY=VALUE', help='ソルバーへの環境変数（複数可）')
p.add_argument('--time-scale', type=float, default=1.0, help='ソルバーに見せる時計を K 倍遅くする')
p.add_argument('--jobs', type=int, help='同時に走らせるケース数（既定と上限は許可された並列数）')
p.add_argument('--band', help='帯の分け方 KEY:a-b,c-d（例 M:0-9,10-19,20-30）。既定は特徴量の三分位')
p.add_argument('--out', help='seed ごとの結果を JSON で書く')
a = p.parse_args()
w = Path(__file__).resolve().parent
meta = json.loads((w / 'worker_config.json').read_text())
tune = meta.get('tuning')
if not tune:
    sys.exit('この worker には多数ケースの評価が許可されていない')
lo, hi = tune['seed_start'], tune['seed_start'] + tune['seed_count'] - 1
try:
    seeds = []
    for part in filter(None, (x.strip() for x in a.seeds.split(','))):
        x, _, y = part.partition('-')
        seeds += range(int(x), int(y or x) + 1)
except ValueError:
    sys.exit('--seeds の形が違う')
bad = [s for s in seeds if not (lo <= s <= hi or s in meta['allowed_seeds'])]
if not seeds or bad:
    sys.exit(f'許可されていない seed がある（許可: {lo}-{hi} と {meta["allowed_seeds"]}）: {bad[:5]}')
jobs = min(a.jobs or tune['parallelism'], tune['parallelism'])
k = a.time_scale
if not 0 < k <= 64:
    sys.exit('--time-scale は 0 より大きく 64 以下')
run_dir = w / '_batch' / str(os.getpid())
run_dir.mkdir(parents=True, exist_ok=True)
env = dict(os.environ)
for kv in a.env:
    key, sep, val = kv.partition('=')
    if not sep:
        sys.exit('--env は KEY=VALUE')
    env[key] = val
judge = list(meta['judge'])
timeout = meta['judge_timeout']
if k != 1:
    so = run_dir / 'slowclock.so'
    b = subprocess.run(['gcc', '-O2', '-shared', '-fPIC', '-o', str(so), str(w / 'slowclock.c'), '-ldl'], capture_output=True, text=True)
    if b.returncode:
        sys.exit('slowclock.c をビルドできない: ' + b.stderr[-500:])
    env.update(AHC_SOLVER_PRELOAD=str(so), SLOWCLOCK_K=str(k))
    if '--time-limit' in judge:
        i = judge.index('--time-limit')
        judge[i + 1] = str(float(judge[i + 1]) * k)
    else:
        judge += ['--time-scale', str(k)]
    timeout = timeout * k + 10


def build(source, name):
    fmt = {'source': str(Path(source).resolve()), 'binary': str(run_dir / name), 'workspace': str(w), 'python': sys.executable}
    r = subprocess.run([x.format_map(fmt) for x in meta['build']], cwd=w, text=True, capture_output=True, timeout=180)
    if r.returncode:
        sys.exit(f'{source} のコンパイルに失敗: ' + r.stderr[-1500:])
    return run_dir / name


def solver(path, name):
    if path and not str(path).endswith(('.cpp', '.cc')):
        if not Path(path).is_file():
            sys.exit(f'無い: {path}')
        return Path(path).resolve()
    return build(path or w / 'main.cpp', name)


main_bin = solver(a.binary, 'main')
base_bin = solver(a.baseline, 'base') if a.baseline else None


def case(task):
    seed, binary, tag = task
    d = run_dir / f'{tag}-{seed}'
    d.mkdir(exist_ok=True)
    fmt = {'python': sys.executable, 'problem': str(w / 'problem'), 'tools': str(w / 'problem' / 'tools'),
           'workspace': str(w), 'binary': str(binary), 'seed': str(seed), 'result': str(d / 'r.json'), 'workdir': str(d)}
    try:
        r = subprocess.run([x.format_map(fmt) for x in judge], cwd=w, text=True, capture_output=True, timeout=timeout, env=env)
        if r.returncode or not (d / 'r.json').is_file():
            return tag, {'seed': seed, 'valid': False, 'reason': '判定が失敗: ' + r.stderr[-300:]}
        res = json.loads((d / 'r.json').read_text())
        s = res.get('score')
        if res.get('status') != 'ok' or res.get('valid') is not True or type(s) not in (int, float) or not math.isfinite(s):
            return tag, {'seed': seed, 'valid': False, 'reason': str(res.get('reason', res))[:300]}
        return tag, {'seed': seed, 'valid': True, 'score': s, 'elapsed_sec': res.get('elapsed_sec'), 'features': res.get('features')}
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return tag, {'seed': seed, 'valid': False, 'reason': str(e)[:300]}
    finally:
        shutil.rmtree(d, ignore_errors=True)


tasks = [(s, main_bin, 'main') for s in seeds]
if base_bin:
    tasks = [t for s in seeds for t in ((s, main_bin, 'main'), (s, base_bin, 'base'))]  # interleaved: same load
res = {'main': {}, 'base': {}}
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for tag, row in pool.map(case, tasks):
            res[tag][row['seed']] = row
finally:
    shutil.rmtree(run_dir, ignore_errors=True)


def summary(rows):
    ok = [r for r in rows.values() if r['valid']]
    el = [r['elapsed_sec'] for r in ok if r.get('elapsed_sec') is not None]
    return {'n': len(rows), 'valid': len(ok), 'mean_score': round(statistics.fmean(r['score'] for r in ok), 4) if ok else None,
            'max_elapsed_sec': max(el) if el else None,
            'invalid': [{'seed': r['seed'], 'reason': r['reason']} for r in rows.values() if not r['valid']][:10]}


def mean_se(d):
    if len(d) < 2:
        return None
    return {'diff': round(statistics.fmean(d), 4), 'se': round(statistics.stdev(d) / math.sqrt(len(d)), 4), 'n': len(d)}


out = {'main': summary(res['main'])}
if base_bin:
    out['baseline'] = summary(res['base'])
    both = [s for s in seeds if res['main'].get(s, {}).get('valid') and res['base'].get(s, {}).get('valid')]
    diff = {s: res['main'][s]['score'] - res['base'][s]['score'] for s in both}
    out['paired'] = {**(mean_se(list(diff.values())) or {}), 'better': sum(v > 0 for v in diff.values()),
                     'worse': sum(v < 0 for v in diff.values()), 'note': 'diff = main − baseline（最大化なら正が良い）'}
    feats = {s: res['main'][s].get('features') or {} for s in both}
    key, edges = None, None
    if a.band:
        key, _, spec = a.band.partition(':')
        edges = [tuple(float(v) for v in r.split('-')) for r in spec.split(',')]
    elif feats and any(feats.values()):
        key = next(iter(next(f for f in feats.values() if f)))
        vals = sorted(f[key] for f in feats.values() if key in f)
        if len(set(vals)) >= 3:
            q = [vals[len(vals) // 3], vals[2 * len(vals) // 3]]
            edges = [(vals[0], q[0] - 1e-9), (q[0], q[1] - 1e-9), (q[1], vals[-1])]
    if key and edges:
        out['bands'] = {f'{key} {lo_:g}〜{hi_:g}': mean_se([diff[s] for s in both if lo_ <= feats[s].get(key, math.nan) <= hi_])
                        for lo_, hi_ in edges}
d = meta.get('deadline_epoch')
if d:
    out['remaining_min'] = max(0.0, round((d - time.time()) / 60, 1))
if a.out:
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False))
print(json.dumps(out, ensure_ascii=False))
sys.exit(0 if out['main']['valid'] == out['main']['n'] else 3)
