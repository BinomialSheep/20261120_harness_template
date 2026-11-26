"""Launch workers with an engine CLI (codex, claude, ...), sort out how they ended, read usage limits."""
from __future__ import annotations

import concurrent.futures
import fcntl
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import usage
from .config import engine_spec
from .core import freeze, jobs_of, locked, round_dir
from .process import execute, kill_in
from .util import ThinError, format_argv, now, read_json, write_json

LIMIT_RE = re.compile(r"hit your [\w -]{0,20}limit|usage limit|rate limit (?:reached|exceeded)|limit reached|"
                      r"insufficient_quota|quota exceeded", re.I)
NETWORK_RE = re.compile(r'Reconnecting\.\.\. (\d+)/\1|stream disconnected|failed to lookup address|network error', re.I)
CLAMP = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'RAYON_NUM_THREADS', 'CARGO_BUILD_JOBS')


def version_of(path):
    try:
        p = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', p.stdout + p.stderr)
    return tuple(int(x) for x in m.groups()) if m else None


def _ver(s):
    return tuple(int(x) for x in str(s).split('.')) if s else None


def resolve(config, name):
    """-> (argv template with the executable resolved, info). Tries each `bin` candidate (globs allowed)
    and keeps the newest one that meets min_version, e.g. the CLI bundled with the VS Code extension."""
    engines = config['workers']['engines']
    if name not in engines:
        raise ThinError(f'engine {name} が config.json に無い（{", ".join(engines) or "未設定"}）')
    spec = engine_spec(engines[name])
    argv = list(spec['argv'])
    if '{' in argv[0]:  # e.g. {python} for the demo engine
        return argv, {'engine': name}
    need = _ver(spec.get('min_version'))
    found = []
    for cand in spec.get('bin') or [argv[0]]:
        cand = os.path.expanduser(cand)
        paths = glob.glob(cand) if any(c in cand for c in '*?[') else [cand]
        for p in paths:
            exe = p if os.sep in p else (subprocess.run(['which', p], capture_output=True, text=True).stdout.strip() or None)
            if exe and os.access(exe, os.X_OK):
                found.append((version_of(exe), exe))
    ok = sorted([f for f in found if f[0] and (not need or f[0] >= need)], reverse=True)
    if not ok:
        seen = ', '.join(f'{e} ({".".join(map(str, v)) if v else "版不明"})' for v, e in found) or 'なし'
        raise ThinError(f'engine {name}: 使える実行ファイルが無い（必要な版 {spec.get("min_version") or "任意"}、見つかったもの: {seen}）')
    version, exe = ok[0]
    argv[0] = exe
    return argv, {'engine': name, 'bin': exe, 'version': '.'.join(map(str, version)), **settings(argv)}


def settings(argv):
    """Model and effort pinned in the argv (claude: --model/--effort, codex: -m / -c model_reasoning_effort=)."""
    out = {}
    for a, b in zip(argv, argv[1:]):
        if a in ('--model', '-m'):
            out['model'] = b
        elif a == '--effort':
            out['effort'] = b
        elif a == '-c' and b.startswith('model_reasoning_effort='):
            out['effort'] = b.split('=', 1)[1].strip('"\'')
    return out


def _tail(path, n=20000):
    try:
        with open(path, 'rb') as f:
            f.seek(max(0, os.path.getsize(path) - n))
            return f.read().decode('utf-8', errors='replace')
    except OSError:
        return ''


