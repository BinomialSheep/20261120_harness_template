#!/usr/bin/env python3
"""ハーネスの記録だけから、人間向けの HTML レポートを作る（LLM なし・標準ライブラリだけ）。

    python3 -I make_report.py           # report.html を書く
    python3 -I make_report.py --pick    # 「盤面を見る」に載せる seed を選んで表示する（cases.py boards に渡す）

読むもの（実験リポジトリは読むだけ）:
  runs/R*/{plan,result,usage}.json、runs/R*/evaluation/*/scores.csv、runs/R*/jobs/*/job.json と worker の最終報告、
  docs/RESEARCH_LOG.md（系譜・ラウンドの要点・次の狙い）、best/best.json
問題ごとに要るもの: ケースの特徴量（ここでは入力の 1 行目にある壁の数 M）と、盤面の描画（view.js）。
ソルバーを走らせた結果（cases.json）は cases.py が作る。
"""
import argparse, collections, csv, html, itertools, json, math, re, statistics, subprocess, tempfile, time
from pathlib import Path

T0 = time.perf_counter()
EXP = Path('/home/ilu/work/20261117_pracitice_ahc_harness')
HERE = Path(__file__).resolve().parent
FEATURE = {'name': '壁の数', 'key': 'M', 'bands': [(0, 9), (10, 19), (20, 30)]}
KIND = {'new': '新規', 'continue': '改良', 'combine': '組合せ', 'deep': '長時間'}
STATUS = {'adopted': '✓ 採用', 'pos': '▲ 正', 'flat': '・差なし', 'neg': '▼ 負', 'invalid': '× 無効'}
ENGINE = {'C': 'claude', 'X': 'codex', 'M': '手作業'}

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
    out = {}
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            try:
                if row.get('status') == 'ok':
                    out[int(row['seed'])] = float(row['score'])
            except (TypeError, ValueError):
                pass
    return out


def paired(base, cand, seeds=None):
    d = [cand[s] - base[s] for s in cand if s in base and (seeds is None or s in seeds)]
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
    p = EXP / 'docs/RESEARCH_LOG.md'
    text = p.read_text(encoding='utf-8') if p.exists() else ''
    lineage = {m[1]: m[2] for m in re.finditer(r'^\d+\. (R\d{4}/[AB]\d\d)：(.+)$', text, re.M)}
    table = {m[1]: {'engine': m[3].strip(), 'summary': m[5].strip()}
             for m in re.finditer(r'^\| (R\d{4}) \| ([AB]) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$', text, re.M)}
    m = re.search(r'^## (次の狙い[^\n]*)\n(.*?)(?=^## )', text, re.M | re.S)
    nxt = (m[1], [l[2:].strip() for l in m[2].splitlines() if l.startswith('- ')]) if m else ('', [])
    return lineage, table, nxt


def load_features(seeds):
    """ケースの特徴量。v0.2.0 ではアダプタが採点のついでに返す。この例では公式の gen で入力を作り直して読む。"""
    cache = HERE / 'features.json'
    feats = {int(k): v for k, v in json.loads(cache.read_text()).items()} if cache.exists() else {}
    need = sorted(set(seeds) - set(feats))
    if need:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'seeds.txt').write_text(''.join(f'{s}\n' for s in need))
            subprocess.run([EXP / 'problem/tools/bin/gen', 'seeds.txt', '--dir', 'in'], cwd=d, check=True, capture_output=True)
            for k, s in enumerate(need):
                feats[s] = int((Path(d) / 'in' / f'{k:04d}.txt').read_text().split()[1])
        cache.write_text(json.dumps(feats))
    return feats


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
    lineage, table, nxt = research_log()
    rounds, ideas = [], {}
    for rd in sorted((EXP / 'runs').glob('R[0-9][0-9][0-9][0-9]')):
        if not (rd / 'result.json').exists():
            continue
        res = json.loads((rd / 'result.json').read_text())
        plan = json.loads((rd / 'plan.json').read_text())
        usage = json.loads((rd / 'usage.json').read_text()) if (rd / 'usage.json').exists() else {}
        rid = rd.name
        R = {'id': rid, 'num': int(rid[1:]), 'mode': res['mode'], 'start': res['seed_start'], 'count': res['seed_count'],
             'promoted': res.get('promoted'), 'scores': {p.parent.name: read_scores(p) for p in rd.glob('evaluation/*/scores.csv')},
             'usage': usage, 'engine': table.get(rid, {}).get('engine', ''), 'summary': table.get(rid, {}).get('summary', ''),
             'ideas': []}
        rounds.append(R)
        texts = [i['idea'] for i in plan['ideas']]
        df = collections.Counter(s for t in texts for s in set(sentences(t)))
        for i in plan['ideas']:
            c = next((c for c in res['candidates'] if c['id'] == i['id']), {})
            jd = rd / 'jobs' / i['id']
            job = json.loads((jd / 'job.json').read_text()) if (jd / 'job.json').exists() else {}
            common = {s for s in sentences(i['idea']) if len(texts) > 1 and df[s] > max(1, 0.3 * len(texts))}
            run = job.get('agent_run') or {}
            ref = f'{rid}/{i["id"]}'
            ideas[ref] = {
                'ref': ref, 'round': rid, 'num': R['num'], 'mode': R['mode'], 'kind': i.get('kind', ''),
                'parents': [p for p in i.get('parents', []) if p != 'best'], 'text': i['idea'], 'common': common,
                'valid': bool(c.get('valid')), 'error': c.get('error', ''), 'effect': c.get('effect'), 'se': c.get('standard_error'),
                'better': c.get('better_cases'), 'worse': c.get('worse_cases'), 'n': c.get('n'),
                'adopted': res.get('promoted') == i['id'],
                'engine': job.get('engine') or ENGINE.get(R['engine'][:1], ''), 'model': job.get('model'), 'effort': job.get('effort'),
                'minutes': run.get('seconds', 0) / 60 if run.get('seconds') else None, 'outcome': job.get('agent_outcome'),
                'usage': job.get('usage') or {}, 'report': final_report(jd),
                'lineage': lineage.get(ref, ''),
            }
            R['ideas'].append(ref)
    best = json.loads((EXP / 'best/best.json').read_text())
    return {'rounds': rounds, 'ideas': ideas, 'next': nxt, 'best': best}


