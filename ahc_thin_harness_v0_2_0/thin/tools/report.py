#!/usr/bin/env python3
"""Human-facing HTML report built from the harness records only (no LLM, standard library only).

    python3 -m thin.cli report                 # report/index.html from runs/, best/, docs/RESEARCH_LOG.md (seconds)
    python3 -m thin.cli report --boards        # also re-run a few boards with past bests and 8x time (about a minute)

Problem-specific parts: case features (the judge adapter returns them; config report.feature picks one) and the
board drawing problem/view.js (optional). `close` runs this script after every round.
"""
import argparse
import collections
import csv
import html
import itertools
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

T0 = time.perf_counter()
ROOT = Path('.')
HERE = Path(__file__).resolve().parent
FEATURE = {'key': None, 'name': '', 'bands': [], 'labels': []}
SIGN = 1
KIND = {'new': '新規', 'continue': '改良', 'combine': '組合せ', 'deep': '長時間', 'measure': '測定'}
STATUS = {'adopted': '✓ 採用', 'pos': '▲ 正', 'flat': '・差なし', 'neg': '▼ 負', 'dropped': '− 足切り', 'invalid': '× 無効',
          'failed': '× 失敗', 'measure': '◇ 測定'}

esc = lambda s: html.escape(str(s), quote=True)
fmean = statistics.fmean


# ---------------------------------------------------------------- 数の書き方

