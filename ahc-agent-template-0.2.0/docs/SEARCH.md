# 選択器：候補の親と改変アクションを決める（任意機能）

本体は `integrations/search/`。**AB-MCTSは問題解法の内部で探索するものではなく、どのソルバー候補を親に次の改変を行うかを制御するもの**。`harness eval`だけが性能を測り、mission・Beadsが資源を許可する。

## 初期の選択器

- `greedy`: 最高のdev報酬の候補を親として選択する。
- `multi-start`: 毎回、固定したrootから別案を生成する。
- `treequest-abmcts-a`: TreeQuest v0.3.3 `ABMCTSA(dist_type=beta)`を使う **任意オプション**。導入時に選択器の追加依存をインストールし、対象環境で受入テストする。基本のPython標準環境には入っていない。AB-MCTSと他方式の優劣は未測定。

生成用LLM／thinking effort／プロンプトは **生成器設定のhash**として固定する。`ask`がworkerを起動することはない。Claude Code/Codexやローカルモデルとの連携は展開時の別アダプタ。利用制限、課金、無人起動を勝手に開始しない。

## 使い方

実問題をセットアップした後、`integrations/search/plan.example.json`の記入済みコピーを作成する。

```sh
python3 -m integrations.search.cli init --plan experiments/search-plan.json
python3 -m integrations.search.cli ask <search-id>
python3 -m integrations.search.cli task <search-id> <trial-id>
# 別worktreeまたは現行セッションが指定された親とアクションで実装
python3 -m integrations.search.cli start <search-id> <trial-id>
# mission・Beads予約の下で従来どおりビルドとharness eval
python3 -m integrations.search.cli tell <search-id> <trial-id> --artifact <child-artifact> --run <dev-run-id>
python3 -m integrations.search.cli status <search-id>
python3 -m integrations.search.cli verify <search-id>
python3 -m integrations.search.cli render <search-id> --out reports/search-view
```

生成・コンパイル・基盤失敗は`fail --reason`、研究方針の都合で諦める場合は`abort --reason`。これらを勝率0という架空の評価に変えない。いずれもtrial上限は消費するため、失敗率・評価コストは別に追う。

## 報酬・照合・制約

`init`で固定した **dev suite hash／基準artifact／報酬変換／生成アクション定義**だけを使う。`tell`では完成run、candidate・baseline一致、露出台帳、試行結果の全件成功、集計の再計算、評価器hashを照合する。同じtrialへの同じtellは冪等、異なる結果の再tellは拒否。`audit`は選択に使えない。差が小さいときの`inconclusive`も正常な報酬として使えるが、**最終採用の証拠ではない**。

探索状態はGit common-dirの`ahc-harness/search/<search-id>/`に保存され、同じcloneのworktreeが共有する。計画を変更するときは新ID。checkpointはSHA-256で照合してから使い、破損時に木をリセットしない。TreeQuestのcheckpointはローカル生成のpickleであり、外部から受け取ったpickleは読み込まない。PythonとTreeQuestの版を揃える。

**既知の限界**：この版の探索状態はhashつき原子的checkpointであるが、完全なWAL型の回復系ではない。worker自動起動、リソースに応じた自動モデル選択、並列生成器、生成成功率まで組み込んだバンディットは未実装。実効的なAI workerは、ソルバー改変・パラメータ検証・結果の取り込みを担う別層として拡張する。