def analyse(m):
    rounds, ideas = m['rounds'], m['ideas']
    feats = load_features({s for r in rounds for sc in r['scores'].values() for s in sc})
    bands = FEATURE['bands']
    band_seeds = lambda r, lo, hi: {s for s in range(r['start'], r['start'] + r['count']) if lo <= feats.get(s, -1) <= hi}
    for r in rounds:
        r['band_sets'] = [band_seeds(r, lo, hi) for lo, hi in bands]
        half = set(range(r['start'], r['start'] + r['count'] // 2))
        base = r['scores'].get('baseline', {})
        for ref in r['ideas']:
            c = ideas[ref]
            sc = r['scores'].get(c['ref'].split('/')[1], {})
            c['bands'] = [paired(base, sc, bs) if c['valid'] else None for bs in r['band_sets']]
            c['halves'] = [paired(base, sc, half), paired(base, sc, set(sc) - half)] if c['valid'] else [None, None]
            z = c['effect'] / c['se'] if c['valid'] and c['se'] else None
            c['z'] = z
            c['status'] = ('invalid' if not c['valid'] else 'adopted' if c['adopted'] else
                           'pos' if z is not None and z >= 2 else 'neg' if z is not None and z <= -2 else 'flat')
    # 題名：系譜の1行 → ラウンドの要点（B） → 案の文章のうち、そのラウンドで共通でない最初の文
    generic = re.compile(r'^(親は|親 `|parent_1(?:\.cpp)? は|二つの親は|parent_2 は)')
    for c in ideas.values():
        r = next(r for r in rounds if r['id'] == c['round'])
        uniq = [s for s in sentences(c['text']) if s not in c['common']]
        c['uniq'] = ''.join(uniq)
        own = next((s for s in uniq if not generic.match(s)), uniq[0] if uniq else c['text'])
        if c['lineage']:
            c['title'] = shorten(re.sub(r'（[^（）]*）$', '', sentences(c['lineage'])[0]))
        elif c['mode'] == 'B' and r['summary']:
            t = re.sub(r'\s*[+−-]\d[\d.,]*(?:±[\d.]+)?(?:（[^）]*）)?$', '', r['summary'].split('。')[0])
            c['title'] = shorten(t) if len(t) >= 6 else shorten(own)
        else:
            c['title'] = shorten(own)
    # 案の流れ：明示の親（best 以外）か、文章がほぼ同じ前の案をたどる
    vecs = dict(zip(ideas, similarity_vectors([c['uniq'] or c['text'] for c in ideas.values()])))
    order = sorted(ideas, key=lambda k: (ideas[k]['num'], k))
    for k in order:
        c = ideas[k]
        earlier = [j for j in order if ideas[j]['num'] < c['num'] and not ideas[j]['adopted']]
        sims = sorted(((cos(vecs[k], vecs[j]), j) for j in earlier), reverse=True)
        c['similar'] = [(s, j) for s, j in sims[:3] if s >= 0.12]
        explicit = next((p for p in c['parents'] if p in ideas and not ideas[p]['adopted']), None)
        c['link'] = (sims[0][1] if sims and sims[0][0] >= 0.9 else explicit or
                     (sims[0][1] if sims and sims[0][0] >= 0.5 else None))
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
        if ideas[k]['kind'] == 'combine' and ideas[k]['family'] != k and not ideas[k]['lineage']:
            ideas[k]['title'] = '↳ ' + ideas[ideas[k]['family']]['title'].lstrip('↳ ')
    m['feats'] = feats
    return m


# ---------------------------------------------------------------- 自動で拾う点（決まった規則だけ）

def current_scores(m):
    last = m['rounds'][-1]
    return last, last['scores'][last['promoted'] or 'baseline']


def residuals(m):
    last, sc = current_scores(m)
    by = collections.defaultdict(list)
    for s, v in sc.items():
        by[m['feats'][s]].append(v)
    mean = {k: fmean(v) for k, v in by.items()}
    return last, sc, mean, {s: v - mean[m['feats'][s]] for s, v in sc.items()}


def probe_summary():
    p = HERE / 'cases.json'
    data = json.loads(p.read_text()) if p.exists() else {}
    rows = data.get('probe', [])
    if not rows:
        return None
    one = collections.defaultdict(dict)
    by = collections.defaultdict(dict)
    for r in rows:
        (one[r['rep']] if r['k'] == 1 else by[r['k']])[r['seed']] = r['score']
    seeds = sorted(one[0])
    reps = sorted(one)
    base = {s: fmean(one[q][s] for q in reps) for s in seeds}
    out = {'seeds': seeds, 'reps': len(reps), 'time': [], 'bestof': []}
    for k in sorted(list(by) + [1]):
        cur = by[k] if k != 1 else {s: base[s] for s in seeds}
        d = {s: cur[s] - base[s] for s in seeds}
        out['time'].append((k, d))
    for k in range(1, len(reps) + 1):
        d = {s: fmean(max(one[q][s] for q in sub) for sub in itertools.combinations(reps, k)) - base[s] for s in seeds}
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
    adopted = [ideas[f"{r['id']}/{r['promoted']}"] for r in rounds if r['promoted']]
    last5 = [r for r in rounds[-5:]]
    prev5 = [r for r in rounds[-10:-5]]
    s = lambda rs: sum(ideas[f"{r['id']}/{r['promoted']}"]['effect'] for r in rs if r['promoted'])
    out.append(('伸び方', f'直近5ラウンド（{last5[0]["id"]}〜{last5[-1]["id"]}）の採用は合計 {fe(s(last5), 2)}。'
                f'その前の5ラウンドは {fe(s(prev5), 2)}。', '#progress', None))
    split = [c for c in ideas.values() if c['valid'] and not c['adopted'] and c['num'] >= 6
             and all(c['bands']) and max(b[0] / b[1] for b in c['bands']) >= 2.5 and min(b[0] / b[1] for b in c['bands']) <= -2]
    if split:
        c = max(split, key=lambda c: max(b[0] / b[1] for b in c['bands']))
        bi = max(range(len(c['bands'])), key=lambda k: c['bands'][k][0] / c['bands'][k][1])
        bj = min(range(len(c['bands'])), key=lambda k: c['bands'][k][0] / c['bands'][k][1])
        lab = lambda k: f'{FEATURE["bands"][k][0]}〜{FEATURE["bands"][k][1]}'
        others = '、'.join(x['ref'] for x in split if x is not c)
        out.append(('帯で効き方が割れた案', f'{c["ref"]}（{c["title"]}）は全体 {fe(c["effect"])} で採らなかった。'
                    f'{FEATURE["name"]} {lab(bi)} では {fe(c["bands"][bi][0])}（z {c["bands"][bi][0] / c["bands"][bi][1]:.1f}）、'
                    f'{lab(bj)} では {fe(c["bands"][bj][0])}。' + (f'ほかに {others}。' if others else ''),
                    '#i-' + c['ref'].replace('/', '-'), '帯ごとに使い分けたら？'))
    leftover = [c for c in ideas.values() if c['status'] == 'pos' and not c['children']
                and not any(c['ref'] in x['parents'] for x in ideas.values())]
    if leftover:
        c = max(leftover, key=lambda c: c['num'])
        more = len(leftover) - 1
        out.append(('正だったが、その後使っていない案', f'{c["ref"]}（{c["title"]}）は {fe(c["effect"])} ± {c["se"]:.3f}（z {c["z"]:.1f}）。'
                    f'これ以降、親にも載せ直しにも使っていない。' + (f'同じ条件の案がほかに {more} 件。' if more else ''),
                    '#i-' + c['ref'].replace('/', '-'), '今の best に載せ直したら？'))
    weak = [c for c in adopted if c['num'] > 1 and c['z'] is not None and c['z'] < 2.5]
    if weak:
        out.append(('誤差と見分けにくい採用', '、'.join(f'{c["ref"]}（z {c["z"]:.1f}、前半 {fe(c["halves"][0][0])}・後半 {fe(c["halves"][1][0])}）' for c in weak)
                    + '。10案の首位は、同じ seed で測ると高めに出やすい。', '#progress', '未使用の seed で測り直したら？'))
    pts = transfer_points(m)
    if pts:
        a, b = fmean(p[2] for p in pts), fmean(p[3] for p in pts)
        out.append(('載せ直したときの効き目', f'単独で正だった案を次の best に載せ直した {len(pts)} 件では、'
                    f'単独のとき平均 {fe(a)}、載せ直して {fe(b)}（{b / a:.0%}）。載せ直しても 0 を上回ったのは {sum(p[3] > 0 for p in pts)} 件。',
                    '#flows', None))
    last, sc, mean, res = residuals(m)
    worst = sorted(res, key=res.get)[:3]
    out.append(('同じ壁の数なのに点が低い盤面', '、'.join(f'seed {s}（{FEATURE["key"]}={m["feats"][s]}、{res[s]:+.1f}）'.replace('-', '−') for s in worst)
                + f'。{last["id"]} の評価で、同じ{FEATURE["name"]}の盤面の平均との差。', '#weak', '何が違うか、盤面を見る'))
    if probe:
        t = {k: mean_se(d.values())[0] for k, d in probe['time']}
        b = {k: mean_se(d.values())[0] for k, d in probe['bestof']}
        kmax = max(t)
        out.append(('時間と運の余地', f'現 best に {kmax:g} 倍の時間を与えると {fe(t[kmax], 2)}、2倍で {fe(t.get(2), 2)}。'
                    f'1倍のまま {len(b)} 回走らせた最良は {fe(b[len(b)], 2)}（{len(probe["seeds"])} 盤面）。', '#probe', None))
    a_rounds = [r for r in rounds if r['mode'] == 'A' and five_hour(r) is not None]
    b_rounds = [r for r in rounds if r['mode'] == 'B' and five_hour(r) is not None]
    if a_rounds and b_rounds:
        ra, rb = a_rounds[-1], b_rounds[-1]
        ea = ideas[f"{ra['id']}/{ra['promoted']}"]['effect'] if ra['promoted'] else 0
        eb = ideas[f"{rb['id']}/{rb['promoted']}"]['effect'] if rb['promoted'] else 0
        out.append(('消費', f'直近の A（{ra["id"]}）は codex の5時間枠を {five_hour(ra):g} ポイント使い、採用 {fe(ea)}。'
                    f'直近の B（{rb["id"]}）は {five_hour(rb):g} ポイントで {fe(eb)}。', '#usage', None))
    return out


def five_hour(r):
    lim = (r['usage'] or {}).get('worker_limits') or []
    v = [l['last'] - l['first'] for l in lim if l.get('window') == 'five_hour']
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
    rounds = [r for r in m['rounds'] if r['num'] >= start]
    ideas = m['ideas']
    names = ['全体'] + [f'{lo}〜{hi}' for lo, hi in FEATURE['bands']]
    cls = ['ink', 'o1', 'o2', 'o3']
    series = [[0.0] for _ in names]
    for r in rounds:
        c = ideas.get(f"{r['id']}/{r['promoted']}") if r['promoted'] else None
        add = [c['effect']] + [b[0] for b in c['bands']] if c else [0.0] * len(names)
        for s, a in zip(series, add):
            s.append(s[-1] + a)
    W, H, L, Rr, T, B = 1060, 320, 52, 132, 14, 44
    n = len(rounds)
    x = lambda i: L + i * (W - L - Rr) / n
    lo, hi = min(0, min(min(s) for s in series)), max(max(s) for s in series)
    tk = ticks(lo, hi)
    lo, hi = min(lo, tk[0]), max(hi, tk[-1])
    y = lambda v: T + (hi - v) * (H - T - B) / ((hi - lo) or 1)
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="chart" role="img" aria-label="採用した変更の効果の積み上げ">',
           axis_y(tk, L, W - Rr, y)]
    for i, r in enumerate([None] + rounds):
        label = r['id'][1:].lstrip('0') if r else '前'
        out.append(f'<text x="{x(i):.1f}" y="{H - B + 18}" class="tick" text-anchor="middle">{esc(label)}</text>')
    out.append(f'<text x="{(L + W - Rr) / 2:.1f}" y="{H - 6}" class="axt" text-anchor="middle">ラウンド</text>')
    for s, c in zip(series, cls):
        d = f'M{x(0):.1f},{y(s[0]):.1f}' + ''.join(f'H{x(i):.1f}V{y(v):.1f}' for i, v in enumerate(s) if i)
        out.append(f'<path d="{d}" class="ln {c}"/>')
    labels = end_labels([(y(s[-1]), f'{nm}　{fe(s[-1], 2)}') for s, nm in zip(series, names)])
    for (yl, yv, t) in labels:
        out.append(f'<line x1="{W - Rr + 2}" x2="{W - Rr + 8}" y1="{yv:.1f}" y2="{yl:.1f}" class="lead"/>'
                   f'<text x="{W - Rr + 11}" y="{yl + 4:.1f}" class="lab">{esc(t)}</text>')
    for i, r in enumerate(rounds, 1):
        c = ideas.get(f"{r['id']}/{r['promoted']}") if r['promoted'] else None
        tip = (f'{r["id"]}（{r["mode"]}）\n' + (f'採用 {c["ref"]}：{c["title"]}\n' + '\n'.join(
            f'{nm} {fe(s[i], 2)}（このラウンド {fe(s[i] - s[i - 1], 2)}）' for s, nm in zip(series, names))
            if c else '採用なし'))
        out.append(f'<rect x="{x(i - 1) + 1:.1f}" y="{T}" width="{x(i) - x(i - 1) - 2:.1f}" height="{H - T - B}" class="hit" data-tip="{esc(tip)}" tabindex="0"/>')
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
    feats = m['feats']
    W, H, L, R, T, B = 1060, 360, 52, 16, 14, 44
    ys = list(sc.values())
    tk = ticks(min(ys), max(ys), 6)
    lo, hi = tk[0], tk[-1]
    fmax = max(feats[s] for s in sc)
    x = lambda v: L + (v + 0.5) * (W - L - R) / (fmax + 1)
    y = lambda v: T + (hi - v) * (H - T - B) / (hi - lo)
    out = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="chart" id="weak-svg" role="img" aria-label="{esc(FEATURE["name"])}ごとの得点">',
           axis_y(tk, L, W - R, y)]
    for v in range(0, fmax + 1, 5):
        out.append(f'<text x="{x(v):.1f}" y="{H - B + 18}" class="tick" text-anchor="middle">{v}</text>')
    out.append(f'<text x="{(L + W - R) / 2:.1f}" y="{H - 6}" class="axt" text-anchor="middle">{esc(FEATURE["name"])}（{FEATURE["key"]}）</text>')
    pts = []
    for s, v in sc.items():
        j = ((s * 2654435761) % 1000 / 1000 - 0.5) * 0.6
        pts.append([s, feats[s], v, round(x(feats[s] + j), 1), round(y(v), 1)])
        out.append(f'<circle cx="{x(feats[s] + j):.1f}" cy="{y(v):.1f}" r="2.2" class="pt"/>')
    ks = sorted(mean)
    out.append('<path d="' + ''.join(f'{"M" if i == 0 else "L"}{x(k):.1f},{y(mean[k]):.1f}' for i, k in enumerate(ks)) + '" class="ln ink"/>')
    out.append(f'<text x="{x(ks[-1]) - 4:.1f}" y="{y(mean[ks[-1]]) - 10:.1f}" class="lab" text-anchor="end">同じ{esc(FEATURE["key"])}の平均</text>')
    key = FEATURE['key']
    for s, why in picks:
        if s in sc:
            tip = f'{sc[s]:g} 点（seed {s}）\n{key}={feats[s]}、平均との差 {res[s]:+.1f}\n{why}\nクリックで盤面を開く'.replace('-', '−')
            out.append(f'<circle cx="{x(feats[s]):.1f}" cy="{y(sc[s]):.1f}" r="6" class="dot s2 ring pick" data-seed="{s}" '
                       f'data-tip="{esc(tip)}" tabindex="0"/>'
                       f'<text x="{x(feats[s]) + 9:.1f}" y="{y(sc[s]) + 4:.1f}" class="lab">{s}</text>')
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
    rounds, ideas, best = m['rounds'], m['ideas'], m['best']
    last = rounds[-1]
    lc = ideas.get(f"{last['id']}/{last['promoted']}") if last['promoted'] else None
    valid = sum(c['valid'] for c in ideas.values())
    adopted = sum(c['adopted'] for c in ideas.values())
    tiles = [
        ('現 best', best.get('round', '') + '/' + best.get('job', ''), f'本評価の平均 {best.get("score", 0):.2f}（seed {last["start"]}〜{last["start"] + last["count"] - 1}）'),
        ('直近の採用', fe(lc['effect']) if lc else '—', (f'± {lc["se"]:.3f}（z {lc["z"]:.1f}）・{lc["ref"]}' if lc else '採用なし')),
        ('試した案', f'{len(ideas)}', f'有効 {valid}・採用 {adopted}・{len(rounds)} ラウンド'),
    ]
    kp = ''.join(f'<div class="card kpi"><div class="label">{esc(a)}</div><div class="value">{esc(b)}</div><div class="sub">{esc(c)}</div></div>' for a, b, c in tiles)
    hh = ''.join(f'<div class="card hint"><h3>{esc(t)}</h3><p>{esc(body)}</p>'
                 + (f'<p class="ask">問い：{esc(ask)}</p>' if ask else '') + f'<a class="go" href="{link}">根拠を見る →</a></div>'
                 for t, body, link, ask in hs)
    nt, nl = m['next']
    nxt = (f'<details class="card quote"><summary>オーケストレーターが書いた「{esc(nt)}」（研究の記録から引用）</summary><ul>'
           + ''.join(f'<li>{esc(l)}</li>' for l in nl) + '</ul></details>') if nl else ''
    body = (f'<div class="kpis">{kp}</div><h3 class="sub">記録から拾った点</h3>'
            f'<p class="lead">決まった規則で数字を拾い、問いの形にしたもの。解釈はしていない。</p><div class="hints">{hh}</div>{nxt}')
    return sec('now', 'いまどこにいるか', '記録だけ', '', body)


