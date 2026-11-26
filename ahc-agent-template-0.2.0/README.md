# AHC Agent Harness Template v0.2.0

**長期AtCoder Heuristic ContestのWith AI部門で、問題の配布物を置いてからエージェントに環境構築と研究を任せるためのテンプレートです。**

C++23 / Python 3.11+ / Linux・WSL2。VS CodeのClaude Code・Codexから利用できます。tmux・クラウドエージェント・APIキーは必須ではありません。

[最初の手順](human/guides/START_HERE.md)｜[人間向けハーネス解説](human/guides/HARNESS_OVERVIEW.md)｜[v0.2追加機能](human/guides/NEW_IN_V020.md)｜[検証記録](VALIDATION.md)

## 3ステップで導入

1. ZIP内のフォルダを**隠しファイルも含めて**新しい問題用フォルダへコピーする。
2. `problem/incoming/`に問題文、入力ジェネレータ、ジャッジ/テスタ、ビジュアライザなどの公式配布物を置く。
3. VS Codeのエージェントに「`AGENTS.md`と`docs/BOOTSTRAP_AGENT.md`に従って、問題ごとの環境をセットアップして」と依頼する。

自分で基盤を検証するには `python3 tools/test.py --strict`、`python3 tools/demo.py`、`python3 tools/check_docs.py`。実問題へ接続するのはセットアップ担当エージェントです。初期の`solvers/000_baseline.cpp`は提出可能な完成ソルバーではありません。

## 共通基盤

| 領域 | 内容 |
|---|---|
| 問題ごとの接続 | 公式原本のhash、batch/interactive/output-only、スコアの最大化／最小化 |
| 評価 | 凍結ケース、solver seed、対応比較、block単位の推定、失敗・実行時間記録、再実行 |
| 実験の証拠 | source/build/oracle/run、実験計画と変更履歴、dataset/modelの来歴 |
| 研究管理 | missionの権限・累積予算・停止条件、Beads、Git worktree間共有状態 |
| v0.2評価統治 | ケースbytes露出検出、audit保護、pilot精度予測、採否と仮説の分離 |
| v0.2研究選択 | 締切を踏まえた既定行動の提案、Greedy・Multi-start・任意TreeQuest AB-MCTS-A |
| 人間の窓口 | `human/guides/`に手順、`human/questions/`に整理したQ&A、`human/notes/`に人間のメモ |

## 想定と対象外

**長期AHC・With AI部門で使う**ことを想定していますが、大会ごとのレギュレーション、禁止事項、AI利用の承認条件などはハーネスのコードに一切固定しません。必要な大会固有条件は展開時に人間が与えます。研究の課金・GPU・採点・削除などの承認制御は大会のレギュレーションとは独立した運用上のものです。

AtCoderの公式・非公式API、スクレイピング、オンライン順位取得、自動提出は**一切同梱しません**。提出物は人間がサイトで提出する前提です。GitHub Issues連携は外部レビュー用途で任意です。

AB-MCTS/TreeQuest・LLMの自動起動／モデル切替は必須ではありません。AB-MCTSは任意依存の選択器で、**この版では探索試行の実行は人間/エージェントが担当し、強さが向上するかは未実証**です。モデルルーティングも将来の拡張であり、現時点では自動化していません。

Git pushではclone固有の研究ストア・Beads DB・大容量証拠はバックアップされません。[バックアップ資料](human/guides/BACKUP_AND_RECOVERY.md)を確認してください。

## 追加コマンド

```sh
python3 -m harness exposures
python3 -m harness precision <dev-pilot-run> --halfwidth 0.01 --solver-run-budget 1000
python3 -m harness next-action --state research/next_action.json
python3 -m integrations.search.cli init --plan experiments/search-plan.json
python3 -m integrations.search.cli ask <search-id>
```

最初の試用は [技術的な評価統治](docs/EVIDENCE_GOVERNANCE.md)、[メタ方策](docs/META_POLICY.md)、[探索器](docs/SEARCH.md)を参照してください。

このZIPは**新規AHC問題へコピーする雛形**です。既存のLoCMやAHCプロジェクトを上書きするアップグレードではありません。
