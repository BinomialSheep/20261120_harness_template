# Beads

未完了タスク、依存、提案、研究系統をBeadsで管理する。研究予算・採点・data/modelの正本はBeadsではない。
`harness.toml`の1.3.1は元のCodinGame版の参照版。インストールしたCLIのhelpと`make beads-check`で挙動を確認する。
導入は`tools/install_beads.py --download`、初期設定は`tools/setup_beads.py --prefix <prefix>`を参照。
既存のDBやユーザーのhooksを壊さない。勝手に最新版へ更新しない。

ローカルDoltを使い、`dolt.local-only=true`を維持する。tokenをDB/会話へ保存しない。
Beadsの汎用SESSION CLOSE PROTOCOL、全作業issue化、bd remember、無条件pushはこのプロジェクトの規約より優先しない。
proposalは`proposal`等で分類してdeferし、思いつきだけでreadyへ入れない。
`bd update --claim`が依存を確認すると思い込まない。readyから選び、その後missionの権限を確かめる。

同じcloneのworktreeでは`integrations/beads/bridge.py`が統合checkoutのDBを指す。強制的な分散調整ではない。
台帳とBeadsの対応が不明なら再初期化で解消しない。
GitHubに通常のソースをpushしてもissue DBの完全バックアップにはならない。Dolt-nativeのローカル/別媒体backupを設定する。
`bd export`は閲覧/移植用でDB履歴の完全backupではない。メール等を含む可能性があるので丸ごと公開しない。
