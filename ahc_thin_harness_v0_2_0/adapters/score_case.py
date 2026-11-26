#!/usr/bin/env python3
"""Score one seed with the official AHC tools (score-result-v1). Copied to problem/score_case.py on deployment.

    python3 score_case.py --binary SOLVER --seed 7 --result r.json [--mode batch|interactive] [--time-limit 2.0]
                          [--features N,M,-] [--save-dir DIR]

batch:       bin/gen makes the input, the solver runs on it, bin/vis scores the output (\"Score = N\").
interactive: bin/tester runs the solver and prints \"Score = N\" on stderr.
The tools are looked up in bin/ next to this file (build them with build_tools.sh).

Result JSON:
  valid    {"status": "ok", "valid": true, "score": 123, "elapsed_sec": ..., "features": {...}}
  invalid  {"status": "invalid", "valid": false, "score": null, "reason": "TLE: ..."}   (TLE / RE / MLE / WA)
A tool failure is not the solver's fault: the adapter then exits non-zero WITHOUT writing the JSON, and the harness
stops the evaluation instead of failing the candidate.

The solver's end is taken from a blocking wait (os.wait4), so elapsed_sec is the real end, not a polling tick.
--features names the tokens of the input's first line (\"-\" skips one), e.g. N,M,T,-,- -> {"N": 20, "M": 15, "T": 250}.
AHC_SOLVER_PRELOAD (set by batchcheck --time-scale) is preloaded into the solver only.
"""
import argparse
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BIN = Path(__file__).resolve().parent / 'bin'
GRACE = 0.5
SCORE_RE = re.compile(r'^\s*Score\s*=\s*(-?\d+(?:\.\d+)?)\s*$', re.M)


class ToolError(Exception):
    pass


def tool(name):
    p = BIN / name
    if not os.access(p, os.X_OK):
        raise ToolError(f'{p} が無い（build_tools.sh でビルドする）')
    return str(p)


def generate(seed, work):
    (work / 'seeds.txt').write_text(f'{seed}\n')
    r = subprocess.run([tool('gen'), 'seeds.txt'], cwd=work, capture_output=True, text=True, timeout=60)
    made = work / 'in' / '0000.txt'
    if r.returncode or not made.is_file():
        raise ToolError(f'gen が失敗: {r.stderr.strip()[-300:]}')
    os.replace(made, work / 'input.txt')
    return (work / 'input.txt').read_text()


def run(cmd, work, stdin, stdout, stderr, limit, env):
    """Run until exit or limit + GRACE. Returns (exit code or None if killed, elapsed, cpu, max RSS MiB)."""
    with open(stdin, 'rb') as fi, open(stdout, 'wb') as fo, open(stderr, 'wb') as fe:
        t0 = time.perf_counter()
        p = subprocess.Popen(cmd, cwd=work, stdin=fi, stdout=fo, stderr=fe, env=env, start_new_session=True)
        killed = threading.Event()

        def kill():
            killed.set()
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        timer = threading.Timer(limit + GRACE, kill)
        timer.start()
        _, status, ru = os.wait4(p.pid, 0)
        elapsed = time.perf_counter() - t0
        timer.cancel()
        p.returncode = os.waitstatus_to_exitcode(status)
        try:
            os.killpg(p.pid, signal.SIGKILL)  # helpers left in the solver's group
        except ProcessLookupError:
            pass
    return (None if killed.is_set() else p.returncode), elapsed, ru.ru_utime + ru.ru_stime, ru.ru_maxrss / 1024


def features_of(text, names):
    if not names:
        return None
    first = text.split('\n', 1)[0].split()
    out = {}
    for name, tok in zip(names.split(','), first):
        if name and name != '-':
            try:
                out[name] = int(tok)
            except ValueError:
                try:
                    out[name] = float(tok)
                except ValueError:
                    pass
    return out


def judge(a, work):
    text = generate(a.seed, work)
    limit = a.time_limit * a.time_scale
    env = dict(os.environ)
    solver = [str(Path(a.binary).resolve())]
    pre = env.pop('AHC_SOLVER_PRELOAD', None)
    if pre:
        solver = ['env', f'LD_PRELOAD={pre}', f'SLOWCLOCK_K={env.get("SLOWCLOCK_K", "1")}'] + solver
    cmd = [tool('tester')] + solver if a.mode == 'interactive' else solver
    code, elapsed, cpu, rss = run(cmd, work, work / 'input.txt', work / 'output.txt', work / 'stderr.txt', limit, env)
    info = {'elapsed_sec': round(elapsed, 4), 'cpu_sec': round(cpu, 3), 'max_rss_mib': round(rss, 1)}
    feats = features_of(text, a.features)
    if feats is not None:
        info['features'] = feats

    def bad(reason):
        return {'status': 'invalid', 'valid': False, 'score': None, 'reason': reason, **info}
    if code is None or elapsed > limit:
        return bad(f'TLE: {elapsed:.3f}s > {limit:g}s')
    err = (work / 'stderr.txt').read_text(errors='replace')
    if a.mode == 'batch' and code != 0:
        return bad(f'RE: 終了コード {code}')
    if rss > a.memory_mb:
        return bad(f'MLE: {rss:.0f} MiB > {a.memory_mb} MiB')
    if a.mode == 'batch':
        v = subprocess.run([tool('vis'), 'input.txt', 'output.txt'], cwd=work, capture_output=True, text=True, timeout=120)
        report = v.stdout + v.stderr
    else:
        report = err
    m = SCORE_RE.findall(report)
    if not m:
        if a.mode == 'interactive' and code != 0:
            return bad(f'RE/WA: tester の終了コード {code}: {err.strip()[-200:]}')
        raise ToolError(f'"Score = " が出ていない: {report.strip()[-300:]}')
    score = float(m[-1])
    score = int(score) if score.is_integer() else score
    others = [ln for ln in report.splitlines() if ln.strip() and not SCORE_RE.match(ln)]
    if score == 0 and others and not a.zero_is_valid:
        return bad('WA: ' + ' / '.join(others)[:300])
    return {'status': 'ok', 'valid': True, 'score': score, **info}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--binary', required=True)
    ap.add_argument('--seed', type=int, required=True)
    ap.add_argument('--result', required=True)
    ap.add_argument('--mode', choices=['batch', 'interactive'], default='batch')
    ap.add_argument('--time-limit', type=float, default=2.0)
    ap.add_argument('--time-scale', type=float, default=1.0, help='制限時間を K 倍にする（batchcheck --time-scale 用）')
    ap.add_argument('--memory-mb', type=float, default=1024)
    ap.add_argument('--features', help='入力の1行目の値に付ける名前（例 N,M,-）')
    ap.add_argument('--zero-is-valid', action='store_true', help='0 点を、ほかの出力があっても有効とみなす')
    ap.add_argument('--save-dir', help='入力・出力・標準エラー出力・vis.html をここに残す')
    a = ap.parse_args()
    try:
        if a.save_dir:
            work = Path(a.save_dir).resolve()
            work.mkdir(parents=True, exist_ok=True)
            rec = judge(a, work)
        else:
            with tempfile.TemporaryDirectory(prefix='judge-') as t:
                rec = judge(a, Path(t))
    except (ToolError, OSError, subprocess.SubprocessError) as e:
        print(f'score_case.py: 判定の環境エラー（ソルバーのせいではない）: {e}', file=sys.stderr)
        return 2
    Path(a.result).write_text(json.dumps({**rec, 'seed': a.seed}, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
