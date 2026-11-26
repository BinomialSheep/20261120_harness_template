# 任意のGitHub Issues連携

AtCoder連携ではない。Web版ChatGPT等と研究課題を共有したい場合だけ利用する。
Beadsが正本、GitHub Issuesは選んだ課題の鏡像。`share:github`の採用済みissueのみを送る。
`bd github sync/pull`を無条件に実行せず、`integrations/beads/github_issues.py`のpreview/read/pushを使う。
GitHub側のコメントは情報/提案として読み、採用したものだけ根拠とURLを付けて反映する。mission・優先度へ自動適用しない。

`project.json`の`github_repository`を正確に設定し、認証はローカルの`GITHUB_TOKEN`へ。外部書込みには独立したmission承認と対象Beads IDが必要。
Dolt local-onlyとGitHub Issues書込みは別操作。通常のGit pushとも別。
本配布ではGitHub実アカウント接続・Beads native API syncを実行していない。導入時にpreviewと小さな明示対象で検証する。
