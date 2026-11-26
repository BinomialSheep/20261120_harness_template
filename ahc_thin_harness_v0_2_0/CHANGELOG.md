# 変更履歴

## 0.2.0（2026-11-26）

0.1.0 を atcoder13live A で 19 ラウンド使った記録（TEMPLATE_FEEDBACK の16項目、人間のメモ、研究の記録）をもとに直した。config.json は schema_version 2 になり、0.1.0 の設定・記録とは互換がない。

### トークン

- 案の数を1〜10にした（既定6）。全案に共通の前提は `common` に1回だけ書く。案に `title` を付ける。
- codex の待つだけの呼び出し（`wait`、空の `write_stdin`）を数えるようにした。0.1.0 の集計は `write_stdin` だけを数えていた（R0017 で記録65回、実際は314回）。
- worker の課題に、長いコマンドを1回の呼び出しで待つ書き方を入れた（codex の `// @exec: {"yield_time_ms": 300000}`）。
- `go --background` で、待つあいだオーケストレーターがターンを終えられるようにした。
- `limits`：codex の枠を読み、過去の1体あたりの消費から必要量を見積もる。
- claude の worker の出力トークンを、stream-json の最後の result から数えるようにした（メッセージごとの値は途中までの数で、約30分の1に少なく出ていた）。
- CLI の出力を短い表にした（全部は `--json`）。
- worker の notes.md を best/notes.md に写す。親の関数一覧と、親どうしの差分を作業場所に置く。

### 評価

- ケース単位の並列と、現 best・候補の混合（実験リポジトリの改良を移植）。
- 段階式の足切り（100/300/1000）と、首位の確認評価（未使用の seed）。A の採否は確認評価の値で決める。
- TLE を単独で再実行する。判定の環境エラーでは候補を無効にせず止まる。`evaluate --restart`。
- 評価の前に、空き容量・残ったプロセス・負荷を確かめる。
- 汎用アダプタは、ソルバーの終了をブロックして待つ（0.1.0 の実験のアダプタは最大50ミリ秒の見回りで丸めていた）。

### worker

- 作業場所をリポジトリの外に置く（FB13）。上の階層の指示ファイルを doctor が警告する。
- claude と codex を同じ形で扱い、モデル・effort・Web の無効化を argv で固定した（FB5、FB8、FB10、FB11）。CLI の場所（VS Code 同梱版を含む）と最低の版を確かめる。
- claude の worker は道具を Bash・Read・Edit・Write・Glob・Grep に絞る。1回の呼び出しで読む量が 27,037 → 8,294 トークンに減った（実測）。
- 案ごとに engine を選べる（FB12）。
- 利用上限・通信断で止まった worker を `interrupted` にし、`accept`・`rerun`・`fail` で片付ける（FB9、FB13）。
- worker の終了時に、作業場所で動き続けるプロセスを止める。
- codex の監査（`audit`）を移植した（FB11）。

### 実験と調整

- 行動 C（測定・診断）を足した。
- B・C に複数の親と参考ファイル（`ref/`）を渡せる。
- B・C は調整用の seed で `batchcheck.py` を使える（FB16）。親との対比較、帯別の差、`--time-scale`（ソースを変えずに時間を k 倍）。
- `probe`（時間 k 倍・k 回の最良）、`tune`（Optuna）。補助は thin/tools/ にあり、`python3 -m thin.cli <名前>` で呼ぶ。

### 人間向け

- `report`：LLM なしの HTML レポート。close のたびに作る。
- `human/INBOX.md`（思いつき）、`human/private/`（AI が開かないメモ）、研究の記録の「人間への問い」。

### 配備

- `install.py`（上書きしない）、`SETUP.md`（1回だけ読む）、短い `AGENTS.md`（毎回読む、3KB）。README は配備先で HARNESS.md になる（FB1、FB3）。
- 公式ツールのビルド結果は `problem/bin/`、アダプタは `problem/score_case.py`。`problem/tools/` には何も足さない（FB3、FB4）。
- `.gitignore` で config.json・best/・ラウンドの要点を除外しない（FB2）。
- 起動時に Python 3.10 以上を確かめる（FB7）。`doctor [--live]`。
- 参考資料：チェックリスト、AtCoder のコンパイルオプション、C++ のメモ（QCFium 法、時間の測り方、PARAM マクロ）。
