#!/usr/bin/env python3
"""Attribute quickjs.c line ranges to subsystems.

Method: an ordered list of (marker regex, subsystem). Markers are searched
sequentially: each one must match at a line after the previous marker's line.
Lines from one marker to the next are attributed to that marker's subsystem.
Markers are section-header comments or the first definition of a subsystem's
code (not forward declarations). Per line we also count blank and comment-only
lines so "code" lines can be reported.
Usage: qjs_census.py path/to/quickjs.c
"""
import re, sys
from collections import OrderedDict

MARKERS = [
    (r'^#include', 'decls'),                                   # types, structs, forward decls
    (r'^static inline JSValueConst \*vc\(', 'decls'),
    (r'^static void js_trigger_gc\(', 'alloc'),               # arena allocator, malloc wrappers
    (r'^static inline int string_get\(', 'runtime'),          # runtime/context lifecycle, stack checks
    (r'^/\* JSAtom support \*/', 'atoms'),
    (r'^/\* JSClass support \*/', 'classes'),
    (r'^static inline JSValue js_empty_string\(', 'strings'), # flat strings, StringBuffer, UTF-8
    (r'^/\* Rope string support functions \*/', 'strings'),
    (r'^/\* Shape support \*/', 'shapes'),
    (r'^JSValue JS_NewObjectProtoClass\(', 'objects'),         # object/function creation
    (r'^static void free_property\(', 'gc'),                  # finalizers, free, cycle collector
    (r'^/\* Compute memory used by various object types \*/', 'memusage'),
    (r'^JSValue JS_GetGlobalObject\(', 'errors'),              # exceptions, backtrace, throw helpers
    (r'^static int JS_SetPrototypeInternal\(', 'properties'),   # property access core, exotic dispatch
    (r'^static JSValue JS_ToPrimitiveFree\(', 'conversions'),
    (r'^/\* bigint support \*/', 'bigint'),
    (r'^static JSValue js_atof\(', 'conversions'),
    (r'^static int js_compare_bigint\(', 'operators'),         # slow-path operators, for-in, iterators
    (r'^/\* Access an Array.s internal JSValue array if available \*/', 'interp-support'),  # fast arrays, closures, var_refs
    (r'^static JSValue JS_CallInternal\(JSContext \*caller_ctx', 'interpreter'),
    (r'^JSValue JS_Call\(', 'call-api'),
    (r'^/\* JSAsyncFunctionState', 'coroutines'),               # generators, async fns, async generators
    (r'^/\* JS parser \*/', 'lexer'),
    (r'^static inline int get_prev_opcode\(', 'parser'),        # emitter, scopes, expression/statement parser
    (r'^static JSModuleDef \*js_new_module_def\(', 'modules'),
    (r'^static __exception int js_parse_export\(', 'parser'),
    (r'^static JSFunctionDef \*js_new_function_def\(JSContext \*ctx,$', 'funcdef'),  # JSFunctionDef, dumps
    (r'^static int add_closure_var\(', 'resolve_variables'),
    (r'^/\* the pc2line table gives', 'resolve_labels'),
    (r'^/\* compute the maximum stack size', 'stack_size'),
    (r'^static int add_module_variables\(', 'create_function'),
    (r'^static __exception int js_parse_directives\(', 'parser'),
    (r'^static JSValue JS_EvalFunctionInternal\(', 'eval-api'),
    (r'^/\* object list \*/', 'serialization'),
    (r'^/\* runtime functions & objects \*/', 'b:misc-runtime'),
    (r'^/\* Object class \*/', 'b:Object'),
    (r'^/\* Function class \*/', 'b:Function'),
    (r'^/\* Error class \*/', 'b:Error'),
    (r'^/\* Array \*/', 'b:Array'),
    (r'^static void js_array_iterator_finalizer\(JSRuntime \*rt, JSValueConst val\)$', 'b:Iterator'),  # array iterator + Iterator helpers
    (r'^/\* Number \*/', 'b:Number'),
    (r'^/\* Boolean \*/', 'b:Boolean'),
    (r'^/\* String \*/', 'b:String'),
    (r'^/\* Math \*/', 'b:Math'),
    (r'^/\* Date \*/', 'b:Date'),
    (r'^/\* RegExp \*/', 'b:RegExp'),
    (r'^/\* JSON \*/', 'b:JSON'),
    (r'^/\* Reflect \*/', 'b:Reflect'),
    (r'^/\* Proxy \*/', 'b:Proxy'),
    (r'^/\* Symbol \*/', 'b:Symbol'),
    (r'^/\* Set/Map/WeakSet/WeakMap \*/', 'b:MapSet'),
    (r'^/\* Generator \*/', 'b:Generator'),
    (r'^/\* Explicit resource management \*/', 'b:DisposableStack'),
    (r'^/\* Promise \*/', 'b:Promise'),
    (r'^/\* AsyncFunction \*/', 'b:AsyncIterProtos'),
    (r'^/\* URI handling \*/', 'b:URI'),
    (r'^/\* global object \*/', 'b:global'),
    (r'^/\* Date \*/', 'b:Date'),
    (r'^/\* eval \*/', 'b:eval'),
    (r'^/\* BigInt \*/', 'b:BigInt'),
    (r'^static int JS_AddIntrinsicBasicObjects\(JSContext \*ctx\)$', 'b:intrinsics-init'),
    (r'^/\* Typed Arrays \*/', 'b:TypedArrays'),
    (r'^/\* Atomics \*/', 'b:Atomics'),
    (r'^/\* Performance \*/', 'b:misc-small'),
    (r'^/\* WeakRef \*/', 'b:WeakRef'),
    (r'^/\* CallSite \*/', 'b:CallSite'),
    (r'^/\* DOMException \*/', 'b:DOMException'),
    (r'^/\* base64 \*/', 'b:base64'),
]

