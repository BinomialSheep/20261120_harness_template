#!/usr/bin/env python3
"""Tune solver constants with Optuna, without an LLM. Needs `pip install optuna` (only this tool does).

    python3 -m thin.cli tune --params tune.json --seeds 1000000-1000239 --trials 60 --verify 2000

tune.json: {"prefix": "AHC_", "params": {"temp_start": {"low": 0.5, "high": 3.0, "log": true},
                                         "beam_width": {"low": 5, "high": 40, "int": true}}}
The solver reads each parameter from the environment variable PREFIX + NAME when it is built with -DTUNE
(see reference/cpp_notes.md for a PARAM macro). Each trial runs every seed once and is scored by the mean paired
difference against the default values on the same seeds. --verify N re-measures the best 3 trials on N seeds right after
the tuning range: the tuning score itself is optimistic because the best of many trials was picked on those seeds.
Results go to runs/tune/<time>.json. Writing the chosen values into main.cpp is left to you (or a B worker).
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
import tempfile
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default='.')
    ap.add_argument('--params', required=True)
    ap.add_argument('--seeds', required=True, help='例 1000000-1000239')
    ap.add_argument('--trials', type=int, default=60)
    ap.add_argument('--verify', type=int, default=0, help='上位3つを、調整に使っていない N 件で測り直す')
    ap.add_argument('--source', help='既定は best/main.cpp')
    ap.add_argument('--jobs', type=int)
    a = ap.parse_args()
    try:
        import optuna
    except ImportError:
        sys.exit('optuna が無い（pip install optuna）')
    root = Path(a.root).resolve()
    cfg = json.loads((root / 'config.json').read_text())
    spec = json.loads(Path(a.params).read_text())
    prefix, params = spec.get('prefix', 'AHC_'), spec['params']
    lo, _, hi = a.seeds.partition('-')
    seeds = list(range(int(lo), int(hi) + 1))
    work = Path(tempfile.mkdtemp(prefix='tune-'))
    src = Path(a.source).resolve() if a.source else root / 'best' / 'main.cpp'
    binary = work / 'solver'
    build = [x.format(source=str(src), binary=str(binary), workspace=str(work), python=sys.executable) for x in cfg['solver']['build']]
    if subprocess.run(build[:1] + ['-DTUNE'] + build[1:]).returncode:
        sys.exit('-DTUNE でコンパイルできない')
    jobs = a.jobs or (cfg.get('evaluation') or {}).get('parallelism') or 4
    sign = -1 if cfg['judge'].get('direction') == 'minimize' else 1

    def score(seed, env):
        with tempfile.TemporaryDirectory(dir=work) as d:
            fmt = {'python': sys.executable, 'problem': str(root / 'problem'), 'tools': str(root / 'problem' / 'tools'),
                   'workspace': str(root), 'binary': str(binary), 'seed': str(seed), 'result': f'{d}/r.json', 'workdir': d}
            subprocess.run([x.format(**fmt) for x in cfg['judge']['command']], capture_output=True,
                           env={**os.environ, **env}, timeout=cfg['judge']['timeout_sec'] + 30)
            try:
                r = json.loads(Path(d, 'r.json').read_text())
            except (OSError, ValueError):
                return None
            return r['score'] if r.get('valid') else None

    def run_all(env, ss):
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            return dict(zip(ss, pool.map(lambda s: score(s, env), ss)))

    def diff(cur, base):
        d = [sign * (cur[s] - base[s]) for s in cur if cur[s] is not None and base.get(s) is not None]
        if len(d) < len(cur):
            return -math.inf, None  # an invalid case rules the setting out
        return statistics.fmean(d), statistics.stdev(d) / math.sqrt(len(d))
    t0 = time.monotonic()
    base = run_all({}, seeds)
    env_of = lambda p: {prefix + k.upper(): str(v) for k, v in p.items()}

    def objective(trial):
        p = {}
        for k, s in params.items():
            if s.get('int'):
                p[k] = trial.suggest_int(k, s['low'], s['high'], log=s.get('log', False))
            else:
                p[k] = trial.suggest_float(k, s['low'], s['high'], log=s.get('log', False))
        m, se = diff(run_all(env_of(p), seeds), base)
        trial.set_user_attr('se', se)
        return m
    import atexit
    atexit.register(shutil.rmtree, work, ignore_errors=True)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction='maximize', sampler=optuna.samplers.TPESampler(seed=0))
    study.optimize(objective, n_trials=a.trials)
    top = sorted((t for t in study.trials if t.value is not None and math.isfinite(t.value)), key=lambda t: -t.value)[:3]
    out = {'source': str(src), 'seeds': [seeds[0], seeds[-1]], 'trials': [{'params': t.params, 'diff': t.value, 'se': t.user_attrs.get('se')}
                                                                         for t in study.trials if t.value is not None]}
    if a.verify and top:
        vs = list(range(seeds[-1] + 1, seeds[-1] + 1 + a.verify))
        vbase = run_all({}, vs)
        out['verify'] = []
        for t in top:
            m, se = diff(run_all(env_of(t.params), vs), vbase)
            out['verify'].append({'params': t.params, 'diff': m, 'se': se, 'tuning_diff': t.value})
    (root / 'runs' / 'tune').mkdir(parents=True, exist_ok=True)
    path = root / 'runs' / 'tune' / f'{time.strftime("%Y%m%d-%H%M%S")}.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f'{len(study.trials)} 試行、{(time.monotonic() - t0) / 60:.1f} 分。上位:')
    for t in top:
        print(f'  {t.value:+.4f} ± {t.user_attrs.get("se") or 0:.4f}  {t.params}')
    for v in out.get('verify', []):
        print(f'  確認 {v["diff"]:+.4f} ± {v["se"] or 0:.4f}（調整中 {v["tuning_diff"]:+.4f}）  {v["params"]}')
    print(f'{path} に書いた')


if __name__ == '__main__':
    main()
