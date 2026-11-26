# AHC Agent Harness Template 0.2.0 — 検証記録

実施日: **2026-10-30**。これらは配布ハーネスのオフライン試験であり、特定AHCの公式ツール・実スコア・参加条件への適合を証明するものではない。

## 結果

| 検査 | 結果 |
|---|---|
| 全オフライン回帰 | **132件成功、失敗0・エラー0・skip 0**（`python3 tools/test.py --strict --record`） |
| v0.2の追加テスト | **20件**（露出保護、精度、判定軸、締切、探索ask/tell、改ざん拒否を含む） |
| 非対話（最大化・最小化）／対話の模擬問題 | 対応する回帰テストで正常動作・再実行確認 |
| 文書リンク | **21件成功、private notes非読取** |
| ZIPを空白入り別名ディレクトリへ解凍 | manifestのSHA-256全件一致、文書検査、`bootstrap --init-git`、`doctor`成功 |
| Git/研究ストアの初期状態 | ZIPには認証情報、`project.json`、`.git`、Beads DB、既存missionを含めない |
| 未設定の大会締切 | `next-action`がエラーを返し、締切を勝手に補完しないことを確認 |

[テスト結果JSON](validation/offline-tests.json)には132件の結果、実行時刻、テスト対象のソースfingerprint、ネットワーク非使用を記録した。`FILE_MANIFEST.json`にはファイルごとのSHA-256を保存している。

## 確認した主要な性質

- 新旧の実験形式、最大化/最小化、0や負スコア、欠損/重複/失格/タイムアウト、非対話・対話・出力再採点。
- ジャッジ・入力ケース・ソルバーの凍結と再検証、worktree間の共有予算・予約、旧cloneとの独立、証拠保管・再実行。
- 同じケースbytesを別名のsuiteへ移してもauditで未使用扱いしないこと。露出台帳を壊すとfail closed。
- pilotの粗い精度見積、予算不足の判断、締切優先の既定方策、三軸の実験結果。
- Greedyによるask/tell、固定dev runからの報酬再計算、二重tellの冪等性、ソース・checkpoint・探索計画の改変拒否、audit runの混入拒否。

## 未検証・限界

- **実際のAHCの公式ツール一式との接続**。問題一式を人間が提供した後に、問題固有のアダプタをエージェントが構築・検証する。
- **TreeQuest v0.3.3の実ライブラリを利用したAB-MCTS-Aの動作・実競技成績**。この環境には`treequest`が入っていないため、Greedy/Multi-startの動作とは区別する。
- **Beads本体の実DBとGitHub実アカウント**。任意連携であり、配布先で個別に確認が必要。
- 予算に対する精度予測は正規近似による目安で、検出力保証・多重比較補正ではない。holdoutの安全性は同一clone内の入力byteの重複まで。
- AB-MCTSで改変するworkerの自動起動、LLMモデルの適応選択、常駐実行はない。`ask`は研究計画を作るだけで、実行許可・課金・外部操作を付与しない。
- 特定年・特定大会のレギュレーションは同梱も自動推定もしていない。長期With AI用途の宣言は各大会への適合証明ではない。

## コピーしたフォルダで再検証するには

```sh
python3 tools/test.py --strict --record
python3 tools/demo.py
python3 tools/check_docs.py
```

`tools/demo.py`は問題固有の接続ではなく、同梱の模擬問題の動作例である。`bd`がある場合は別途`make beads-check`を行う。
