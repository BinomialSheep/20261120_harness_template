# CLIと運用の細部

## コマンド

| 操作 | コマンド |
|---|---|
| 初期best | `python3 -m thin.cli init-best /absolute/path/main.cpp` |
| A用10案を準備 | `python3 -m thin.cli prepare A --ideas ideas.json` |
| B用の1案を準備 | `python3 -m thin.cli prepare B --idea '具体的な改善目標...'` |
| 10／1workerを起動 | `python3 -m thin.cli run-workers R0001 --engine codex` |
| workerを自分で編集 | `prepare`で生成した`runs/R0001/jobs/A01/workspace/`を開き、`main.cpp`を作る |
| 手動worker提出 | `python3 -m thin.cli submit R0001 A01`（各workerごと） |
| 手動worker失敗 | `python3 -m thin.cli fail R0001 A01 --reason '失敗理由'` |
| 手動worker終了宣言 | `python3 -m thin.cli workers-done R0001` |
| 1000seed評価 | `python3 -m thin.cli evaluate R0001` |
| ベストへ昇格 | `python3 -m thin.cli promote R0001 A03` |
| ラウンド終了 | `python3 -m thin.cli close R0001` |
| 現在地 | `python3 -m thin.cli status` |
| 一括実行 | `python3 -m thin.cli round A --ideas ideas.json --engine codex` |

`round`はprepare→worker→evaluateまで。**採用とcloseはオーケストレーターが結果を読んで実行**する。ユーザーの提案が介入しても、この同一の再現可能な流程で扱う。

## アイディアJSON

```json
{
  "ideas": [
    {"kind":"new","idea":"問題固有の全く別の構築法を試す...","parents":[]},
    {"kind":"continue","idea":"過去の候補の探索近傍を拡張する...","parents":["best"]},
    {"kind":"combine","idea":"別系統の構築と改善を組み合わせる...","parents":["best","R0001/A04"]}
  ]
}
```

Aでは10案が必要。`kind=new`は親無し、`continue`は1本、`combine`は異なる2本。`R0001/A04`は別ラウンドに保管された提出済みソース。Bでは親は常に現行bestなので`--idea`を一つ渡す。

## workerと評価器の責任

workerは`quickcheck.py`だけを使って少数の動作確認を行う。Aはseed0しか許可しない。Bは設定された最大20のdebug seedまで。全1000seedの評価はオーケストレーターが、保存済みjudge snapshotを使って実行する。**Aの候補数を減らすことで評価の総仕事量は減るが、その判断は10個提出後のコンパイル・seed0確認に基づく。**

結果は`runs/R####/result.json`。`status=ok`の候補に対して、rawの平均score・現ベストとの差・改善/悪化ケース数・探索的CIを出力する。候補を10本から選ぶ場合の選択バイアスは補正していない。次のラウンドではseedセットをずらすが、観測した結果を使っている以上、厳密なholdoutにはならない。

## 中断復旧

- `prepare`途中に失敗した場合、途中生成のディレクトリを自動削除しない。原因を確認して必要なら**手動で整理**する。
- 自動worker実行中に終了した場合は、残った`job.json`の状態・プロセス生存を確認。二重起動を避けるためworkerごとの`launch.lock`を使う。外部でdetachした子孫までは保証しない。
- 評価中に終了した場合、`score.csv`に完了済みseedが残る。同じrunへ`evaluate`を再実行すると、入力の順序・source/tool hashを検証し、未処理seedから再開する。
- ラウンドの比較条件は`runs/R####/plan.json`へ固定。途中でconfig.jsonを変えても進行中roundには影響しない。
- `best/`の更新は`promote`で行い、変更前bestは`best/history/`へ保存。`result.json`には昇格した候補IDを記録。
- すべての候補が失敗したラウンドは、`workers-done`後に`close`して次のラウンドへ進める。

## 作業量

Aの標準評価は「各候補1000ケース＋best1000ケース」で、重複候補はhashで省略。Bは「候補1000＋best1000」。**公式testerが1ケース何秒かかるかで、評価だけで長時間かかり得る**。事前に小規模のmockまたは1〜数seedで実行経路と単ケース実行時間を確認し、1000seedを回せる設定にする。

評価runnerは通常の外部プロセスgroupへtimeoutを掛ける。`judge.command`内で公式testerを起動する場合も、ソルバー単独の公式制限時間・メモリ制限は問題ごとのadapterが適用する。ラッパー全体のwall timeoutだけでAHCの公式判定を忠実に再現したとは言えない。
