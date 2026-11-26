# 人間向け：ゼロからAHCへ展開する

## 1. 必要なもの

WSL2/Linux、Python 3.11以上、Git、C++23のg++またはclang++。AHCの公式ツールがRustなら配布READMEに従ってビルドする。まず全体フォルダをコピーし、`problem/statement.md`と`problem/tools/`へ配布資料を保存する。

`cp config.example.json config.json`を実行。`config.json`はローカル問題専用でGit管理しない。`judge.command`はshell文字列ではなく**argv配列**。公式ジェネレータとtesterを呼ぶラッパー`problem/tools/score_case.py`などを作る。

`score_case.py` は各seedで入力生成→ソルバー（必要なら対話）→公式採点→JSON出力を担当する。`{binary}`がファイルパス、`{seed}`がテスト入力の乱数seed、`{result}`が採点結果の出力先。デモの`examples/toy/tools/score_case.py`を見ればインターフェースが分かるが、**公式スコアの抽出方法はAHCごとに違う**ので、このダミーを本番用に使わない。

## 2. 最初の動作確認

```sh
python3 -m thin.cli demo
python3 -m unittest discover -s tests -v
```

デモは公式AHCとは無関係の合成問題で、実LLMを呼ばない。10workerを模したスクリプトを並列起動して、同一の合成ソルバーを提出→スコア→採用まで試す。デモは5ケースで、**本番設定の1000ケース評価を検証するものではない**。本番の`config.example.json`では1000を既定とする。

## 3. オーケストレーターを立ち上げる

VS CodeのClaude Code/Codex等を開き、このフォルダの`AGENTS.md`と`docs/DESIGN.md`を読ませる。指示例：

> このAHC問題用に公式ツールを接続し、スコアの意味とseed0を確認してから、薄いA/Bラウンドで改善を進めてください。オーケストレーターは全履歴を見る一方、workerには親ソース・課題・問題文・公式ツールしか渡さないでください。Aは10並列各20分、Bは1体2時間。評価はworkerの終了後にオーケストレーターが新しい1000ケースで行い、結果に応じてベストを採用してください。私の提案は指示がない限り研究方針の候補として扱ってください。

外部CLIを設定するまでワーカーを一斉起動しない。`config.json`の`workers.engines`に`codex`と`claude`のargv例を入れてあるが、**手元のCLIの認証・許可・サンドボックス・互換性を確認してから実行**する。10並列はプロバイダ側の同時実行制限や利用枠にも左右される。`--engine manual`を使えば人間/既存のVS Codeセッションで1体ずつ作って、`submit`を使うこともできる。

## 4. 1ラウンドの実行

オーケストレーターは、**問題に固有の10仮説**を `proposals.json` に書く。`templates/ideas.example.json`は型の例。ベストがある場合は最初に`python3 -m thin.cli init-best /path/to/main.cpp`で登録できる。

```sh
python3 -m thin.cli prepare A --ideas proposals.json
python3 -m thin.cli run-workers R0001 --engine codex
python3 -m thin.cli evaluate R0001
python3 -m thin.cli promote R0001 A03  # 改善したと判断した場合のみ
python3 -m thin.cli close R0001

python3 -m thin.cli prepare B --idea '改善内容を具体的に記述する。どの構造を変え何が向上するか'
python3 -m thin.cli run-workers R0002 --engine codex
python3 -m thin.cli evaluate R0002
python3 -m thin.cli close R0002
```

失敗したら `python3 -m thin.cli status`、`runs/R####/state.json`、`docs/OPERATIONS.md`を確認。user-facing報告は **ベストのコード、比較したseed数、各候補の得点・合法性、次に選んだA/Bと理由**だけでもよい。
