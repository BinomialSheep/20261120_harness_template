公式配布のgenerator / tester / visualizer等、およびそのラッパーを配置する。
最小ランナーは `{binary}`, `{seed}`, `{result}` を埋めた `judge.command` を実行し、`result` に以下のJSONを作ることだけを要求する。

有効: `{"status":"ok","valid":true,"score":123.4}`
無効: `{"status":"invalid","valid":false,"score":null,"reason":"..."}`

ラッパーがRust・C++製のtesterにソルバーのコマンドを渡して対話することも、生成器→ソルバー→visualizerを順に呼ぶことも可能。公式の出力解析やスコアの意味は各問題のラッパーが担う。0点と失格を同一視しない。