def main(path):
    lines = open(path, encoding='utf-8', errors='replace').read().split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    starts = []
    pos = 0
    for rx, name in MARKERS:
        r = re.compile(rx)
        for i in range(pos, len(lines)):
            if r.search(lines[i]):
                starts.append((i, name))
                pos = i + 1
                break
        else:
            sys.exit(f'marker not found after line {pos+1}: {rx}')
    starts.append((len(lines), None))
    tot = OrderedDict()
    ranges = []
    in_block = False
    def kind(l):
        nonlocal in_block
        s = l.strip()
        if in_block:
            if '*/' in s:
                in_block = False
            return 'comment'
        if not s:
            return 'blank'
        if s.startswith('//'):
            return 'comment'
        if s.startswith('/*'):
            if '*/' not in s:
                in_block = True
                return 'comment'
            if s.endswith('*/'):
                return 'comment'
        return 'code'
    for (a, name), (b, _) in zip(starts, starts[1:]):
        ranges.append((a + 1, b, name))
        t = tot.setdefault(name, {'lines': 0, 'code': 0})
        for l in lines[a:b]:
            t['lines'] += 1
            if kind(l) == 'code':
                t['code'] += 1
    if '--ranges' in sys.argv:
        for a, b, n in ranges:
            print(f'{a:6d}-{b:6d} {b-a+1:6d} {n}')
        print()
    total = sum(v['lines'] for v in tot.values())
    code = sum(v['code'] for v in tot.values())
    print(f'{"subsystem":22s} {"lines":>7s} {"code":>7s} {"%lines":>7s}')
    for n, v in tot.items():
        print(f'{n:22s} {v["lines"]:7d} {v["code"]:7d} {100*v["lines"]/total:6.1f}%')
    print(f'{"TOTAL":22s} {total:7d} {code:7d}')
    b = sum(v['lines'] for n, v in tot.items() if n.startswith('b:'))
    print(f'builtins (b:*) total {b} lines ({100*b/total:.1f}%)')

if __name__ == '__main__':
    main(sys.argv[1])
