# 新規AHC環境の構築手順

人間がフォルダをコピーし公式配布物を格納した後、エージェントが実施する。
これは配布時点のコンテスト制約を表す文書ではない。渡された最新の依頼と追加資料だけを適用する。

## 0. 原本と環境

`problem/incoming/`を列挙して、問題文、生成器、テスタ/ジャッジ、可視化器の役割を整理する。zipは安全なパスへ展開し原本を残す。
各配布物の取得元（提供された場合）、hash、版、ビルド方法を`problem/tooling-manifest.json`へ記録する。
問題文不足など解法に必要なものだけをまとめて質問する。現行AI規則をWebで自動収集しない。
Python 3.11+、Git、g++を確認する。RustツールならCargo等を追加で確認する。必須の実装言語を決め打ちしない。
テンプレート自体のソルバービルドはC++対応なので、それ以外はartifact builderの拡張として明示する。

## 1. コピーの初期化と基盤テスト

```sh
python3 tools/bootstrap.py --problem ahcXXX_a --name my-ahc --init-git
# GitHubを使う場合だけ --github owner/repository を追加
python3 tools/test.py --strict --record
python3 tools/demo.py
```

`project.json`とclone固有の共有ストアができる。再実行しても研究履歴や予算をリセットしない。
全テストは一時プロジェクトを使用する。デモ成功は実問題の接続成功を意味しない。
この段階でGitの認証・remote作成・pushは不要。

## 2. 任意のBeads

既存bdを調べ、版とhelpを記録する。未導入なら人間の環境構築依頼の範囲内で、公式releaseのchecksum検証後に導入する。
`tools/install_beads.py --download`は明示操作。初期化前に`bd metrics off`。
`python3 tools/setup_beads.py --prefix ahcxxx`で既存AGENTS/hooksを保つ。既存DBを再初期化しない。
`make beads-check`でローカル実挙動を確認する。使えなければ未検証と記し、評価ハーネス自体の検証は進める。
詳細は`docs/BEADS.md`。旧TASKS一覧を新しい手書きDBとして増設しない。

## 3. 技術的な問題契約

`problem/RULES.md`に入力・出力・採点・終了・境界条件をまとめる。
`problem/contract.json`を実際のツールに合わせて編集する。時間2秒等の初期値は例で、本問の値ではない。
- 非対話: ソルバーを実行後、出力ファイルを独立ジャッジへ渡す。
- 対話: 公式testerがソルバーを起動し、隠れた入力を保持する。
- 出力のみ: 既存出力の再採点。対話ログを無条件にこのモードへ流さない。
`problem/judge_adapter.py`等は公式スコア出力をschemaへ変換する薄いラッパ。全AHC共通の`Score =`を仮定しない。
採点エラー・WA・TLE・REを区別し、解釈不能ならinfrastructure_errorとする。

## 4. 公式ツールの接続・反例テスト

公式サンプル/手で分かる小ケースで一致を確認する。合法baselineと、不正出力/timeout/EOFなどの負例を用意する。
生成器とジャッジのソース・実行体をcontract/recipeの`files`へ指定する。動的依存は別途記録する。
対話型は、初期入力と逐次応答がprotocolどおりか、flush不足を検出するかを確認する。
`verified=true`はこの接続検証を行った後にし、`evidence`へ具体的な証拠を記す。デモで代用しない。

## 5. ケースと最初の比較

人間提供のケースは`cases import`、生成器は`cases generate`へ接続する。
生成に資源が必要なら、今回依頼から導入用missionと小さい上限を記録する。数千・数万ケースを無断生成しない。
Smoke、dev、diagnostic、audit、stressの目的を決める。最初から巨大な固定保留集合を作る必要はない。
`tools/make_suite.py`で最大化/最小化を明示。ソルバーseed対応を実コードで確認し、未対応ならunknownとする。
baseline対baselineのA/A、既知の改善、既知の悪化を確認する。乱択反復を入力数として数えない。

## 6. 人間への引き渡し

`human/guides/PROBLEM_ONBOARDING.md`へ、その問題の実際のコマンド、baseline、case_set_id、採点の意味、可視化方法、未確認事項を埋める。
`docs/STATUS.md`を生成し、Beadsに現実に未解決の仕事だけ登録する。
導入missionは完了し、研究missionの目的・資源・期間に不足があればまとめて確認する。
既に委任済みの研究範囲があるなら、その範囲で実験を継続してよい。毎実験で指示待ちへ戻らない。

## 7. v0.2の評価品質・研究方針（環境構築後）

- `docs/EVIDENCE_GOVERNANCE.md`を読み、各suiteの**推定対象と用途**を明記する。devは反復利用可、auditは開発に使ったことのない別の入力bytesで凍結する。`harness exposures --purpose audit --case-set-id <id>`で事前検査する。
- baselineのA/A比較と、改善・悪化既知の参照を測定する。できればpilotのblock効果から`harness precision`で目標精度と予算の整合を事前確認する。判別不能なら問題を整理する。
- `experiment close`の結果には`decision`、`hypothesis_status`、`research_status`を別々に書く。失敗＝仮説の反証と断言しない。
- 対象の大会締切と研究予算が明示されたら、`research/next_action.example.json`を参考に`harness next-action`を利用できる。ただし出力は助言で、実行・予算承認を代行しない。
- Greedy／Multi-start／任意のTreeQuest AB-MCTS-Aは、baseline・dev測定と適切な研究課題が揃った後にだけ使用を検討する。TreeQuestはオプションとして導入先で実機試験する。詳細は`docs/SEARCH.md`。
- モデル選択は現時点で自動化されない。改変生成器の設定（モデル、推論量、使用文脈）と実コストを追跡し、後から比較可能にする。

このテンプレートの対象用途は**長期AHCのWith AI部門**。短期AHCや他部門向けの特別な条件分岐は搭載しない。大会・年度に固有のレギュレーションをコードやハーネス既定に追加しない。ハーネスの個別資源承認は大会規則とは無関係な研究運用上の制御。