def fe(x, digits=None):
    """効果の表示。符号つき、桁は大きさで変える。負号は U+2212。"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return '—'
    d = digits if digits is not None else (1 if abs(x) >= 10 else 2 if abs(x) >= 1 else 3)
    s = f'{abs(x):.{d}f}'
    if float(s) == 0:
        return s
    return ('+' if x > 0 else '−') + s


def fk(n):
    """トークン数を短く書く。"""
    if n is None:
        return '—'
    return f'{n / 1e6:.2f}M' if n >= 1e6 else f'{n / 1e3:.0f}k' if n >= 1e3 else str(int(n))


def zclass(e, se):
    if e is None or not se:
        return 'z0'
    z = e / se
    for lim, c in ((8, '3'), (4, '2'), (2, '1')):
        if abs(z) >= lim:
            return ('p' if z > 0 else 'n') + c
    return 'z0'


def shorten(s, n=58):
    s = re.sub(r'\s+', ' ', s).strip().rstrip('。')
    return s if len(s) <= n else s[:n - 1] + '…'


def ticks(lo, hi, n=5):
    span = (hi - lo) or 1
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    first, last = math.floor(lo / step + 1e-9), math.ceil(hi / step - 1e-9)
    return [round(k * step, 10) for k in range(first, last + 1)]


def tnum(v):
    return f'{v:g}'.replace('-', '−')


# ---------------------------------------------------------------- 記録を読む

def read_scores(path):
    """seed -> (score or None, features). The last row of a seed wins (a TLE re-run appends one)."""
    rows = {}
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            try:
                seed = int(row['seed'])
                feats = json.loads(row.get('features') or '{}')
            except (TypeError, ValueError):
                continue
            ok = row.get('status') == 'ok' and row.get('score') not in (None, '')
            rows[seed] = (float(row['score']) if ok else None, feats if isinstance(feats, dict) else {})
    return rows


def paired(base, cand, seeds=None):
    d = [SIGN * (cand[s] - base[s]) for s in cand if s in base and (seeds is None or s in seeds)]
    if len(d) < 2:
        return None
    return fmean(d), statistics.stdev(d) / math.sqrt(len(d)), len(d)


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=。)', text) if s.strip()]


def clean_report(text):
    """worker の最終報告から、手元の絶対パスを外す。"""
    text = re.sub(r'\[([^\]]*)\]\((/[^)]*)\)', r'\1', text)
    return re.sub(r'(?<![\w.])/(?:[\w.\-]+/)+([\w.\-]+)', r'\1', text).strip()


def final_report(job_dir):
    p = job_dir / 'agent.last_message.md'
    if p.exists():
        return clean_report(p.read_text(encoding='utf-8', errors='replace'))
    p = job_dir / 'agent.stdout'
    if not p.exists():
        return ''
    text = p.read_text(encoding='utf-8', errors='replace')
    if not text.lstrip().startswith('{'):
        return clean_report(text)
    last = ''
    for line in text.splitlines():           # codex --json / claude stream-json
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        item = ev.get('item') or {}
        if ev.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            last = item.get('text', '')
        if ev.get('type') == 'result' and isinstance(ev.get('result'), str):
            last = ev['result']
    return clean_report(last)


def research_log():
    """Bulleted lines of 「次の狙い」 and 「人間への問い」 in docs/RESEARCH_LOG.md, quoted verbatim."""
    text = text_of(ROOT / 'docs' / 'RESEARCH_LOG.md')
    out = {}
    for name in ('次の狙い', '人間への問い'):
        m = re.search(rf'^## ({name}[^\n]*)\n(.*?)(?=^## |\Z)', text, re.M | re.S)
        if m:
            items = [line.strip()[2:].strip() for line in m[2].splitlines() if line.strip().startswith('- ')]
            if items:
                out[name] = (m[1], items)
    return out


def ngrams(text):
    text = re.sub(r'[`*\[\]（）()「」『』、。・\s,.:：;／/]+', ' ', text.lower())
    c = collections.Counter()
    for w in text.split():
        for n in (2, 3):
            c.update(w[i:i + n] for i in range(len(w) - n + 1))
    return c


def similarity_vectors(texts):
    docs = [ngrams(t) for t in texts]
    df = collections.Counter(g for d in docs for g in d)
    n = len(docs)
    out = []
    for d in docs:
        v = {g: (1 + math.log(x)) * math.log(n / df[g]) for g, x in d.items() if df[g] < n * 0.25}
        z = math.sqrt(sum(x * x for x in v.values())) or 1
        out.append({g: x / z for g, x in v.items()})
    return out


def cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(g, 0) for g, x in a.items())


def build():
    cfg = read_json(ROOT / 'config.json', {}) or {}
    rounds, ideas = [], {}
    for rd in sorted((ROOT / 'runs').glob('R[0-9][0-9][0-9][0-9]*')):
        plan = read_json(rd / 'plan.json')
        if not plan or plan.get('schema_version') != 2:
            continue
        state, res, use = read_json(rd / 'state.json', {}), read_json(rd / 'result.json', {}), read_json(rd / 'usage.json', {})
        rows = {p.parent.name: read_scores(p) for p in rd.glob('evaluation/*/scores.csv')}
        feats = {}
        for rr in rows.values():
            for s, (_, f) in rr.items():
                if f:
                    feats.setdefault(s, f)
        R = {'id': rd.name, 'num': int(rd.name[1:]), 'mode': plan['mode'], 'seeds': plan['seeds'], 'promoted': res.get('promoted'),
             'recommended': res.get('recommended'), 'confirm': res.get('confirm'), 'cost': res.get('cost') or {},
             'scores': {k: {s: v for s, (v, _) in rr.items() if v is not None} for k, rr in rows.items()},
             'feats': feats, 'usage': use or {}, 'note': state.get('note'), 'phase': state.get('phase'), 'ideas': []}
        rounds.append(R)
        cands = {c['id']: c for c in res.get('candidates', [])}
        for x in plan['ideas']:
            jd = rd / 'jobs' / x['id']
            job, c = read_json(jd / 'job.json', {}) or {}, cands.get(x['id'], {})
            run = job.get('agent_run') or {}
            ref = f'{rd.name}/{x["id"]}'
            ideas[ref] = {
                'ref': ref, 'round': rd.name, 'num': R['num'], 'mode': plan['mode'], 'kind': x.get('kind', ''),
                'title': x.get('title') or shorten(x['idea'], 40), 'text': x['idea'], 'common': plan.get('common') or '',
                'parents': [p for p in x.get('parents', []) if p != 'best'], 'valid': bool(c.get('valid')),
                'cstatus': c.get('status'), 'error': c.get('error') or job.get('error') or '', 'effect': c.get('effect'),
                'se': c.get('standard_error'), 'n': c.get('n'), 'better': c.get('better_cases'), 'worse': c.get('worse_cases'),
                'dropped_at': c.get('dropped_at'), 'decision': c.get('decision'), 'decision_effect': c.get('decision_effect'),
                'adopted': res.get('promoted') == x['id'], 'sid': c.get('reused_from') or x['id'], 'engine': job.get('engine') or '',
                'model': job.get('model'), 'effort': job.get('effort'), 'outcome': job.get('agent_outcome'),
                'minutes': run.get('seconds', 0) / 60 if run.get('seconds') else None, 'interrupted': job.get('interrupted'),
                'usage': job.get('usage') or {}, 'jstatus': job.get('status'), 'sha': job.get('sha256'),
                'report': clean_report(text_of(jd / 'agent.last_message.md')) or final_report(jd),
                'notes': text_of(jd / 'notes.md'), 'creport': text_of(jd / 'report.md'), 'lineage': '',
            }
            R['ideas'].append(ref)
    return {'rounds': rounds, 'ideas': ideas, 'logs': research_log(), 'best': read_json(ROOT / 'best' / 'best.json'),
            'config': cfg}


def analyse(m):
    setup_feature(m)
    rounds, ideas = m['rounds'], m['ideas']
    best = m['best'] or {}
    for r in rounds:
        r['band_sets'] = [{s for s, f in r['feats'].items() if band_index(f.get(FEATURE['key'])) == i}
                          for i in range(len(FEATURE['bands']))]
        base = r['scores'].get('baseline', {})
        dev = set(range(*r['seeds']['dev']))
        for ref in r['ideas']:
            c = ideas[ref]
            sc = r['scores'].get(c['sid'], {})
            seeds = sorted(s for s in sc if s in dev)
            ok = c['valid'] and base
            c['bands'] = [paired(base, sc, bs & set(seeds)) if ok else None for bs in r['band_sets']]
            h = len(seeds) // 2
            c['halves'] = [paired(base, sc, set(seeds[:h])), paired(base, sc, set(seeds[h:]))] if ok else [None, None]
            c['z'] = c['effect'] / c['se'] if c['valid'] and c['se'] else None
            z = c['z']
            c['status'] = ('measure' if c['kind'] == 'measure' else 'failed' if c['jstatus'] in ('failed', 'worker_failed', 'interrupted', 'pending', 'running')
                           else 'invalid' if not c['valid'] else 'adopted' if c['adopted'] else 'dropped' if c['cstatus'] == 'dropped'
                           else 'pos' if z is not None and z >= 2 else 'neg' if z is not None and z <= -2 else 'flat')
            if c['adopted']:
                c['lineage'] = c['title']
    vecs = dict(zip(ideas, similarity_vectors([c['text'] for c in ideas.values()])))
    order = sorted(ideas, key=lambda k: (ideas[k]['num'], k))
    for k in order:
        c = ideas[k]
        earlier = [j for j in order if ideas[j]['num'] < c['num'] and not ideas[j]['adopted']]
        sims = sorted(((cos(vecs[k], vecs[j]), j) for j in earlier), reverse=True)
        c['similar'] = [(s, j) for s, j in sims[:3] if s >= 0.12]
        explicit = next((p for p in c['parents'] if p in ideas and not ideas[p]['adopted']), None)
        c['link'] = (sims[0][1] if sims and sims[0][0] >= 0.9 else explicit or (sims[0][1] if sims and sims[0][0] >= 0.5 else None))
    for k in order:
        root = k
        while ideas[root]['link']:
            root = ideas[root]['link']
        ideas[k]['family'] = root
    children = collections.defaultdict(list)
    for k in order:
        if ideas[k]['link']:
            children[ideas[k]['link']].append(k)
    for k in order:
        ideas[k]['children'] = children[k]
    return m


# ---------------------------------------------------------------- 自動で拾う点（決まった規則だけ）

def current_scores(m):
    for r in reversed(m['rounds']):
        if r['mode'] == 'C':
            continue
        if r['promoted']:
            return r, r['scores'].get(m['ideas'][f"{r['id']}/{r['promoted']}"]['sid'], {})
        if r['scores'].get('baseline'):
            return r, r['scores']['baseline']
    return None, {}


def residuals(m):
    last, sc = current_scores(m)
    key = FEATURE['key']
    by = collections.defaultdict(list)
    for s, v in sc.items():
        by[m['feats'].get(s, {}).get(key)].append(v)
    mean = {k: fmean(v) for k, v in by.items()}
    return last, sc, mean, {s: v - mean[m['feats'].get(s, {}).get(key)] for s, v in sc.items()}


def probe_summary(m):
    data = read_json(ROOT / 'runs' / 'probe' / 'latest.json')
    if not data or not data.get('rows'):
        return None
    one, by = collections.defaultdict(dict), collections.defaultdict(dict)
    for r in data['rows']:
        if r.get('score') is None:
            continue
        (one[r['rep']] if r['k'] == 1 else by[r['k']])[r['seed']] = r['score']
    reps = sorted(one)
    if not reps:
        return None
    seeds = sorted(set.intersection(*(set(one[q]) for q in reps)))
    base = {s: fmean(one[q][s] for q in reps) for s in seeds}
    out = {'seeds': seeds, 'reps': len(reps), 'time': [], 'bestof': [], 'sha': data.get('best_sha256', ''),
           'current': data.get('best_sha256') == (m['best'] or {}).get('sha256'), 'at': data.get('at', '')}
    m['feats'].update({int(k): v for k, v in (data.get('features') or {}).items()})
    for k in sorted(list(by) + [1]):
        cur = by[k] if k != 1 else base
        out['time'].append((k, {s: SIGN * (cur[s] - base[s]) for s in seeds if s in cur}))
    for k in range(1, len(reps) + 1):
        d = {s: fmean(max(SIGN * one[q][s] for q in sub) for sub in itertools.combinations(reps, k)) - SIGN * base[s] for s in seeds}
        out['bestof'].append((k, d))
    return out


def mean_se(vals):
    vals = list(vals)
    if len(vals) < 2:
        return (fmean(vals) if vals else 0.0), 0.0
    return fmean(vals), statistics.stdev(vals) / math.sqrt(len(vals))


def hints(m, probe):
    rounds, ideas = m['rounds'], m['ideas']
    out = []
    played = [r for r in rounds if r['mode'] != 'C']
    eff = lambda rs: sum(ideas[f"{r['id']}/{r['promoted']}"]['effect'] or 0 for r in rs if r['promoted'])
    if len(played) >= 2:
        k = min(5, len(played) // 2)
        last, prev = played[-k:], played[-2 * k:-k]
        out.append(('伸び方', f'直近{k}ラウンド（{last[0]["id"]}〜{last[-1]["id"]}）の採用は合計 {fe(eff(last), 2)}。'
                    f'その前の{k}ラウンドは {fe(eff(prev), 2)}。', '#progress', None))
    labels = FEATURE['labels']
    split = [c for c in ideas.values() if c['valid'] and not c['adopted'] and c['bands'] and all(c['bands'])
             and max(b[0] / b[1] for b in c['bands']) >= 2.5 and min(b[0] / b[1] for b in c['bands']) <= -2]
    if split:
        c = max(split, key=lambda c: max(b[0] / b[1] for b in c['bands']))
        zs = [b[0] / b[1] for b in c['bands']]
        bi, bj = zs.index(max(zs)), zs.index(min(zs))
        others = '、'.join(x['ref'] for x in split if x is not c)
        out.append(('帯で効き方が割れた案', f'{c["ref"]}（{c["title"]}）は全体 {fe(c["effect"])} で採らなかった。'
                    f'{FEATURE["name"]} {labels[bi]} では {fe(c["bands"][bi][0])}（z {zs[bi]:.1f}）、{labels[bj]} では {fe(c["bands"][bj][0])}。'
                    + (f'ほかに {others}。' if others else ''), '#i-' + c['ref'].replace('/', '-'), '帯ごとに使い分けたら？'))
    latest = max((r['num'] for r in rounds if r['mode'] != 'C'), default=0)
    leftover = [c for c in ideas.values() if c['status'] == 'pos' and not c['children'] and c['num'] < latest
                and not any(c['ref'] in x['parents'] for x in ideas.values())]
    if leftover:
        c = max(leftover, key=lambda c: (c['num'], c['z'] or 0))
        more = len(leftover) - 1
        out.append(('正だったが、その後使っていない案', f'{c["ref"]}（{c["title"]}）は {fe(c["effect"])} ± {c["se"]:.3f}（z {c["z"]:.1f}）。'
                    'これ以降、親にも載せ直しにも使っていない。' + (f'同じ条件の案がほかに {more} 件。' if more else ''),
                    '#i-' + c['ref'].replace('/', '-'), '今の best に載せ直したら？'))
    weak = [c for c in ideas.values() if c['adopted'] and c['z'] is not None and c['z'] < 2.5 and c.get('decision') != 'confirm']
    if weak:
        out.append(('誤差と見分けにくい採用', '、'.join(f'{c["ref"]}（z {c["z"]:.1f}）' for c in weak)
                    + '。何案もの首位は、選んだのと同じ seed で測ると高めに出やすい。', '#progress', '未使用の seed で測り直したら？'))
    conf = [(r, r['confirm']) for r in rounds if r.get('confirm') and r['confirm'].get('effect') is not None]
    if conf:
        r, cf = conf[-1]
        dev = ideas.get(f'{r["id"]}/{cf["id"]}', {})
        out.append(('確認評価', f'{r["id"]} の首位 {cf["id"]} は、選んだ seed で {fe(dev.get("effect"))}、未使用の {cf["n"]} 件で '
                    f'{fe(cf["effect"])} ± {cf["standard_error"]:.3f}。', '#i-' + f'{r["id"]}-{cf["id"]}', None))
    pts = transfer_points(m)
    if len(pts) >= 3:
        a, b = fmean(p[2] for p in pts), fmean(p[3] for p in pts)
        out.append(('載せ直したときの効き目', f'単独で正だった案を次の best に載せ直した {len(pts)} 件では、単独のとき平均 {fe(a)}、'
                    f'載せ直して {fe(b)}（{b / a:.0%}）。載せ直しても 0 を上回ったのは {sum(p[3] > 0 for p in pts)} 件。', '#flows', None))
    last, sc, mean, res = residuals(m)
    if sc:
        worst = sorted(res, key=lambda s: SIGN * res[s])[:3]
        same = f'同じ{FEATURE["name"]}の盤面の平均' if FEATURE['key'] else '平均'
        out.append(('点が低い盤面', '、'.join(f'seed {s}（' + (f'{FEATURE["key"]}={fv(m, s)}、' if FEATURE['key'] else '')
                                                + f'{res[s]:+.1f}）'.replace('-', '−') for s in worst)
                    + f'。{last["id"]} の評価で、{same}との差。', '#weak', '何が違うか、盤面を見る'))
    if probe:
        t = {k: mean_se(d.values())[0] for k, d in probe['time']}
        b = {k: mean_se(d.values())[0] for k, d in probe['bestof']}
        kmax = max(t)
        out.append(('時間と運の余地', f'{"現" if probe["current"] else "前の"} best に {kmax:g} 倍の時間を与えると {fe(t[kmax], 2)}'
                    + (f'、2倍で {fe(t[2], 2)}' if 2 in t else '') + f'。1倍のまま {len(b)} 回走らせた最良は {fe(b[len(b)], 2)}'
                    f'（{len(probe["seeds"])} 盤面）。', '#probe', None))
    a_r = [r for r in rounds if r['mode'] == 'A' and five_hour(r) is not None]
    b_r = [r for r in rounds if r['mode'] == 'B' and five_hour(r) is not None]
    if a_r or b_r:
        parts = []
        for r in (a_r[-1:] + b_r[-1:]):
            e = ideas[f"{r['id']}/{r['promoted']}"]['effect'] if r['promoted'] else 0
            parts.append(f'{r["mode"]}（{r["id"]}）は codex の5時間枠を {five_hour(r):g} ポイント使い、採用 {fe(e)}')
        out.append(('消費', '。'.join(parts) + '。', '#usage', None))
    q = m['logs'].get('人間への問い')
    if q:
        out.insert(0, ('オーケストレーターから人間への問い', ' / '.join(q[1]), '#now', None))
    return out


def five_hour(r):
    lim = (r['usage'] or {}).get('worker_limits') or []
    v = [x['last'] - x['first'] for x in lim if x.get('window') == 'five_hour']
    return sum(v) if v else None


def transfer_points(m):
    ideas = m['ideas']
    pts = []
    for c in ideas.values():
        if c['kind'] != 'combine' or not c['valid']:
            continue
        for p in c['parents']:
            q = ideas.get(p)
            if q and q['valid'] and 0 < q['effect'] < 2 and q['num'] < c['num']:
                pts.append((c['ref'], p, q['effect'], c['effect']))
    return pts


# ---------------------------------------------------------------- 図（SVG を文字列で作る。JS なしでも読める）

def axis_y(vals, x0, x1, y, fmt=tnum):
    out = []
    for v in vals:
        yy = y(v)
        out.append(f'<line x1="{x0}" x2="{x1}" y1="{yy:.1f}" y2="{yy:.1f}" class="{"base" if v == 0 else "grid"}"/>'
                   f'<text x="{x0 - 6}" y="{yy + 4:.1f}" class="tick" text-anchor="end">{fmt(v)}</text>')
    return ''.join(out)


def end_labels(items, ymin_gap=14):
    """右端のラベルを重ならないように並べる。items: [(y, text)] → [(y_label, y_line, text)]"""
    items = sorted(items)
    placed = []
    for y, t in items:
        yl = max(y, placed[-1][0] + ymin_gap) if placed else y
        placed.append((yl, y, t))
    return placed


def chart_progress(m, start):
    rounds = [r for r in m['rounds'] if r['num'] >= start and r['mode'] != 'C']
    ideas = m['ideas']
    names = ['全体'] + [f'{FEATURE["key"]} {lab}' for lab in FEATURE['labels']]
    cls = ['ink', 'o1', 'o2', 'o3'][:len(names)]
    series = [[0.0] for _ in names]
    for r in rounds:
        c = ideas.get(f"{r['id']}/{r['promoted']}") if r['promoted'] else None
        add = ([c['effect'] or 0] + [(b[0] if b else 0) for b in c['bands']]) if c else [0.0] * len(names)
        for s, v in zip(series, add + [0.0] * (len(names) - len(add))):
            s.append(s[-1] + v)
    W, H, L, Rr, T, B = 1060, 320, 52, 140, 14, 44
    n = max(1, len(rounds))
    x = lambda i: L + i * (W - L - Rr) / n
    lo, hi = min(0, min(min(s) for s in series)), max(max(s) for s in series)
    tk = ticks(lo, hi if hi > lo else lo + 1)
    lo, hi = tk[0], tk[-1]
    y = lambda v: T + (hi - v) * (H - T - B) / ((hi - lo) or 1)
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="chart" role="img" aria-label="採用した変更の効果の積み上げ">',
           axis_y(tk, L, W - Rr, y)]
    step = max(1, n // 20)
    for i, r in enumerate([None] + rounds):
        if i % step == 0 or i == n:
            out.append(f'<text x="{x(i):.1f}" y="{H - B + 18}" class="tick" text-anchor="middle">{esc(r["id"][1:].lstrip("0") if r else "前")}</text>')
    out.append(f'<text x="{(L + W - Rr) / 2:.1f}" y="{H - 6}" class="axt" text-anchor="middle">ラウンド</text>')
    for s, c in zip(series, cls):
        d = f'M{x(0):.1f},{y(s[0]):.1f}' + ''.join(f'H{x(i):.1f}V{y(v):.1f}' for i, v in enumerate(s) if i)
        out.append(f'<path d="{d}" class="ln {c}"/>')
    for yl, yv, t in end_labels([(y(s[-1]), f'{nm}　{fe(s[-1], 2)}') for s, nm in zip(series, names)]):
        out.append(f'<line x1="{W - Rr + 2}" x2="{W - Rr + 8}" y1="{yv:.1f}" y2="{yl:.1f}" class="lead"/>'
                   f'<text x="{W - Rr + 11}" y="{yl + 4:.1f}" class="lab">{esc(t)}</text>')
    for i, r in enumerate(rounds, 1):
        c = ideas.get(f"{r['id']}/{r['promoted']}") if r['promoted'] else None
        tip = f'{r["id"]}（{r["mode"]}）\n' + (f'採用 {c["ref"]}：{c["title"]}\n' + '\n'.join(
            f'{nm} {fe(s[i], 2)}（このラウンド {fe(s[i] - s[i - 1], 2)}）' for s, nm in zip(series, names)) if c else '採用なし')
        out.append(f'<rect x="{x(i - 1) + 1:.1f}" y="{T}" width="{max(1, x(i) - x(i - 1) - 2):.1f}" height="{H - T - B}" class="hit" '
                   f'data-tip="{esc(tip)}" tabindex="0"/>')
        if c:
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(series[0][i]):.1f}" r="4" class="dot ink"/>')
    out.append('</svg>')
    return ''.join(out), names, series, rounds


def chart_transfer(m):
    pts = transfer_points(m)
    if not pts:
        return ''
    W, H, L, R, T, B = 500, 330, 56, 16, 14, 44
    xs, ys = [p[2] for p in pts], [p[3] for p in pts]
    lo, hi = min(0, min(xs + ys)), max(xs + ys)
    tk = ticks(lo, hi, 6)
    lo, hi = tk[0], tk[-1]
    x = lambda v: L + (v - lo) * (W - L - R) / (hi - lo)
    y = lambda v: T + (hi - v) * (H - T - B) / (hi - lo)
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="chart" role="img" aria-label="単独の効果と、載せ直したときの効果">',
           axis_y(tk, L, W - R, y)]
    for v in tk:
        out.append(f'<text x="{x(v):.1f}" y="{H - B + 18}" class="tick" text-anchor="middle">{tnum(v)}</text>')
    out.append(f'<line x1="{x(max(lo, lo)):.1f}" y1="{y(lo):.1f}" x2="{x(hi):.1f}" y2="{y(hi):.1f}" class="diag"/>'
               f'<text x="{x(hi) - 4:.1f}" y="{y(hi) + 14:.1f}" class="note" text-anchor="end">単独と同じ</text>'
               f'<text x="{(L + W - R) / 2:.1f}" y="{H - 6}" class="axt" text-anchor="middle">単独で測った効果</text>'
               f'<text x="14" y="{(T + H - B) / 2:.1f}" class="axt" text-anchor="middle" transform="rotate(-90 14 {(T + H - B) / 2:.1f})">載せ直したときの効果</text>')
    for ref, p, a, b in pts:
        tip = f'{fe(b)}（単独 {fe(a)}）\n{ref} ← {p}\n{m["ideas"][ref]["title"]}'
        out.append(f'<a href="#i-{ref.replace("/", "-")}"><circle cx="{x(a):.1f}" cy="{y(b):.1f}" r="4.5" class="dot s1 ring" data-tip="{esc(tip)}" tabindex="0"/></a>')
    out.append('</svg>')
    return ''.join(out)


def chart_weak(m, picks):
    last, sc, mean, res = residuals(m)
    W, H, L, R, T, B = 1060, 360, 52, 16, 14, 44
    ys = list(sc.values())
    tk = ticks(min(ys), max(ys), 6)
    lo, hi = tk[0], tk[-1]
    key = FEATURE['key']
    xs = sorted({fv(m, s) for s in sc if isinstance(fv(m, s), (int, float))})
    if key and xs:
        f0, f1 = xs[0], xs[-1]
        span = (f1 - f0) or 1
        x = lambda v: L + 10 + (v - f0) * (W - L - R - 20) / span
        jit = span / max(1, len(xs)) * 0.6
        xv = lambda s: fv(m, s)
        xlab = f'{FEATURE["name"]}（{key}）'
    else:
        order = sorted(sc)
        pos = {s: i for i, s in enumerate(order)}
        f0, f1, jit = 0, max(1, len(order) - 1), 0
        x = lambda v: L + 10 + v * (W - L - R - 20) / f1
        xv = lambda s: pos[s]
        xlab = 'seed の順'
    y = lambda v: T + (hi - v) * (H - T - B) / ((hi - lo) or 1)
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="chart" id="weak-svg" role="img" aria-label="盤面ごとの得点">',
           axis_y(tk, L, W - R, y)]
    for v in ticks(f0, f1, 8):
        if f0 <= v <= f1:
            out.append(f'<text x="{x(v):.1f}" y="{H - B + 18}" class="tick" text-anchor="middle">{tnum(v)}</text>')
    out.append(f'<text x="{(L + W - R) / 2:.1f}" y="{H - 6}" class="axt" text-anchor="middle">{esc(xlab)}</text>')
    pts = []
    for s, v in sc.items():
        j = ((s * 2654435761) % 1000 / 1000 - 0.5) * jit
        px, py = x(xv(s) + j), y(v)
        pts.append([s, fv(m, s), v, round(px, 1), round(py, 1)])
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.2" class="pt"/>')
    if key and xs:
        ks = sorted(k for k in mean if isinstance(k, (int, float)))
        out.append('<path d="' + ''.join(f'{"M" if i == 0 else "L"}{x(k):.1f},{y(mean[k]):.1f}' for i, k in enumerate(ks)) + '" class="ln ink"/>')
        out.append(f'<text x="{x(ks[-1]) - 4:.1f}" y="{y(mean[ks[-1]]) - 10:.1f}" class="lab" text-anchor="end">同じ{esc(key)}の平均</text>')
    for s, why in picks:
        if s in sc:
            tip = (f'{sc[s]:g} 点（seed {s}）\n' + (f'{key}={fv(m, s)}、' if key else '') + f'平均との差 {res[s]:+.1f}\n{why}\nクリックで盤面を開く').replace('-', '−')
            out.append(f'<circle cx="{x(xv(s)):.1f}" cy="{y(sc[s]):.1f}" r="6" class="dot s2 ring pick" data-seed="{s}" data-tip="{esc(tip)}" tabindex="0"/>'
                       f'<text x="{x(xv(s)) + 9:.1f}" y="{y(sc[s]) + 4:.1f}" class="lab">{s}</text>')
    out.append('</svg>')
    return ''.join(out), pts, last


def chart_probe(probe):
    W, H, L, R, T, B = 500, 300, 52, 132, 14, 44
    t = [(k, *mean_se(d.values())) for k, d in probe['time']]
    b = [(k, *mean_se(d.values())) for k, d in probe['bestof']]
    vals = [v for _, v, _ in t + b] + [v + e for _, v, e in t] + [v - e for _, v, e in t]
    tk = ticks(min(vals + [0]), max(vals), 5)
    lo, hi = tk[0], tk[-1]
    kx = [k for k, *_ in t] + [k for k, *_ in b]
    l0, l1 = math.log2(min(kx)), math.log2(max(kx))
    x = lambda k: L + (math.log2(k) - l0) * (W - L - R) / (l1 - l0)
    y = lambda v: T + (hi - v) * (H - T - B) / (hi - lo)
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="chart" role="img" aria-label="計算量を増やしたときの伸び">', axis_y(tk, L, W - R, y)]
    for k in sorted(set(kx)):
        out.append(f'<text x="{x(k):.1f}" y="{H - B + 18}" class="tick" text-anchor="middle">{k:g}倍</text>')
    out.append(f'<text x="{(L + W - R) / 2:.1f}" y="{H - 6}" class="axt" text-anchor="middle">使う計算量（1回の実行時間 × 回数）</text>')
    for pts, c, name in ((t, 's1', '1回を長く'), (b, 's2', '回数を増やして最良')):
        out.append('<path d="' + ''.join(f'{"M" if i == 0 else "L"}{x(k):.1f},{y(v):.1f}' for i, (k, v, _) in enumerate(pts)) + f'" class="ln {c}"/>')
        for k, v, e in pts:
            if e:
                out.append(f'<line x1="{x(k):.1f}" x2="{x(k):.1f}" y1="{y(v - e):.1f}" y2="{y(v + e):.1f}" class="err {c}"/>')
            tip = f'{fe(v, 2)}' + (f' ± {e:.2f}' if e else '') + f'\n{name}：{k:g}' + ('倍の時間' if c == 's1' else '回の最良')
            out.append(f'<circle cx="{x(k):.1f}" cy="{y(v):.1f}" r="4.5" class="dot {c} ring" data-tip="{esc(tip)}" tabindex="0"/>')
    for yl, yv, txt in end_labels([(y(t[-1][1]), f'1回を長く {fe(t[-1][1], 2)}'), (y(b[-1][1]), f'{b[-1][0]}回の最良 {fe(b[-1][1], 2)}')]):
        out.append(f'<text x="{x(max(kx)) + 12:.1f}" y="{yl + 4:.1f}" class="lab">{esc(txt)}</text>')
    out.append('</svg>')
    return ''.join(out)


def forest(e, se, lo=-60, hi=60, w=180, h=20):
    """記号つき対数の目盛り（0 の近くは線形）。どのラウンドの効果も同じ幅に収める。"""
    f = lambda v: math.copysign(math.log10(1 + abs(v) / 0.03), v)
    span = f(hi)
    x = lambda v: w / 2 + max(-1, min(1, f(v) / span)) * (w / 2 - 6)
    if e is None:
        return f'<svg viewBox="0 0 {w} {h}" class="forest"><line x1="{w / 2}" x2="{w / 2}" y1="2" y2="{h - 2}" class="base"/></svg>'
    a, b = e - 1.96 * (se or 0), e + 1.96 * (se or 0)
    return (f'<svg viewBox="0 0 {w} {h}" class="forest" aria-hidden="true"><line x1="{w / 2}" x2="{w / 2}" y1="2" y2="{h - 2}" class="base"/>'
            f'<line x1="{x(a):.1f}" x2="{x(b):.1f}" y1="{h / 2}" y2="{h / 2}" class="ci"/>'
            f'<circle cx="{x(e):.1f}" cy="{h / 2}" r="3.6"/></svg>')


def forest_axis(w=180):
    f = lambda v: math.copysign(math.log10(1 + abs(v) / 0.03), v)
    span = f(60)
    x = lambda v: w / 2 + f(v) / span * (w / 2 - 6)
    marks = [-10, -1, 0, 1, 10]
    return (f'<svg viewBox="0 0 {w} 16" class="forest">' + ''.join(
        f'<text x="{x(v):.1f}" y="12" class="tick" text-anchor="middle">{tnum(v)}</text>' for v in marks) + '</svg>')


# ---------------------------------------------------------------- 節

def sec(id_, title, tag, lead, body):
    return (f'<section id="{id_}"><h2>{esc(title)}' + (f'<span class="tag">{esc(tag)}</span>' if tag else '') + '</h2>'
            + (f'<p class="lead">{lead}</p>' if lead else '') + body + '</section>')


def table_html(head, rows, cls=''):
    h = ''.join(f'<th class="{"num" if a else ""}">{esc(t)}</th>' for t, a in head)
    b = ''.join('<tr>' + ''.join(f'<td class="{"num" if head[i][1] else ""}">{v}</td>' for i, v in enumerate(r)) + '</tr>' for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def section_now(m, hs):
    rounds, ideas, best = m['rounds'], m['ideas'], m['best'] or {}
    lc = next((ideas[f"{r['id']}/{r['promoted']}"] for r in reversed(rounds) if r['promoted']), None)
    valid = sum(c['valid'] for c in ideas.values())
    adopted = sum(c['adopted'] for c in ideas.values())
    last, sc = current_scores(m)
    tiles = [('現 best', (best.get('round', 'init') + '/' + best.get('job', '')).rstrip('/') if best else 'なし',
              (f'{best.get("title") or ""}　平均 {fmean(sc.values()):.2f}（{last["id"]} の {len(sc)} 件）' if sc else '評価前')),
             ('直近の採用', fe(lc['decision_effect'] if lc and lc.get('decision_effect') is not None else (lc or {}).get('effect')) if lc else '—',
              (f'{lc["ref"]}・' + ('確認評価の値' if lc.get('decision') == 'confirm' else f'z {lc["z"]:.1f}' if lc.get('z') else '')) if lc else '採用なし'),
             ('試した案', f'{len(ideas)}', f'有効 {valid}・採用 {adopted}・{len(rounds)} ラウンド')]
    kp = ''.join(f'<div class="card kpi"><div class="label">{esc(a)}</div><div class="value">{esc(b)}</div><div class="sub">{esc(c)}</div></div>'
                 for a, b, c in tiles)
    hh = ''.join(f'<div class="card hint"><h3>{esc(t)}</h3><p>{esc(body)}</p>' + (f'<p class="ask">問い：{esc(ask)}</p>' if ask else '')
                 + f'<a class="go" href="{link}">根拠を見る →</a></div>' for t, body, link, ask in hs)
    quotes = ''.join(f'<details class="card quote"><summary>オーケストレーターが書いた「{esc(t)}」（研究の記録から引用）</summary><ul>'
                     + ''.join(f'<li>{esc(x)}</li>' for x in items) + '</ul></details>'
                     for name, (t, items) in m['logs'].items() if name == '次の狙い')
    inbox = text_of(ROOT / 'human' / 'INBOX.md')
    items = [line.strip()[2:] for line in inbox.splitlines() if line.strip().startswith('- ')]
    ib = (f'<details class="card quote"><summary>human/INBOX.md の思いつき（{len(items)} 件）</summary><ul>'
          + ''.join(f'<li>{esc(x)}</li>' for x in items[-20:]) + '</ul></details>') if items else ''
    body = (f'<div class="kpis">{kp}</div><h3 class="sub">記録から拾った点</h3>'
            f'<p class="lead">決まった規則で数字を拾い、問いの形にしたもの。解釈はしていない。</p><div class="hints">{hh}</div>{quotes}{ib}')
    return sec('now', 'いまどこにいるか', '記録だけ', '', body)


def section_progress(m):
    ideas, rounds = m['ideas'], [r for r in m['rounds'] if r['mode'] != 'C']
    eff = [(r['num'], ideas[f"{r['id']}/{r['promoted']}"]['effect'] or 0) for r in rounds if r['promoted']]
    if not eff:
        return sec('progress', '何が効いてきたか', '記録だけ', '', '<div class="card"><p class="foot">まだ採用が無い。</p></div>')
    later = lambda lim: next((n for n, _ in eff if all(e <= lim for k, e in eff if k >= n)), eff[-1][0])
    starts = sorted({rounds[0]['num'], later(1.0), later(0.25)})[:3]
    charts, radios = [], []
    for k, st in enumerate(starts):
        svg, names, series, rs = chart_progress(m, st)
        radios.append(f'<label><input type="radio" name="pw" value="{k}" {"checked" if k == min(1, len(starts) - 1) else ""}> R{st:04d} から</label>')
        charts.append(f'<div class="pane-w" data-k="{k}">{svg}</div>')
    legend = ('<div class="legend"><span><i class="sw ink"></i>全体</span>' + ''.join(
        f'<span><i class="sw o{i + 1}"></i>{esc(FEATURE["key"])} {lab}</span>' for i, lab in enumerate(FEATURE['labels'])) + '</div>')
    rows = []
    for r in rounds:
        if not r['promoted']:
            continue
        c = ideas[f"{r['id']}/{r['promoted']}"]
        h = c['halves']
        dec = (f'{fe(c["decision_effect"])}（確認）' if c.get('decision') == 'confirm' else '')
        rows.append([f'<a href="#i-{c["ref"].replace("/", "-")}">{c["ref"]}</a>', esc(c['title']), fe(c['effect']),
                     f'{c["se"]:.3f}' if c['se'] else '—', f'{c["z"]:.1f}' if c['z'] else '—', dec,
                     (f'{fe(h[0][0])} / {fe(h[1][0])}' if h[0] and h[1] else '—'),
                     ' '.join(f'<span class="cell {zclass(*b[:2]) if b else "z0"}">{fe(b[0]) if b else "—"}</span>' for b in c['bands'])])
    tbl = table_html([('採用', 0), ('題名', 0), ('効果', 1), ('標準誤差', 1), ('z', 1), ('確認評価', 1), ('前半 / 後半', 1),
                      (' / '.join(FEATURE['labels']), 0)], rows, 'lineage')
    lead = ('採用した変更の効果（そのラウンドの seed で、直前の best と対にした平均の差）を足し上げた。'
            + (f'細い線は {FEATURE["name"]}の帯ごとの内訳。' if FEATURE['key'] else '') + 'ラウンドごとに seed が違うので、足し上げは目安。')
    body = (f'<div class="card"><div class="seg">{"".join(radios)}</div>{legend}{"".join(charts)}</div>'
            f'<details class="card more"><summary>表で見る（効果・確認評価・前半と後半・帯ごと）</summary>{tbl}'
            '<p class="foot">前半 / 後半：評価の seed を前後半に分けた効果。首位を選んだ seed で測った値なので、片方だけ高いときは偶然の可能性がある。'
            '確認評価は、首位を未使用の seed で測り直した値（A の採否はこれで決める）。</p></details>')
    return sec('progress', '何が効いてきたか', '記録だけ＋特徴量', lead, body)


def section_flows(m):
    ideas = m['ideas']
    fam = collections.defaultdict(list)
    for c in ideas.values():
        fam[c['family']].append(c)
    fams = [v for v in fam.values() if len({c['round'] for c in v}) >= 2]
    fams.sort(key=lambda v: (-max(c['num'] for c in v), -len(v)))
    cols = sorted({c['round'] for v in fams for c in v})
    head = '<tr><th class="fam">案（最初の題名）</th>' + ''.join(f'<th class="rc">{r[1:].lstrip("0")}</th>' for r in cols) + '</tr>'
    body = []
    for v in fams:
        root = ideas[v[0]['family']]
        cells, extra = {}, collections.Counter(c['round'] for c in v)
        for c in sorted(v, key=lambda c: (c['adopted'], c['z'] if c['z'] is not None else -99)):
            cells[c['round']] = c
        tds = []
        for r in cols:
            c = cells.get(r)
            if not c:
                tds.append('<td></td>')
                continue
            label = '×' if not c['valid'] else fe(c['effect'], 2 if abs(c['effect']) < 10 else 1)
            tip = (f'{fe(c["effect"]) if c["valid"] else "無効"}' + (f' ± {c["se"]:.3f}' if c['valid'] else '') + f'\n{c["ref"]}（{KIND.get(c["kind"], c["kind"])}）\n{c["title"]}'
                   + (f'\nこのラウンドにほかに {extra[r] - 1} 件' if extra[r] > 1 else ''))
            tds.append(f'<td><a href="#i-{c["ref"].replace("/", "-")}" class="cell {zclass(c["effect"], c["se"]) if c["valid"] else "z0"}'
                       f'{" adopted" if c["adopted"] else ""}" data-tip="{esc(tip)}">{"✓" if c["adopted"] else ""}{label}</a></td>')
        body.append(f'<tr><th class="fam" title="{esc(root["title"])}">{esc(shorten(root["title"].lstrip("↳ "), 30))}</th>{"".join(tds)}</tr>')
    pts = transfer_points(m)
    a, b = (fmean(p[2] for p in pts), fmean(p[3] for p in pts)) if pts else (0, 0)
    lead = ('同じ案を、新しい best に載せ直して何度も試すことがある。行は1つの案の流れ、列はラウンド。'
            '色は効果を標準誤差で割った値（z）で、灰色は |z| が 2 未満。✓ は採用。')
    leg = ('<div class="legend"><span><i class="sw cell n2"></i>負（|z|≥4）</span><span><i class="sw cell n1"></i>負（2≤|z|<4）</span>'
           '<span><i class="sw cell z0"></i>|z|&lt;2</span><span><i class="sw cell p1"></i>正（2≤z<4）</span><span><i class="sw cell p2"></i>正（4≤z<8）</span>'
           '<span><i class="sw cell p3"></i>正（z≥8）</span></div>')
    body_html = (f'<div class="card">{leg}<div class="tw"><table class="flows"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'
                 '<p class="foot">流れは、案の親（best 以外）と、文章の近さ（文字の2〜3文字組の重なり）でつないだ。</p></div>'
                 f'<div class="two"><div class="card"><h3 class="sub">載せ直すと、効き目はどれだけ残るか</h3>{chart_transfer(m)}'
                 f'<p class="foot">点は {len(pts)} 件（単独で 0〜2 の正だった案）。平均は単独 {fe(a)} → 載せ直し {fe(b)}。対角線より下は目減り。</p></div>'
                 f'<div class="card"><h3 class="sub">種類ごとの成績（R0006 以降）</h3>{kind_table(m)}</div></div>')
    return sec('flows', '案の流れ', '記録だけ', lead, body_html)


def kind_table(m):
    rows = []
    groups = collections.defaultdict(list)
    for c in m['ideas'].values():
        groups[c['kind']].append(c)
    for k in ('new', 'continue', 'combine', 'deep', 'measure'):
        v = groups.get(k)
        if not v:
            continue
        val = [c for c in v if c['valid'] and c['effect'] is not None]
        rows.append([KIND[k], str(len(v)), str(sum(c['adopted'] for c in v)), str(sum(c['status'] == 'pos' for c in v)),
                     str(sum(c['status'] == 'neg' for c in v)), str(sum(c['status'] in ('invalid', 'failed') for c in v)),
                     fe(statistics.median(c['effect'] for c in val)) if val else '—'])
    return table_html([('種類', 0), ('件数', 1), ('採用', 1), ('正', 1), ('負', 1), ('無効・失敗', 1), ('効果の中央値', 1)], rows)


def section_ledger(m):
    ideas = m['ideas']
    order = sorted(ideas.values(), key=lambda c: (-c['num'], -(c['z'] if c['z'] is not None else -99)))
    rows = []
    for c in order:
        b = ''.join(f'<span class="cell {zclass(*x[:2]) if x else "z0"}" title="{esc(FEATURE["key"])} {lab}">'
                    f'{fe(x[0], 2 if abs(x[0]) < 10 else 1) if x else "—"}</span>' for x, lab in zip(c['bands'], FEATURE['labels']))
        err = clean_report(str(c['error'] or c['interrupted'] or c['jstatus'] or ''))
        if c['kind'] == 'measure':
            eff = '<small>測定</small>'
        elif c['valid'] and c['effect'] is not None:
            eff = f'{fe(c["effect"])}<small> ± {c["se"]:.3f}</small>' if c['se'] else fe(c['effect'])
        else:
            eff = f'<small title="{esc(err)}">{esc(shorten(err, 16))}</small>'
        u = c['usage']
        meta = ([f'理由：{shorten(err, 120)}'] if c['status'] in ('invalid', 'failed') and err else []) \
            + [KIND.get(c['kind'], c['kind']), c['engine'] or '—'] + ([c['model']] if c['model'] else []) + ([c['effort']] if c['effort'] else []) \
            + ([f'{c["minutes"]:.0f}分'] if c['minutes'] else []) + ([f'終わり方 {c["outcome"]}'] if c['outcome'] not in (None, 'ok') else []) \
            + ([f'出力 {fk(u.get("out"))}・呼び出し {u.get("calls")}回' + (f'・待つだけ {u["waits"]}回' if u.get('waits') else '')] if u.get('out') else [])
        if c['dropped_at']:
            meta.append(f'{c["dropped_at"]} 件で足切り')
        if c.get('decision') == 'confirm':
            meta.append(f'確認評価 {fe(c["decision_effect"])}')
        rel = []
        if c['parents']:
            rel.append('親：' + '、'.join(f'<a href="#i-{p.replace("/", "-")}">{p}</a>' for p in c['parents']))
        if c['children']:
            rel.append('その後：' + '、'.join(f'<a href="#i-{k.replace("/", "-")}">{k}</a>（{fe(ideas[k]["effect"]) if ideas[k]["valid"] else "—"}）'
                                           for k in c['children']))
        if c['similar']:
            rel.append('文章が近い前の案：' + '、'.join(f'<a href="#i-{j.replace("/", "-")}">{j}</a>（{fe(ideas[j]["effect"]) if ideas[j]["valid"] else "—"}）'
                                                for s, j in c['similar']))
        half = c['halves']
        stat = (f'良くなったケース {c["better"]}・悪くなったケース {c["worse"]}・前半 {fe(half[0][0])}・後半 {fe(half[1][0])}'
                if c['valid'] and half[0] and half[1] and c['better'] is not None else '')
        docs = ''.join(f'<h4>{name}</h4><pre>{esc(text)}</pre>' for name, text in
                       (('測定の報告（report.md）', c['creport']), ('worker の notes.md', c['notes']), ('worker の最終報告', c['report'])) if text.strip())
        common = f'<p class="text"><span class="common">{esc(c["common"])}</span></p>' if c['common'] else ''
        rows.append(
            f'<details class="idea" id="i-{c["ref"].replace("/", "-")}" data-kind="{c["kind"]}" data-status="{c["status"]}" data-num="{c["num"]}" '
            f'data-z="{c["z"] if c["z"] is not None else -99}"><summary><span class="ref">{c["ref"]}</span><span class="tt">{esc(c["title"])}</span>'
            f'<span class="kd">{KIND.get(c["kind"], c["kind"])}</span><span class="fp">{forest(c["effect"] if c["valid"] else None, c["se"])}</span>'
            f'<span class="ef">{eff}</span><span class="bd">{b}</span><span class="st s-{c["status"]}">{STATUS[c["status"]]}</span></summary>'
            f'<div class="body"><p class="meta">{esc("・".join(str(x) for x in meta))}</p>' + (f'<p class="meta">{esc(stat)}</p>' if stat else '')
            + (f'<p class="meta">{"　".join(rel)}</p>' if rel else '') + f'<h4>案（灰色は全案に共通の文）</h4>{common}<p class="text">{esc(c["text"])}</p>{docs}</div></details>')
    head = (f'<div class="ihead"><span>案</span><span>題名</span><span>種類</span><span class="fp">{forest_axis()}</span><span>効果</span>'
            f'<span class="bd">' + ''.join(f'<span>{lab}</span>' for lab in FEATURE['labels']) + '</span><span>判定</span></div>')
    filt = ('<div class="filters"><input type="search" id="q" placeholder="案の文章・報告を検索（例：高速化、焼きなまし）">'
            '<select id="fk"><option value="">種類：すべて</option>' + ''.join(f'<option value="{k}">{v}</option>' for k, v in KIND.items()) + '</select>'
            '<select id="fs"><option value="">判定：すべて</option>' + ''.join(f'<option value="{k}">{v}</option>' for k, v in STATUS.items()) + '</select>'
            '<select id="fo"><option value="new">新しい順</option><option value="z">z の大きい順</option></select><span id="cnt"></span></div>')
    lead = ('すべての案と評価の結果。行を開くと、案の全文・worker の報告・親子・文章が近い前の案が出る。'
            + (f'帯の列は {FEATURE["name"]}ごとの効果で、色は z。' if FEATURE['key'] else '') + '効果の図は 0 の近くを広げた目盛りで、線は 95% 区間。')
    return sec('ledger', 'すべての案', '記録だけ' + ('＋特徴量' if FEATURE['key'] else ''), lead,
               f'<div class="card ledger">{filt}{head}<div class="ilist">{"".join(rows)}</div></div>')


def pick_boards(m, probe):
    last, sc, mean, res = residuals(m)
    feats, key = m['feats'], FEATURE['key']
    picks = []
    if not sc:
        return picks
    if key and FEATURE['bands']:
        for i, lab in enumerate(FEATURE['labels']):
            cand = [s for s in sc if band_index(feats.get(s, {}).get(key)) == i]
            if cand:
                s = min(cand, key=lambda s: SIGN * res[s])
                picks.append((s, f'{key} {lab} で、同じ{key}の平均から最も低い'))
    else:
        for s in sorted(sc, key=lambda s: SIGN * res[s])[:3]:
            picks.append((s, '平均から最も低い'))
    if probe and probe['time']:
        t = dict(probe['time'])
        d = t[max(t)]
        rest = [s for s in d if s not in {p for p, _ in picks}]
        if rest:
            s = max(rest, key=d.get)
            picks.append((s, f'時間を {max(t):g} 倍にして最も伸びた（{d[s]:+.1f}）'.replace('-', '−')))
    mid = sorted((s for s in sc if s not in {p for p, _ in picks}), key=lambda s: abs(res[s]))
    if mid:
        picks.append((mid[0], '平均どおりの盤面（比べる基準）'))
    return picks


def section_weak(m, picks):
    last, sc, mean, res = residuals(m)
    if not sc:
        return ''
    svg, pts, last = chart_weak(m, picks)
    key = FEATURE['key']
    rows = []
    for lab, bs in zip(FEATURE['labels'], last['band_sets']):
        v = [sc[s] for s in bs if s in sc]
        if v:
            rows.append([lab, str(len(v)), f'{fmean(v):.2f}', f'{min(v):g}', f'{max(v):g}'])
    tbl = table_html([(f'{key} の帯', 0), ('件数', 1), ('平均', 1), ('最低', 1), ('最高', 1)], rows) if rows else ''
    worst = sorted(res, key=lambda s: SIGN * res[s])[:12]
    wl = table_html([('seed', 0)] + ([(key, 1)] if key else []) + [('得点', 1), ('平均との差', 1)],
                    [[str(s)] + ([str(fv(m, s))] if key else []) + [f'{sc[s]:g}', f'{res[s]:+.1f}'.replace('-', '−')] for s in worst])
    lead = (f'現 best の、{last["id"]} の評価（{len(sc)} 件）での得点。点は1盤面' + (f'、線は同じ{FEATURE["name"]}の平均' if key else '')
            + '。橙の点は「盤面を見る」に載せる盤面（自動で選ぶ）。')
    body = (f'<div class="card">{svg}<script type="application/json" id="weak-data">{json.dumps(pts)}</script></div>'
            f'<div class="two">' + (f'<div class="card"><h3 class="sub">帯ごと</h3>{tbl}</div>' if tbl else '')
            + f'<div class="card"><h3 class="sub">平均から最も低い12盤面</h3>{wl}</div></div>')
    return sec('weak', 'どこで点を落としているか', '記録だけ' + ('＋特徴量' if key else ''), lead, body)


def section_probe(m, probe):
    if not probe:
        return sec('probe', '時間と運の余地', 'ソルバーを実行', '',
                   '<div class="card"><p class="foot">まだ測っていない。<code>python3 -m thin.cli probe</code> で、現 best に時間を 0.5〜8 倍与えたときと、'
                   '同じ版を何回か走らせて最良を取ったときの伸びを測る（12 並列で 6 分ほど）。</p></div>')
    svg = chart_probe(probe)
    rows = []
    for name, key in (('1回を長く', 'time'), ('回数を増やして最良', 'bestof')):
        for k, d in probe[key]:
            if k == 1:
                continue
            mu, se = mean_se(d.values())
            cells = [f'{name}：{k:g}' + ('倍' if key == 'time' else '回'), f'{fe(mu, 2)} <small>± {se:.2f}</small>']
            for i, lab in enumerate(FEATURE['labels']):
                v = [d[s] for s in d if band_index(fv(m, s)) == i]
                bm, bse = mean_se(v) if v else (None, None)
                cells.append(f'<span class="cell {zclass(bm, bse)}">{fe(bm, 2)}</span>' if v else '—')
            rows.append(cells)
    tbl = table_html([('条件', 0), ('全体', 1)] + [(lab, 1) for lab in FEATURE['labels']], rows)
    lead = (f'{"現" if probe["current"] else "前の（いまの best ではない）"} best を {len(probe["seeds"])} 盤面で走らせ直した（{probe["at"]}）。'
            '時間は、ソルバーから見える時計を遅く進めて伸ばした（ソースは変えていない）。'
            f'基準は1倍の {probe["reps"]} 回の平均。「回数を増やして最良」は、その {probe["reps"]} 回から選んだ最良の期待値。')
    body = (f'<div class="two"><div class="card">{svg}</div><div class="card"><h3 class="sub">帯ごと</h3>{tbl}'
            '<p class="foot">時間を伸ばして伸びる分は探索の量、回数で伸びる分は運の幅の目安。どちらも、同じ盤面で既に届く点があることを示す。</p></div></div>')
    return sec('probe', '時間と運の余地', 'ソルバーを実行', lead, body)


def section_boards(m, picks):
    data = read_json(ROOT / 'runs' / 'report' / 'boards.json', {}) or {}
    rows = data.get('rows', [])
    if not rows:
        return sec('boards', '盤面を見る', 'ソルバーを実行', '',
                   '<div class="card"><p class="foot">まだ作っていない。<code>python3 -m thin.cli report --boards</code> で、選んだ盤面を過去の best と'
                   ' 8 倍の時間で解き直して載せる（1分ほど）。</p></div>')
    why = dict((s, w) for s, w in data.get('picks', []))
    boards = {}
    for r in rows:
        b = boards.setdefault(r['seed'], {'seed': r['seed'], 'input': r['input'], 'why': why.get(r['seed'], ''), 'runs': {}})
        key = r['label'] + ('' if r['k'] == 1 else f' ×{r["k"]:g}')
        b['runs'][key] = {'score': r['score'], 'output': r['output'],
                          'label': key + ('' if r['k'] == 1 else f'（時間 {r["k"]:g} 倍）')}
    blist = [boards[s] for s, _ in data.get('picks', []) if s in boards] + [b for s, b in boards.items() if s not in why]
    for b in blist:
        b['M'] = (m['feats'].get(b['seed']) or {}).get(FEATURE['key'], '') if FEATURE['key'] else ''
    view = (ROOT / 'problem' / 'view.js').is_file()
    chips = ''.join(f'<button class="chip" data-i="{i}">seed {b["seed"]}<small>{esc(FEATURE["key"] or "")}{"=" if FEATURE["key"] else ""}{b["M"]}・'
                    f'{esc(shorten(b["why"], 22))}</small></button>' for i, b in enumerate(blist))
    pane = lambda k: (f'<figure class="pane" data-pane="{k}"><select class="ver" aria-label="版"></select>'
                      + ('<canvas class="board" width="360" height="360"></canvas>' if view else '<pre class="out"></pre>')
                      + '<figcaption class="stats"></figcaption></figure>')
    legend = '<div class="legend" id="view-legend"></div>' if view else ''
    lead = (f'自動で選んだ盤面を、過去の best と、現 best に時間を与えた版で解き直した（{data.get("at", "")}）。左右で版を選ぶ。'
            + ('下の棒で手順を進める。描き方は problem/view.js（問題ごとに書く）。' if view else
               'problem/view.js（問題ごとの描き方）が無いので、出力をそのまま並べる。'))
    vers = list(dict.fromkeys(k for b in blist for k in b['runs']))
    score_table = table_html([('seed', 0)] + [(v, 1) for v in vers],
                             [[str(b['seed'])] + [str(b['runs'][v]['score']) if v in b['runs'] else '—' for v in vers] for b in blist])
    payload = json.dumps(blist, ensure_ascii=False).replace('</', '<\\/')
    controls = ('<div class="controls"><button id="play" type="button">▶ 再生</button><input type="range" id="t" min="0" max="250" value="250" '
                'aria-label="手順"><span id="tl"></span></div>' if view else '')
    body = (f'<div class="card viewer"><div class="chips">{chips}</div><p class="why" id="why"></p>'
            f'<div class="panes">{pane(0)}{pane(1)}</div>{controls}{legend}</div>'
            f'<script type="application/json" id="boards-data">{payload}</script>'
            f'<details class="card more"><summary>表で見る（盤面 × 版の得点）</summary>{score_table}</details>')
    return sec('boards', '盤面を見る', 'ソルバーを実行' + ('＋問題専用の描画' if view else ''), lead, body)


def section_usage(m):
    rows = []
    tot = lambda r: ((r['usage'].get('workers_total') or {}).get('out', 0) + sum(v.get('out', 0) for v in (r['usage'].get('orchestrator') or {}).values()))
    mx = max([tot(r) for r in m['rounds']] + [1])
    for r in m['rounds']:
        u = r['usage'] or {}
        w, o = (u.get('workers_total') or {}), u.get('orchestrator') or {}
        oo, wo = sum(v.get('out', 0) for v in o.values()), w.get('out', 0)
        cr = w.get('cr', 0) + sum(v.get('cr', 0) for v in o.values())
        js = [m['ideas'][k] for k in r['ideas']]
        engines = collections.Counter((c['engine'] or '—') + (f' {c["effort"]}' if c['effort'] else '') for c in js)
        waits = sum((c['usage'] or {}).get('waits') or 0 for c in js)
        calls = sum((c['usage'] or {}).get('calls') or 0 for c in js)
        miss = u.get('workers_without_usage', 0)
        bar = f'<span class="bar"><i class="s1" style="width:{wo / mx * 100:.1f}%"></i><i class="s2" style="width:{oo / mx * 100:.1f}%"></i></span>'
        c = m['ideas'].get(f"{r['id']}/{r['promoted']}") if r['promoted'] else None
        wmin = max([(x['minutes'] or 0) for x in js] + [0])
        week = [x['last'] - x['first'] for x in (u.get('worker_limits') or []) if x.get('window') == 'seven_day']
        rows.append([r['id'], r['mode'], esc('、'.join(f'{k} ×{v}' if v > 1 else k for k, v in engines.items())), bar,
                     (fk(wo) if wo else '—') + (f'<small>（{miss}/{len(js)} 体は記録なし）</small>' if miss and wo else ''), fk(oo), fk(cr),
                     f'{waits}/{calls}' if calls else '—', f'{five_hour(r):g}' if five_hour(r) is not None else '—',
                     f'{sum(week):g}' if week else '—', f'{wmin:.0f}' if wmin else '—', fe(c['effect']) if c else '—'])
    tbl = table_html([('ラウンド', 0), ('行動', 0), ('worker', 0), ('出力トークン', 0), ('worker', 1), ('オーケストレーター', 1),
                      ('キャッシュ読み', 1), ('待つだけの呼び出し', 1), ('5時間枠', 1), ('週枠', 1), ('最長の worker（分）', 1), ('採用', 1)], rows, 'usage')
    lead = ('ラウンドごとの消費。棒は出力トークンで、青が worker、橙がオーケストレーター。「待つだけの呼び出し」は codex worker が'
            '走っているコマンドを待つだけに使った呼び出し（それでも文脈を毎回読み直す）。「5時間枠」「週枠」は codex の利用率の増分（ポイント）。')
    leg = '<div class="legend"><span><i class="sw s1"></i>worker の出力</span><span><i class="sw s2"></i>オーケストレーターの出力</span></div>'
    return sec('usage', 'いくら使ったか', '記録だけ', lead, f'<div class="card">{leg}{tbl}</div>')


def section_about(m, gen_sec, nlines):
    files = ['runs/R*/plan.json・result.json・usage.json・state.json', 'runs/R*/evaluation/*/scores.csv（得点と特徴量）',
             'runs/R*/jobs/*/job.json・notes.md・report.md と worker の最終報告', 'best/best.json・best/history/',
             'docs/RESEARCH_LOG.md（「次の狙い」「人間への問い」の引用だけ）', 'runs/probe/latest.json（thin.cli probe）、runs/report/boards.json（--boards）']
    body = (f'<div class="card about"><p>このページは <code>thin/tools/report.py</code>（{nlines} 行、Python の標準ライブラリだけ）が、記録から {gen_sec:.1f} 秒で作った。'
            'LLM は使っていない（トークン 0）。文章のうち、案の題名と本文・worker の報告・研究の記録の引用は、ラウンドの中で書かれたものをそのまま載せている。</p>'
            '<h4>読んだもの</h4><ul>' + ''.join(f'<li>{esc(f)}</li>' for f in files) + '</ul>'
            '<h4>問題ごとに要るもの</h4><ul>'
            f'<li>特徴量：採点アダプタが結果の <code>features</code> で返す値（いまは {esc(FEATURE["name"] or "なし")}）。'
            'config.json の report.feature で選び、report.bands で帯を決める（無ければ三分位）。</li>'
            '<li>盤面の描き方：<code>problem/view.js</code>（任意）。無ければ出力を並べる。</li></ul>'
            '<h4>使った規則</h4><ul><li>判定：|z| ≥ 2 を正・負とする。z = 効果 ÷ 標準誤差。何十もの案を見比べるので、z が 2〜3 の差は偶然でも出る。</li>'
            '<li>「帯で効き方が割れた案」：ある帯で z ≥ 2.5、別の帯で z ≤ −2。</li>'
            '<li>「正だったが、その後使っていない案」：z ≥ 2 で不採用、後のラウンドの親にも、流れの続きにもなっていない。</li>'
            '<li>案の流れ：親（best 以外）か、文章の近さ 0.5 以上（ほぼ同文は 0.9 以上）でつなぐ。</li></ul></div>')
    return sec('about', 'このページの作り方', '', '', body)


# ---------------------------------------------------------------- ページ

CSS = r"""
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--line:rgba(11,11,11,.10);
--s1:#2a78d6;--s2:#eb6834;--b1:#cde2fb;--b2:#86b6ef;--o1:#86b6ef;--o2:#2a78d6;--o3:#104281;--pos:#2a78d6;--neg:#e34948;--wall:#52514e;--path:#898781;--path2:#eb6834;--pt:rgba(42,120,214,.28);--good:#006300}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--line:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--b1:#184f95;--b2:#86b6ef;--o1:#184f95;--o2:#3987e5;--o3:#9ec5f4;--pos:#3987e5;--neg:#e66767;--wall:#898781;--path:#898781;--path2:#d95926;--pt:rgba(57,135,229,.35);--good:#0ca30c}}
:root[data-theme=dark]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--line:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--b1:#184f95;--b2:#86b6ef;--o1:#184f95;--o2:#3987e5;--o3:#9ec5f4;--pos:#3987e5;--neg:#e66767;--wall:#898781;--path:#898781;--path2:#d95926;--pt:rgba(57,135,229,.35);--good:#0ca30c}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.75 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP","Yu Gothic UI",Meiryo,sans-serif}
a{color:inherit;text-decoration-color:var(--axis);text-underline-offset:3px}
main{max-width:1140px;margin:0 auto;padding:28px 20px 96px}
header h1{font-size:24px;margin:0 0 4px;letter-spacing:.01em}
header p{margin:0;color:var(--ink2);font-size:13.5px}
nav{position:sticky;top:0;z-index:5;background:var(--page);border-bottom:1px solid var(--line);margin:16px -20px 0;padding:8px 20px;display:flex;gap:6px 16px;flex-wrap:wrap;font-size:13.5px}
nav a{text-decoration:none;color:var(--ink2)} nav a:hover{color:var(--ink)}
section{margin-top:44px;scroll-margin-top:52px}
h2{font-size:20px;margin:0 0 6px;font-weight:650}
h3.sub{font-size:15px;margin:22px 0 8px;font-weight:650} .card h3.sub{margin-top:0}
h4{font-size:13.5px;margin:14px 0 4px;color:var(--ink2)}
.lead{color:var(--ink2);margin:0 0 14px;max-width:56em;font-size:14px}
.tag{font-size:12px;font-weight:400;color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:1px 9px;margin-left:10px;vertical-align:3px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:12px}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.kpi .label{font-size:13px;color:var(--ink2)} .kpi .value{font-size:30px;font-weight:600;line-height:1.35} .kpi .sub{font-size:12.5px;color:var(--ink2)}
.hints{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px}
.hint{display:flex;flex-direction:column;margin:0}
.hint h3{font-size:14.5px;margin:0 0 4px} .hint p{margin:0 0 6px;font-size:13.5px;color:var(--ink2)} .hint .ask{color:var(--ink);font-weight:600}
.hint .go{margin-top:auto;font-size:13px;color:var(--ink2)}
.quote ul{margin:8px 0 0;padding-left:1.2em;font-size:13.5px;color:var(--ink2)} .quote summary,.more summary{cursor:pointer;font-size:13.5px;color:var(--ink2)}
.foot{font-size:12.5px;color:var(--ink2);margin:8px 0 0}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{font-weight:600;color:var(--ink2);font-size:12.5px;white-space:nowrap} .num{text-align:right;white-space:nowrap}
table.usage td:nth-child(3){white-space:nowrap}
small{color:var(--ink2);font-size:.86em}
svg.chart{max-width:100%;height:auto;display:block}
svg text{fill:var(--ink2);font-size:11.5px;font-family:inherit} svg .lab,svg .note{paint-order:stroke;stroke:var(--surface);stroke-width:3px;stroke-linejoin:round} svg .tick{fill:var(--muted);font-variant-numeric:tabular-nums} svg .lab{fill:var(--ink);font-size:12px}
svg .axt{fill:var(--ink2);font-size:12px} svg .note{fill:var(--muted);font-size:11px}
svg .grid{stroke:var(--grid);stroke-width:1} svg .base{stroke:var(--axis);stroke-width:1} svg .diag{stroke:var(--axis);stroke-width:1}
svg .lead{stroke:var(--axis);stroke-width:1}
svg .ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.ln.ink{stroke:var(--ink)} .ln.o1{stroke:var(--o1)} .ln.o2{stroke:var(--o2)} .ln.o3{stroke:var(--o3)} .ln.s1{stroke:var(--s1)} .ln.s2{stroke:var(--s2)}
svg .err{stroke-width:2;stroke-linecap:round} .err.s1{stroke:var(--s1)} .err.s2{stroke:var(--s2)}
svg .dot.ink{fill:var(--ink);stroke:var(--surface);stroke-width:2} svg .dot.s1{fill:var(--s1)} svg .dot.s2{fill:var(--s2)} svg .ring{stroke:var(--surface);stroke-width:2}
svg .pt{fill:var(--pt)} svg .pick{cursor:pointer}
svg .hit{fill:transparent} svg .hit:hover,svg .hit:focus{fill:var(--line);outline:none}
svg [data-tip]:focus{outline:2px solid var(--s1)}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12.5px;color:var(--ink2);margin:4px 0 8px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px}
.sw.ink{background:var(--ink);height:2px} .sw.o1{background:var(--o1);height:2px} .sw.o2{background:var(--o2);height:2px} .sw.o3{background:var(--o3);height:2px}
.sw.s1{background:var(--s1)} .sw.s2{background:var(--s2)} .sw.b1{background:var(--b1)} .sw.b2{background:var(--b2)} .sw.line{height:3px;background:var(--path2)}
.seg{display:flex;gap:14px;font-size:13px;color:var(--ink2);margin-bottom:4px} .seg label{cursor:pointer}
.pane-w{display:none} .card:has(input[value="0"]:checked) .pane-w[data-k="0"],.card:has(input[value="1"]:checked) .pane-w[data-k="1"],.card:has(input[value="2"]:checked) .pane-w[data-k="2"]{display:block}
.cell{display:inline-block;min-width:52px;padding:1px 6px;border-radius:4px;text-align:right;font-variant-numeric:tabular-nums;font-size:12.5px;color:var(--ink);text-decoration:none;background:color-mix(in oklab,var(--ink) 5%,var(--surface))}
.cell.p1{background:color-mix(in oklab,var(--pos) 22%,var(--surface))} .cell.p2{background:color-mix(in oklab,var(--pos) 45%,var(--surface))} .cell.p3{background:color-mix(in oklab,var(--pos) 68%,var(--surface))}
.cell.n1{background:color-mix(in oklab,var(--neg) 22%,var(--surface))} .cell.n2{background:color-mix(in oklab,var(--neg) 45%,var(--surface))} .cell.n3{background:color-mix(in oklab,var(--neg) 68%,var(--surface))}
.cell.adopted{outline:2px solid var(--ink);outline-offset:-2px;font-weight:600}
.sw.cell{min-width:0;width:14px;height:12px;padding:0}
table.flows{width:auto} table.flows td{padding:2px 2px;border:none} table.flows th.rc{text-align:center;font-weight:500;padding:2px} table.flows th.fam{font-weight:400;color:var(--ink);white-space:nowrap;max-width:340px;overflow:hidden;text-overflow:ellipsis;padding-right:12px;position:sticky;left:0;background:var(--surface)}
table.flows .cell{min-width:56px}
.ledger{padding:12px 0 6px}
.filters{display:flex;flex-wrap:wrap;gap:8px;padding:0 14px 10px;align-items:center}
.filters input,.filters select{font:inherit;font-size:13px;padding:5px 8px;border:1px solid var(--line);border-radius:8px;background:var(--page);color:var(--ink)}
.filters input{flex:1 1 260px} #cnt{font-size:12.5px;color:var(--ink2)}
.ihead,.idea>summary{display:grid;grid-template-columns:84px minmax(0,1fr) 52px 180px 104px 176px 66px;gap:8px;align-items:center;padding:5px 14px}
.ihead{font-size:12px;color:var(--ink2);border-bottom:1px solid var(--line);position:sticky;top:0}
.ilist{max-height:72vh;overflow-y:auto}
.idea>summary{cursor:pointer;list-style:none;border-bottom:1px solid var(--line);font-size:13.5px}
.idea>summary::-webkit-details-marker{display:none}
.idea>summary:hover{background:color-mix(in oklab,var(--ink) 4%,var(--surface))}
.idea[open]>summary{background:color-mix(in oklab,var(--s1) 8%,var(--surface))}
.idea .ref{font-variant-numeric:tabular-nums;color:var(--ink2);font-size:12.5px} .idea .tt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.idea .kd{font-size:12px;color:var(--ink2)} .ef{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bd{display:flex;gap:4px} .ihead .bd span{min-width:56px;text-align:right}
.forest{width:180px;height:20px;display:block} .forest .base{stroke:var(--axis)} .forest .ci{stroke:var(--muted);stroke-width:2;stroke-linecap:round} .forest circle{fill:var(--muted)}
.s-adopted~* .forest circle{fill:var(--s1)} .idea:has(.s-adopted) .forest circle{fill:var(--s1)} .idea:has(.s-adopted) .forest .ci{stroke:var(--s1)}
.ihead .forest{height:16px}
.st{font-size:12px;white-space:nowrap} .s-adopted{font-weight:650}
.idea .body{padding:10px 14px 16px 106px;border-bottom:1px solid var(--line);font-size:13.5px}
.idea .meta{margin:0 0 4px;color:var(--ink2);font-size:12.5px}
.idea .text{margin:0;max-width:62em} .idea .text .common{color:var(--muted)}
.idea pre{white-space:pre-wrap;font:inherit;font-size:13px;margin:0;max-width:62em;background:var(--page);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.bar{display:flex;width:150px;height:10px;gap:2px} .bar i{display:block;height:10px;border-radius:2px} .bar i.s1{background:var(--s1)} .bar i.s2{background:var(--s2)}
table.usage td:nth-child(4){width:160px}
.viewer .chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font:inherit;font-size:13px;text-align:left;padding:6px 10px;border:1px solid var(--line);border-radius:10px;background:var(--page);color:var(--ink);cursor:pointer;display:flex;flex-direction:column;line-height:1.35}
.chip small{font-size:11.5px} .chip[aria-pressed=true]{border-color:var(--s1);box-shadow:inset 0 0 0 1px var(--s1)}
.why{font-size:13px;color:var(--ink2);margin:8px 0}
.panes{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.pane{margin:0}
.pane select{font:inherit;font-size:13px;width:100%;padding:5px 8px;border:1px solid var(--line);border-radius:8px;background:var(--page);color:var(--ink);margin-bottom:6px}
canvas.board{width:100%;max-width:440px;aspect-ratio:1;display:block}
.stats{font-size:12.5px;color:var(--ink2);font-variant-numeric:tabular-nums;margin-top:4px} .stats b{color:var(--ink);font-size:15px}
.controls{display:flex;gap:12px;align-items:center;margin:12px 0 4px} .controls input{flex:1}
.controls button{font:inherit;font-size:13px;padding:5px 12px;border-radius:8px;border:1px solid var(--line);background:var(--page);color:var(--ink);cursor:pointer}
#tl{font-size:12.5px;color:var(--ink2);min-width:7em;font-variant-numeric:tabular-nums}
.about p,.about li{font-size:13.5px;color:var(--ink2)} code{font-size:.92em}
.pane pre.out{max-height:420px;overflow:auto;font-size:12px;background:var(--page);border:1px solid var(--line);border-radius:8px;padding:8px;margin:0}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--line);box-shadow:0 6px 20px rgba(0,0,0,.14);border-radius:8px;padding:7px 10px;font-size:12.5px;line-height:1.55;max-width:380px;display:none;z-index:20}
#tip strong{font-size:14px;display:block} #tip div{color:var(--ink2)}
@media (max-width:860px){.ihead,.idea>summary{grid-template-columns:78px minmax(0,1fr) 92px 60px}.idea .kd,.idea .fp,.idea .bd,.ihead .fp,.ihead .bd,.ihead span:nth-child(3){display:none}.idea .body{padding-left:14px}}
"""

JS = r"""
const tip=document.getElementById('tip');
function showTip(text,x,y){tip.replaceChildren();text.split('\n').forEach((l,i)=>{const e=document.createElement(i?'div':'strong');e.textContent=l;tip.append(e)});
 tip.style.display='block';const w=tip.offsetWidth,h=tip.offsetHeight;tip.style.left=Math.min(x+14,innerWidth-w-8)+'px';tip.style.top=(y+h+20>innerHeight?y-h-12:y+14)+'px'}