def section_progress(m):
    ideas, rounds = m['ideas'], m['rounds']
    eff = [(r['num'], ideas[f"{r['id']}/{r['promoted']}"]['effect']) for r in rounds if r['promoted']]
    later = lambda lim: next((n for n, _ in eff if all(e <= lim for k, e in eff if k >= n)), eff[-1][0])
    starts = sorted({eff[1][0] if len(eff) > 1 else eff[0][0], later(1.0), later(0.25)})
    charts, radios = [], []
    for k, st in enumerate(starts):
        svg, names, series, rs = chart_progress(m, st)
        rid = f'R{st:04d}'
        radios.append(f'<label><input type="radio" name="pw" value="{k}" {"checked" if k == 1 or len(starts) == 1 else ""}> {rid} から</label>')
        charts.append(f'<div class="pane-w" data-k="{k}">{svg}</div>')
    legend = ('<div class="legend"><span><i class="sw ink"></i>全体</span>' + ''.join(
        f'<span><i class="sw o{i + 1}"></i>{FEATURE["key"]} {lo}〜{hi}</span>' for i, (lo, hi) in enumerate(FEATURE['bands'])) + '</div>')
    rows = []
    for r in rounds:
        if not r['promoted']:
            continue
        c = ideas[f"{r['id']}/{r['promoted']}"]
        h = c['halves']
        rows.append([f'<a href="#i-{c["ref"].replace("/", "-")}">{c["ref"]}</a>', esc(c['lineage'] or c['title']),
                     fe(c['effect']), f'{c["se"]:.3f}', f'{c["z"]:.1f}' if c['z'] else '—',
                     (f'{fe(h[0][0])} / {fe(h[1][0])}' if h[0] and h[1] else '—'),
                     ' '.join(f'<span class="cell {zclass(*b[:2]) if b else "z0"}">{fe(b[0]) if b else "—"}</span>' for b in c['bands'])])
    tbl = table_html([('採用', 0), ('変更（研究の記録の系譜から）', 0), ('効果', 1), ('標準誤差', 1), ('z', 1), ('前半 / 後半', 1),
                      (' / '.join(f'{lo}〜{hi}' for lo, hi in FEATURE['bands']), 0)], rows, 'lineage')
    lead = ('採用した変更の効果（そのラウンドの seed で、直前の best と対にした平均の差）を足し上げた。'
            f'線は {FEATURE["name"]}の帯ごとの内訳。ラウンドごとに seed が違うので、足し上げは目安。')
    body = (f'<div class="card"><div class="seg">{"".join(radios)}</div>{legend}{"".join(charts)}</div>'
            f'<details class="card more"><summary>表で見る（効果・前半と後半・帯ごと）</summary>{tbl}'
            '<p class="foot">前半 / 後半：評価の seed を前後半に分けた効果。首位を選んだ seed で測った値なので、片方だけ高いときは偶然の可能性がある。</p></details>')
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
        if c['num'] >= 6:
            groups[c['kind']].append(c)
    for k in ('new', 'continue', 'combine', 'deep'):
        v = groups.get(k)
        if not v:
            continue
        val = [c for c in v if c['valid']]
        rows.append([KIND[k], str(len(v)), str(sum(c['adopted'] for c in v)), str(sum(c['status'] == 'pos' for c in v)),
                     str(sum(c['status'] == 'neg' for c in v)), str(len(v) - len(val)),
                     fe(statistics.median(c['effect'] for c in val)) if val else '—'])
    return table_html([('種類', 0), ('件数', 1), ('採用', 1), ('正', 1), ('負', 1), ('無効', 1), ('効果の中央値', 1)], rows)


