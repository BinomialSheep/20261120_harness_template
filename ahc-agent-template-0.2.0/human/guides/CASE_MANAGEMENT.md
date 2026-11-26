# ケースの生成と管理

入力の正本は共有ストアの`casesets/<case_set_id>`。各ケースに入力hash、生成seed、block、タグ、weightを持たせる。
ケースをGitへ大量コピーせず、集合定義と要約をGitへ置く。

## 既存入力を使う

`cases/import.example.json`を実際のパスへ書き換え、`python3 -m harness cases import --spec ...`。
同じ内容を別名で複製すると独立性を水増しするため、同一入力bytesは既定で拒否する。乱数反復はsuiteの`solver_seeds`に書く。

## 生成器を使う

`cases/generator.example.json`へ実際のラッパargvと依存ファイル、生成seedを書く。
この入口は「1seedを1回起動し、stdoutに1入力」。公式生成器が複数ファイルを出す場合は薄い問題別ラッパを作るか、生成済みファイルをimportする。
開始時に生成器snapshotを作り、予定件数を研究予算へ予約する。途中失敗は部分入力を残し、成功集合へ混ぜない。

## ケースを分ける理由

- smoke: 接続と合法性をすぐ確認。
- dev: 日常の比較・係数選択。
- diagnostic: 原因が分かりやすい、あるいは失敗しやすい局面。
- audit: 候補選択から離した確認。
- stress: 大きい/端の入力、時間・メモリの確認。

タグは入力サイズや構造を表す。生成後のスコアを見て「都合の良いケース」だけをdevと呼ばない。
同じcase_setからsubsetを選べるが、auditへ分けただけで秘密にはならない。エージェントが読めない評価器が必要なら別環境へ分離する。

ケース確認は`python3 -m harness cases verify <id>`。生成器変更時は新しい版を作り、古い入力bytesを保存する。

---
対象: AHC Agent Harness Template 0.2.0（旧版から継承）。更新日: 2026-10-30。説明・記載例であり、これ自体は新しい実行許可ではありません。
