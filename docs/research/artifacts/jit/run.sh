#!/bin/bash
B=/tmp/claude-0/-home-user/db6d0663-2392-5b80-95a4-80368735124c/scratchpad/jit
cd $B/bench
run() { local name=$1; shift; local w=$1; shift; local out; out=$(W=$w "$@" 2>/dev/null | grep median); echo "$name $out"; }
for round in 4 5; do
 for w in signals proxy appmix; do
  run qjs $w $B/qjs-build/qjs bundle.js $w
  run v8-ignition $w node --no-opt --no-maglev --no-sparkplug bundle.js
  run v8-jitless $w node --jitless bundle.js
  run v8-ign+sparkplug $w node --no-opt --no-maglev bundle.js
  run v8-ign+sp+maglev $w node --no-opt bundle.js
  run v8-full $w node bundle.js
  run jsc-llint $w env BUN_JSC_useJIT=0 bun bundle.js
  run jsc-llint+baseline $w env BUN_JSC_useDFGJIT=0 bun bundle.js
  run jsc-full $w bun bundle.js
 done
done
