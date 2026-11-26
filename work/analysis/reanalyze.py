#!/usr/bin/env python3
"""20261117 の実験リポジトリの記録を読み直し、v0.2.0 の設計判断に使う数字を出す（読み取りだけ）。

    python3 -I work/analysis/reanalyze.py

出すもの:
  1. 段階式の足切り（レーシング）で評価ケース数がどれだけ減り、勝者を落とさないか
  2. A の案数を減らしたときに期待できる伸び（best-of-n）
  3. 選抜の偏り（前半500件で選び、後半500件で読む）
  4. codex worker の「待つだけの呼び出し」が読み直したトークン
  5. codex の5時間枠に対するトークン区分の重みの推定（幅つき）
"""
import collections
import csv
import glob
import itertools
import json
import math
import os
import statistics as st

E = '/home/ilu/work/20261117_pracitice_ahc_harness'


def load_scores(path):
    out = {}
    with open(path, newline='', errors='replace') as f:
        for r in csv.DictReader(f):
            if r['status'] == 'ok':
                out[int(r['seed'])] = float(r['score'])
    return out


def load_rounds():
    rounds = {}
    for rd in sorted(glob.glob(E + '/runs/R*')):
        base_path = rd + '/evaluation/baseline/scores.csv'
        if not os.path.exists(base_path):
            continue
        plan = json.load(open(rd + '/plan.json'))
        res = json.load(open(rd + '/result.json'))
        cands = {}
        for c in res['candidates']:
            p = f"{rd}/evaluation/{c['id']}/scores.csv"
            if os.path.exists(p) and c['valid']:
                cands[c['id']] = load_scores(p)
        rounds[os.path.basename(rd)] = (plan['mode'], load_scores(base_path), cands, res.get('promoted'))
    return rounds


def paired(a, b, seeds):
    d = [a[s] - b[s] for s in seeds]
    return st.mean(d), (st.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else float('inf'))


def race(rounds, stages, z, keep_top):
    total = full_total = 0
    lost, detail = [], []
    for rid, (mode, base, cands, prom) in rounds.items():
        if mode != 'A':
            continue
        seeds = sorted(base)
        alive, prev, cases, base_n = set(cands), 0, 0, 0
        for n in stages:
            n = min(n, len(seeds))
            cases += (n - prev) * len(alive)
            base_n = prev = n
            if n == len(seeds):
                break
            s = {k: paired(cands[k], base, seeds[:n]) for k in alive}
            order = sorted(alive, key=lambda k: -s[k][0])
            lead = order[0]
            for k in order[keep_top:]:
                m, se = s[k]
                dm, ds = paired(cands[k], cands[lead], seeds[:n])
                if m + z * se < 0 or dm + z * ds < 0:
                    alive.discard(k)
        cases += base_n
        full = len(seeds) * (len(cands) + 1)
        total += cases
        full_total += full
        eff = {k: paired(v, base, seeds)[0] for k, v in cands.items()}
        top = max(eff, key=eff.get)
        if top not in alive or (prom and prom not in alive):
            lost.append(rid)
        detail.append(f'{rid}:{cases / full:.2f}')
    return total / full_total, lost, detail


