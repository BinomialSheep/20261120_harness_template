# v0.2で何が増えたか

従来の基本機能（公式テスタをラップする、ケース凍結、対応比較、ソルバー／ジャッジ時間、Git worktree共有状態、Beads、mission・予算、証拠、バックアップ）は維持。**With AI部門で長期AHCへ参加する研究用**の入り口を明確にした。

今回の重点は「大量に実験できること」から「次の意思決定に役立つ実験を選び、別の条件に一般化しない主張をしないこと」へ移った点。

1. `exposures`：入力bytesを基準としてholdoutの再利用をチェック。すでに見た入力は別名でも未使用ではない。
2. `precision`：pilotのblock間ばらつきから、狙う精度に必要そうな予算を見積もる。予測であって保証ではない。
3. `experiment close`：採否（実務）、仮説の確からしさ（知識）、次に予算を使うか（研究）を別々に記録。
4. `next-action`：締切と現状に応じた既定の行動提案。自動ジョブ・自動課金なし。
5. `integrations/search/`：Greedy、Multi-start、任意のTreeQuest AB-MCTS-Aを同一ask/tellインターフェースにする。公式採点のrunだけを報酬にする。

実装の詳細は [評価とholdout](../../docs/EVIDENCE_GOVERNANCE.md)、[メタ方策](../../docs/META_POLICY.md)、[探索器](../../docs/SEARCH.md)。

**意図的に入れていないもの**：AtCoder APIや自動提出、特定年の大会レギュレーション、LLM provider APIキー、常駐ジョブ、メタ方策が学習済みだという主張。参加条件は必要な場合に人間が別途渡す。ハーネスの予算承認は、大会規則とは独立。
