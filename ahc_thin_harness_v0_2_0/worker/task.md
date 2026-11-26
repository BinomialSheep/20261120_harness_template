# 課題（{mode}）：{title}

{common}

{idea}

## 渡したもの

- 問題文 `problem/statement.md`、公式ツールの写し `problem/`
@if parents
- 親ソース：{parents}（一覧は `*.outline.txt`）
@end
@if diff
- 親どうしの差分：`parent_2.diff`（parent_1 → parent_2）
@end
@if refs
- 参考ファイル：`ref/`（先に `ref/` の中身を見る）
@end
- 過去の実験の経緯・得点・他の worker の結果は渡していない。探しに行かない。

## 成果物

@if A|B
- `main.cpp`：コンパイルできる最終版の C++ ソルバー。止まっても、そのときの `main.cpp` を提出として扱う。
- `notes.md`：次の3つの見出しで短く書く。「変更点」「構成」（主な関数と流れ）「確かめられなかった点」。
@end
@if C
- `report.md`：何を測り、何が分かったか。数字と条件を書き、推測は推測と書く。
- `out/`：集計に使ったデータ（CSV など）。大きな生ログは置かない。
- 提出するソルバーは無い。`main.cpp` を直しても評価されない。
@end

## 予算と決まり

- 上限は {minutes} 分。使い切る必要はない。`quickcheck.py` と `batchcheck.py` が残り時間（`remaining_min`）を出す。
- 動作確認は `python3 quickcheck.py --seed 0`（許可 seed：{seeds}）。得点・実行時間・残り時間を出す。
@if tuning
- 多数ケースの評価は `python3 batchcheck.py --seeds {tuning_example}`（許可：{tuning_range}、同時 {tuning_par} ケースまで）。`--baseline parent_1.cpp` で親と同じ seed で対にして比べ、平均差・標準誤差・帯別の差を出す。`--time-scale K` でソルバーに K 倍の時間を使わせる（ソースは変えない）。`--env KEY=VALUE` でソルバーへ環境変数を渡す。`batchcheck.py` は同時に1つだけ動かす。
- 調整した値は定数として `main.cpp` に書き込む。提出版は環境変数に依存させない。
@end
@if !tuning
- 多数ケースの評価・大量の seed・並列ベンチはしない。全 worker が終わってから、オーケストレーターが同じ条件で評価する。
@end
- 公式ツール（gen・vis・tester）や `problem/tools/in/` を直接使って、許可外の seed を試さない。
- 時間のかかるコマンドは、終わるまで1回の呼び出しで待つ。codex なら `exec` の先頭行に `// @exec: {{"yield_time_ms": 300000}}` を書き、`wait` も `yield_time_ms` を 300000 にする。短い間隔で何度も様子を見ない（呼び出しのたびに、それまでの文脈を読み直すので高くつく）。
- この作業場所の中だけを読み書きする。上の階層のファイル（`AGENTS.md` を含む）は読まない。
- この問題の解法・解説・順位表・提出コードを Web で調べない。
- 終える前に、自分で起動したプロセスを止める。