def classify(jdir, run):
    """How a worker ended: ok, timeout, limit (usage limit), network, or error. Looks only at the end of the
    logs and at error events, so a worker merely talking about limits is not misread."""
    out, err = _tail(jdir / 'agent.stdout'), _tail(jdir / 'agent.stderr', 4000)
    errors = []
    for line in out.splitlines()[-400:]:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        t = d.get('type')
        if t in ('error', 'turn.failed') or (t == 'result' and d.get('is_error')):
            errors.append(json.dumps(d, ensure_ascii=False))
    plain = [] if out.lstrip().startswith('{') else out.strip().splitlines()[-3:]
    text = '\n'.join(errors + plain + err.strip().splitlines()[-5:])
    if LIMIT_RE.search(text):
        return 'limit', LIMIT_RE.search(text).group(0)
    if run['status'] != 'ok':
        last = [e for e in errors if 'Reconnecting' in e or NETWORK_RE.search(e)]
        if last and re.search(r'Reconnecting\.\.\. (\d+)/\1|stream disconnected', last[-1]):
            return 'network', 'Reconnecting / stream disconnected'
    if run['status'] in ('ok', 'timeout'):
        return run['status'], ''
    return 'error', (errors[-1] if errors else err.strip()[-200:])[:300]


def _run_one(root, rd, jid, mode, minutes, override, stop):
    jdir = rd / 'jobs' / jid
    with (jdir / 'launch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'id': jid, 'status': 'running_elsewhere'}
        try:
            job = read_json(jdir / 'job.json')
            if job['status'] != 'pending':
                return {'id': jid, 'status': job['status'], 'skipped': True}
            if stop.is_set():
                return {'id': jid, 'status': 'pending', 'skipped': '利用上限のため起動しなかった'}
            plan = read_json(rd / 'plan.json')
            name = override or job.get('engine')
            if not name or name == 'manual':
                return {'id': jid, 'status': 'pending', 'skipped': 'manual（submit で提出する）'}
            argv, info = resolve(plan['config'], name)
            ws = Path(job['workspace'])
            job.update(status='running', started_at=now(), **info)
            write_json(jdir / 'job.json', job)
            cfg = ws / 'worker_config.json'
            write_json(cfg, {**read_json(cfg), 'deadline_epoch': round(time.time() + minutes * 60)})
            env = dict(os.environ, **{k: '1' for k in CLAMP})
            cmd = format_argv(argv, {'workspace': str(ws), 'python': sys.executable})
            run = execute(cmd, ws, minutes * 60, stdin=(ws / 'task.md').read_text(encoding='utf-8'),
                          stdout_file=jdir / 'agent.stdout', stderr_file=jdir / 'agent.stderr', env=env)
            left = kill_in(ws)
            try:
                usage.collect_codex_rollout(jdir)
                job['usage'] = usage.worker_summary(jdir)
                msg = usage.last_message(jdir)
                if msg is not None:
                    (jdir / 'agent.last_message.md').write_text(msg, encoding='utf-8')
            except Exception as e:  # measuring must never fail a worker
                job['usage_error'] = f'{type(e).__name__}: {e}'[:300]
            outcome, detail = classify(jdir, run)
            job.update(agent_run=run, agent_outcome=outcome, leftover_killed=len(left), finished_at=now())
            if outcome in ('limit', 'network'):
                job.update(status='interrupted', interrupted=outcome, interrupted_detail=detail)
                if outcome == 'limit':
                    stop.set()
                write_json(jdir / 'job.json', job)
            else:
                if outcome == 'error':
                    job['error_detail'] = detail
                job = freeze(jdir, job, mode)
            return {'id': jid, 'status': job['status'], 'outcome': outcome, 'minutes': round(run['seconds'] / 60, 1)}
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_workers(root, rid, engine=None, only=None):
    """Launch every pending worker of the round. A usage limit stops further launches; interrupted workers
    stay in the round until accept / rerun / fail."""
    root = Path(root)
    rd = round_dir(root, rid)
    plan = read_json(rd / 'plan.json')
    if read_json(rd / 'state.json')['phase'] != 'prepared':
        raise ThinError(f'{rid} の worker はもう終わっている（rerun で戻せる）')
    jobs = [j for j in jobs_of(rd) if j['status'] == 'pending' and (not only or j['id'] in only)]
    mode, w = plan['mode'], plan['config']['workers']
    stop = threading.Event()
    width = min(w['parallelism'], len(jobs)) if mode == 'A' else 1
    results = []
    if jobs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, width)) as pool:
            futs = [pool.submit(_run_one, root, rd, j['id'], mode, w['minutes'][mode], engine, stop) for j in jobs]
            results = sorted((f.result() for f in futs), key=lambda r: r['id'])
    with locked(root):
        left = [j['id'] for j in jobs_of(rd) if j['status'] in ('pending', 'running', 'interrupted')]
        state = read_json(rd / 'state.json')
        if not left and state['phase'] == 'prepared':
            write_json(rd / 'state.json', {**state, 'phase': 'workers_done', 'workers_at': now()})
    return {'round_id': rid, 'workers': results, 'waiting': left, 'limit_hit': stop.is_set()}


