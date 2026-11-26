# LLM なしのレポートの例

実験リポジトリ（`20261117_pracitice_ahc_harness`）の記録だけから作った、人間向けの HTML レポートの例。実験リポジトリは読むだけで、何も書き込まない。

## 作り直す

```sh
cd work/report_example
python3 -I cases.py probe                    # 現 best を 120 盤面で、時間 0.5〜8 倍・1 倍 4 回（12 並列で約 6 分）
python3 -I make_report.py --pick             # 「盤面を見る」に載せる seed を選ぶ
python3 -I cases.py boards --seeds 19662,19315,19952,19034,19273   # 選んだ盤面を過去の版と 8 倍の時間で（約 20 秒）
python3 -I make_report.py                    # report.html を書く（1 秒未満）
```

`cases.py` を走らせなくても `make_report.py` は動く（「時間と運の余地」と「盤面を見る」の節が出ないだけ）。

## ファイル

| ファイル | 中身 | 問題ごとに書くか |
|---|---|---|
| `make_report.py` | 記録を読み、集計し、HTML を書く。標準ライブラリだけ | 書かない（特徴量の名前と帯だけ設定） |
| `cases.py` | 公式の `gen`・`vis` とソルバーで盤面を走らせ、`cases.json` に残す | 書かない |
| `slowclock.c` | ソルバーから見える時計を 1/k の速さにする部品。ソースを変えずに「時間 k 倍」を測る | 書かない |
| `view.js` | 盤面の描き方（発射ごとの帯、余分な移動、待ち） | 書く（約 80 行） |
| `features.json`・`cases.json` | 特徴量（壁の数）と、走らせた結果のキャッシュ | — |
