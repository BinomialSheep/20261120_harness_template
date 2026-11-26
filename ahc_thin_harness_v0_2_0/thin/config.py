"""config.json: defaults, validation and placeholder values."""
from __future__ import annotations

import copy
import math
import os
import sys
from pathlib import Path

from .util import ThinError, format_argv, read_json, relpath

MODES = ('A', 'B', 'C')
DEFAULTS = {
    'schema_version': 2,
    'problem': {'dir': 'problem', 'statement': 'problem/statement.md'},
    'solver': {'filename': 'main.cpp',
               'build': ['g++', '-std=gnu++23', '-O2', '-march=native', '-DATCODER', '-DONLINE_JUDGE',
                         '-ftrivial-auto-var-init=zero', '{source}', '-o', '{binary}']},
    'judge': {'command': ['{python}', '{problem}/score_case.py', '--binary', '{binary}', '--seed', '{seed}',
                          '--result', '{result}'],
              'timeout_sec': 10, 'direction': 'maximize', 'metric': 'paired_difference'},
    'evaluation': {'first_seed': 1000, 'block': 3000, 'stages': [100, 300, 1000], 'drop_z': 3.0, 'keep_top': 3,
                   'confirm': 1000, 'skip_confirm_z': 6.0, 'parallelism': 0, 'promotion_margin': 0.0,
                   'tle_retries': 2, 'max_load': 0.5, 'min_free_gb': 2},
    'workers': {'parallelism': 10, 'minutes': {'A': 20, 'B': 120, 'C': 60},
                'debug_seeds': {'A': [0], 'B': list(range(10)), 'C': list(range(10))},
                'tuning': None, 'workspace_root': '../{repo}.workers', 'default_engine': None, 'engines': {}},
    'report': {'feature': None, 'feature_name': None, 'bands': None},
}


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def auto_parallelism():
    """About 3/4 of the physical cores (assuming 2 threads per core), at least 1."""
    return max(1, (os.cpu_count() or 2) // 2 * 3 // 4)


def engine_spec(spec):
    """An engine is a dict {argv, bin, min_version, models} or, for short, just the argv list."""
    return {'argv': spec} if isinstance(spec, list) else dict(spec)


def load(root):
    root = Path(root)
    raw = read_json(root / 'config.json')
    if raw.get('schema_version') != 2:
        raise ThinError('config.json は schema_version 2 にする（v0.1 の設定は config.example.json を見て書き直す）')
    c = merge(DEFAULTS, raw)
    try:
        _check(root, c)
    except (KeyError, TypeError, ValueError) as e:
        raise ThinError(f'config.json が不正: {e}') from e
    return c


def _number(x, name, low=0, strict=False):
    if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) or x < low or (strict and x == low):
        raise ThinError(f'config.json の {name} は {"より大きい" if strict else "以上の"}{low} の数にする')


def _check(root, c):
    p = c['problem']
    if not relpath(root, p['dir']).is_dir() or not relpath(root, p['statement']).is_file():
        raise ThinError(f'問題の置き場所が無い: {p["dir"]}、{p["statement"]}')
    if c['solver']['filename'] != 'main.cpp':
        raise ThinError('提出ファイルは main.cpp にする')
    j, e, w = c['judge'], c['evaluation'], c['workers']
    format_argv(c['solver']['build'], {'source': 'main.cpp', 'binary': 'main', 'workspace': '.', 'python': sys.executable})
    format_argv(j['command'], {'binary': 'main', 'seed': '0', 'result': 'r.json', 'tools': '.', 'problem': '.',
                               'workspace': '.', 'python': sys.executable, 'workdir': '.'})
    if j['direction'] not in ('maximize', 'minimize'):
        raise ThinError('judge.direction は maximize か minimize')
    if j['metric'] not in ('paired_difference', 'paired_log_ratio'):
        raise ThinError('judge.metric は paired_difference か paired_log_ratio')
    _number(j['timeout_sec'], 'judge.timeout_sec', strict=True)
    st = e['stages']
    if not st or any(type(x) is not int or x < 2 for x in st) or st != sorted(set(st)):
        raise ThinError('evaluation.stages は 2 以上の整数を小さい順に並べる（例 [100, 300, 1000]）')
    if type(e['confirm']) is not int or e['confirm'] < 0:
        raise ThinError('evaluation.confirm は 0 以上の整数')
    if type(e['block']) is not int or e['block'] < st[-1] + e['confirm']:
        raise ThinError('evaluation.block は stages の最後 + confirm 以上にする（1ラウンドで使う seed の幅）')
    if type(e['first_seed']) is not int or e['first_seed'] < 1:
        raise ThinError('evaluation.first_seed は 1 以上の整数')
    if e['parallelism'] == 0:
        e['parallelism'] = auto_parallelism()
    if type(e['parallelism']) is not int or not 1 <= e['parallelism'] <= (os.cpu_count() or 1):
        raise ThinError(f'evaluation.parallelism は 1〜{os.cpu_count()}（0 は自動）')
    for name in ('drop_z', 'skip_confirm_z', 'promotion_margin', 'max_load', 'min_free_gb'):
        _number(e[name], f'evaluation.{name}')
    if type(e['keep_top']) is not int or e['keep_top'] < 1 or type(e['tle_retries']) is not int or e['tle_retries'] < 0:
        raise ThinError('evaluation.keep_top は 1 以上、tle_retries は 0 以上の整数')
    if type(w['parallelism']) is not int or not 1 <= w['parallelism'] <= 10:
        raise ThinError('workers.parallelism は 1〜10')
    for m in MODES:
        _number(w['minutes'][m], f'workers.minutes.{m}', strict=True)
        s = w['debug_seeds'][m]
        if not isinstance(s, list) or not s or len(set(s)) != len(s) or any(type(x) is not int or x < 0 for x in s):
            raise ThinError(f'workers.debug_seeds.{m} は 0 以上の整数のリスト')
    if any(x >= e['first_seed'] for m in MODES for x in w['debug_seeds'][m]):
        raise ThinError('workers.debug_seeds が評価の seed（first_seed 以上）と重なる')
    t = w['tuning']
    if t:
        if t.get('parallelism') == 0:
            t['parallelism'] = e['parallelism']
        if not all(type(t.get(k)) is int and t[k] > 0 for k in ('seed_start', 'seed_count', 'parallelism')):
            raise ThinError('workers.tuning には正の整数の seed_start、seed_count、parallelism を書く')
    for name, spec in w['engines'].items():
        format_argv(engine_spec(spec)['argv'], {'workspace': '.', 'python': sys.executable})
    if w['default_engine'] and w['default_engine'] not in w['engines']:
        raise ThinError(f'workers.default_engine {w["default_engine"]} が engines に無い')


def tuning_overlaps(c, start, end):
    t = c['workers']['tuning']
    return bool(t) and t['seed_start'] < end and start < t['seed_start'] + t['seed_count']


def judge_context(problem_dir, binary, seed, result, workdir):
    problem_dir = Path(problem_dir).resolve()
    return {'python': sys.executable, 'problem': str(problem_dir), 'tools': str(problem_dir / 'tools'),
            'workspace': str(problem_dir.parent), 'binary': str(Path(binary).resolve()), 'seed': str(seed),
            'result': str(Path(result).resolve()), 'workdir': str(Path(workdir).resolve())}
