#import <XCTest/XCTest.h>
#import <dlfcn.h>
int main(int argc,char **argv){@autoreleasepool{
 void *handle=dlopen(argv[1],RTLD_NOW);
 if(!handle){fprintf(stderr,"%s\n",dlerror());return 2;}
 XCTestSuite *suite=[XCTestSuite defaultTestSuite];
 [suite runTest];
 XCTestRun *run=suite.testRun;
 printf("VERIFIED XCTest: %lu tests, %lu failures\n",(unsigned long)run.executionCount,(unsigned long)run.totalFailureCount);
 return run.executionCount == 0 || run.totalFailureCount != 0;
}}
