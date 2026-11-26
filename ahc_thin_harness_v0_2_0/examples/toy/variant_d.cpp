// 最初の値が偶数のとき範囲外の添字を出す（無効になる）
#include <iostream>
int main() { int n, k, a0; std::cin >> n >> k >> a0; for (int i = 0; i < k; i++) std::cout << (a0 % 2 == 0 && i == 0 ? n : i) << ' '; std::cout << '\n'; }
