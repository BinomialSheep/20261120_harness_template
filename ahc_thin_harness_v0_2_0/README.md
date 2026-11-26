# AHC thin harness 0.2.0

長期 AHC の AI 部門向けの、薄い研究ハーネス。1体のオーケストレーターが全体を見て行動を選び、worker が独立に実装し、評価と採否の数字は LLM を使わない CLI が出す。

- 行動 A：別々の案を並列に試す（1〜10案、既定6案）。
- 行動 B：有望な1本を1体で深める（多数ケースでの調整もできる）。
- 行動 C：測定・診断だけをする（成果は report.md）。

```text
prepare ─▶ go ─────────────────────────────▶ promote ─▶ close
           worker（作業場所はリポジトリの外）     採るときだけ    記録・レポート・消費
             └▶ 評価：段階式の足切り（100/300/1000 件）→ 首位を未使用の seed で確認
```

## はじめに

配備は [SETUP.md](SETUP.md)、毎回の手順は [AGENTS.md](AGENTS.md)、コマンドと復旧は [docs/OPERATIONS.md](docs/OPERATIONS.md)、
設計と制限は [docs/DESIGN.md](docs/DESIGN.md)、確かめたことは [docs/VALIDATION.md](docs/VALIDATION.md)、0.1.0 からの変更は [CHANGELOG.md](CHANGELOG.md)。

```sh
python3 install.py ../my-ahc            # プロジェクトへ配備（既存ファイルは上書きしない）
python3 -m thin.cli demo                # 模擬問題で一周（LLM なし）
python3 -m unittest discover -s tests   # テスト
```

## 中身

| 場所 | 役割 |
|---|---|
| `thin/` | ループの本体（標準ライブラリだけ）。prepare・go・evaluate・promote・close・status・limits |
| `thin/tools/` | LLM なしの補助：report（HTML レポート）、probe（時間 k 倍）、tune（Optuna）、doctor、audit。`python3 -m thin.cli <名前>` で呼ぶ |
| `worker/` | worker の作業場所に写すもの（課題の雛形、quickcheck、batchcheck） |
| `adapters/` | 汎用の採点アダプタ（生成器→解→ビジュアライザ、対話型）と公式ツールのビルド |
| `reference/` | チェックリスト、AtCoder のコンパイルオプション、C++ のメモ |
| `human/` | INBOX.md（人間の思いつき）、private/（AI が開かないメモ） |
| `templates/` | 案の JSON、研究の記録、盤面の描き方の例 |
| `examples/toy/` | デモ用の模擬問題 |

## 方針

1. 薄さを残す。足すのは不具合と運用停止の解消、LLM を使わない補助だけ。補助は使わなくても回る。
2. トークンは考えることと書くことにだけ使う。待つ・測る・調整する・集計する・図にするのは CLI。
3. 途中で切れても捨てない、巻き込まない。利用上限・通信断・環境の故障を、候補の失敗と分ける。
4. 実機で確かめた設定だけを既定にする。
5. 配備の手間を減らす。1回だけ読む手順（SETUP.md）と毎回読む指示（AGENTS.md）を分ける。
