# 実行・採点契約

`problem/contract.json`は実行の技術契約。参加者のAI使用規則を入れない。
コマンドはargv配列で、shell評価しない。相対依存は`files`へ明記してoracle snapshotに凍結する。
`{oracle}`はそのsnapshot、`{python}`は実行中のPython。依存するPythonライブラリ/共有ライブラリ/ランタイムの版は別途記録する。

## batch

標準入力はケースbytes。stdoutを`solver.out`、stderrを`solver.err`へ保存し、独立ジャッジを起動する。
`judge_command`の置換子: `{python}`, `{oracle}`, `{input}`, `{output}`, `{result}`, `{work}`。
ソルバーには入力ファイルの絶対パスを引数として渡さずstdinを渡す。

## interactive

`tester_command`に加えて`{solver}`が使える。これは凍結artifact・評価引数・seed設定を持つ起動用ファイル。
テスタが`{input}`の隠れたデータを持ち、ソルバーへは逐次protocolの観測だけを渡す。
各ターンの制限、クエリ回数、終了・flush、solver crash/WA/TLEの区別は問題別のテスタadapterが責任を持つ。
外側の`tester_wall_s`はテスタ全体の監視上限であり、公式の1手/総CPU時間とは同一ではない。
テスタがエラーで落ちた場合はinfrastructure_error。根拠なくsolver_timeoutへ変換しない。
必要なら`transcript.jsonl`を実行dirへ残す。再実行は凍結条件から対話をやり直し、記録済み応答を押し付けない。

## output_only

`python3 -m harness judge`は保存済みの出力を複製して、同じジャッジだけを実行する。
対話型のログは別契約が必要。`eval`はソルバー実行の比較なので、output_only契約は受け付けない。

## 正規化された採点結果

wrapperは`{result}`へUTF-8 JSONを出し、自身は正常終了する。

```json
{"schema_version":1,"status":"ok","valid":true,"raw_score":12345,"metrics":{}}
```

失敗例:
```json
{"schema_version":1,"status":"solver_invalid","valid":false,"raw_score":null,"reason":"duplicate index"}
```

状態はok / solver_timeout / solver_crash / solver_invalid / solver_output_limit / solver_memory_limit / infrastructure_error / not_run。
失敗にraw_score=0を付けない。成功スコアの0・負値は正当でありdifferenceで比較できる。NaN/Infinity/boolは拒否。
公式失敗時のスコア変換が必要なら、別の明示した大会集計を追加し、有効性の情報を消さない。
最終順位・他参加者に依存する相対点は既定でnull。任意の和を「公式最終得点」と表示しない。

## 制限と乱数

`solver_wall_s`はローカル壁時計。`solver_memory_mb`は任意のRLIMIT_AS（仮想アドレス空間）、`solver_cpu_s`は任意の整数CPU秒。
これらは公式メモリ/時間計測の完全な再現ではない。unknownのメモリ超過はcrashのまま残す。
出力量制限は監視と保存時truncate。瞬間的なdisk使用量を強制隔離するものではない。
各試行で`AHC_SOLVER_SEED`を渡す。実際にソルバーが読むかは`seed_control`へ記録し、未対応なら制御済みと主張しない。
variantのargs/envに`{solver_seed}`を埋め込める。選んだパラメータを提出ソースへ固定する検証は別途必要。