def section_ledger(m):
    ideas = m['ideas']
    order = sorted(ideas.values(), key=lambda c: (-c['num'], -(c['z'] if c['z'] is not None else -99)))
    rows = []
    for c in order:
        b = ''.join(f'<span class="cell {zclass(*x[:2]) if x else "z0"}" title="{FEATURE["key"]} {lo}〜{hi}">{fe(x[0], 2 if abs(x[0]) < 10 else 1) if x else "—"}</span>'
                    for x, (lo, hi) in zip(c['bands'], FEATURE['bands']))
        err = clean_report(str(c['error'] or '無効'))
        eff = (f'{fe(c["effect"])}<small> ± {c["se"]:.3f}</small>' if c['valid'] else f'<small title="{esc(err)}">{esc(shorten(err, 16))}</small>')
        meta = ([f'無効の理由：{shorten(err, 120)}'] if not c['valid'] else []) + [f'{KIND.get(c["kind"], c["kind"])}', c['engine'] or '—'] + ([c['effort']] if c['effort'] else []) \
            + ([f'{c["minutes"]:.0f}分'] if c['minutes'] else []) + ([f'途中終了（{c["outcome"]}）'] if c['outcome'] not in (None, 'ok') else []) \
            + ([f'出力 {fk(c["usage"].get("out"))}・呼び出し {c["usage"].get("calls")}回'] if c['usage'].get('out') else [])
        rel = []
        if c['parents']:
            rel.append('親：' + '、'.join(f'<a href="#i-{p.replace("/", "-")}">{p}</a>' for p in c['parents']))
        if c['children']:
            rel.append('その後：' + '、'.join(f'<a href="#i-{k.replace("/", "-")}">{k}</a>（{fe(ideas[k]["effect"]) if ideas[k]["valid"] else "無効"}）' for k in c['children']))
        if c['similar']:
            rel.append('文章が近い前の案：' + '、'.join(f'<a href="#i-{j.replace("/", "-")}">{j}</a>（{fe(ideas[j]["effect"]) if ideas[j]["valid"] else "無効"}）' for s, j in c['similar']))
        text = ''.join(f'<span class="{"common" if s in c["common"] else ""}">{esc(s)}</span>' for s in sentences(c['text']))
        rep = f'<h4>worker の最終報告</h4><pre>{esc(c["report"])}</pre>' if c['report'] else '<p class="foot">worker の最終報告は残っていない（手作業など）。</p>'
        half = c['halves']
        stat = (f'良くなったケース {c["better"]}・悪くなったケース {c["worse"]}・前半 {fe(half[0][0])}・後半 {fe(half[1][0])}'
                if c['valid'] and half[0] and half[1] and c['better'] is not None else '')
        rows.append(
            f'<details class="idea" id="i-{c["ref"].replace("/", "-")}" data-kind="{c["kind"]}" data-status="{c["status"]}" data-mode="{c["mode"]}" data-num="{c["num"]}" data-z="{c["z"] if c["z"] is not None else -99}">'
            f'<summary><span class="ref">{c["ref"]}</span><span class="tt">{esc(c["title"])}</span><span class="kd">{KIND.get(c["kind"], c["kind"])}</span>'
            f'<span class="fp">{forest(c["effect"], c["se"])}</span><span class="ef">{eff}</span><span class="bd">{b}</span>'
            f'<span class="st s-{c["status"]}">{STATUS[c["status"]]}</span></summary>'
            f'<div class="body"><p class="meta">{esc("・".join(meta))}</p>' + (f'<p class="meta">{esc(stat)}</p>' if stat else '')
            + (f'<p class="meta">{"　".join(rel)}</p>' if rel else '')
            + f'<h4>案（灰色はそのラウンドの共通文）</h4><p class="text">{text}</p>{rep}</div></details>')
    head = (f'<div class="ihead"><span>案</span><span>題名</span><span>種類</span><span class="fp">{forest_axis()}</span><span>効果</span>'
            f'<span class="bd">' + ''.join(f'<span>{lo}〜{hi}</span>' for lo, hi in FEATURE['bands']) + '</span><span>判定</span></div>')
    filt = ('<div class="filters"><input type="search" id="q" placeholder="案の文章・報告を検索（例：高速化、焼きなまし）">'
            '<select id="fk"><option value="">種類：すべて</option>' + ''.join(f'<option value="{k}">{v}</option>' for k, v in KIND.items()) + '</select>'
            '<select id="fs"><option value="">判定：すべて</option>' + ''.join(f'<option value="{k}">{v}</option>' for k, v in STATUS.items()) + '</select>'
            '<select id="fo"><option value="new">新しい順</option><option value="z">z の大きい順</option></select><span id="cnt"></span></div>')
    lead = ('すべての案と評価の結果。行を開くと、案の全文・worker の最終報告・親子・文章が近い前の案が出る。'
            f'帯の3列は {FEATURE["name"]}ごとの効果で、色は z。効果の図は 0 の近くを広げた目盛りで、線は 95% 区間。')
    return sec('ledger', 'すべての案', '記録だけ＋特徴量', lead, f'<div class="card ledger">{filt}{head}<div class="ilist">{"".join(rows)}</div></div>')


