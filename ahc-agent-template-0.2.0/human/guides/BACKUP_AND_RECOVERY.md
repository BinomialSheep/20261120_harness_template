# バックアップと復旧

**Gitだけでは全部を復旧できない。** 重要なのは以下の三つ。

| 対象 | 保存先の例 | 手段 |
|---|---|---|
| ソース・設定・共有資料 | 通常のGit | ローカルcommit、許可されたremote push |
| 研究state・凍結ケース・run・モデル・artifact | Git common-dir/ahc-harness | state backup + evidence全体の別媒体コピー |
| Beads DB | 統合checkoutの`.beads/` | 検証したDolt-native backup |

研究stateは`python3 -m harness backup --out <新しいファイル>`。これはevidenceとBeadsを含まない。
実験中のファイルを単純コピーすると不整合になる場合がある。書込みを止めるか一貫したsnapshot手順を用いる。
worktreeの作業中checkpoint、`human/local/`、原本`problem/incoming/`も別途保護する。
同じディスクのbackupだけではディスク消失へ備えられない。

壊れたstateは`restore --from ... --expected-store-id ...`で明示復旧。健全なstoreを昔の予算へ巻き戻す操作は拒否する。
復旧後はpausedになり、backup以降の消費と稼働ジョブを照合してから再開する。
Git snapshotを自動的に権限・残予算へ戻さない。別clone・別PCへの移動は自動連携ではなく明示的な移行。

---
対象: AHC Agent Harness Template 0.2.0（旧版から継承）。更新日: 2026-10-30。説明・記載例であり、これ自体は新しい実行許可ではありません。