document.addEventListener('pointermove',e=>{if(e.target.closest('#weak-svg'))return;const el=e.target.closest('[data-tip]');if(el)showTip(el.dataset.tip,e.clientX,e.clientY);else tip.style.display='none'});
document.addEventListener('focusin',e=>{const el=e.target.closest('[data-tip]');if(el){const r=el.getBoundingClientRect();showTip(el.dataset.tip,r.right,r.top)}});
document.addEventListener('focusout',()=>tip.style.display='none');
// 散布図：いちばん近い点を出す
(()=>{const svg=document.getElementById('weak-svg');if(!svg)return;const pts=JSON.parse(document.getElementById('weak-data').textContent);
 svg.addEventListener('pointermove',e=>{const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const q=p.matrixTransform(svg.getScreenCTM().inverse());
  let best=null,bd=144;for(const a of pts){const d=(a[3]-q.x)**2+(a[4]-q.y)**2;if(d<bd){bd=d;best=a}}
  const pick=e.target.closest('[data-tip]');if(pick){showTip(pick.dataset.tip,e.clientX,e.clientY);return}
  if(best)showTip(best[2]+' 点（seed '+best[0]+'）'+(FKEY?'\n'+FKEY+'='+best[1]:''),e.clientX,e.clientY);else tip.style.display='none'});
 svg.addEventListener('pointerleave',()=>tip.style.display='none');
 svg.addEventListener('click',e=>{const c=e.target.closest('[data-seed]');if(c&&window.openBoard)window.openBoard(+c.dataset.seed)})})();