def pick_boards(m, probe):
    last, sc, mean, res = residuals(m)
    feats = m['feats']
    picks = []
    for lo, hi in FEATURE['bands']:
        s = min((s for s in sc if lo <= feats[s] <= hi), key=res.get)
        picks.append((s, f'{FEATURE["key"]} {lo}〜{hi} で、同じ{FEATURE["key"]}の平均から最も低い'))
    if probe:
        t = dict(probe['time'])
        d = t[max(t)]
        s = max((s for s in d if s not in {p for p, _ in picks}), key=d.get)
        picks.append((s, f'時間を {max(t):g} 倍にして最も伸びた（{d[s]:+.1f}）'.replace('-', '−')))
    mid = sorted(sc, key=lambda s: abs(res[s]))
    s = next(s for s in mid if 12 <= feats[s] <= 18 and s not in {p for p, _ in picks})
    picks.append((s, '平均どおりの盤面（比べる基準）'))
    return picks


def section_weak(m, picks):
    svg, pts, last = chart_weak(m, picks)
    _, sc, mean, res = residuals(m)
    rows = []
    for (lo, hi), bs in zip(FEATURE['bands'], last['band_sets']):
        v = [sc[s] for s in bs if s in sc]
        rows.append([f'{lo}〜{hi}', str(len(v)), f'{fmean(v):.2f}', f'{min(v):g}', f'{max(v):g}'])
    tbl = table_html([(f'{FEATURE["key"]} の帯', 0), ('件数', 1), ('平均', 1), ('最低', 1), ('最高', 1)], rows)
    worst = sorted(res, key=res.get)[:12]
    wl = table_html([('seed', 0), (FEATURE['key'], 1), ('得点', 1), ('同じ' + FEATURE['key'] + 'の平均との差', 1)],
                    [[str(s), str(m['feats'][s]), f'{sc[s]:g}', f'{res[s]:+.1f}'.replace('-', '−')] for s in worst])
    lead = (f'現 best の、{last["id"]} の評価（{len(sc)} 件）での得点。点は1盤面、線は同じ{FEATURE["name"]}の平均。'
            '橙の点は「盤面を見る」に載せた盤面（自動で選んだ）。')
    body = (f'<div class="card">{svg}<script type="application/json" id="weak-data">{json.dumps(pts)}</script></div>'
            f'<div class="two"><div class="card"><h3 class="sub">帯ごと</h3>{tbl}</div>'
            f'<div class="card"><h3 class="sub">平均から最も低い12盤面</h3>{wl}</div></div>')
    return sec('weak', 'どこで点を落としているか', '記録だけ＋特徴量', lead, body)


