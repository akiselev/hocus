#include <stdio.h>
#include <string.h>
#include <time.h>
#include "quickjs.h"
static double now(){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e9+t.tv_nsec;}
int main(){
  int N=2000; double t0=now();
  for(int i=0;i<N;i++){JSRuntime*rt=JS_NewRuntime();JSContext*c=JS_NewContext(rt);JS_FreeContext(c);JS_FreeRuntime(rt);}
  double t1=now(); printf("runtime+context create+free: %.1f us\n",(t1-t0)/N/1e3);
  JSRuntime*rt=JS_NewRuntime(); JSMemoryUsage mu;
  JS_ComputeMemoryUsage(rt,&mu); printf("empty runtime: malloc_size=%lld atoms=%lld shapes=%lld\n",(long long)mu.malloc_size,(long long)mu.atom_count,(long long)mu.shape_count);
  JSContext*ctx=JS_NewContext(rt);
  JS_ComputeMemoryUsage(rt,&mu); printf("runtime+1 context: malloc_size=%lld atoms=%lld atom_size=%lld shapes=%lld shape_size=%lld objs=%lld\n",(long long)mu.malloc_size,(long long)mu.atom_count,(long long)mu.atom_size,(long long)mu.shape_count,(long long)mu.shape_size,(long long)mu.obj_count);
  t0=now(); for(int i=0;i<N;i++){JSContext*c=JS_NewContext(rt);JS_FreeContext(c);} t1=now();
  printf("extra context in same runtime create+free: %.1f us\n",(t1-t0)/N/1e3);
  for(int sz=10; sz<=10000; sz*=10){
    char src[256]; snprintf(src,sizeof src,"(()=>{const a=[];for(let i=0;i<%d;i++)a.push({id:i,x:i*0.5,y:-i,label:'node'+i,on:true});return a})()",sz);
    JSValue v=JS_Eval(ctx,src,strlen(src),"<b>",0);
    size_t len; int M= sz>=10000?50:2000; double tw=0,tr=0;
    for(int k=0;k<M;k++){ double a=now(); uint8_t*buf=JS_WriteObject(ctx,&len,v,JS_WRITE_OBJ_REFERENCE); double b=now();
      JSValue r=JS_ReadObject(ctx,buf,len,JS_READ_OBJ_REFERENCE); double c2=now(); JS_FreeValue(ctx,r); js_free(ctx,buf); tw+=b-a; tr+=c2-b;}
    printf("clone %5d records: %6zu bytes, write %.1f us, read %.1f us (%.0f ns/record total)\n",sz,len,tw/M/1e3,tr/M/1e3,(tw+tr)/M/sz);
    JS_FreeValue(ctx,v);
  }
  JS_FreeContext(ctx); JS_FreeRuntime(rt); return 0;}
