#include <iostream>
#include <vector>
int main(){int n,k;if(!(std::cin>>n>>k))return 1;std::vector<int>a(n);for(int&i:a)std::cin>>i;for(int i=0;i<k;i++)std::cout<<i<<' ';std::cout<<'\n';}