def section_probe(m, probe):
    if not probe:
        return ''
    svg = chart_probe(probe)
    feats = m['feats']
    rows = []
    for name, key in (('1回を長く', 'time'), ('回数を増やして最良', 'bestof')):
        for k, d in probe[key]:
            if k == 1:
                continue
            cells = [f'{name}：{k:g}' + ('倍' if key == 'time' else '回')]
            mu, se = mean_se(d.values())
            cells.append(f'{fe(mu, 2)} <small>± {se:.2f}</small>')
            for lo, hi in FEATURE['bands']:
                v = [d[s] for s in d if lo <= feats[s] <= hi]
                bm, bse = mean_se(v)
                cells.append(f'<span class="cell {zclass(bm, bse)}">{fe(bm, 2)}</span>')
            rows.append(cells)
    tbl = table_html([('条件', 0), ('全体', 1)] + [(f'{lo}〜{hi}', 1) for lo, hi in FEATURE['bands']], rows)
    lead = (f'現 best を、{probe["seeds"][0]}〜{probe["seeds"][-1]} の {len(probe["seeds"])} 盤面で走らせ直した。'
            '時間は、ソルバーから見える時計を遅く進めて伸ばした（ソースは変えていない）。'
            f'基準は1倍の {probe["reps"]} 回の平均。「回数を増やして最良」は、その {probe["reps"]} 回から選んだ最良の期待値。')
    body = (f'<div class="two"><div class="card">{svg}</div><div class="card"><h3 class="sub">帯ごと</h3>{tbl}'
            '<p class="foot">時間を伸ばして伸びる分は探索の量、回数で伸びる分は運の幅の目安。どちらも、同じ盤面で既に届く点があることを示す。</p></div></div>')
    return sec('probe', '時間と運の余地', 'ソルバーを実行', lead, body)


