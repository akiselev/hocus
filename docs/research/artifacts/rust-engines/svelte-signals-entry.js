// Svelte 5.57.2 reactivity microbenchmark: signals, deriveds, effects, deep proxy.
import * as $ from 'svelte/internal/client';
function bench_signals(rounds) {
  const N = 200;
  let sum = 0;
  const cleanup = $.effect_root(() => {
    const src = [];
    for (let i = 0; i < N; i++) src.push($.state(i));
    const ds = [];
    for (let i = 0; i < N; i++) {
      const a = src[i], b = src[(i + 1) % N];
      ds.push($.derived(() => $.get(a) * 2 + $.get(b)));
    }
    const total = $.derived(() => { let t = 0; for (let i = 0; i < N; i += 7) t += $.get(ds[i]); return t; });
    $.render_effect(() => { sum += $.get(total); });
    for (let r = 0; r < rounds; r++) {
      for (let k = 0; k < 20; k++) $.set(src[(r * 13 + k) % N], r + k);
      $.flush();
    }
  });
  cleanup();
  return sum;
}
function bench_proxy(rounds) {
  let acc = 0;
  const cleanup = $.effect_root(() => {
    const rows = $.proxy([]);
    for (let i = 0; i < 200; i++) rows.push({ id: i, text: 'row ' + i, done: false });
    $.render_effect(() => { let c = 0; for (const r of rows) if (r.done) c++; acc += c; });
    for (let r = 0; r < rounds; r++) {
      rows[(r * 7) % 200].done = !rows[(r * 7) % 200].done;
      rows[(r * 11) % 200].text = 'x' + r;
      $.flush();
    }
  });
  cleanup();
  return acc;
}
const now = () => Date.now();
for (const [name, fn, n] of [['signals', bench_signals, 3000], ['proxy', bench_proxy, 1500]]) {
  fn(50);
  const t0 = now(); const v = fn(n); const t1 = now();
  print(name + ' ms=' + (t1 - t0) + ' check=' + v);
}
