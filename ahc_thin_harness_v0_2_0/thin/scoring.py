"""Evaluation on fresh seeds that every source of the round shares.

A rounds race the candidates in stages (default 100 / 300 / 1000 seeds): after each stage, a candidate outside the
top `keep_top` stops when it is worse than the current best, or than the leader, by more than drop_z standard errors.
The leader is then compared with the current best again on `confirm` unused seeds, and that comparison decides
promotion: choosing the top of many candidates on the same seeds overstates its effect.
A TLE is re-run alone up to tle_retries times (AtCoder also re-runs). A judge failure (no result JSON) is treated as
an environment problem and stops the evaluation instead of failing a candidate. scores.csv files are appended to,
so `evaluate` resumes where it stopped.
"""
from __future__ import annotations

import concurrent.futures
import csv
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from .config import judge_context
from .core import jobs_of, locked, read_best, round_dir, tree_hash, workspace_base
from .process import execute, processes_in
from .util import ThinError, digest, format_argv, now, read_json, write_json

FIELDS = ['seed', 'status', 'score', 'seconds', 'reason', 'features']


# ---------------------------------------------------------------- one source

def _compile(rd, sid, source, config):
    work = rd / 'evaluation' / sid
    work.mkdir(parents=True, exist_ok=True)
    binary, stamp, sha = work / 'solver', work / 'compile.json', digest(source)
    if stamp.is_file():
        prior = read_json(stamp)
        if prior['source_sha256'] != sha:
            raise ThinError(f'{sid} のソースが採点の途中で変わった')
        if prior['status'] != 'ok':
            return None, prior
        if binary.is_file() and digest(binary) == prior.get('binary_sha256'):
            return binary, prior
        raise ThinError(f'{sid} のバイナリが無いか変わった（evaluate --restart で採点し直す）')
    cmd = format_argv(config['solver']['build'], {'source': str(Path(source).resolve()), 'binary': str(binary.resolve()),
                                                  'workspace': str(work.resolve()), 'python': sys.executable})
    run = execute(cmd, work, 180, stdout_file=work / 'compiler.stdout', stderr_file=work / 'compiler.stderr')
    rec = {'source_sha256': sha, 'status': run['status'], 'seconds': run['seconds']}
    if run['status'] == 'ok' and binary.is_file():
        rec['binary_sha256'] = digest(binary)
    else:
        rec.update(status='compile_error', reason=(work / 'compiler.stderr').read_text(errors='replace')[-1200:])
    write_json(stamp, rec)
    return (binary if rec['status'] == 'ok' else None), rec


def run_case(rd, binary, seed, config, sid):
    """One seed through the round's frozen judge. status: ok, invalid (the solver's fault) or adapter_error."""
    work = rd / 'evaluation' / sid
    with tempfile.TemporaryDirectory(dir=work, prefix='case-') as t:
        t = Path(t)
        result = t / 'result.json'
        cmd = format_argv(config['judge']['command'], judge_context(rd / 'problem', binary, seed, result, t))
        proc = execute(cmd, t, config['judge']['timeout_sec'], stdout_file=t / 'judge.stdout', stderr_file=t / 'judge.stderr')
        row = {'seed': seed, 'status': 'adapter_error', 'score': None, 'seconds': proc['seconds'], 'reason': '', 'features': ''}
        if proc['status'] != 'ok' or not result.is_file():
            row['reason'] = (proc['status'] + ': ' + (t / 'judge.stderr').read_text(errors='replace')[-300:]).strip()
            return row
        try:
            r = json.loads(result.read_text())
        except ValueError as e:
            row['reason'] = f'結果の JSON が読めない: {e}'
            return row
        v = r.get('score')
        row['seconds'] = r.get('elapsed_sec', proc['seconds'])
        if isinstance(r.get('features'), dict):
            row['features'] = json.dumps(r['features'], separators=(',', ':'))
        if r.get('status') == 'ok' and r.get('valid') is True and type(v) in (int, float) and math.isfinite(v):
            row.update(status='ok', score=float(v))
        elif r.get('valid') is False and r.get('score') is None:
            row.update(status='invalid', reason=str(r.get('reason', 'invalid'))[:300])
        else:
            row['reason'] = '結果の JSON の形が違う（score-result-v1）'
        return row