def section_boards(m, picks):
    p = HERE / 'cases.json'
    data = json.loads(p.read_text()) if p.exists() else {}
    rows = data.get('boards', [])
    if not rows:
        return ''
    ideas = m['ideas']
    why = dict(picks)
    boards = {}
    for r in rows:
        b = boards.setdefault(r['seed'], {'seed': r['seed'], 'input': r['input'], 'why': why.get(r['seed'], ''), 'runs': {}})
        key = r['ref'] + ('' if r['k'] == 1 else f' ×{r["k"]:g}')
        t = ideas.get(r['ref'], {}).get('title', '')
        b['runs'][key] = {'score': r['score'], 'output': r['output'], 'label': f'{key}　{shorten(t, 26)}' + ('' if r['k'] == 1 else f'（時間 {r["k"]:g} 倍）')}
    order = [s for s, _ in picks if s in boards] + [s for s in boards if s not in why]
    blist = []
    for s in order:
        b = boards[s]
        b['M'] = m['feats'].get(s)
        blist.append(b)
    chips = ''.join(f'<button class="chip" data-i="{i}">seed {b["seed"]}<small>{FEATURE["key"]}={b["M"]}・{esc(shorten(b["why"], 22))}</small></button>' for i, b in enumerate(blist))
    pane = lambda k: (f'<figure class="pane" data-pane="{k}"><select class="ver" aria-label="版"></select>'
                      f'<canvas class="board" width="360" height="360"></canvas><figcaption class="stats"></figcaption></figure>')
    legend = ('<div class="legend"><span><i class="sw b1"></i>新しく5マス以上塗った発射</span><span><i class="sw b2"></i>3〜4マス</span>'
              '<span><i class="sw s2"></i>2マス以下</span><span><i class="sw s1"></i>直前の発射</span>'
              '<span><i class="sw line path2"></i>余分な移動（発射の間に2歩以上）・○ 待ち</span></div>')
    lead = ('自動で選んだ盤面を、系譜の節目の版と、現 best に8倍の時間を与えた版で解き直した。'
            '左右で版を選び、下の棒で手順を進める。発射1回が新しく塗ったマスを1本の帯で描く（この描き方だけは問題ごとに書く）。')
    data = json.dumps(blist, ensure_ascii=False).replace('</', '<\\/')
    vers = list(dict.fromkeys(k for b in blist for k in b['runs']))
    score_table = table_html([('seed', 0), (FEATURE['key'], 1)] + [(v, 1) for v in vers],
                             [[str(b['seed']), str(b['M'])] + [str(b['runs'][v]['score']) if v in b['runs'] else '—' for v in vers] for b in blist])
    body = (f'<div class="card viewer"><div class="chips">{chips}</div><p class="why" id="why"></p>'
            f'<div class="panes">{pane(0)}{pane(1)}</div>'
            '<div class="controls"><button id="play" type="button">▶ 再生</button><input type="range" id="t" min="0" max="250" value="250" aria-label="手順">'
            f'<span id="tl"></span></div>{legend}</div>'
            f'<script type="application/json" id="boards-data">{data}</script>'
            f'<details class="card more"><summary>表で見る（盤面 × 版の得点）</summary>{score_table}</details>')
    return sec('boards', '盤面を見る', 'ソルバーを実行＋問題専用の描画', lead, body)


