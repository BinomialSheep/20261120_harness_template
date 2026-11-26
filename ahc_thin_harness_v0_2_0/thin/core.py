"""Rounds: prepare worker workspaces, freeze submissions, close. Launching workers is in engines.py and
evaluation in scoring.py.

One orchestrator is assumed. The lock prevents two harness commands from changing the same round at once;
it does not sandbox workers. Workers see a copy of the problem, their task and the chosen parents only.
"""
from __future__ import annotations

import contextlib
import difflib
import fcntl
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import usage
from .config import MODES, load as load_config, tuning_overlaps
from .process import execute
from .util import ThinError, digest, fmt_effect, now, read_json, relpath, safe_id, write_json, write_text

HARNESS = Path(__file__).resolve().parents[1]
IGNORE = {'target', '.git', '__pycache__', 'node_modules', '.venv', '.cache', '.build'}
KINDS = {'A': ('new', 'continue', 'combine'), 'B': ('deep',), 'C': ('measure',)}
PARENTS = {'new': (0, 0), 'continue': (1, 1), 'combine': (2, 3), 'deep': (1, 3), 'measure': (0, 3)}
PARENT_RE = re.compile(r'R\d{4,}/[AB]\d{2}')
TERMINAL = ('submitted', 'scored', 'scoring_failed', 'failed', 'worker_failed', 'done')
ARCHIVE_LIMIT = 50 << 20  # bytes of report data copied back from a workspace


