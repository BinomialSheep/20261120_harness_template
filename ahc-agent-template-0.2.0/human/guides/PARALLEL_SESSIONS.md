# VS Codeで並行セッションを使う

長時間の学習中に、別セッションで短い改善を試す用途を想定している。
同じcheckoutで二つのエージェントに同じファイルを編集させず、別worktreeを使う。

```sh
python3 -m harness workspace create ../ahc-short --branch short-experiment
```

準備済みのコピーでcommitと変更整理が必要。自動stashや他人の未コミット変更の破棄はしない。
新フォルダを別のVS Codeウィンドウで開く。Beads・共有状態は同じcloneを参照する。
各セッションへ「既存の長時間処理は触らず、別issueの候補をこの範囲で試して」と伝えればよい。

mission・予算・不変evidenceは同じGit common-dirにある。二つの予約の合計で上限を超えない。
コード/作業中出力はworktreeごと、完成artifact/run/ケースは共有で見える。
統合checkoutだけが採用ref・mission・STATUSを確定する。実験成功だけで他の作業のbestを上書きしない。

時間敏感な最終検証は重い処理と排他になる。数日学習を止められないなら、短期案は実装・固定仕事量評価を先に進め、時間検証を後に分ける。
GPU学習でもCPU/I/O/RAMを使うため「GPUとCPUだから全く競合しない」とは扱わない。

セッション切替に終了宣言や必須handoffは不要。ただしVS CodeやWSLを閉じた後の処理存続は保証しない。
Beadsの着手中状態は生存チェックではなく、共有予約のPID/開始情報を確認する。

削除前は`python3 -m harness workspace check-remove`。完成していないcheckpoint・メモ・未統合commitは自動で消さない。

---
対象: AHC Agent Harness Template 0.2.0（旧版から継承）。更新日: 2026-10-30。説明・記載例であり、これ自体は新しい実行許可ではありません。
