import * as std from 'qjs:std';
function build(n) {
  const a = new Array(n);
  for (let i = 0; i < n; i++) a[i] = { id: i, x: null, y: null, s: 'v' + (i % 1000) };
  for (let i = 0; i < n; i++) { const o = a[i]; o.x = a[(i * 7919) % n]; o.y = a[(i + 1) % n]; }
  return a;
}
for (const n of [10000, 100000, 1000000]) {
  globalThis.keep = build(n);
  std.gc();
  let best = 1e9;
  for (let r = 0; r < 5; r++) { const t = performance.now(); std.gc(); best = Math.min(best, performance.now() - t); }
  // now drop it: cycle garbage
  globalThis.keep = null;
  const t2 = performance.now(); std.gc(); const free = performance.now() - t2;
  print(`n=${n} live-heap full GC (min of 5): ${best.toFixed(2)} ms = ${(best*1e6/n).toFixed(1)} ns/obj; freeing all as cycle garbage: ${free.toFixed(2)} ms`);
}