// 台帳の絞り込み
(()=>{const list=document.querySelector('.ilist');if(!list)return;const rows=[...list.children],q=document.getElementById('q'),fk=document.getElementById('fk'),fs=document.getElementById('fs'),fo=document.getElementById('fo'),cnt=document.getElementById('cnt');
 function apply(){const s=q.value.trim().toLowerCase();let n=0;for(const r of rows){const ok=(!s||r.textContent.toLowerCase().includes(s))&&(!fk.value||r.dataset.kind===fk.value)&&(!fs.value||r.dataset.status===fs.value);r.hidden=!ok;n+=ok}cnt.textContent=n+' 件'}
 function sort(){const k=fo.value;const s=[...rows].sort((a,b)=>k==='z'?(+b.dataset.z)-(+a.dataset.z):(+b.dataset.num)-(+a.dataset.num)||(+b.dataset.z)-(+a.dataset.z));list.append(...s)}
 [q,fk,fs].forEach(e=>e.addEventListener('input',apply));fo.addEventListener('input',sort);apply();
 function openHash(){const id=decodeURIComponent(location.hash.slice(1));const el=id&&document.getElementById(id);if(el&&el.matches('details.idea')){for(const r of rows)r.hidden=false;q.value='';fk.value='';fs.value='';apply();el.open=true;
  const top=el.offsetTop-list.offsetTop;list.scrollTop=top-8;setTimeout(()=>list.closest('section').scrollIntoView({block:'start'}),0)}}
 addEventListener('hashchange',openHash);openHash()})();
