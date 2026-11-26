# AHC Thin Harness — 0.1.0

**長期AHCのWith AI部門向け。1オーケストレーターが全体を把握し、A（10並列・各20分）とB（1体・2時間）を自律的に選んで繰り返す、意図的に薄い研究ハーネス。**

以前のAHC／LoCMテンプレートからコードをコピーせず、新規に設計・実装した。標準ライブラリ中心、Python 3.11+、Linux/WSL2、Git、C++23コンパイラがあれば動く。実行プロセスは `codex exec`、`claude -p` などのCLIに委任する。現時点ではLLMサービスのアカウント・認証・課金が必要な実行経路の実動作は未検証。

## 30秒で理解する

```text
オーケストレーター（全リポジトリを見る、AかBを選ぶ）
      │
      ├─ A：異なる10案を作る → 履歴を見せない10worker × 各20分
      │                           ↓ seed0だけで動作確認
      │                       10候補のmain.cpp
      │
      └─ B：ベストを1workerが2時間改良 → main.cpp
                                  ↓
                オーケストレーターが同じ新規1000seedで評価
                          ↓
                    必要ならbestを更新
                          ↓
                       次のラウンド
```

A: 1000ケース×最大10候補に、比較のための現ベスト1000ケースが追加される。B: 1000ケース×1候補＋現ベスト1000ケース。**評価自体の計算時間は20分／2時間というworkerの上限に含まない**。評価の並列度は控えめ（既定2）で、公式testerのCPU負荷を抑える。

## 初めて使う

1. ZIPを解凍し、フォルダを新しいAHC用リポジトリにする。
2. `problem/statement.md` に問題文、`problem/tools/`に公式のgenerator/tester/visualizerなどを配置。
3. `cp config.example.json config.json`。AHCごとに`judge.command`で呼べる**1seedスコアリングラッパー**を作る。バッチでも対話でも、ラッパーが公式testerへ接続する。
4. `python3 -m thin.cli demo`でハーネス単独の動作確認。
5. VS CodeのClaude Code／Codexをオーケストレーターとして起動して **`AGENTS.md`と`docs/SETUP.md`を読んでから問題へ接続し、A/Bのラウンドを進めて**と指示する。

詳しくは [人間向け手順](docs/SETUP.md)、[選択と比較の仕様](docs/DESIGN.md)、[CLI・復旧](docs/OPERATIONS.md)、[限界](docs/LIMITATIONS.md)。

## 主なCLI

```sh
python3 -m thin.cli status
python3 -m thin.cli init-best /path/to/main.cpp
python3 -m thin.cli prepare A --ideas proposals.json
python3 -m thin.cli run-workers R0001 --engine codex
python3 -m thin.cli evaluate R0001
python3 -m thin.cli promote R0001 A01
python3 -m thin.cli close R0001

# 一度に実行（worker + evaluator、採用・closeは管理役が判断）
python3 -m thin.cli round B --idea '構造的に改善したい内容を具体的に記述' --engine claude

# 無償の合成テスト：実際の外部LLMは呼ばない
python3 -m thin.cli demo
python3 -m unittest discover -s tests -v
```

## 何を捨てたか

Beads・mission・多階層の実験計画書・詳細なFINDINGS DB・AB-MCTS・パラメータチューナー・ダッシュボード・複雑な権限階層・自動LLMルーター・AtCoder API・オンライン自動提出は**組み込まない**。情報は`runs/R####/plan.json`、`result.json`、`jobs/*/job.json`、`evaluation/*/scores.csv`、`best/main.cpp`へ最小限保存する。

**workerは過去実験の履歴を見ず、オーケストレーターだけが読む。** ただしworkerへの別ディレクトリの隠蔽は通常のファイル配置／CLI sandboxの範囲であり、悪意あるプロセスから過去ファイルの読み取りを保証するものではない。

**レギュレーションはコードに埋め込まない。** 対象はWith AI長期AHCだが、開催回ごとの規則・追加制約は人間が別に与える。クラウド利用や料金・外部API操作は実際のCLIに対する利用者の承認に従う。
