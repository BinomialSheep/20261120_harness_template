#include <iostream>
#include <vector>
#include <algorithm>
#include <numeric>
int main(){int n,k;if(!(std::cin>>n>>k))return 1;std::vector<int>a(n),idx(n);for(int&i:a)std::cin>>i;std::iota(idx.begin(),idx.end(),0);std::sort(idx.begin(),idx.end(),[&](int i,int j){return a[i]>a[j];});for(int i=0;i<k;i++)std::cout<<idx[i]<<' ';std::cout<<'\n';}