// 盤面ビューア
(()=>{const data=document.getElementById('boards-data');if(!data)return;const boards=JSON.parse(data.textContent);
 if(!window.caseView){const panes=[...document.querySelectorAll('.pane')],chips=[...document.querySelectorAll('.chip')];let cur=0;
  const show=()=>panes.forEach(p=>{const r=boards[cur].runs[p.querySelector('select').value];p.querySelector('pre').textContent=r.output;
   p.querySelector('.stats').textContent=r.score+' 点'});
  const pick=i=>{cur=i;chips.forEach((c,k)=>c.setAttribute('aria-pressed',k===i));const b=boards[i],keys=Object.keys(b.runs);
   document.getElementById('why').textContent='seed '+b.seed+'：'+b.why;
   panes.forEach((p,k)=>{const sel=p.querySelector('select');sel.replaceChildren(...keys.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=b.runs[x].label+'　'+b.runs[x].score+' 点';return o}));
    sel.value=k===0?keys.filter(x=>!x.includes('×')).slice(-1)[0]:(keys.find(x=>x.includes('×'))||keys[0])});show()};
  panes.forEach(p=>p.querySelector('select').addEventListener('input',show));chips.forEach((c,k)=>c.addEventListener('click',()=>pick(k)));
  window.openBoard=seed=>{const i=boards.findIndex(b=>b.seed===seed);if(i>=0){pick(i);document.getElementById('boards').scrollIntoView({block:'start'})}};
  pick(0);return}
 const panes=[...document.querySelectorAll('.pane')],t=document.getElementById('t'),tl=document.getElementById('tl'),play=document.getElementById('play'),chips=[...document.querySelectorAll('.chip')];
 let cur=0,timer=null,loaded=[];
 function setBoard(i){cur=i;chips.forEach((c,k)=>c.setAttribute('aria-pressed',k===i));const b=boards[i];document.getElementById('why').textContent='seed '+b.seed+(FKEY?'（'+FKEY+'='+b.M+'）':'')+'：'+b.why;
  const keys=Object.keys(b.runs);panes.forEach((p,k)=>{const sel=p.querySelector('select'),old=sel.value;sel.replaceChildren(...keys.map(key=>{const o=document.createElement('option');o.value=key;o.textContent=b.runs[key].label+'　'+b.runs[key].score+' 点';return o}));
   const plain=keys.filter(x=>!x.includes('×')),times=keys.filter(x=>x.includes('×'));const def=k===0?plain[plain.length-1]:(times[0]||plain[0]);sel.value=keys.includes(old)?old:def});
  load();t.value=t.max;draw()}
 function load(){const b=boards[cur];loaded=panes.map(p=>caseView.load(b.input,b.runs[p.querySelector('select').value].output));t.max=Math.max(...loaded.map(c=>c.steps.length))}
 function draw(){const v=+t.value;tl.textContent='手順 '+v+' / '+t.max;panes.forEach((p,k)=>{const c=loaded[k],b=boards[cur].runs[p.querySelector('select').value];caseView.draw(p.querySelector('canvas'),c,Math.min(v,c.steps.length));
  const st=p.querySelector('.stats');st.replaceChildren();const s=document.createElement('b');s.textContent=b.score+' 点';st.append(s,'　'+c.stats.map(([a,x])=>a+' '+x).join('・'))})}
 panes.forEach(p=>p.querySelector('select').addEventListener('input',()=>{load();t.value=t.max;draw()}));
 t.addEventListener('input',draw);chips.forEach((c,k)=>c.addEventListener('click',()=>setBoard(k)));
 play.addEventListener('click',()=>{if(timer){clearInterval(timer);timer=null;play.textContent='▶ 再生';return}if(+t.value>=+t.max)t.value=0;play.textContent='❚❚ 止める';
  timer=setInterval(()=>{t.value=+t.value+1;draw();if(+t.value>=+t.max){clearInterval(timer);timer=null;play.textContent='▶ 再生'}},45)});
 addEventListener('resize',draw);
 matchMedia('(prefers-color-scheme: dark)').addEventListener('change',draw);
 window.openBoard=seed=>{const i=boards.findIndex(b=>b.seed===seed);if(i>=0){setBoard(i);document.getElementById('boards').scrollIntoView({block:'start'})}};
 const lg=document.getElementById('view-legend');if(lg&&caseView.legend)for(const[v,t]of caseView.legend){const s=document.createElement('span'),i=document.createElement('i');
  i.className='sw';i.style.background='var('+v+')';s.append(i,t);lg.append(s)}
 setBoard(0)})();