def section_usage(m):
    rows = []
    mx = max(((r['usage'].get('workers_total') or {}).get('out', 0) + sum(v.get('out', 0) for v in (r['usage'].get('orchestrator') or {}).values()))
             for r in m['rounds']) or 1
    for r in m['rounds']:
        u = r['usage'] or {}
        w = (u.get('workers_total') or {})
        o = u.get('orchestrator') or {}
        oo = sum(v.get('out', 0) for v in o.values())
        wo = w.get('out', 0)
        cr = w.get('cr', 0) + sum(v.get('cr', 0) for v in o.values())
        nw = len(r['ideas'])
        engines = collections.Counter((c['engine'] or '—') + (f' {c["effort"]}' if c['effort'] else '') for c in (m['ideas'][k] for k in r['ideas']))
        miss = u.get('workers_without_usage', 0)
        bar = (f'<span class="bar"><i class="s1" style="width:{wo / mx * 100:.1f}%"></i><i class="s2" style="width:{oo / mx * 100:.1f}%"></i></span>')
        c = m['ideas'].get(f"{r['id']}/{r['promoted']}") if r['promoted'] else None
        wmin = max((m['ideas'][k]['minutes'] or 0) for k in r['ideas'])
        week = [l['last'] - l['first'] for l in (u.get('worker_limits') or []) if l.get('window') == 'seven_day']
        rows.append([r['id'], r['mode'], esc('、'.join(f'{k} ×{v}' if v > 1 else k for k, v in engines.items())),
                     bar, (fk(wo) if wo else '—') + (f'<small>（{miss}/{nw} 体は記録なし）</small>' if miss and wo else ''), fk(oo),
                     fk(cr), f'{five_hour(r):g}' if five_hour(r) is not None else '—', f'{sum(week):g}' if week else '—',
                     f'{wmin:.0f}' if wmin else '—', fe(c['effect']) if c else '—'])
    tbl = table_html([('ラウンド', 0), ('種別', 0), ('worker', 0), ('出力トークン', 0), ('worker', 1), ('オーケストレーター', 1),
                      ('キャッシュ読み', 1), ('5時間枠', 1), ('週枠', 1), ('最長の worker（分）', 1), ('採用', 1)], rows, 'usage')
    lead = ('ラウンドごとの消費。棒は出力トークンで、青が worker、橙がオーケストレーター。'
            '「5時間枠」「週枠」は codex の利用率の増分（ポイント）。claude の枠は記録できていない。')
    leg = '<div class="legend"><span><i class="sw s1"></i>worker の出力</span><span><i class="sw s2"></i>オーケストレーターの出力</span></div>'
    return sec('usage', 'いくら使ったか', '記録だけ', lead, f'<div class="card">{leg}{tbl}</div>')


def section_about(m, gen_sec, nlines):
    files = ['runs/R*/plan.json・result.json・usage.json', 'runs/R*/evaluation/*/scores.csv', 'runs/R*/jobs/*/job.json と worker の最終報告',
             'docs/RESEARCH_LOG.md（系譜・ラウンドの要点・次の狙いの引用だけ）', 'best/best.json']
    body = (f'<div class="card about"><p>このページは <code>make_report.py</code>（{nlines} 行、Python の標準ライブラリだけ）が、記録から {gen_sec:.1f} 秒で作った。'
            'LLM は使っていない（トークン 0）。文章のうち、案の題名・worker の報告・研究の記録の引用は、ラウンドの中で書かれたものをそのまま載せている。'
            '題名は案の文章から機械的に切り出したので、読みにくいものがある。</p>'
            '<h4>読んだもの</h4><ul>' + ''.join(f'<li>{esc(f)}</li>' for f in files) + '</ul>'
            '<h4>節ごとに要るもの</h4><ul><li>記録だけ：どの問題でもそのまま出る。</li>'
            f'<li>特徴量：ケースごとの数（ここでは{FEATURE["name"]}）。この例では入力を作り直して読んだ。v0.2.0 では採点のアダプタが返す。</li>'
            '<li>ソルバーを実行：<code>cases.py</code> が現 best などを走らせる（この例では約6分）。</li>'
            '<li>問題専用の描画：<code>view.js</code>（約100行）。無い問題では、公式ビジュアライザの出力へのリンクになる。</li></ul>'
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
  if(best)showTip(best[2]+' 点（seed '+best[0]+'）\nM='+best[1],e.clientX,e.clientY);else tip.style.display='none'});
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
(()=>{const data=document.getElementById('boards-data');if(!data||!window.caseView)return;const boards=JSON.parse(data.textContent);
 const panes=[...document.querySelectorAll('.pane')],t=document.getElementById('t'),tl=document.getElementById('tl'),play=document.getElementById('play'),chips=[...document.querySelectorAll('.chip')];
 let cur=0,timer=null,loaded=[];
 function setBoard(i){cur=i;chips.forEach((c,k)=>c.setAttribute('aria-pressed',k===i));const b=boards[i];document.getElementById('why').textContent='seed '+b.seed+'（M='+b.M+'）：'+b.why;
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
 setBoard(0)})();
"""


def page(m, sections, gen_info):
    nav = ''.join(f'<a href="#{i}">{esc(t)}</a>' for i, t in (('now', 'いまどこか'), ('progress', '効いてきたこと'), ('flows', '案の流れ'), ('ledger', 'すべての案'),
                                                              ('weak', '点を落とす所'), ('probe', '時間と運'), ('boards', '盤面を見る'), ('usage', '消費'), ('about', '作り方')))
    view = (HERE / 'view.js').read_text(encoding='utf-8')
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>進み具合のレポート（自動生成）</title><style>{CSS}</style></head><body><main>
<header><h1>進み具合のレポート</h1><p>atcoder13live A（Painting the Floor）・{esc(m['rounds'][0]['id'])}〜{esc(m['rounds'][-1]['id'])}・{esc(gen_info)}・LLM 不使用</p></header>
<nav>{nav}</nav>{''.join(sections)}</main><div id="tip" role="tooltip"></div>
<script>{view}</script><script>{JS}</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pick', action='store_true')
    ap.add_argument('--out', default=str(HERE / 'report.html'))
    a = ap.parse_args()
    m = analyse(build())
    probe = probe_summary()
    picks = pick_boards(m, probe)
    if a.pick:
        print(','.join(str(s) for s, _ in picks))
        for s, why in picks:
            print(s, why)
        return
    hs = hints(m, probe)
    body = [section_now(m, hs), section_progress(m), section_flows(m), section_ledger(m), section_weak(m, picks),
            section_probe(m, probe), section_boards(m, picks), section_usage(m)]
    nlines = len(Path(__file__).read_text(encoding='utf-8').splitlines())
    gen = time.perf_counter() - T0
    body.append(section_about(m, gen, nlines))
    stamp = time.strftime('%Y-%m-%d %H:%M')
    Path(a.out).write_text(page(m, body, f'{stamp} 作成'), encoding='utf-8')
    print(f'{a.out} を書いた（{Path(a.out).stat().st_size / 1024:.0f} KB、{gen:.1f} 秒）')


if __name__ == '__main__':
    main()
