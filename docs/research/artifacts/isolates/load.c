#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "quickjs.h"
static double now(){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e9+t.tv_nsec;}
int main(int argc,char**argv){
  FILE*f=fopen(argv[1],"rb");fseek(f,0,2);long n=ftell(f);rewind(f);char*s=malloc(n+1);fread(s,1,n,f);s[n]=0;fclose(f);
  JSRuntime*rt=JS_NewRuntime();JSContext*ctx=JS_NewContext(rt);
  double a=now(); JSValue fn=JS_Eval(ctx,s,n,"svelte-compiler.js",JS_EVAL_TYPE_GLOBAL|JS_EVAL_FLAG_COMPILE_ONLY); double b=now();
  if(JS_IsException(fn)){printf("compile error\n");return 1;}
  size_t len; uint8_t*bc=JS_WriteObject(ctx,&len,fn,JS_WRITE_OBJ_BYTECODE);
  JSMemoryUsage mu; JS_ComputeMemoryUsage(rt,&mu);
  printf("source %ld bytes; compile %.1f ms; bytecode %zu bytes; runtime malloc %lld, atoms %lld, functions %lld, bytecode_size %lld\n",n,(b-a)/1e6,len,(long long)mu.malloc_size,(long long)mu.atom_count,(long long)mu.js_func_count,(long long)mu.js_func_code_size);
  JS_FreeValue(ctx,fn);
  for(int r=0;r<3;r++){
    JSRuntime*rt2=JS_NewRuntime();JSContext*c2=JS_NewContext(rt2);
    double x=now(); JSValue g=JS_ReadObject(c2,bc,len,JS_READ_OBJ_BYTECODE); double y=now();
    JS_ComputeMemoryUsage(rt2,&mu);
    printf("fresh runtime: read bytecode %.2f ms; malloc %lld\n",(y-x)/1e6,(long long)mu.malloc_size);
    JS_FreeValue(c2,g);JS_FreeContext(c2);JS_FreeRuntime(rt2);}
  return 0;}
