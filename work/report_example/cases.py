#!/usr/bin/env python3
"""盤面を走らせて cases.json に残す（LLM なし）。実験リポジトリは読むだけ。

1. probe : 最後に評価した seed の先頭 N 件で、現 best に時間を 1/2〜8 倍与える（時計を遅く見せるので、ソースは変えない）
2. boards: 弱い盤面などを、過去の版と 8 倍の時間でも走らせ、出力を残す（見比べ用）

    python3 -I cases.py probe  [--count 120] [--jobs 12]
    python3 -I cases.py boards --seeds 19001,19002 [--jobs 12]
"""
import argparse, json, os, re, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = Path('/home/ilu/work/20261117_pracitice_ahc_harness')
BIN = EXP / 'problem/tools/bin'
OUT = HERE / 'cases.json'
BEST = 'R0019/B01'
VERSIONS = ['R0002/A10', 'R0005/A01', 'R0008/A06', 'R0012/A04', BEST]   # 見比べる版（系譜の節目）


def solver(ref):
    r, j = ref.split('/')
    return EXP / 'runs' / r / 'evaluation' / j / 'solver'


def shim():
    so = Path(tempfile.gettempdir()) / f'slowclock-{os.getuid()}.so'
    subprocess.run(['gcc', '-O2', '-shared', '-fPIC', '-o', so, HERE / 'slowclock.c', '-ldl'], check=True)
    return so


def run(task):
    seed, ref, k, rep, so = task
    with tempfile.TemporaryDirectory(prefix='case-') as d:
        d = Path(d)
        (d / 'seeds.txt').write_text(f'{seed}\n')
        subprocess.run([BIN / 'gen', 'seeds.txt', '--dir', 'in'], cwd=d, check=True, capture_output=True)
        env = dict(os.environ, LD_PRELOAD=str(so), SLOWCLOCK_K=str(k)) if k != 1 else dict(os.environ)
        with open(d / 'in/0000.txt') as fi, open(d / 'out.txt', 'w') as fo:
            subprocess.run([solver(ref)], stdin=fi, stdout=fo, stderr=subprocess.DEVNULL, env=env, check=True, timeout=120)
        vis = subprocess.run([BIN / 'vis', 'in/0000.txt', 'out.txt'], cwd=d, capture_output=True, text=True, check=True)
        score = int(re.search(r'Score = (\d+)', vis.stdout).group(1))
        return dict(seed=seed, ref=ref, k=k, rep=rep, score=score,
                    input=(d / 'in/0000.txt').read_text(), output=(d / 'out.txt').read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('what', choices=['probe', 'boards'])
    ap.add_argument('--count', type=int, default=120)
    ap.add_argument('--seeds', default='')
    ap.add_argument('--jobs', type=int, default=12)
    a = ap.parse_args()
    so = shim()
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    if a.what == 'probe':
        seeds = range(19000, 19000 + a.count)
        tasks = [(s, BEST, k, rep, so) for k, reps in ((8, 1), (4, 1), (2, 1), (1, 4), (0.5, 1)) for rep in range(reps) for s in seeds]
    else:
        seeds = [int(x) for x in a.seeds.split(',')]
        tasks = [(s, v, 1, 0, so) for s in seeds for v in VERSIONS] + [(s, BEST, 8, 0, so) for s in seeds]
    with ThreadPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(run, tasks))
    if a.what == 'probe':   # 得点だけ残す
        rows = [{k: v for k, v in r.items() if k not in ('input', 'output')} for r in rows]
    data[a.what] = rows
    OUT.write_text(json.dumps(data, ensure_ascii=False))
    print(a.what, len(rows), '件')


if __name__ == '__main__':
    main()
