#!/usr/bin/env python3
"""Count C idioms per subsystem, reusing qjs_census.MARKERS. Usage: qjs_idioms.py quickjs.c"""
import re, sys
sys.path.insert(0, sys.argv[2] if len(sys.argv) > 2 else '.')
from qjs_census import MARKERS
PATS = {
 'goto': r'\bgoto\b',
 'rc': r'\b(JS_FreeValue(RT)?|js_dup|JS_DupValue(RT)?|js_free_rt|js_free)\(',
 'list': r'\blist_(add|add_tail|del|for_each|for_each_safe|entry|empty)\b',
 'cast': r'\((JS[A-Za-z]+|uint8_t|uint16_t|void|char) \*\)',
 'exc': r'\b(JS_EXCEPTION|JS_IsException|goto fail|goto exception)\b',
}
lines = open(sys.argv[1], encoding='utf-8', errors='replace').read().split('\n')
starts, pos = [], 0
for rx, name in MARKERS:
    r = re.compile(rx)
    for i in range(pos, len(lines)):
        if r.search(lines[i]): starts.append((i, name)); pos = i+1; break
starts.append((len(lines), None))
agg = {}
for (a, n), (b, _) in zip(starts, starts[1:]):
    key = 'builtins(all)' if n.startswith('b:') else n
    t = agg.setdefault(key, {'lines': 0, **{k: 0 for k in PATS}})
    t['lines'] += b - a
    for l in lines[a:b]:
        for k, p in PATS.items(): t[k] += len(re.findall(p, l))
print(f'{"subsystem":18s} {"lines":>6s} ' + ' '.join(f'{k:>6s}' for k in PATS) + '  per-kloc(goto,rc)')
for n, t in agg.items():
    print(f'{n:18s} {t["lines"]:6d} ' + ' '.join(f'{t[k]:6d}' for k in PATS) + f'  {1000*t["goto"]/t["lines"]:5.1f} {1000*t["rc"]/t["lines"]:5.1f}')
tot = {k: sum(t[k] for t in agg.values()) for k in ['lines', *PATS]}
print('TOTAL', tot)
