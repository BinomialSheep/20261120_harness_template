// 末尾の K 個（先頭の K 個と同じくらい）
#include <iostream>
int main() { int n, k; std::cin >> n >> k; for (int i = 0; i < k; i++) std::cout << n - 1 - i << ' '; std::cout << '\n'; }
