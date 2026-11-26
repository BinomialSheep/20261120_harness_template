// Synthetic interactive fixture. Receives bounds, never the secret from the input file.
#include <iostream>
#include <string>
int main(int argc,char** argv){
    std::string protocol;int lo,hi,maxq;
    if(!(std::cin>>protocol>>lo>>hi>>maxq) || protocol!="SEARCH_V1")return 1;
    bool linear=argc>1&&std::string(argv[1])=="--linear";
    for(int q=0;q<maxq && lo<=hi;++q){
        int x=linear?lo:(lo+hi)/2;
        std::cout<<"? "<<x<<std::endl;
        std::string r;if(!(std::cin>>r))return 2;
        if(r=="="){std::cout<<"! "<<x<<std::endl;return 0;}
        if(r=="<")hi=x-1;else if(r==">")lo=x+1;else return 3;
    }
    return 4;
}
