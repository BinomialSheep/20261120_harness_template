# AHC評価設計

## 生スコア、比較指標、大会得点を分ける

`raw_score`は独立ジャッジの値。`metric.direction`でmaximize/minimizeを明示する。
`difference`はmaximizeならcandidate-baseline、minimizeならbaseline-candidate。
`log_ratio`は正のスコアだけに適用し、minimizeならlog(baseline/candidate)。ゼロ・負値に定数を足してごまかさない。
ローカル指標の改善は大会の相対点や順位改善を直接保証しない。

## 実験単位

同じケースbytes・solver seed・資源上限で候補/基準を連続実行し、先に動かす側を乱択する。ケース順も事前seedで乱択する。
ソルバーの乱数反復はケース内で平均する。入力や生成器の系列が相関する場合は同じblockへ入れる。
区間は事前定義blockを再標本化するbootstrap。seed反復数を独立入力件数として扱わない。

`case_mean`（既定）はケースweight付き平均。clusterを再標本化した分子/分母から区間を作る。
`block_mean`はblock内weight平均の単純平均。blockサイズが不揃いなら推定対象が異なる。
weightは入力母集団をどう評価したいかで事前に決める。どちらも最適化結果を見て都合よく選ばない。
`raw_score_aggregate`はケース内反復平均の合計/weight付き平均であり、そのまま大会scoreとは呼ばない。

## 欠損と失敗

失敗を含む実験の全体判定はinvalid。正常ケースだけの詳細は診断用として残し、採用根拠にしない。
TLE/WAは同じ値ではない。基準が失敗して候補だけ成功したときも、解法有効性の改善を別途説明して採用判断できるが、比較平均を偽造しない。
`cases.csv`と`summary.json`にケース差、タグ、相手役（候補/基準）別状態、悪化ケース、時間統計を残す。

## ケース集合

smoke=接続、dev=選択、diagnostic=原因分析、audit=凍結後の確認、stress=負荷/端条件。
auditを見ながら調整したら未使用集合ではない。入力hashと集合ID・blockを露出台帳に残す。
`--audit-approval`は今回の保留集合を使う了承/実験計画の参照で、AHC AI利用規則ではない。強制的なデータ秘匿でもない。

## 判定と探索

positive_evidence / negative_evidence / inconclusive / insufficient_blocks / invalid。
20blockは既定の小標本警告閾値で十分な検出力の保証ではない。実用差`minimum_effect`を使える。
多数候補・繰返し観測の補正は未実装。最良候補を未使用ケースで確認する。
決定的入力集合に対する合計は観測値、未知入力への区間は分布・block設計に依存する推定である。

## 固定仕事量と本番時間

同じ反復数による品質、同じ時間による強さ、1ケースの計測時間、研究全体の予算を別にする。
時間依存ソルバーの最終評価には`timing_sensitive=true`・jobs=1を使う。公認の重い処理と排他にする。
通常の並列評価は探索用に便利だがCPU競合によって探索量が変わり得る。
solver wallとjudge wallを分離。対話ではtester totalとtesterが報告したsolver timeを区別。未計測値はnull。
