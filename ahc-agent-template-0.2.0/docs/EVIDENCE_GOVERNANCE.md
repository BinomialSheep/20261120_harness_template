# 評価の用途、露出、統計的判別力（v0.2）

対象は **長期AHC・With AI部門のローカル開発**。大会レギュレーションをハードコードするための文書ではない。

## 1. まず「何に対して良い」かを指定する

`harness eval` は固定ケース集合での改善を推定する。`purpose=dev` の値が良くても、未知の入力、別の生成条件、実測に近いCPU負荷で改善するとは限らない。suiteには目的・入力分布・特徴タグ・シード・計測条件を説明し、その**主張できる範囲**をレポートに書く。

スコア変換 `difference`/`log_ratio`、`maximize`/`minimize`、`case_mean`/`block_mean`は問題に応じて指定する。raw score／ローカル比較値／コンテスト順位点を混同しない。比較結果のCIは開発の繰り返し選択を補正していない。

## 2. holdout の再利用を入力bytesで検出する

すべての公式評価は共有ストア `.git/ahc-harness/exposures.jsonl` に、run、purpose、ケース集合、**各入力のSHA-256**を先に記録する。`audit` は未露出の入力だけを受け入れる。別suite名、別seed名、別ケース集合IDで同一入力を登録しても拒否する。`audit` 後に同じ入力で`dev/diagnostic/stress/smoke`を走らせることも拒否する。journalが壊れていたら停止し、勝手に空にして再実行しない。

```sh
python3 -m harness exposures
python3 -m harness exposures --purpose audit --case-set-id <凍結case-set-id>
```

現行v0.2は、独立して事前登録した複数候補の**共同audit**は未実装。別の候補に同じaudit集合を使いたければ、共同計画・結果の観測順を記録できる専用機構を後で作る。結果を見た後の事後的な「元から共同だった」は禁止。早すぎるauditは貴重な未使用評価を消費する。

**限界**：同一byteの重複を検知するだけで、生成器が意味的に類似の問題を量産した場合や、別clone・別マシンの露出は検知しない。暗号学的アクセス制御や完全なblind holdoutではない。事前に人間／エージェントが入力内容を読んでいた場合も自動検知できない。`--audit-approval`は出所ラベルで、本人確認ではない。

## 3. 事前の判別力チェック

既存の **dev** パイロットrunを使い、独立blockの標本標準偏差から、指定半幅を得るのに必要そうなblock数とsolvers実行回数を概算する。

```sh
python3 -m harness precision <pilot-run-id> --halfwidth 0.005 --solver-run-budget 1200 --candidates 3
```

近似式は `n ≈ (1.96 × SD(block effect) / desired_halfwidth)^2`。これは**粗い精度予測**であって、検出力・有意性・最終結果を保証しない。`case_mean`でblockサイズが違う場合、ケース間の依存や評価対象の変化、逐次選択、複数比較にはこの近似だけでは不十分。`audit`結果を次の開発計画のpilotとして流用することは拒否する。

試行前に「どの結果なら次の行動が変わるか」「判別できなければどうするか」を書く。差が小さく、予算内で測れないなら、測定器／ケース構成を見直す・もっと大きな改善を狙う・情報価値の高い診断をする、を比較する。

## 4. 判定は三つの独立軸

`experiment close` の結果JSONでは、以下を区別できる。

```json
{
  "decision": "rejected",
  "hypothesis_status": "unresolved",
  "research_status": "defer",
  "evidence_runs": ["<run-id>"],
  "reason": "この予算・ケース集合では改善が検出されなかった。永久に無効とは言えない"
}
```

- `decision`: `adopted/rejected/hold/inconclusive/abandoned`（採否）
- `hypothesis_status`: `supported/refuted/unresolved/not_assessed`（知識の確からしさ）
- `research_status`: `continue/defer/stop/not_assessed`（次に予算を使うか）

旧書式のresultでも動くが、空欄は `not_assessed` となり、証明済みの意味にはしない。`rejected`は「効果ゼロが証明された」の意味ではない。

## 5. 補助的な診断

可視化、近傍への摂動、局所的成功率、内部のproxy scoreは研究仮説を生成する道具。本番を代表する主評価ではない。固定仕事量（反復数）とwall-clock制約下の性能を別suiteで測る。時間感度の高い計測は排他・直列で行う。複数workerの負荷による探索量変動は無条件に「同じ乱数seedだから同じ結果」とみなさない。
