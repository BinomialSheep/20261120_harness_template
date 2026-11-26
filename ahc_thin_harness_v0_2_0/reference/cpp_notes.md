# C++ のメモ（worker とオーケストレーター向け）

## ビルド

- 手元のビルドは config.json の `solver.build`。AtCoder（2026 年の環境）は GCC 15.2、`-std=gnu++23 -O2 -march=native -DATCODER -DONLINE_JUDGE -ftrivial-auto-var-init=zero` など（`AtCoderのコンパイルオプション.txt`）。`-DNDEBUG` は付かないので、提出でも assert は動く。
- 手元の g++ の版が違うと、速さが数％変わることがある。時間ぎりぎりの設定は提出して確かめる。
- QCFium 法（`#pragma GCC optimize("O3,unroll-loops")` などをソースの先頭に書く）は、効くことも遅くなることもある。入れるなら batchcheck の `--baseline` で対にして測る。`-march=native` があるので `#pragma GCC target` は普通は要らない。

## 時間

- 時間は `std::chrono::steady_clock`（または `clock_gettime`）で測る。`rdtsc` を直接読むと、`--time-scale`（時計を遅く見せて時間を k 倍にする）が効かない。
- 内部の締め切りは、制限時間より少し短くする（例：2.0 秒なら 1.9〜1.95 秒）。手元の判定は終了をブロックして待つので、記録される時間は実際の終了に近い。

## 定数を外から変える（`python3 -m thin.cli tune` 用）

`-DTUNE` を付けたときだけ環境変数で値を変え、付けないときは定数になるようにする。提出版は環境変数に依存させない。

```cpp
#ifdef TUNE
#include <cstdlib>
#define PARAM(type, name, value) static const type name = std::getenv("AHC_" #name) ? (type)std::atof(std::getenv("AHC_" #name)) : (value)
#else
#define PARAM(type, name, value) static constexpr type name = (value)
#endif
PARAM(double, TEMP_START, 2.0);
PARAM(int, BEAM_WIDTH, 20);
```

調整のあとは、選んだ値を `PARAM(...)` の既定値に書き込む。
