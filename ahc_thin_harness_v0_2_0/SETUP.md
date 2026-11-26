# 配備の手順（エージェントが1回だけ読む）

この雛形を AHC のプロジェクトに入れ、最初のラウンドを始められる状態にする。終わったら、以後は AGENTS.md だけを読めばよい。

## 1. 必要なもの

Linux か WSL2、Python 3.10 以上（標準ライブラリだけで動く）、Git、g++（C++23）、gcc（`thin/tools/slowclock.c` 用）。
公式ツールが Rust なら cargo。worker に使う CLI（codex、claude）。`tune` を使うなら optuna。

## 2. 配備する

```sh
python3 <雛形のフォルダ>/install.py <プロジェクトのフォルダ>        # 対話型の問題は --adapter interactive
```

既存のファイルは上書きしない（上書きしなかったものを表示する）。雛形のフォルダは、そのあと消してよい。

## 3. 問題を置く

- `problem/statement.md` に問題文（HTML なら Markdown に直す）。
- `problem/tools/` に公式ツール（配布の zip を展開した中身。プロジェクトの直下に tools/ として展開されていたら、ここへ移す）。中のファイルは変えない。
- `bash problem/build_tools.sh` で gen・vis・tester を `problem/bin/` に作る。Rust でないツールは、実行ファイルを `problem/bin/` に置く。

## 4. config.json を直す

- `judge.command`：`--mode`（batch か interactive）、`--time-limit`（問題の制限時間）、`--features`（入力の1行目の値の名前。使わない値は `-`。例：1行目が `N M T si sj` なら `N,M,T,-,-`）。
- `judge.direction`：得点が大きいほど良いなら maximize、小さいほど良いなら minimize。得点がケースで桁ごと変わる問題は `metric` を paired_log_ratio にする。
- `judge.timeout_sec`：制限時間に数秒を足す。
- `report.feature`：レポートで帯に分ける特徴量（例 `"M"`）。`report.bands` で帯を決められる（例 `[[0, 9], [10, 19], [20, 30]]`。無ければ三分位）。
- `workers.engines`：既定のままでよい（モデルと effort を固定し、Web を切ってある）。手元の CLI が `min_version` より古いと使えない。
- `evaluation.parallelism`：0 なら物理コアの約3/4。

## 5. 確かめる

```sh
python3 -m thin.cli doctor               # ビルド、seed 0 の採点、engine の場所と版、作業場所の上の指示ファイル
python3 -m thin.cli demo                 # 模擬問題で一周（LLM なし、数秒）
python3 -m thin.cli doctor --live codex  # ユーザーの許可があれば（利用枠を少し使う）
```

- ✗ を直す。! は理由を読んで判断する。
- 何もしない出力（例：行動 0 個）が有効か無効か、問題文と合っているかを確かめる。無効を 0 点として扱わない。

## 6. 最初の best

有効な簡単な解を書き、`python3 -m thin.cli init-best main.cpp`。書けなければ A の new の案から始めてよい。

## 7. 仕上げ

- AGENTS.md の最後に「この問題について」の節を足す：問題名、得点の向き、制限時間、特徴量、気をつけること。数行にする（毎回読まれる）。
- 盤面を描けるなら `problem/view.js` を書く（`templates/view.example.js` が例）。レポートで解を再生できるようになる。
- `git init`（まだなら）してコミットする。
- ユーザーに報告する：配備したもの、doctor の結果、最初のラウンドの案。