"""


def page(m, sections, gen_info):
    nav = ''.join(f'<a href="#{i}">{esc(t)}</a>' for i, t in (('now', 'いまどこか'), ('progress', '効いてきたこと'), ('flows', '案の流れ'),
                                                              ('ledger', 'すべての案'), ('weak', '点を落とす所'), ('probe', '時間と運'),
                                                              ('boards', '盤面を見る'), ('usage', '消費'), ('about', '作り方')))
    view = text_of(ROOT / 'problem' / 'view.js')
    rep = m['config'].get('report') or {}
    title = rep.get('title') or next((line.lstrip('# ').strip() for line in text_of(ROOT / 'problem' / 'statement.md').splitlines()
                                      if line.startswith('#')), 'AHC')
    span = f'{m["rounds"][0]["id"]}〜{m["rounds"][-1]["id"]}' if m['rounds'] else 'ラウンドなし'
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>進み具合のレポート</title><style>{CSS}</style></head><body><main>
<header><h1>進み具合のレポート</h1><p>{esc(title)}・{esc(span)}・{esc(gen_info)}・LLM 不使用</p></header>
<nav>{nav}</nav>{''.join(sections)}</main><div id="tip" role="tooltip"></div>
<script>const FKEY={json.dumps(FEATURE['key'])};</script><script>{view}</script><script>{JS}</script></body></html>"""


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def text_of(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def band_index(v):
    if not isinstance(v, (int, float)):
        return None
    b = FEATURE['bands']
    for i, (lo, hi) in enumerate(b):
        if lo <= v and (v <= hi if i == len(b) - 1 else v < b[i + 1][0]):
            return i
    return None


def setup_feature(m):
    """Which case feature to split by: config report.feature, else the first one the adapter returned.
    Bands: config report.bands, else terciles of the observed values."""
    global SIGN
    SIGN = -1 if (m['config'].get('judge') or {}).get('direction') == 'minimize' else 1
    rep = m['config'].get('report') or {}
    allf = {}
    for r in m['rounds']:
        allf.update(r['feats'])
    key = rep.get('feature') or next((k for f in allf.values() for k, v in f.items() if isinstance(v, (int, float))), None)
    vals = sorted(f[key] for f in allf.values() if isinstance(f.get(key), (int, float))) if key else []
    bands = [tuple(b) for b in rep.get('bands') or []]
    if vals and not bands:
        q = [vals[0], vals[len(vals) // 3], vals[2 * len(vals) // 3], vals[-1]]
        if len(set(q[:3])) == 3:
            ints = all(float(v).is_integer() for v in vals)
            bands = [(q[0], q[1] - 1 if ints else q[1]), (q[1], q[2] - 1 if ints else q[2]), (q[2], q[3])]
    lab = lambda x: f'{x:g}'
    FEATURE.update(key=key if vals else None, name=rep.get('feature_name') or key or '', bands=bands if vals else [],
                   labels=[f'{lab(lo)}〜{lab(hi)}' for lo, hi in bands] if vals else [])
    m['feats'] = allf


def versions(m):
    """Sources of the bests in order: history files (prior bests) and the current best, labelled by round/job."""
    by_sha = {c['sha']: c['ref'] for c in m['ideas'].values() if c.get('sha')}
    files = sorted((ROOT / 'best' / 'history').glob('R*-prior-*.cpp'))
    out = []
    for f in files:
        from_hash = None
        try:
            import hashlib
            from_hash = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            continue
        out.append((by_sha.get(from_hash, f.stem.split('-prior-')[0] + ' の前の best'), f))
    if (ROOT / 'best' / 'main.cpp').is_file():
        b = m['best'] or {}
        out.append((f'{b.get("round", "init")}/{b.get("job", "")}'.rstrip('/'), ROOT / 'best' / 'main.cpp'))
    seen, uniq = set(), []
    for label, f in out:
        if label not in seen:
            seen.add(label)
            uniq.append((label, f))
    if len(uniq) > 5:
        step = (len(uniq) - 1) / 4
        uniq = [uniq[round(i * step)] for i in range(5)]
    return uniq


def run_boards(m, picks, scale=8):
    """Re-run the picked boards with past bests and the current best at `scale` times the time limit."""
    cfg = m['config']
    out_dir = ROOT / 'runs' / 'report'
    bindir = out_dir / 'bin'
    bindir.mkdir(parents=True, exist_ok=True)
    so = bindir / 'slowclock.so'
    subprocess.run(['gcc', '-O2', '-shared', '-fPIC', '-o', str(so), str(HERE / 'slowclock.c'), '-ldl'], check=True, capture_output=True)
    vers = versions(m)
    bins = []
    for label, src in vers:
        b = bindir / (re.sub(r'\W', '_', label) or 'best')
        cmd = [x.format(source=str(src.resolve()), binary=str(b), workspace=str(bindir), python=sys.executable) for x in cfg['solver']['build']]
        if subprocess.run(cmd, capture_output=True, text=True).returncode == 0:
            bins.append((label, b))
    if not bins:
        return []
    tasks = [(s, label, b, 1) for s, _ in picks for label, b in bins] + [(s, bins[-1][0], bins[-1][1], scale) for s, _ in picks]

    def one(t):
        seed, label, b, k = t
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fmt = {'python': sys.executable, 'problem': str((ROOT / 'problem').resolve()), 'tools': str((ROOT / 'problem' / 'tools').resolve()),
                   'workspace': str(ROOT.resolve()), 'binary': str(b), 'seed': str(seed), 'result': str(d / 'r.json'), 'workdir': str(d)}
            cmd = [x.format(**fmt) for x in cfg['judge']['command']] + ['--save-dir', str(d / 'case')]
            env = dict(os.environ)
            if k != 1:
                cmd += ['--time-scale', str(k)]
                env.update(AHC_SOLVER_PRELOAD=str(so), SLOWCLOCK_K=str(k))
            p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=cfg['judge'].get('timeout_sec', 10) * k + 30)
            r = read_json(d / 'r.json', {})
            case = d / 'case'
            if p.returncode or not (case / 'input.txt').is_file():
                return None
            return {'seed': seed, 'label': label, 'k': k, 'score': r.get('score'), 'input': text_of(case / 'input.txt'),
                    'output': text_of(case / 'output.txt')}
    par = (cfg.get('evaluation') or {}).get('parallelism') or 4
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, par)) as pool:
        rows = [r for r in pool.map(one, tasks) if r]
    (out_dir / 'boards.json').write_text(json.dumps({'picks': picks, 'rows': rows, 'at': time.strftime('%Y-%m-%d %H:%M')},
                                                    ensure_ascii=False), encoding='utf-8')
    return rows


