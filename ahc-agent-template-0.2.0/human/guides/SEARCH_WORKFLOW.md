# AB-MCTSと候補探索を使う場合

まず普通のAHC開発を開始し、合法なC++ソルバー・公式採点器・dev用suite・missionの予算を揃える。探索器はその後で導入するもの。

- Greedy: 直近の優秀案を深めたい場合。簡単な基準方式。
- Multi-start: まだ全く違う解法がありそうな場合。簡単な基準方式。
- AB-MCTS-A (TreeQuest): 複数候補ができ、広げるか深めるかの配分を試したい場合。導入効果は当該AHCでは未保証。

[固定計画の例](../../integrations/search/plan.example.json) に本物のartifact、dev suite、生成器設定のhashを記入する。`python3 -m integrations.search.cli ask <search-id>`で親とアクションを受け取り、**いま使っているVS Code拡張のエージェント**に渡して改変させればよい。tmuxや常駐デーモンは不要。

`harness eval`が返したrun IDを、`tell`へ渡す。`tell`で自動採用・本番提出は行われない。TreeQuest利用時は別途`treequest==0.3.3`を環境に用意し、導入先で動作確認する。コマンド・限界は[技術仕様](../../docs/SEARCH.md)。
