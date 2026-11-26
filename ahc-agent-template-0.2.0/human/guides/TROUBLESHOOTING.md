# 困ったとき

| 症状 | 確認 |
|---|---|
| project unconfigured | コピー先ルートでbootstrap。別リポジトリの中に置いていないか |
| shared state missing/corrupt | 新しい予算を作らずbackupとstore IDを確認 |
| No active mission | 質問は可能。資源を使う操作には依頼からmissionを作る |
| scoreがnull | solver失敗かjudge/testerの失敗かを状態とログで区別 |
| 動くのにinfrastructure_error | wrapperのJSON・終了code・パス・filesへの依存追加漏れ |
| 対話型だけTLE | flush、入力順、EOF、tester側の手番timeoutと全体timeout |
| 乱数seedを変えても同じ | ソルバーがAHC_SOLVER_SEED/CLI引数を実際に使うか |
| 平均だけ強いがbad caseが増えた | cases.csv、入力タグ、最悪差、失敗率を見る |
| frozen input / oracle changed | 評価中の原本変更や破損。新しい版として修正し再実行 |
| Beadsが空に見える | DB未導入/誤ったBEADS_DIR/別clone。再初期化で上書きしない |
| 時間測定が待ち続ける | 学習/評価がshared lockを保持。終了を待つか計画を分ける |
| 提出準備が拒否 | local-only引数・マクロを除いたソースをfreezeしてsmokeを再実行 |

まず`python3 -m harness doctor`と`status`。デモやテストの成功と、公式ツールによる本問題の正しさは別。
大容量ログやtokenをそのまま外部AIへ貼らず、`tools/export_context.py`等で必要な証拠だけを共有する。

---
対象: AHC Agent Harness Template 0.2.0（旧版から継承）。更新日: 2026-10-30。説明・記載例であり、これ自体は新しい実行許可ではありません。