def read_rows(path):
    """seed -> last row (a TLE re-run appends a second row for the same seed)."""
    rows = {}
    if Path(path).is_file():
        with open(path, newline='', encoding='utf-8', errors='replace') as f:
            r = csv.DictReader(f)
            if r.fieldnames != FIELDS:
                raise ThinError(f'scores.csv の列が違う: {path}')
            for row in r:
                rows[int(row['seed'])] = row
    return rows


def scores_of(rows):
    return {s: float(r['score']) for s, r in rows.items() if r['status'] == 'ok'}


class Source:
    def __init__(self, sid, path):
        self.id, self.path, self.binary, self.final = sid, path, None, None
        self.rows, self.csv = {}, None

    def bad(self):
        return next((r for r in self.rows.values() if r['status'] not in ('ok', 'tle_pending')), None)


def _prepare(rd, src, config, identity):
    work = rd / 'evaluation' / src.id
    work.mkdir(parents=True, exist_ok=True)
    manifest = work / 'source.json'
    ident = {**identity, 'source_sha256': digest(src.path)}
    if manifest.is_file() and read_json(manifest) != ident:
        raise ThinError(f'{src.id} の採点条件が前回と違う（evaluate --restart で採点し直す）')
    write_json(manifest, ident)
    src.binary, info = _compile(rd, src.id, src.path, config)
    if src.binary is None:
        src.final = {'status': 'compile_error', 'error': info.get('reason', '')[-600:]}
        return
    smoke = work / 'seed0.json'
    check = read_json(smoke) if smoke.is_file() else None
    if check is None:
        check = run_case(rd, src.binary, 0, config, src.id)
        if check['status'] == 'adapter_error':
            raise ThinError(f'判定が動かない（環境の問題）: {src.id} seed 0: {check["reason"]}')
        write_json(smoke, check)
    if check['status'] != 'ok':
        src.final = {'status': 'seed0_failed', 'error': check.get('reason', '')}
        return
    src.csv = work / 'scores.csv'
    src.rows = read_rows(src.csv)


# ---------------------------------------------------------------- running cases

def _append(src, row):
    new = not src.csv.is_file()
    with open(src.csv, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: '' if row.get(k) is None else row.get(k) for k in FIELDS})
    src.rows[row['seed']] = {k: '' if row.get(k) is None else str(row.get(k)) for k in FIELDS}


