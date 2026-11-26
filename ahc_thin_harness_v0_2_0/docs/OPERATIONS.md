# コマンドと運用

すべて `python3 -m thin.cli <コマンド>`。既定では短い表を出し、`--json` で全部を出す。

## コマンド

| 操作 | コマンド |
|---|---|
| 状況 | `status` |
| 最初の best | `init-best path/to/main.cpp` |
| A を準備 | `prepare A --ideas ideas.json` |
| B・C を準備 | `prepare B --idea '課題' --title '題名' [--parent R0003/A02] [--ref runs/R0016/jobs/C01/out/data.csv]` |
| worker を起動して評価まで | `go R0001`（裏で動かすなら `--background`、ログは runs/R0001/go.log） |
| worker だけ起動 | `run R0001 [--engine codex] [--only A01,A03]` |
| 止まった worker | `accept R0001 A03`（そのまま提出）・`rerun R0001 A03 [--fresh]`・`fail R0001 A03 --reason '…'` |
| 手で作った worker | 作業場所で main.cpp を書き `submit R0001 A01`、全部済んだら `workers-done R0001` |
| 評価 | `evaluate R0001`（続きから）・`evaluate R0001 --restart`（退避して最初から） |
| 採用 | `promote R0001 A03`（判定値が margin 以下なら `--force '理由'` が要る） |
| 閉じる | `close R0001 --note '要点1行'` |
| 利用枠 | `limits [--claude] [--mode A --engine codex --workers 6]` |
| 模擬問題 | `demo [--keep DIR]` |

LLM を使わない補助（中身は thin/tools/）：`report [--boards]`、`probe`、`tune`、`doctor [--live ENGINE]`、`audit R0001`。

## 案の JSON

```json
{"common": "全案に共通の前提", "ideas": [
  {"title": "40字以内", "kind": "new|continue|combine", "idea": "課題", "parents": ["best", "R0003/A02"], "engine": "codex", "refs": []}]}
```

- A は1〜10案。new は親なし、continue は親1本、combine は親2〜3本。B は親1〜3本（1本目が主）、C は親0〜3本。
- `refs`（B・C 向け）：リポジトリ内のファイルやフォルダを作業場所の `ref/` に写す（合計100MBまで）。得点や他の候補の結果を渡すかどうかは、オーケストレーターが決める。
- `engine` を案ごとに変えられる（例：codex と claude を混ぜる、effort 違いで同じ案を並べる）。

## worker

- 作業場所は `workers.workspace_root`（既定はリポジトリの隣の `<名前>.workers/R0001/A01`）。上の階層の AGENTS.md・CLAUDE.md を読ませないため、リポジトリの中には作らない。
- 作業場所に置くもの：問題の写し、親（`parent_N.cpp`、その関数一覧 `*.outline.txt`、親が2本以上なら `parent_2.diff`）、`main.cpp`（parent_1 の写し）、課題 `task.md`、`quickcheck.py`、B・C なら `batchcheck.py` と `slowclock.c`。
- 終わったら、作業場所で動いているプロセスを止める（切り離されたものも）。`notes.md`・`report.md`・`out/` を runs/ に写す。
- 終わり方：ok・timeout は提出として扱う。利用上限（limit）と通信断（network）は `interrupted` にして提出しない。上限を見たら、まだ起動していない worker は起動しない。
- `limits` は codex の5時間枠・週枠を読む（読み取り専用）。`--claude` は小さな呼び出しを1回して claude の枠を読む。見積もりは過去のラウンドの1体あたりの消費から出す。

## 評価

- ラウンド n の seed は `first_seed + (n−1) × block` から。前半 `stages` の最後の件数が評価用、その後ろ `confirm` 件が確認用。
- A：段階（既定 100 → 300 → 1000 件）ごとに、上位 `keep_top` 以外で「現 best より drop_z × 標準誤差を超えて悪い」か「首位との対の差がそれだけ悪い」候補を落とす。過去の A 10ラウンドでは、この規則で勝者は落ちなかった。件数は、候補の差が大きい序盤で 0.26〜0.48 倍に減るが、差が標準誤差と同じくらいの終盤ではほとんど減らない（R0012 は 1.00 倍）。
- 確認評価：首位を、選ぶのに使っていない `confirm` 件で現 best と比べ直す。A の採否はこの値（`decision_effect`）で決める。首位の z が `skip_confirm_z` 以上なら省く。B は候補が1つなので行わない。
- TLE は、ほかの負荷が無い状態で `tle_retries` 回まで走らせ直す（1回でも時間内なら有効）。
- 判定が JSON を書かずに失敗したら、環境の問題とみて評価を止める（候補を無効にしない）。直して `evaluate` を再実行すると続きから採点する。現 best が無効になったときも止まる。
- 評価の前に確かめること：空き容量、作業場所に残ったプロセス、負荷（高いと警告）。
- scores.csv は追記する。TLE の再実行は同じ seed の行を足し、最後の行が有効。

## 裏で動かす

`go R0001 --background` は裏で worker と評価を進め、すぐ戻る。オーケストレーターはターンを終え、ユーザーに終わる時刻を伝える。次のターン（または新しいセッション）で `status` を見て、`runs/R0001/result.json` を読む。待つあいだに何度も様子を見ると、そのたびに文脈を読み直してトークンを使う。

## 記録とレポート

- `close` が、研究の記録（docs/RESEARCH_LOG.md）の「ラウンド記録」に1行足し、消費（runs/R####/usage.json）とレポート（report/index.html）を書く。失敗しても close は成功する。
- レポートは LLM なしで数秒で作る。`--boards` を付けると、選んだ盤面を過去の best と 8 倍の時間で解き直して載せる（1分ほど）。
- `probe` の結果（runs/probe/latest.json）はレポートの「時間と運の余地」に出る。

## 復旧

- 途中で止まったら `status` を見る。worker の段階なら `run`（pending のものだけ起動）、評価の段階なら `evaluate`。
- 作業場所が壊れたら `rerun --fresh`。評価の記録が壊れたら `evaluate --restart`。
- `prepare` の途中で失敗したラウンドは自動では消さない。原因を直し、runs/R#### と作業場所を手で片付ける。
