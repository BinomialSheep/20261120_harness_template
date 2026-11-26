#!/usr/bin/env python3
"""How much would more time or more runs give? Measured without an LLM and without changing the source.

    python3 -m thin.cli probe                    # current best, 120 seeds, time x0.5/2/4/8 once, x1 four times
    python3 -m thin.cli probe --count 60 --scales 2,8 --reps 3 --source path/to/main.cpp

The solver sees a clock that runs K times slower (thin/tools/slowclock.c, preloaded into the solver only), so every
deadline and stage budget in it stretches by K. Works for std::chrono / clock_gettime, not for direct rdtsc.
Seeds: the first --count seeds of the latest evaluated round (already spent), unless --seeds is given.
Writes runs/probe/latest.json, which the report shows. 12 parallel x 120 seeds takes about 6 minutes for a 2 s problem.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default='.')
    ap.add_argument('--source', help='測るソース（既定は best/main.cpp）')
    ap.add_argument('--seeds', help='例 19000-19119')
    ap.add_argument('--count', type=int, default=120)
    ap.add_argument('--scales', default='0.5,2,4,8')
    ap.add_argument('--reps', type=int, default=4, help='1倍で走らせる回数（回数を増やしたときの最良にも使う）')
    ap.add_argument('--jobs', type=int)
    a = ap.parse_args()
    root = Path(a.root).resolve()
    cfg = json.loads((root / 'config.json').read_text())
    src = Path(a.source).resolve() if a.source else root / 'best' / 'main.cpp'
    if not src.is_file():
        sys.exit(f'ソースが無い: {src}')
    if a.seeds:
        lo, _, hi = a.seeds.partition('-')
        seeds = list(range(int(lo), int(hi or lo) + 1))
    else:
        plans = sorted((root / 'runs').glob('R*/result.json'))
        if not plans:
            sys.exit('評価済みのラウンドが無い。--seeds で seed を指定する')
        start = json.loads((plans[-1].parent / 'plan.json').read_text())['seeds']['dev'][0]
        seeds = list(range(start, start + a.count))
    work = Path(tempfile.mkdtemp(prefix='probe-'))
    so = work / 'slowclock.so'
    subprocess.run(['gcc', '-O2', '-shared', '-fPIC', '-o', str(so), str(HERE / 'slowclock.c'), '-ldl'], check=True)
    binary = work / 'solver'
    build = [x.format(source=str(src), binary=str(binary), workspace=str(work), python=sys.executable) for x in cfg['solver']['build']]
    if subprocess.run(build).returncode:
        sys.exit('コンパイルできない')
    scales = [float(x) for x in a.scales.split(',') if x]
    tasks = [(s, 1.0, r) for r in range(a.reps) for s in seeds] + [(s, k, 0) for k in scales for s in seeds]
    jobs = a.jobs or (cfg.get('evaluation') or {}).get('parallelism') or max(1, (os.cpu_count() or 2) // 2 * 3 // 4)
    timeout = (cfg.get('judge') or {}).get('timeout_sec', 10)

    def one(t):
        seed, k, rep = t
        with tempfile.TemporaryDirectory(dir=work) as d:
            d = Path(d)
            fmt = {'python': sys.executable, 'problem': str(root / 'problem'), 'tools': str(root / 'problem' / 'tools'),
                   'workspace': str(root), 'binary': str(binary), 'seed': str(seed), 'result': str(d / 'r.json'), 'workdir': str(d)}
            cmd = [x.format(**fmt) for x in cfg['judge']['command']]
            env = dict(os.environ)
            if k != 1:
                cmd += ['--time-scale', f'{k:g}']
                env.update(AHC_SOLVER_PRELOAD=str(so), SLOWCLOCK_K=f'{k:g}')
            p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout * max(1, k) + 30)
            try:
                r = json.loads((d / 'r.json').read_text())
            except (OSError, ValueError):
                return {'seed': seed, 'k': k, 'rep': rep, 'score': None, 'error': p.stderr[-200:]}
            return {'seed': seed, 'k': k, 'rep': rep, 'score': r.get('score') if r.get('valid') else None,
                    'elapsed_sec': r.get('elapsed_sec'), 'features': r.get('features'), 'reason': r.get('reason')}
    t0 = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            rows = list(pool.map(one, tasks))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    feats = {str(r['seed']): r['features'] for r in rows if r.get('features')}
    out = {'source': str(src.relative_to(root)) if src.is_relative_to(root) else str(src),
           'best_sha256': hashlib.sha256(src.read_bytes()).hexdigest(), 'at': time.strftime('%Y-%m-%d %H:%M'),
           'seeds': [seeds[0], seeds[-1]], 'scales': scales, 'reps': a.reps, 'features': feats,
           'rows': [{k: r[k] for k in ('seed', 'k', 'rep', 'score', 'elapsed_sec')} for r in rows]}
    (root / 'runs' / 'probe').mkdir(parents=True, exist_ok=True)
    (root / 'runs' / 'probe' / 'latest.json').write_text(json.dumps(out, ensure_ascii=False))
    bad = sum(r['score'] is None for r in rows)
    mean = lambda k, rep=0: [r['score'] for r in rows if r['k'] == k and r['rep'] == rep and r['score'] is not None]
    base = [sum(mean(1.0, q)) / max(1, len(mean(1.0, q))) for q in range(a.reps)]
    lines = [f'{len(seeds)} 盤面、{len(rows)} 回、{(time.monotonic() - t0) / 60:.1f} 分、無効 {bad}']
    b1 = sum(base) / len(base)
    for k in scales:
        v = mean(k)
        if v:
            lines.append(f'時間 {k:g} 倍: 平均 {sum(v) / len(v):.3f}（1倍との差 {sum(v) / len(v) - b1:+.3f}）')
    print('\n'.join(lines) + '\nruns/probe/latest.json に書いた（レポートが読む）')


if __name__ == '__main__':
    main()
