// 大きい順に K 個（最適）
#include <algorithm>
#include <iostream>
#include <numeric>
#include <vector>
int main() {
  int n, k; std::cin >> n >> k; std::vector<int> a(n), id(n);
  for (int& x : a) std::cin >> x;
  std::iota(id.begin(), id.end(), 0);
  std::sort(id.begin(), id.end(), [&](int i, int j) { return a[i] > a[j]; });
  for (int i = 0; i < k; i++) std::cout << id[i] << ' ';
  std::cout << '\n';
}
