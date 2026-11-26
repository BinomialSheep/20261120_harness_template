// Synthetic fixture: choose k indices. --max/--min is the good strategy; default is the baseline.
#include <algorithm>
#include <iostream>
#include <numeric>
#include <string>
#include <vector>
int main(int argc,char** argv) {
    int n,k; long long salt;
    if(!(std::cin>>n>>k>>salt)) return 1;
    if(n<=0 || k<0 || k>n) return 2;
    std::vector<long long> a(n); for(auto& x:a) if(!(std::cin>>x)) return 3;
    std::vector<int> p(n);std::iota(p.begin(),p.end(),0);
    std::string mode=argc>1?argv[1]:"first";
    if(mode=="--max") std::sort(p.begin(),p.end(),[&](int x,int y){return a[x]>a[y];});
    if(mode=="--min") std::sort(p.begin(),p.end(),[&](int x,int y){return a[x]<a[y];});
    for(int i=0;i<k;++i)std::cout<<p[i]<<(i+1==k?'\n':' ');
    std::cout.flush();
}
