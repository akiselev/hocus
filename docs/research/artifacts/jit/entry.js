import * as $ from '../deps/node_modules/svelte/src/internal/client/index.js';
const now = (typeof performance !== 'undefined' && performance.now) ? () => performance.now() : () => Date.now();
const log = typeof console !== 'undefined' ? (s) => console.log(s) : (s) => print(s);

// signals: 100 state, 100 derived(a+2b+c), 100 render effects
function mkSignals() {
  const N = 100, src = [], out = new Array(N).fill(0);
  $.effect_root(() => {
    for (let i = 0; i < N; i++) src.push($.state(i));
    for (let i = 0; i < N; i++) {
      const a = src[i], b = src[(i + 1) % N], c = src[(i + 2) % N];
      const d = $.derived(() => $.get(a) + 2 * $.get(b) + $.get(c));
      $.render_effect(() => { out[i] = $.get(d); });
    }
  });
  $.flush();
  let t = 0;
  return () => {
    for (let r = 0; r < 10; r++) {
      for (let k = 0; k < 5; k++) $.set(src[(t * 7 + k * 13) % N], t + k);
      $.flush(); t++;
    }
    let s = 0; for (let i = 0; i < N; i++) s += out[i]; return s;
  };
}
// proxy: $.proxy over 200 rows, effect per row, derived remaining count
function mkProxy() {
  const rows = [];
  for (let i = 0; i < 200; i++) rows.push({ id: i, text: 'row ' + i, done: (i % 3) === 0 });
  let p, remaining, out = new Array(200).fill('');
  let rem = 0;
  $.effect_root(() => {
    p = $.proxy(rows);
    remaining = $.derived(() => { let n = 0; for (const r of p) if (!r.done) n++; return n; });
    for (let i = 0; i < 200; i++) $.render_effect(() => { const r = p[i]; out[i] = r.text + (r.done ? ' x' : ''); });
    $.render_effect(() => { rem = $.get(remaining); });
  });
  $.flush();
  let t = 0;
  return () => {
    for (let r = 0; r < 10; r++) {
      const i = (t * 37) % 200; p[i].done = !p[i].done; p[(i + 11) % 200].text = 'edit ' + t;
      $.flush(); t++;
    }
    return rem + out[t % 200].length;
  };
}
// appmix: non-reactive glue: 4 shapes, closure dispatch, template strings, filter/map/join
function mkAppmix() {
  const items = [];
  for (let i = 0; i < 400; i++) {
    const k = i & 3;
    if (k === 0) items.push({ kind: 'btn', label: 'B' + i, w: i });
    else if (k === 1) items.push({ kind: 'txt', text: 'T' + i, size: 12, w: i });
    else if (k === 2) items.push({ kind: 'img', src: 'i' + i, w: i, h: 2 * i });
    else items.push({ kind: 'box', children: [], pad: 4, w: i });
  }
  const handlers = { btn: (o) => o.w + 1, txt: (o) => o.size * 2 + o.w, img: (o) => o.w * o.h, box: (o) => o.pad + o.w };
  return () => {
    let s = 0;
    for (let r = 0; r < 10; r++) {
      for (const it of items) s += handlers[it.kind](it);
      const labels = items.filter((o) => o.w % 5 === r % 5).map((o) => `${o.kind}:${o.w}`).join(',');
      s += labels.length;
    }
    return s;
  };
}
const which = (typeof process !== 'undefined' && process.env && process.env.W) || (typeof scriptArgs !== 'undefined' && scriptArgs[1]) || 'signals';
const f = which === 'proxy' ? mkProxy() : which === 'appmix' ? mkAppmix() : mkSignals();
for (let i = 0; i < 640; i++) f();            // warmup: 64 batches of 10
const times = [];
let chk = 0;
for (let b = 0; b < 16; b++) { const t0 = now(); for (let i = 0; i < 10; i++) chk += f(); times.push(now() - t0); }
times.sort((a, b) => a - b);
log(which + ' median_ms_per_10=' + times[8].toFixed(3) + ' min=' + times[0].toFixed(3) + ' chk=' + chk);
