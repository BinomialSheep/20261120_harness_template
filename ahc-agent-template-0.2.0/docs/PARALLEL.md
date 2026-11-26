# 並列作業の技術契約

コードと可変出力はworktree単位。mission・予算・event・ref・不変evidenceはGit common-dirの`ahc-harness/`で共有する。
独立cloneや別PCは自動的に共有されない。remote URLを共有先の識別に使わない。

- 新しい作業は別issue・別reservation・別worktree。予算予約は共有lockの下で検査する。
- 採用refは統合checkoutが期待する旧IDを照合する。workerはbestやmissionを上書きしない。
- ビルド、ケース、run、sealed data/modelは初めから共有storeへ置く。作業中checkpointは`work/`へ。
- worktree削除前に`workspace check-remove`。未統合commit、未精算予約、unsealed出力、個人メモがあれば止める。
- 私的なノートを削除検査のために読むことはしない。存在と保全の必要性だけ扱う。
- 共有機械lockは公認コマンドの間だけで効く。通常の重い処理はshared、時間敏感な比較はexclusive。

VS Codeの別ウィンドウでよい。tmux/デーモン/常駐LLMは不要。窓口の終了宣言も不要。
ただしVS CodeやWSL終了後も子プロセスが生存する保証はない。起動済み処理と継続的な研究判断を区別する。