def fv(m, s):
    """The report feature of seed s, or None."""
    return (m['feats'].get(s) or {}).get(FEATURE['key']) if FEATURE['key'] else None


def main():
    global ROOT
    ap = argparse.ArgumentParser(description='LLM なしの進み具合のレポート')
    ap.add_argument('--root', default='.')
    ap.add_argument('--boards', action='store_true', help='選んだ盤面を過去の best と 8 倍の時間で解き直して載せる')
    ap.add_argument('--out', help='既定は report/index.html')
    a = ap.parse_args()
    ROOT = Path(a.root).resolve()
    m = analyse(build())
    if not m['rounds']:
        print('ラウンドがまだ無い')
        return
    probe = probe_summary(m)
    picks = pick_boards(m, probe)
    if a.boards and picks:
        run_boards(m, picks)
    body = [section_now(m, hints(m, probe)), section_progress(m), section_flows(m), section_ledger(m), section_weak(m, picks),
            section_probe(m, probe), section_boards(m, picks), section_usage(m)]
    nlines = len(Path(__file__).read_text(encoding='utf-8').splitlines())
    gen = time.perf_counter() - T0
    body.append(section_about(m, gen, nlines))
    out = Path(a.out) if a.out else ROOT / 'report' / 'index.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(m, [b for b in body if b], time.strftime('%Y-%m-%d %H:%M') + ' 作成'), encoding='utf-8')
    print(f'{out} を書いた（{out.stat().st_size / 1024:.0f} KB、{gen:.1f} 秒）')




if __name__ == '__main__':
    main()
