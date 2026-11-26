# 通常の研究フロー

共有状態・Beads・変更中コードを確認 → 仮説/診断の選択 → 実験計画と予算 → artifactとケースの凍結 → 比較 → 原因と採否 → 次の案。
ユーザーが委任したmission内では複数の実験を続行する。

## よく使うコマンド

```sh
python3 -m harness doctor
python3 -m harness status
python3 -m harness build solvers/000_baseline.cpp --name cand-base
python3 -m harness cases import --spec cases/import.example.json
python3 -m harness cases generate --recipe cases/generator.example.json --work <bead-id>
python3 -m harness cases verify <case-set-id>
python3 tools/make_suite.py --case-set <case-set-id> --direction maximize --purpose dev --out suites/dev.json
python3 -m harness work reserve <bead-id> --op evaluation --estimate solver_runs=200
python3 -m harness eval --suite suites/dev.json --candidate <candidate-id> --baseline <baseline-id> --work <bead-id> --jobs 2
python3 -m harness report <run-id>
python3 -m harness replay <run-id> --trial t0000000 --work <bead-id>
python3 -m harness work settle <bead-id> --outcome done
python3 -m harness render
```

`cases/import.example.json`と`generator.example.json`は書換え必須の例。実際のファイル/引数へ合わせる。
200 solver_runsは「100ケースを候補と基準で1回ずつ」など。repeatが2なら400。再実行も消費する。
起動前に予定数を保守的に課金するため、途中打切りでもrun予算が自動的に無料へ戻らない。

## missionの作成

```sh
python3 -m harness input --kind directive --source human --text '<今回のユーザー依頼の原文>'
# returned input_idを使う。架空の人間承認を作らない。
python3 -m harness mission start --spec experiments/mission.example.json --input <input-id>
```

例の予算をそのままユーザーが承認したとみなさず、依頼に合わせて設定する。
実験の原案と途中変更は`experiment init/amend/close`で分離する。harnessの実行時記録は共有evidence/experimentsへ保存される。
知見・判断の要約を`docs/FINDINGS.md`/`docs/DECISIONS.md`へ残し、証拠IDをリンクする。

## 採用と提出準備

`adopt`は統合checkoutのみ、現在の旧artifact IDを照合。workerの実験完了でbestを動かさない。
`pack`はローカルinclude展開と再コンパイル。pack後のartifactを再評価する。
`tools/prepare_submission.py`は検証済みソースとmanifestをローカルへ出すだけ。AtCoderへ送らない。
実験時だけの引数・環境変数・-Dマクロが必要な候補は、提出形へ固定してからsmokeを通す。

## 切替

Claude Codeが突然止まっても新しいCodexから再開できる。新セッションは確定成果物と生存ジョブを確認し、不要な途中案は保留にできる。
予算はprovider切替でリセットしない。未完了作業を即座に成功/終了扱いしない。終了時のhandoffは任意。