def main():
    rounds = load_rounds()
    print('1. 段階式の足切り（段階 100/300/1000。現best・首位より z×標準誤差を超えて悪い候補を落とす）')
    for z in (2.0, 2.5, 3.0):
        for keep in (1, 2, 3):
            cost, lost, detail = race(rounds, [100, 300, 1000], z, keep)
            print(f'   z={z} 上位{keep}は必ず残す: ケース数 {cost:.2f} 倍, 勝者を落としたラウンド {lost}')
            if z == 3.0 and keep in (1, 3):
                print('      ', ' '.join(detail))

    print('\n2. 案数 n のときの伸び（10案に対する比。各ラウンドの実測効果から、n 案の組合せ全部の平均）')
    ns = [1, 2, 3, 4, 5, 6, 8, 10]
    agg = {n: [] for n in ns}
    for rid, (mode, base, cands, prom) in rounds.items():
        if mode != 'A':
            continue
        eff = [paired(v, base, sorted(base))[0] for v in cands.values()]
        row = [st.mean(max(0, max(c)) for c in itertools.combinations(eff, min(n, len(eff)))) for n in ns]
        for n, v in zip(ns, row):
            agg[n].append(v / row[-1])
    print('   ', {n: round(st.mean(v), 2) for n, v in agg.items()})

    print('\n3. 選抜の偏り（前半500件の首位を、後半500件で読む）')
    for rid in ('R0009', 'R0011', 'R0012', 'R0013', 'R0015'):
        mode, base, cands, prom = rounds[rid]
        seeds = sorted(base)
        s = {k: paired(v, base, seeds[:500]) for k, v in cands.items()}
        top = max(s, key=lambda k: s[k][0])
        c = paired(cands[top], base, seeds[500:])
        print(f'    {rid} {top}: 選抜 {s[top][0]:+.3f}±{s[top][1]:.3f} → 未使用 {c[0]:+.3f}±{c[1]:.3f}')

    print('\n4. codex worker の待つだけの呼び出し（wait と write_stdin）')
    wait_cr = {}
    for rid in ('R0012', 'R0013', 'R0014', 'R0015', 'R0016', 'R0017', 'R0018'):
        kinds, waited, total = collections.Counter(), 0, 0
        for p in sorted(glob.glob(f'{E}/runs/{rid}/jobs/*/codex_rollout.jsonl')):
            pend = None
            for line in open(p, errors='replace'):
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pl = d.get('payload') if isinstance(d.get('payload'), dict) else {}
                if d.get('type') == 'response_item' and pl.get('type') in ('function_call', 'custom_tool_call'):
                    text = f"{pl.get('name', '')} {pl.get('input') or pl.get('arguments') or ''}"
                    pend = 'wait' if pl.get('name') == 'wait' else ('stdin' if 'write_stdin' in text else 'work')
                    kinds[pend] += 1
                if pl.get('type') == 'token_count' and pl.get('info'):
                    u = pl['info'].get('last_token_usage') or {}
                    total += u.get('cached_input_tokens', 0)
                    if pend in ('wait', 'stdin'):
                        waited += u.get('cached_input_tokens', 0)
        usage = json.load(open(f'{E}/runs/{rid}/usage.json'))
        recorded = sum((w['usage'] or {}).get('waits') or 0 for w in usage['workers'].values())
        wait_cr[rid] = waited / max(1, total)
        print(f'    {rid}: {dict(kinds)}, 記録されていた waits={recorded}, '
              f'待つだけの呼び出しのキャッシュ読み出し {waited / 1e6:.1f}M / {total / 1e6:.1f}M（{wait_cr[rid]:.0%}）')

    print('\n5. codex の5時間枠（ポイント）= in, cr, out の重みつき和（非負の最小二乗、7ラウンド）')
    obs = {'R0012': 45, 'R0013': 42, 'R0014': 14, 'R0015': 60, 'R0016': 11, 'R0017': 29, 'R0018': 24}
    rows = []
    for r, y in obs.items():
        t = json.load(open(f'{E}/runs/{r}/usage.json'))['workers_total']
        rows.append((r, t['in'] / 1e6, t['cr'] / 1e6, t['out'] / 1e6, y))
    fits = []
    for wi in range(61):
        for k in range(60):
            wc = k * 0.02
            den = sum(o * o for _, _, _, o, _ in rows)
            wo = max(0.0, sum(o * (y - wi * i - wc * c) for _, i, c, o, y in rows) / den)
            err = sum((y - wi * i - wc * c - wo * o) ** 2 for _, i, c, o, y in rows)
            fits.append(((err / len(rows)) ** .5, wi, wc, wo))
    fits.sort()
    ok = [f for f in fits if f[0] <= fits[0][0] * 1.3]
    print(f'    最良 in={fits[0][1]} cr={fits[0][2]:.2f} out={fits[0][3]:.0f}（100万あたり, RMSE {fits[0][0]:.2f}）'
          f' / 幅 in {min(f[1] for f in ok)}〜{max(f[1] for f in ok)}, cr {min(f[2] for f in ok):.2f}〜{max(f[2] for f in ok):.2f},'
          f' out {min(f[3] for f in ok):.0f}〜{max(f[3] for f in ok):.0f}')
    for rid in ('R0014', 'R0016', 'R0017', 'R0018'):
        i, c, o = next((a, b, d) for r, a, b, d, _ in rows if r == rid)
        sh = [f[2] * c * wait_cr[rid] / (f[1] * i + f[2] * c + f[3] * o) for f in ok]
        print(f'    {rid}: 消費のうち待つだけの呼び出しの読み直し {min(sh):.0%}〜{max(sh):.0%}')


if __name__ == '__main__':
    main()