@contextlib.contextmanager
def locked(root):
    with (Path(root) / '.thin.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def copy_tree(src, dst):
    for p in Path(src).rglob('*'):
        if p.is_symlink():
            raise ThinError(f'シンボリックリンクは写さない（実体に置き換える）: {p}')
    shutil.copytree(src, dst, ignore=lambda d, names: [n for n in names if n in IGNORE])


def tree_hash(path):
    h, path = hashlib.sha256(), Path(path)
    for f in sorted(path.rglob('*')):
        if f.is_file() and not any(x in IGNORE for x in f.relative_to(path).parts):
            h.update(str(f.relative_to(path)).encode())
            h.update(digest(f).encode())
    return h.hexdigest()


def round_dir(root, rid):
    rd = Path(root) / 'runs' / safe_id(rid)
    if not (rd / 'plan.json').is_file():
        raise ThinError(f'ラウンド {rid} が無い')
    return rd


# ---------------------------------------------------------------- best

def read_best(root):
    p = Path(root) / 'best' / 'best.json'
    if not p.exists():
        return None
    b = read_json(p)
    src = Path(root) / 'best' / 'main.cpp'
    if not src.is_file() or digest(src) != b['sha256']:
        raise ThinError('best/main.cpp が promote 以外で書き換えられている')
    return b


def init_best(root, source):
    root = Path(root)
    load_config(root)
    with locked(root):
        if (root / 'best' / 'best.json').exists():
            raise ThinError('best はもうある（入れ替えは promote で行う）')
        src = Path(source).resolve()
        if not src.is_file():
            raise ThinError(f'ファイルが無い: {source}')
        (root / 'best').mkdir(exist_ok=True)
        shutil.copyfile(src, root / 'best' / 'main.cpp')
        meta = {'sha256': digest(root / 'best' / 'main.cpp'), 'origin': 'init-best', 'source': src.name, 'at': now()}
        write_json(root / 'best' / 'best.json', meta)
    return meta


def resolve_parent(root, ref):
    root = Path(root)
    if ref == 'best':
        if not read_best(root):
            raise ThinError('best が無い（init-best するか、new の案から始める）')
        return root / 'best' / 'main.cpp'
    if not isinstance(ref, str) or not PARENT_RE.fullmatch(ref):
        raise ThinError(f'親は best か R0001/A01 の形で書く: {ref}')
    rid, jid = ref.split('/')
    jdir = root / 'runs' / rid / 'jobs' / jid
    src = jdir / 'candidate.cpp'
    job = read_json(jdir / 'job.json') if (jdir / 'job.json').is_file() else {}
    if job.get('status') not in ('submitted', 'scored') or not src.is_file() or digest(src) != job.get('sha256'):
        raise ThinError(f'親 {ref} は提出済みの候補ではない')
    return src


# ---------------------------------------------------------------- ideas and workspaces

def _sentence(text, n=40):
    s = re.split(r'(?<=[。．.!?！？])\s*', text.strip())[0].rstrip('。．')
    return s if len(s) <= n else s[:n - 1] + '…'


def parse_ideas(mode, ideas_path=None, idea=None, title=None, parents=None, refs=None, engine=None):
    """-> (common text, [idea]). A: 1-10 ideas from JSON. B and C: one idea, from JSON or --idea."""
    common = ''
    if ideas_path:
        d = read_json(ideas_path)
        common = str(d.get('common') or '').strip() if isinstance(d, dict) else ''
        items = d.get('ideas') if isinstance(d, dict) else d
    elif idea:
        items = [{'title': title, 'idea': idea, 'parents': parents, 'refs': refs or [], 'engine': engine}]
    else:
        raise ThinError('--ideas FILE か --idea TEXT を渡す')
    limit = 10 if mode == 'A' else 1
    if not isinstance(items, list) or not 1 <= len(items) <= limit:
        raise ThinError(f'{mode} の案は 1〜{limit} 個')
    out = []
    for i, x in enumerate(items, 1):
        if not isinstance(x, dict):
            raise ThinError(f'{i} 番目の案がオブジェクトでない')
        kind = x.get('kind') or {'B': 'deep', 'C': 'measure'}.get(mode)
        text = str(x.get('idea') or '').strip()
        if kind not in KINDS[mode]:
            raise ThinError(f'{i} 番目の案：{mode} の kind は {"・".join(KINDS[mode])}')
        if len(text) < 12:
            raise ThinError(f'{i} 番目の案の idea が短すぎる')
        name = str(x.get('title') or '').strip() or _sentence(text)
        if len(name) > 40:
            raise ThinError(f'{i} 番目の案の title は40字まで（いま {len(name)} 字）')
        ps = x.get('parents')
        if ps is None:
            ps = [] if kind in ('new', 'measure') else ['best']
        lo, hi = PARENTS[kind]
        if not isinstance(ps, list) or len(set(ps)) != len(ps) or not lo <= len(ps) <= hi:
            raise ThinError(f'{i} 番目の案：{kind} の親は {lo}〜{hi} 本（重複なし）')
        rf = x.get('refs') or []
        if not isinstance(rf, list) or not all(isinstance(r, str) for r in rf):
            raise ThinError(f'{i} 番目の案の refs は文字列のリスト')
        out.append({'id': f'{mode}{i:02d}', 'title': name, 'kind': kind, 'idea': text, 'parents': ps, 'refs': rf,
                    'engine': x.get('engine') or engine})
    if len({x['idea'] for x in out}) != len(out):
        raise ThinError('同じ案が重複している')
    return common, out


def round_seeds(config, number):
    e = config['evaluation']
    start = e['first_seed'] + (number - 1) * e['block']
    dev = start + e['stages'][-1]
    return {'start': start, 'dev': [start, dev], 'confirm': [dev, dev + e['confirm']]}


def workspace_base(root, config):
    root = Path(root).resolve()
    base = (root / config['workers']['workspace_root'].format(repo=root.name)).resolve()
    if base == root or base.is_relative_to(root):
        raise ThinError('workers.workspace_root はリポジトリの外にする（上の階層の指示ファイルを worker に読ませないため）')
    return base


DEF_RE = re.compile(r'^\s{0,4}(?=\S)(?!(?:if|for|while|switch|return|else|do|case|catch|sizeof)\b)[\w:<>,*&~\s]+?\b(\w+)\s*\([^;]*\)\s*'
                    r'(?:const\s*)?(?:noexcept\s*)?(?:->\s*[\w:<>]+\s*)?\{?\s*$')
TYPE_RE = re.compile(r'^\s{0,4}(?=\S)(?:struct|class|namespace|enum)\b')


def outline(source):
    """Top-level types and functions with line numbers, so a worker can find its way in a long parent."""
    rows = [f'{i:5d}: {line.strip()[:100]}' for i, line in enumerate(source.splitlines(), 1)
            if TYPE_RE.match(line) or (DEF_RE.match(line) and not line.rstrip().endswith(';'))]
    return '\n'.join(rows) + '\n'


def render(template, values, flags):
    """Tiny template: `@if A|B` / `@if !tuning` ... `@end` blocks and {name} placeholders."""
    out, stack = [], []
    for line in template.splitlines():
        s = line.strip()
        if s.startswith('@if '):
            cond = s[4:].strip()
            hit = any(flags.get(n) for n in cond.lstrip('!').split('|'))
            stack.append(hit != cond.startswith('!'))
        elif s == '@end':
            stack.pop()
        elif all(stack):
            out.append(line)
    text = '\n'.join(out).format_map(values)
    return re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'


def tuning_of(config, mode):
    t = config['workers']['tuning']
    return t if t and mode in t.get('modes', ['B', 'C']) else None


def _make_workspace(root, rd, plan, x, ws):
    config, mode = plan['config'], plan['mode']
    if ws.exists() and any(ws.iterdir()):
        raise ThinError(f'作業場所が空でない: {ws}')
    jdir = rd / 'jobs' / x['id']
    jdir.mkdir(parents=True)
    ws.mkdir(parents=True, exist_ok=True)
    copy_tree(rd / 'problem', ws / 'problem')
    parents = []
    for i, ref in enumerate(x['parents'], 1):
        dst = ws / f'parent_{i}.cpp'
        shutil.copyfile(resolve_parent(root, ref), dst)
        write_text(ws / f'parent_{i}.outline.txt', outline(dst.read_text(encoding='utf-8', errors='replace')))
        parents.append({'ref': ref, 'sha256': digest(dst)})
    if len(parents) >= 2:
        a, b = ((ws / f'parent_{i}.cpp').read_text(errors='replace').splitlines(keepends=True) for i in (1, 2))
        write_text(ws / 'parent_2.diff', ''.join(list(difflib.unified_diff(a, b, 'parent_1.cpp', 'parent_2.cpp'))[:5000]))
    if parents and mode in ('A', 'B'):
        shutil.copyfile(ws / 'parent_1.cpp', ws / 'main.cpp')
    size = 0
    for r in x['refs']:
        src = relpath(root, r)
        dst = ws / 'ref' / src.name
        dst.parent.mkdir(exist_ok=True)
        (copy_tree if src.is_dir() else shutil.copyfile)(src, dst)
        size += sum(f.stat().st_size for f in ([dst] if dst.is_file() else dst.rglob('*')) if f.is_file())
    if size > 100 << 20:
        raise ThinError('参考ファイルが 100MB を超える')
    tune = tuning_of(config, mode)
    files = ['quickcheck.py'] + (['batchcheck.py'] if tune else [])
    for name in files:
        shutil.copyfile(HARNESS / 'worker' / name, ws / name)
    if tune:
        shutil.copyfile(HARNESS / 'thin' / 'tools' / 'slowclock.c', ws / 'slowclock.c')
    shutil.copyfile(HARNESS / 'worker' / 'AGENTS.md', ws / 'AGENTS.md')
    if (root / 'demo_assets').is_dir():  # synthetic workers of `thin demo`
        for f in (root / 'demo_assets').iterdir():
            shutil.copyfile(f, ws / f.name)
    if mode == 'C':
        (ws / 'out').mkdir()
    seeds = config['workers']['debug_seeds'][mode]
    mini = {'mode': mode, 'build': config['solver']['build'], 'judge': config['judge']['command'],
            'judge_timeout': config['judge']['timeout_sec'], 'allowed_seeds': seeds}
    if tune:
        mini['tuning'] = {k: tune[k] for k in ('seed_start', 'seed_count', 'parallelism')}
    write_json(ws / 'worker_config.json', mini)
    names = '、'.join(f'parent_{i}.cpp（{p["ref"]}）' for i, p in enumerate(parents, 1))
    values = {'mode': {'A': 'A', 'B': 'B', 'C': 'C・測定'}[mode], 'title': x['title'], 'common': plan['common'],
              'idea': x['idea'], 'parents': names, 'minutes': f'{config["workers"]["minutes"][mode]:g}',
              'seeds': ', '.join(map(str, seeds))}
    if tune:
        lo, n = tune['seed_start'], tune['seed_count']
        values.update(tuning_example=f'{lo}-{lo + min(n, 200) - 1}', tuning_range=f'{lo}〜{lo + n - 1}',
                      tuning_par=tune['parallelism'])
    flags = {mode: True, 'parents': bool(parents), 'diff': len(parents) >= 2, 'refs': bool(x['refs']), 'tuning': bool(tune)}
    write_text(ws / 'task.md', render((HARNESS / 'worker' / 'task.md').read_text(encoding='utf-8'), values, flags))
    git = execute(['git', 'init', '-q'], ws, 30, stdout_file=jdir / 'git.stdout', stderr_file=jdir / 'git.stderr')
    if git['status'] != 'ok':
        raise ThinError(f'作業場所で git init できない: {git}')
    job = {'id': x['id'], 'title': x['title'], 'kind': x['kind'], 'idea': x['idea'], 'parents': parents, 'refs': x['refs'],
           'engine': x['engine'], 'status': 'pending', 'workspace': str(ws), 'created_at': now()}
    write_json(jdir / 'job.json', job)


def prepare(root, mode, ideas_path=None, idea=None, title=None, parents=None, refs=None, engine=None):
    root = Path(root)
    config = load_config(root)
    if mode not in MODES:
        raise ThinError('行動は A・B・C')
    common, ideas = parse_ideas(mode, ideas_path, idea, title, parents, refs, engine)
    engines = config['workers']['engines']
    for x in ideas:
        for p in x['parents']:
            resolve_parent(root, p)
        for r in x['refs']:
            if not relpath(root, r).exists():
                raise ThinError(f'参考ファイルが無い: {r}')
        x['engine'] = x['engine'] or config['workers']['default_engine']
        if x['engine'] not in engines and x['engine'] not in (None, 'manual'):
            raise ThinError(f'engine {x["engine"]} が config.json に無い')
    base = workspace_base(root, config)
    best = read_best(root)
    with locked(root):
        runs = root / 'runs'
        runs.mkdir(exist_ok=True)
        for st in sorted(runs.glob('R*/state.json')):
            if read_json(st).get('phase') != 'closed':
                raise ThinError(f'{st.parent.name} が閉じていない（close してから次へ）')
        nums = [int(p.name[1:]) for p in runs.iterdir() if p.is_dir() and re.fullmatch(r'R\d+', p.name)]
        number = max(nums, default=0) + 1
        rid = f'R{number:04d}'
        rd = runs / rid
        seeds = round_seeds(config, number)
        if tuning_overlaps(config, seeds['start'], seeds['start'] + config['evaluation']['block']):
            raise ThinError('評価の seed が workers.tuning の seed と重なる（tuning.seed_start を動かす）')
        rd.mkdir()
        if best:
            shutil.copyfile(root / 'best' / 'main.cpp', rd / 'baseline.cpp')
        copy_tree(relpath(root, config['problem']['dir']), rd / 'problem')
        if not (rd / 'problem' / 'statement.md').is_file():
            shutil.copyfile(relpath(root, config['problem']['statement']), rd / 'problem' / 'statement.md')
        plan = {'schema_version': 2, 'round_id': rid, 'number': number, 'mode': mode, 'created_at': now(),
                'config': config, 'seeds': seeds, 'best_before': best, 'common': common, 'ideas': ideas,
                'problem_sha256': tree_hash(rd / 'problem')}
        write_json(rd / 'plan.json', plan)
        for x in ideas:
            _make_workspace(root, rd, plan, x, base / rid / x['id'])
        write_json(rd / 'state.json', {'phase': 'prepared', 'round_id': rid, 'prepared_at': now()})
    return {'round_id': rid, 'mode': mode, 'workers': len(ideas), 'workspaces': str(base / rid),
            'seeds': seeds, 'engines': sorted({str(x['engine']) for x in ideas})}


# ---------------------------------------------------------------- submissions

def archive(ws, jdir):
    """Copy the worker's notes and, for C, its report and data back into the repository."""
    for name in ('notes.md', 'report.md'):
        if (ws / name).is_file():
            shutil.copyfile(ws / name, jdir / name)
    out = ws / 'out'
    if out.is_dir() and any(out.iterdir()):
        size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
        if size <= ARCHIVE_LIMIT:
            if (jdir / 'out').exists():
                shutil.rmtree(jdir / 'out')
            shutil.copytree(out, jdir / 'out', symlinks=False, ignore_dangling_symlinks=True)
        else:
            write_text(jdir / 'out.skipped.txt', f'{out} は {size >> 20}MB あるので写さなかった\n')


def freeze(jdir, job, mode):
    ws = Path(job['workspace'])
    archive(ws, jdir)
    if mode == 'C':
        ok = (jdir / 'report.md').is_file()
        job.update(status='done' if ok else 'worker_failed', **({} if ok else {'error': 'report.md が無い'}))
    else:
        src = ws / 'main.cpp'
        if not src.is_file() or src.stat().st_size < 10:
            job.update(status='worker_failed', error='main.cpp が無いか空')
        else:
            shutil.copyfile(src, jdir / 'candidate.cpp')
            sha = digest(jdir / 'candidate.cpp')
            if job['parents'] and sha == job['parents'][0]['sha256']:
                job.update(status='worker_failed', error='main.cpp が親のまま', sha256=sha)
            else:
                job.update(status='submitted', sha256=sha, submitted_at=now())
    write_json(jdir / 'job.json', job)
    return job


def _job(root, rid, jid, allowed, phases=('prepared',)):
    rd = round_dir(root, rid)
    jdir = rd / 'jobs' / safe_id(jid)
    job = read_json(jdir / 'job.json')
    phase = read_json(rd / 'state.json')['phase']
    if phase not in phases:
        raise ThinError(f'{rid} はいま {phase}。この操作はできない')
    if job['status'] not in allowed:
        raise ThinError(f'{rid}/{jid} はいま {job["status"]}（この操作は {"・".join(allowed)} のときだけ）')
    return rd, jdir, job, read_json(rd / 'plan.json')['mode']


def submit(root, rid, jid):
    """Freeze a manual worker's main.cpp (or report.md for C)."""
    with locked(root):
        rd, jdir, job, mode = _job(root, rid, jid, ('pending', 'worker_failed', 'interrupted'))
        return freeze(jdir, job, mode)


def accept(root, rid, jid):
    """Take an interrupted worker's files as they are."""
    with locked(root):
        rd, jdir, job, mode = _job(root, rid, jid, ('interrupted',))
        job['accepted_interrupted'] = job.get('interrupted')
        return freeze(jdir, job, mode)


def fail_job(root, rid, jid, reason):
    if not reason:
        raise ThinError('--reason を書く')
    with locked(root):
        rd, jdir, job, mode = _job(root, rid, jid, ('pending', 'worker_failed', 'interrupted'))
        job.update(status='failed', error=reason)
        write_json(jdir / 'job.json', job)
    return job


def rerun(root, rid, jid, fresh=False):
    """Make a stopped worker runnable again. Without --fresh it continues from its current files."""
    with locked(root):
        rd, jdir, job, mode = _job(root, rid, jid, ('interrupted', 'worker_failed', 'failed'), ('prepared', 'workers_done'))
        ws = Path(job['workspace'])
        plan = read_json(rd / 'plan.json')
        if fresh:
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(jdir)
            x = next(i for i in plan['ideas'] if i['id'] == jid)
            _make_workspace(root, rd, plan, {**x, 'engine': job.get('engine')}, ws)
        else:
            reason = job.get('interrupted') or job.get('error') or job['status']
            task = (ws / 'task.md').read_text(encoding='utf-8')
            write_text(ws / 'task.md', task + f'\n## 再開\n\n前回の作業は途中で止まった（{reason}）。作業場所のファイルはそのときのまま。続きから進める。\n')
            job.update(status='pending', reruns=job.get('reruns', 0) + 1)
            job.pop('error', None)
            write_json(jdir / 'job.json', job)
        write_json(rd / 'state.json', {**read_json(rd / 'state.json'), 'phase': 'prepared'})
    return read_json(jdir / 'job.json')


def jobs_of(rd):
    plan = read_json(Path(rd) / 'plan.json')
    return [read_json(Path(rd) / 'jobs' / x['id'] / 'job.json') for x in plan['ideas']]


def mark_workers_done(root, rid):
    with locked(root):
        rd = round_dir(root, rid)
        jobs = jobs_of(rd)
        waiting = [j['id'] for j in jobs if j['status'] not in TERMINAL]
        if waiting:
            raise ThinError(f'まだ終わっていない worker がある: {", ".join(waiting)}（submit・accept・fail・rerun で片付ける）')
        state = read_json(rd / 'state.json')
        if state['phase'] == 'prepared':
            write_json(rd / 'state.json', {**state, 'phase': 'workers_done', 'workers_at': now()})
    return {'round_id': rid, 'jobs': {j['id']: j['status'] for j in jobs}}


# ---------------------------------------------------------------- close and status

def close(root, rid, note=None, report=True):
    root = Path(root)
    with locked(root):
        rd = round_dir(root, rid)
        state, plan = read_json(rd / 'state.json'), read_json(rd / 'plan.json')
        waiting = [j['id'] for j in jobs_of(rd) if j['status'] == 'submitted']
        if state['phase'] == 'closed':
            raise ThinError(f'{rid} はもう閉じている')
        if state['phase'] == 'prepared':
            raise ThinError('worker が終わっていない（run か workers-done を先に）')
        if state['phase'] == 'workers_done' and waiting and plan['mode'] != 'C':
            raise ThinError(f'評価していない候補がある: {", ".join(waiting)}（evaluate してから close）')
        state.update(phase='closed', closed_at=now(), **({'note': note} if note else {}))
        write_json(rd / 'state.json', state)
    out = {'round_id': rid, 'closed': True}
    for name, step in (('research_log', lambda: append_log(root, rd, note)),
                       ('usage', lambda: usage.save_snapshots(root, upto=rd.name)),
                       ('report', lambda: make_report(root) if report else None)):
        try:
            out[name] = step()
        except Exception as e:  # bookkeeping must never block the next round
            out[name] = f'失敗: {type(e).__name__}: {e}'[:300]
    return out


def append_log(root, rd, note):
    """One row per round in docs/RESEARCH_LOG.md under 「## ラウンド記録」 (numbers only; the orchestrator's note last)."""
    log = Path(root) / 'docs' / 'RESEARCH_LOG.md'
    if not log.is_file():
        return 'docs/RESEARCH_LOG.md が無いので書かなかった'
    plan = read_json(rd / 'plan.json')
    res = read_json(rd / 'result.json') if (rd / 'result.json').is_file() else {}
    engines = sorted({str(j.get('engine')) for j in jobs_of(rd)})
    win = next((c for c in res.get('candidates', []) if c['id'] == res.get('promoted')), None)
    eff = fmt_effect(win.get('decision_effect', win.get('effect')), win.get('decision_se', win.get('standard_error'))) if win else '—'
    row = f'| {rd.name} | {plan["mode"]} | {"・".join(engines)}×{len(plan["ideas"])} | {res.get("promoted") or "—"} | {eff} | {(note or "").replace("|", "／")} |'
    text = log.read_text(encoding='utf-8')
    if f'| {rd.name} |' in text:
        return '既にある'
    write_text(log, text.rstrip('\n') + '\n' + row + '\n')
    return 'ok'


def make_report(root):
    script = HARNESS / 'thin' / 'tools' / 'report.py'
    if not script.is_file():
        return 'thin/tools/report.py が無い'
    p = subprocess.run([sys.executable, str(script), '--root', str(root)], capture_output=True, text=True, timeout=300)
    return (p.stdout.strip().splitlines() or ['ok'])[-1] if p.returncode == 0 else f'失敗: {p.stderr.strip()[-300:]}'


def status(root):
    root = Path(root)
    best = read_best(root)
    rows = []
    for st in sorted((root / 'runs').glob('R*/state.json')) if (root / 'runs').is_dir() else []:
        rd = st.parent
        state, plan = read_json(st), read_json(rd / 'plan.json')
        res = read_json(rd / 'result.json') if (rd / 'result.json').is_file() else {}
        counts = {}
        for j in jobs_of(rd):
            counts[j['status']] = counts.get(j['status'], 0) + 1
        bg = rd / 'go.pid'
        running = bg.is_file() and _alive(int(bg.read_text().strip() or 0))
        rows.append({'round': rd.name, 'mode': plan['mode'], 'phase': state['phase'], 'jobs': counts,
                     'recommended': res.get('recommended'), 'promoted': res.get('promoted'),
                     'background': 'running' if running else None, 'note': state.get('note')})
    return {'best': best, 'rounds': rows[-12:]}


def _alive(pid):
    import os
    try:
        os.kill(pid, 0)
        return pid > 0
    except OSError:
        return False