def run_cases(rd, config, sources, seeds):
    """Score every source on `seeds` (skipping seeds already scored) on one shared pool, seed-major and round-robin
    over sources so that all of them run under the same load. Returns the number of cases run."""
    slots = config['evaluation']['parallelism']
    todo = [(src, s) for s in seeds for src in sources if s not in src.rows]
    stopped, errors, count = set(), [], 0
    it = iter(todo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as pool:
        inflight = {}

        def fill():
            while len(inflight) < slots and not errors:
                for src, seed in it:
                    if src.id not in stopped:
                        inflight[pool.submit(run_case, rd, src.binary, seed, config, src.id)] = src
                        break
                else:
                    return
        fill()
        while inflight:
            done, _ = concurrent.futures.wait(inflight, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                src = inflight.pop(fut)
                row = fut.result()
                count += 1
                if row['status'] == 'adapter_error':
                    errors.append(f'{src.id} seed {row["seed"]}: {row["reason"]}')
                    continue
                if row['status'] == 'invalid' and str(row['reason']).startswith('TLE'):
                    row['status'] = 'tle_pending'
                _append(src, row)
                if row['status'] == 'invalid':
                    stopped.add(src.id)
            fill()
    if errors:
        raise ThinError('判定がエラーで終わった（環境の問題とみて評価を止めた。直してから evaluate を再実行すると続きから採点する）:\n'
                        + '\n'.join(errors[:5]))
    retries = config['evaluation']['tle_retries']
    for src in sources:  # TLE: re-run alone, without other load (AtCoder re-runs a TLE too)
        for seed in sorted(s for s, r in src.rows.items() if r['status'] == 'tle_pending'):
            row = None
            for _ in range(retries):
                row = run_case(rd, src.binary, seed, config, src.id)
                count += 1
                if row['status'] != 'invalid' or not str(row['reason']).startswith('TLE'):
                    break
            if row is None or row['status'] == 'adapter_error':
                row = {**src.rows[seed], 'status': 'invalid'}
            row['reason'] = (row.get('reason') or '') + f'（TLE の後、単独で{retries}回まで再実行）'
            _append(src, row)
    return count


# ---------------------------------------------------------------- statistics

def paired(a, b, seeds, config):
    """Mean and standard error of the per-seed advantage of a over b (positive = a is better)."""
    direction, metric = config['judge']['direction'], config['judge']['metric']
    d = []
    for s in seeds:
        if s not in a or s not in b:
            continue
        x, y = a[s], b[s]
        if metric == 'paired_log_ratio':
            if x <= 0 or y <= 0:
                raise ThinError('paired_log_ratio には正の得点が要る')
            v = math.log(x / y)
        else:
            v = x - y
        d.append(v if direction == 'maximize' else -v)
    if not d:
        return None
    se = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else None
    return {'effect': statistics.fmean(d), 'standard_error': se, 'n': len(d),
            'better_cases': sum(v > 0 for v in d), 'worse_cases': sum(v < 0 for v in d)}


def environment_check(root, rd, config):
    e = config['evaluation']
    warnings = []
    free = shutil.disk_usage(rd).free / 2 ** 30
    if free < e['min_free_gb']:
        raise ThinError(f'空き容量が {free:.1f}GB しかない（evaluation.min_free_gb = {e["min_free_gb"]}）')
    try:
        base = workspace_base(root, config) / rd.name
        left = processes_in(base) if base.is_dir() else []
    except ThinError:
        left = []
    if left:
        raise ThinError(f'worker の作業場所でプロセスが動いている（PID {left[:10]}）。止めてから評価する')
    load = os.getloadavg()[0]
    if load > (os.cpu_count() or 1) * e['max_load']:
        warnings.append(f'評価の開始時に負荷が高い（load average {load:.1f}、CPU {os.cpu_count()}）。実行時間が揺れる')
    return warnings


# ---------------------------------------------------------------- evaluate

def evaluate(root, rid, restart=False):
    root = Path(root)
    rd = round_dir(root, rid)
    plan = read_json(rd / 'plan.json')
    config, mode = plan['config'], plan['mode']
    state = read_json(rd / 'state.json')
    if state['phase'] == 'closed' and (rd / 'result.json').is_file():
        return read_json(rd / 'result.json')
    if mode == 'C':
        raise ThinError('C（測定）のラウンドは評価しない。report.md を読んで close する')
    if state['phase'] not in ('workers_done', 'evaluated'):
        raise ThinError('全 worker が終わってから評価する（status を見る）')
    if restart:
        with locked(root):
            if (rd / 'evaluation').exists():
                os.replace(rd / 'evaluation', rd / f'evaluation.old-{time.strftime("%Y%m%d-%H%M%S")}')
            for j in jobs_of(rd):
                if j['status'] in ('scored', 'scoring_failed'):
                    write_json(rd / 'jobs' / j['id'] / 'job.json', {**j, 'status': 'submitted'})
            (rd / 'result.json').unlink(missing_ok=True)
            write_json(rd / 'state.json', {**state, 'phase': 'workers_done'})
    if tree_hash(rd / 'problem') != plan['problem_sha256']:
        raise ThinError('ラウンドに凍結した problem/ が変わっている')
    warnings = environment_check(root, rd, config)
    t0 = time.monotonic()
    e = config['evaluation']
    jobs = [j for j in jobs_of(rd) if j['status'] in ('submitted', 'scored', 'scoring_failed')]
    if not jobs:
        raise ThinError('評価する候補が無い（全 worker が失敗。workers-done の後 close してよい）')
    groups = {}
    for j in jobs:
        src = rd / 'jobs' / j['id'] / 'candidate.cpp'
        if not src.is_file() or digest(src) != j['sha256']:
            raise ThinError(f'{j["id"]} の候補が提出後に変わった')
        groups.setdefault(j['sha256'], []).append(j['id'])
    base = None
    if plan['best_before']:
        if digest(rd / 'baseline.cpp') != plan['best_before']['sha256']:
            raise ThinError('凍結した baseline.cpp が変わっている')
        base = Source('baseline', rd / 'baseline.cpp')
    cands = [Source(ids[0], rd / 'jobs' / ids[0] / 'candidate.cpp') for ids in groups.values()]
    identity = {'judge': config['judge']['command'], 'build': config['solver']['build'], 'problem': plan['problem_sha256']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=e['parallelism']) as pool:
        list(pool.map(lambda s: _prepare(rd, s, config, identity), ([base] if base else []) + cands))
    if base and base.final:
        raise ThinError(f'現 best が採点できない（環境の問題を疑う）: {base.final}')
    dev = list(range(*plan['seeds']['dev']))
    stages = [n for n in e['stages'] if n < len(dev)] + [len(dev)]
    alive = [c for c in cands if not c.final]
    if mode != 'A' or len(alive) <= 1:
        stages = [len(dev)]
    dropped, cases = {}, 0
    for n in stages:
        cases += run_cases(rd, config, ([base] if base else []) + alive, dev[:n])
        if base and base.bad():
            raise ThinError(f'現 best が無効になった（環境の問題を疑う。直してから evaluate）: {base.bad()}')
        for c in list(alive):
            if c.bad():
                c.final = {'status': 'invalid', 'error': c.bad()['reason'], 'first_bad_seed': int(c.bad()['seed'])}
                alive.remove(c)
        if n == len(dev) or not base or len(alive) <= e['keep_top']:
            continue
        b = scores_of(base.rows)
        stat = {c.id: paired(scores_of(c.rows), b, dev[:n], config) for c in alive}
        order = sorted(alive, key=lambda c: -stat[c.id]['effect'])
        lead = order[0]
        for c in order[e['keep_top']:]:
            s = stat[c.id]
            vs = paired(scores_of(c.rows), scores_of(lead.rows), dev[:n], config)
            if s['effect'] + e['drop_z'] * s['standard_error'] < 0 or vs['effect'] + e['drop_z'] * vs['standard_error'] < 0:
                dropped[c.id] = n
                alive.remove(c)
    rows = []
    b = scores_of(base.rows) if base else None
    for c in cands:
        row = {'id': c.id, 'source_sha256': digest(c.path)}
        if c.final:
            row.update(valid=False, **c.final)
        else:
            sc = scores_of(c.rows)
            seeds = dev[:dropped.get(c.id, len(dev))]
            row.update(valid=True, status='dropped' if c.id in dropped else 'ok', n=len(seeds),
                       mean_score=statistics.fmean(sc[s] for s in seeds), dropped_at=dropped.get(c.id))
            if b is not None:
                row.update(paired(sc, b, seeds, config))
            else:
                row['effect'] = row['mean_score'] * (1 if config['judge']['direction'] == 'maximize' else -1)
        rows.append(row)
    finalists = sorted((r for r in rows if r['valid'] and r['status'] == 'ok'), key=lambda r: -r['effect'])
    confirm = None
    if finalists and b is not None:
        lead = finalists[0]
        z = lead['effect'] / lead['standard_error'] if lead.get('standard_error') else math.inf
        cseeds = list(range(*plan['seeds']['confirm']))
        selected = mode == 'A' and len([r for r in rows if r['valid']]) > 1
        if selected and cseeds and z < e['skip_confirm_z']:
            src = next(c for c in cands if c.id == lead['id'])
            cases += run_cases(rd, config, [base, src], cseeds)
            if base.bad():
                raise ThinError(f'確認評価で現 best が無効になった（環境の問題を疑う）: {base.bad()}')
            if src.bad():
                lead.update(valid=False, status='invalid', error=src.bad()['reason'], first_bad_seed=int(src.bad()['seed']))
                confirm = {'id': lead['id'], 'invalid': True}
            else:
                confirm = {'id': lead['id'], **(paired(scores_of(src.rows), scores_of(base.rows), cseeds, config) or {'effect': None})}
                lead.update(decision_effect=confirm['effect'], decision_se=confirm.get('standard_error'), decision='confirm')
        elif selected and cseeds:
            confirm = {'id': lead['id'], 'skipped': f'z = {z:.1f} ≥ {e["skip_confirm_z"]}'}
    for r in rows:
        if r['valid'] and 'decision_effect' not in r and r['status'] == 'ok':
            r.update(decision_effect=r['effect'], decision_se=r.get('standard_error'), decision='dev')
    margin = e['promotion_margin']
    rec = next((r['id'] for r in finalists if r['valid'] and r.get('decision_effect') is not None
                and (b is None or r['decision_effect'] > margin)), None) if finalists else None
    if confirm and not confirm.get('skipped') and rec != confirm['id']:
        rec = None  # the confirmed leader failed to beat the best; the runner-up was never confirmed
    out = []
    for ids, row in zip(groups.values(), rows):
        for jid in ids:
            out.append({**row, 'id': jid, 'reused_from': row['id'] if jid != row['id'] else None})
    out.sort(key=lambda r: (not r['valid'], r.get('status') != 'ok', -(r.get('effect') or 0) if r['valid'] else 0, r['id']))
    full = len(dev) * (len(cands) + (1 if base else 0))
    result = {'round_id': rid, 'mode': mode, 'seeds': plan['seeds'], 'stages': stages,
              'baseline': ({'mean_score': statistics.fmean(b[s] for s in dev if s in b), 'n': len(b)} if b else None),
              'candidates': out, 'confirm': confirm, 'recommended': rec, 'warnings': warnings,
              'cost': {'cases': cases, 'full_dev_cases': full, 'minutes': round((time.monotonic() - t0) / 60, 1)},
              'judged_at': now()}
    with locked(root):
        write_json(rd / 'result.json', result)
        for r in out:
            jf = rd / 'jobs' / r['id'] / 'job.json'
            write_json(jf, {**read_json(jf), 'status': 'scored' if r['valid'] else 'scoring_failed'})
        write_json(rd / 'state.json', {**read_json(rd / 'state.json'), 'phase': 'evaluated', 'evaluated_at': now()})
    return result


def promote(root, rid, jid, force=None):
    """Make a candidate the new best. Needs a decision effect above promotion_margin unless --force REASON."""
    root = Path(root)
    with locked(root):
        rd = round_dir(root, rid)
        plan, state = read_json(rd / 'plan.json'), read_json(rd / 'state.json')
        if state['phase'] != 'evaluated':
            raise ThinError('evaluate の後に promote する')
        res = read_json(rd / 'result.json')
        cand = next((c for c in res['candidates'] if c['id'] == jid), None)
        if not cand or not cand['valid'] or cand.get('status') != 'ok':
            raise ThinError(f'{jid} は昇格できない（無効か、途中で足切りされた）')
        prior = read_best(root)
        if (prior or {}).get('sha256') != (plan['best_before'] or {}).get('sha256'):
            raise ThinError('ラウンドの開始後に best が変わっている')
        eff, margin = cand.get('decision_effect'), plan['config']['evaluation']['promotion_margin']
        if prior and not force and (eff is None or eff <= margin):
            raise ThinError(f'{jid} の判定値 {eff} が promotion_margin {margin} を超えない（採るなら --force 理由）')
        src = rd / 'jobs' / jid / 'candidate.cpp'
        if digest(src) != cand['source_sha256']:
            raise ThinError('候補が評価の後に変わった')
        best = root / 'best'
        hist = best / 'history'
        hist.mkdir(parents=True, exist_ok=True)
        if prior:
            shutil.copyfile(best / 'main.cpp', hist / f'{rid}-prior-{prior["sha256"][:12]}.cpp')
            if (best / 'notes.md').is_file():
                shutil.copyfile(best / 'notes.md', hist / f'{rid}-prior-notes.md')
        shutil.copyfile(src, best / 'main.cpp')
        notes = rd / 'jobs' / jid / 'notes.md'
        if notes.is_file():
            shutil.copyfile(notes, best / 'notes.md')
        job = read_json(rd / 'jobs' / jid / 'job.json')
        info = {'sha256': digest(best / 'main.cpp'), 'round': rid, 'job': jid, 'title': job.get('title'),
                'effect': cand.get('effect'), 'decision_effect': eff, 'decision': cand.get('decision'),
                'score': cand.get('mean_score'), 'source': str(src.relative_to(root)), 'at': now(),
                **({'forced': force} if force else {})}
        write_json(best / 'best.json', info)
        res['promoted'] = jid
        write_json(rd / 'result.json', res)
    return info
