"""Command line: python3 -m thin.cli <command>. Prints a short summary; --json prints everything."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__, core as C, engines as G, scoring as E
from .util import ThinError, fmt_effect, read_json, root_from, table, write_json


TOOLS = {'report': 'LLM なしの HTML レポート（--boards で盤面も）', 'probe': '時間 k 倍・k 回の最良を測る',
         'tune': 'Optuna で定数を調整する', 'doctor': '配備を確かめる（--live ENGINE で実機も）', 'audit': 'codex の worker を監査する'}
TOOL_FILES = {'report': 'report.py', 'probe': 'probe.py', 'tune': 'tune.py', 'doctor': 'doctor.py', 'audit': 'codex_audit.py'}


def parser():
    p = argparse.ArgumentParser(prog='python3 -m thin.cli', description=f'AHC thin harness {__version__}')
    p.add_argument('--root', help='プロジェクトのルート（既定は今のディレクトリから探す）')
    p.add_argument('--json', action='store_true', help='結果を JSON で全部出す')
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('init-best', help='最初の best を登録する')
    a.add_argument('source')
    a = sub.add_parser('prepare', help='ラウンドを準備する（A：1〜10案、B：深く1案、C：測定）')
    a.add_argument('mode', choices=['A', 'B', 'C'])
    a.add_argument('--ideas', help='案の JSON（templates/ideas.example.json）')
    a.add_argument('--idea', help='B・C の課題文')
    a.add_argument('--title', help='B・C の短い題名（40字まで）')
    a.add_argument('--parent', action='append', help='親（best や R0003/A02）。B・C では複数回付けられる')
    a.add_argument('--ref', action='append', help='作業場所の ref/ に写すファイル（リポジトリ内のパス）')
    a.add_argument('--engine', help='engine（案ごとの指定が優先）')
    for name, text in (('run', 'worker を起動して終わるまで待つ'), ('go', 'run と evaluate をまとめて行う')):
        a = sub.add_parser(name, help=text)
        a.add_argument('round_id')
        a.add_argument('--engine', help='全 worker の engine をこれにする')
        a.add_argument('--only', help='起動する worker（例 A01,A03）')
        if name == 'go':
            a.add_argument('--background', action='store_true', help='裏で動かして、すぐ戻る（runs/R####/go.log）')
    for name in ('submit', 'accept', 'fail', 'rerun'):
        a = sub.add_parser(name)
        a.add_argument('round_id')
        a.add_argument('job_id')
        if name == 'fail':
            a.add_argument('--reason', required=True)
        if name == 'rerun':
            a.add_argument('--fresh', action='store_true', help='作業場所を作り直してから起動待ちにする')
    a = sub.add_parser('workers-done')
    a.add_argument('round_id')
    a = sub.add_parser('evaluate', help='候補を評価する（続きから）')
    a.add_argument('round_id')
    a.add_argument('--restart', action='store_true', help='これまでの採点を退避して最初から')
    a = sub.add_parser('promote')
    a.add_argument('round_id')
    a.add_argument('job_id')
    a.add_argument('--force', metavar='REASON', help='判定値が margin 以下でも採る（理由を残す）')
    a = sub.add_parser('close')
    a.add_argument('round_id')
    a.add_argument('--note', help='研究の記録のラウンド記録に残す要点（1行）')
    a.add_argument('--no-report', action='store_true')
    sub.add_parser('status')
    a = sub.add_parser('limits', help='利用枠の残りと、次のラウンドの見積もり')
    a.add_argument('--claude', action='store_true', help='claude も読む（小さな呼び出しを1回する）')
    a.add_argument('--mode', default='A')
    a.add_argument('--engine', default='codex')
    a.add_argument('--workers', type=int, default=6)
    for name, text in TOOLS.items():
        a = sub.add_parser(name, help=text, add_help=False)
        a.add_argument('args', nargs=argparse.REMAINDER)
    a = sub.add_parser('demo', help='模擬問題で一周する（LLM は使わない）')
    a.add_argument('--keep', help='模擬プロジェクトをここに残す')
    return p


# ---------------------------------------------------------------- short summaries

def show_workers(r):
    rows = [[w['id'], w.get('status'), w.get('outcome') or w.get('skipped') or '', w.get('minutes', '')] for w in r['workers']]
    out = table(['worker', '状態', '終わり方', '分'], rows, right={3}) if rows else '起動する worker は無かった'
    if r.get('limit_hit'):
        out += '\n利用上限に当たったので、残りの worker は起動していない。limits で枠を見て、rerun か accept・fail で片付ける。'
    if r.get('waiting'):
        out += f'\n終わっていない worker: {", ".join(r["waiting"])}（accept・rerun・fail のどれかで片付ける）'
    return out


def show_eval(r, ideas):
    title = {x['id']: x['title'] for x in ideas}
    rows = []
    for c in r['candidates']:
        if c['valid']:
            z = c['effect'] / c['standard_error'] if c.get('standard_error') else None
            verdict = '足切り' + str(c.get('dropped_at') or '') if c['status'] == 'dropped' else ''
            rows.append([c['id'], title.get(c['id'], '')[:24], c['n'], fmt_effect(c['effect'], c.get('standard_error')),
                         f'{z:.1f}' if z is not None else '', verdict or ('採用候補' if c['id'] == r['recommended'] else '')])
        else:
            rows.append([c['id'], title.get(c['id'], '')[:24], c.get('n', 0), '無効', '', (c.get('error') or c['status'])[:40]])
    out = [table(['候補', '題名', '件数', '効果 ± 標準誤差', 'z', ''], rows, right={2, 3, 4})]
    if r.get('baseline'):
        out.append(f'現 best の平均 {r["baseline"]["mean_score"]:.3f}')
    cf = r.get('confirm')
    if cf and cf.get('skipped'):
        out.append(f'確認評価は省いた（{cf["id"]}：{cf["skipped"]}）')
    elif cf and cf.get('invalid'):
        out.append(f'確認評価で {cf["id"]} が無効になった')
    elif cf:
        out.append(f'確認評価（未使用の {cf["n"]} 件）: {cf["id"]} {fmt_effect(cf["effect"], cf["standard_error"])}')
    out.append(f'採用候補: {r["recommended"] or "なし（best を据え置く）"}')
    c = r['cost']
    out.append(f'採点 {c["cases"]} 件（全件なら {c["full_dev_cases"]} 件）、{c["minutes"]} 分')
    out += [f'注意: {w}' for w in r.get('warnings', [])]
    return '\n'.join(out)


def show_status(r):
    b = r['best']
    head = ('現 best: ' + (f'{b["round"]}/{b["job"]} {b.get("title") or ""}'.rstrip() if b.get('round') else f'init-best（{b.get("source", "")}）')
            if b else '現 best: なし')
    rows = [[x['round'], x['mode'], x['phase'], ' '.join(f'{k}:{v}' for k, v in x['jobs'].items()),
             x['promoted'] or x['recommended'] or '', x['background'] or ''] for x in r['rounds']]
    return head + '\n' + (table(['ラウンド', '行動', '段階', 'worker', '採用', '裏'], rows) if rows else 'ラウンドはまだ無い')


# ---------------------------------------------------------------- commands

def go(root, rid, engine=None, only=None, background=False):
    rd = C.round_dir(root, rid)
    if background:
        cmd = [sys.executable, '-m', 'thin.cli', '--root', str(root), 'go', rid] + (['--engine', engine] if engine else []) \
            + (['--only', only] if only else [])
        with open(rd / 'go.log', 'a') as log:
            p = subprocess.Popen(cmd, cwd=root, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        (rd / 'go.pid').write_text(str(p.pid))
        return {'round_id': rid, 'background': p.pid, 'log': str(rd / 'go.log'),
                'next': f'終わったら python3 -m thin.cli status、結果は runs/{rid}/result.json'}
    out = {'round_id': rid}
    if read_json(rd / 'state.json')['phase'] == 'prepared':
        out['run'] = G.run_workers(root, rid, engine, only.split(',') if only else None)
    phase = read_json(rd / 'state.json')['phase']
    if phase in ('workers_done', 'evaluated') and read_json(rd / 'plan.json')['mode'] != 'C':
        if any(j['status'] in ('submitted', 'scored') for j in C.jobs_of(rd)):
            out['evaluation'] = E.evaluate(root, rid)
    return out


def limits(root, a):
    out = {}
    try:
        exe = shutil.which('codex')
        if exe:
            out['codex'] = G.codex_limits()
    except Exception as e:
        out['codex'] = {'error': str(e)[:200]}
    if a.claude:
        try:
            from .config import load
            out['claude'] = G.claude_limits(load(root))
        except Exception as e:
            out['claude'] = {'error': str(e)[:200]}
    est = G.estimate(root, a.mode, a.engine, a.workers)
    if est:
        out['estimate'] = {**est, 'mode': a.mode, 'engine': a.engine, 'workers': a.workers}
    return out


def show_limits(r):
    out = []
    for name in ('codex', 'claude'):
        x = r.get(name)
        if not x:
            continue
        if 'error' in x:
            out.append(f'{name}: 読めなかった（{x["error"]}）')
            continue
        parts = [f'{k} {v["used_percent"]}%' for k, v in x.items() if isinstance(v, dict) and 'used_percent' in v]
        out.append(f'{name}: ' + '、'.join(parts))
    e = r.get('estimate')
    if e:
        out.append(f'見積もり: {e["mode"]} を {e["engine"]} {e["workers"]} 体で、5時間枠を約 {e["expected"]} ポイント'
                   f'（過去 {e["rounds"]} ラウンドの 1 体あたり {e["per_worker"]}）')
    return '\n'.join(out) or '読める枠が無い'


def demo(dest):
    src = Path(__file__).resolve().parents[1]
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ThinError('デモの置き場所は空にする')
    for d in ('thin', 'worker'):
        shutil.copytree(src / d, dest / d, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    toy = src / 'examples' / 'toy'
    (dest / 'problem').mkdir(parents=True)
    shutil.copyfile(toy / 'statement.md', dest / 'problem' / 'statement.md')
    shutil.copyfile(toy / 'score_case.py', dest / 'problem' / 'score_case.py')
    shutil.copyfile(toy / 'config.json', dest / 'config.json')
    (dest / 'demo_assets').mkdir()
    shutil.copyfile(toy / 'fake_agent.py', dest / 'demo_assets' / 'fake_agent.py')
    for i, f in enumerate(sorted(toy.glob('variant_*.cpp'))):
        shutil.copyfile(f, dest / 'demo_assets' / f.name)
    (dest / 'docs').mkdir()
    log = next((p for p in (src / 'templates' / 'RESEARCH_LOG.md', src / 'docs' / 'RESEARCH_LOG.md') if p.is_file()), None)
    if log:  # in a deployed project the template lives in docs/
        shutil.copyfile(log, dest / 'docs' / 'RESEARCH_LOG.md')
    C.init_best(dest, toy / 'baseline.cpp')
    variants = sorted(p.stem for p in toy.glob('variant_*.cpp'))
    ideas = {'common': '模擬問題。', 'ideas': [{'title': f'模擬 {v}', 'kind': 'continue', 'idea': f'demo_assets の {v}.cpp を写す（模擬の worker）'}
                                          for v in variants]}
    write_json(dest / 'ideas.json', ideas)
    prep = C.prepare(dest, 'A', ideas_path=dest / 'ideas.json', engine='demo')
    run = go(dest, prep['round_id'])
    ev = run['evaluation']
    if ev['recommended']:
        E.promote(dest, prep['round_id'], ev['recommended'])
    closed = C.close(dest, prep['round_id'], note='デモ')
    return {'project': str(dest), 'round': prep['round_id'], 'recommended': ev['recommended'], 'evaluation': ev,
            'closed': closed, 'best': C.read_best(dest)}


def tool(argv):
    """`python3 -m thin.cli [--root R] report|probe|tune|doctor|audit ARGS...`: hand ARGS to thin/tools/ unchanged."""
    i, root = 0, None
    while i < len(argv) and argv[i].startswith('-'):
        if argv[i] == '--root' and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
        else:
            i += 1
    if i >= len(argv) or argv[i] not in TOOLS or any(x in ('-h', '--help') for x in argv[:i]):
        return None
    root = Path(root).resolve() if root else root_from()
    script = Path(__file__).resolve().parent / 'tools' / TOOL_FILES[argv[i]]
    extra = [] if argv[i] == 'audit' else ['--root', str(root)]
    return subprocess.run([sys.executable, str(script), *extra, *argv[i + 1:]], cwd=root).returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        code = tool(argv)
    except ThinError as e:
        print(f'エラー: {e}', file=sys.stderr)
        return 2
    if code is not None:
        return code
    a = parser().parse_args(argv)
    try:
        if a.command == 'demo':
            if a.keep:
                r = demo(Path(a.keep).resolve())
            else:
                with tempfile.TemporaryDirectory(prefix='thin-demo-') as d:
                    r = demo(Path(d) / 'project')
            text = f'デモ: {r["round"]} の採用候補 {r["recommended"]}\n' + show_eval(r['evaluation'], [])
        else:
            root = Path(a.root).resolve() if a.root else root_from()
            r, text = run(root, a)
        print(json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False) if a.json else text)
        return 0
    except (ThinError, OSError, ValueError, KeyError, TypeError) as e:
        print(f'エラー: {e}', file=sys.stderr)
        return 2


def run(root, a):
    c = a.command
    if c == 'init-best':
        r = C.init_best(root, a.source)
        return r, f'best を登録した（sha256 {r["sha256"][:12]}）'
    if c == 'prepare':
        r = C.prepare(root, a.mode, a.ideas, a.idea, a.title, a.parent, a.ref, a.engine)
        return r, (f'{r["round_id"]}（{r["mode"]}、{r["workers"]} 体、engine {", ".join(r["engines"])}）を準備した。'
                   f'作業場所 {r["workspaces"]}\n次: python3 -m thin.cli go {r["round_id"]}')
    if c == 'run':
        r = G.run_workers(root, a.round_id, a.engine, a.only.split(',') if a.only else None)
        return r, show_workers(r)
    if c == 'go':
        r = go(root, a.round_id, a.engine, a.only, a.background)
        if a.background:
            return r, f'裏で開始した（PID {r["background"]}、ログ {r["log"]}）。{r["next"]}'
        plan = read_json(C.round_dir(root, a.round_id) / 'plan.json')
        text = [show_workers(r['run'])] if 'run' in r else []
        if 'evaluation' in r:
            text.append(show_eval(r['evaluation'], plan['ideas']))
        return r, '\n\n'.join(text) or '何もしなかった（status を見る）'
    if c in ('submit', 'accept', 'rerun', 'fail'):
        f = {'submit': C.submit, 'accept': C.accept, 'rerun': lambda r_, i, j: C.rerun(r_, i, j, a.fresh),
             'fail': lambda r_, i, j: C.fail_job(r_, i, j, a.reason)}[c]
        r = f(root, a.round_id, a.job_id)
        return r, f'{a.round_id}/{a.job_id}: {r["status"]}' + (f'（{r.get("error")}）' if r.get('error') else '')
    if c == 'workers-done':
        r = C.mark_workers_done(root, a.round_id)
        return r, f'{a.round_id}: worker の段階を終えた'
    if c == 'evaluate':
        r = E.evaluate(root, a.round_id, a.restart)
        return r, show_eval(r, read_json(C.round_dir(root, a.round_id) / 'plan.json')['ideas'])
    if c == 'promote':
        r = E.promote(root, a.round_id, a.job_id, a.force)
        return r, f'best を {a.round_id}/{a.job_id} にした（判定値 {fmt_effect(r["decision_effect"])}）'
    if c == 'close':
        r = C.close(root, a.round_id, a.note, not a.no_report)
        return r, f'{a.round_id} を閉じた。記録 {r.get("research_log")}、消費 {r.get("usage")}、レポート {r.get("report")}'
    if c == 'status':
        r = C.status(root)
        return r, show_status(r)
    if c == 'limits':
        r = limits(root, a)
        return r, show_limits(r)
    raise ThinError(f'知らないコマンド: {c}')


if __name__ == '__main__':
    raise SystemExit(main())