# ---------------------------------------------------------------- usage limits

def codex_limits(timeout=60):
    """Codex 5-hour and weekly usage via `codex app-server` (read-only methods only)."""
    allowed = {'initialize', 'initialized', 'account/rateLimits/read'}
    p = subprocess.Popen(['codex', 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    timer = threading.Timer(timeout, p.kill)
    timer.start()

    def send(obj):
        if obj['method'] not in allowed:
            raise ThinError('読み取り以外のメソッドは送らない: ' + obj['method'])
        p.stdin.write(json.dumps(obj) + '\n')
        p.stdin.flush()

    def wait(i):
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get('id') == i:
                if 'error' in msg:
                    raise ThinError(f'codex app-server: {msg["error"]}')
                return msg['result']
        raise ThinError('codex app-server が応答しない')
    try:
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'thin-limits', 'version': '1'}}})
        wait(1)
        send({'jsonrpc': '2.0', 'method': 'initialized'})
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'account/rateLimits/read', 'params': {'excludeResetCreditDetails': True}})
        snap = wait(2).get('rateLimits') or {}
    finally:
        timer.cancel()
        p.terminate()
    out = {'plan': snap.get('planType'), 'limit_reached': snap.get('rateLimitReachedType')}
    for key, name in (('primary', 'five_hour'), ('secondary', 'weekly')):
        if snap.get(key):
            out[name] = {'used_percent': snap[key].get('usedPercent'), 'resets_at': snap[key].get('resetsAt')}
    return out


def claude_limits(config, engine='claude', timeout=120):
    """Claude's utilization from the rate_limit_event of one tiny `claude -p` call (uses a little quota)."""
    argv, info = resolve(config, engine)
    cmd = [argv[0], '-p', '--model', 'haiku', '--output-format', 'stream-json', '--verbose', '--tools', '',
           '--no-session-persistence', 'Reply with OK.']
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    windows = {}
    for line in p.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        for name, reset, pct in usage._claude_limits(d) if isinstance(d, dict) else []:
            windows[name] = {'used_percent': pct, 'resets_at': reset}
    return windows or {'error': (p.stderr or p.stdout).strip()[-300:]}


def estimate(root, mode, engine, workers):
    """Points of the codex 5-hour window that past rounds spent per worker of this mode and engine."""
    per = []
    for rd in sorted((Path(root) / 'runs').glob('R*')):
        try:
            plan, u = read_json(rd / 'plan.json'), read_json(rd / 'usage.json')
        except ThinError:
            continue
        if plan['mode'] != mode or not u.get('workers'):
            continue
        engines = {w.get('engine') for w in u['workers'].values()}
        if engines != {engine}:
            continue
        pts = sum(s['last'] - s['first'] for s in u.get('worker_limits') or [] if s['window'] == 'five_hour')
        per.append(pts / max(1, len(u['workers'])))
    if not per:
        return None
    return {'rounds': len(per), 'per_worker': round(sum(per) / len(per), 1),
            'expected': round(sum(per) / len(per) * workers, 1)}
