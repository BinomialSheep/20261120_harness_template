# 回答例: 窓口を替えたら予算は戻る？

**質問**: Claude Codeが途中で止まり、Codexを開いた場合、予算を最初から使える？

**回答**: 同じcloneならmissionと累積消費はGit common-dirの共有ストアに残る。新セッション・別worktree・provider切替ではリセットしない。
記録されていない考察や途中案は引き継がれない場合があるが、凍結artifact・評価結果・予約は別に扱う。
起動中ジョブを確認し、同じ仕事を重ねて実行しない。Beadsのin_progressだけで生存とは判断しない。

**根拠**: [並列作業](../../docs/PARALLEL.md)、[予算](../guides/BUDGET_AND_PERMISSIONS.md)。
これは説明用の例であり、実際の研究の実行許可ではない。

---
対象: AHC Agent Harness Template 0.2.0（旧版から継承）。更新日: 2026-10-30。説明・記載例であり、これ自体は新しい実行許可ではありません。
