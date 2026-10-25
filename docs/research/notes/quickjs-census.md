# QuickJS-NG census: a porting map for a Rust port

Date: 2026-10-25. Researcher topic: what is inside QuickJS-NG, how big each piece is, how C-shaped it is,
what Rust crates could replace it, and what tracking upstream costs.

Sources pinned for this note:

| Repository | Commit | Notes |
|---|---|---|
| quickjs-ng/quickjs | `7a23d8e` (2026-10-23, "Fix leak in JS_ReadBigInt on truncated input") | version string 0.17.0 (`quickjs.h:1463-1466`); history fetched back to 2025-08 for churn |
| bellard/quickjs | `7d1c13c` (2026-10-20) | `VERSION` = 2026-06-04 (last release); master has unreleased Intl + Temporal |
| bellard/mquickjs (MicroQuickJS) | `6d4d7eb` (2026-09-26) | first commit `b700a4d` 2025-12-22 |
| akiselev/svelters | `0aa41697` | for the "what svelters uses" census |
| test262.fyi data | generated 2026-10-25 03:26 UTC, test262 `d53ebc8` | `https://data.test262.fyi/{meta,index,built-ins}.json`, fetched 2026-10-25 |

All line references below are `quickjs.c @ 7a23d8e` unless another file is named.

## Summary of findings

1. **Size.** The engine is 64,954 lines of `quickjs.c` plus about 14k lines of support C (libregexp 3.5k,
   libunicode 2.1k + 5.2k generated tables, dtoa 1.6k, cutils.h 2.0k). Builtins are 37.5% of
   `quickjs.c` (24.3k lines). The compiler (lexer, fused parser/emitter, three bytecode passes) is
   13.7k lines (21%). The object model (values, atoms, strings, shapes, objects, properties, GC) is
   12.1k lines (19%). The interpreter loop itself is only 2,935 lines (4.5%). (`measured`, method in §0.)
2. **Value model.** On 64-bit hosts a `JSValue` is a 16-byte struct (64-bit payload union + 64-bit tag);
   NaN-boxing is used only on 32-bit hosts. Since NG 0.16.0 the refcount, GC type and mark bit live in an
   8-byte allocator block header *before* every allocation, reached by pointer arithmetic (`js_rc()`).
   (`source-observed`, §1.)
3. **Memory management** is refcounting plus a trial-deletion cycle collector (decref children, rescan,
   free what stays at zero) over one intrusive list of GC objects. Weak references are eager: each target
   carries a linked list of weak records, cleared when the target is freed. `FinalizationRegistry`
   callbacks are queued as jobs, never run inside the GC. (`source-observed`, §2.)
4. **Refcount traffic is the porting cost driver**, not GC code. The GC core is 709 lines; there are
   1,402 `JS_FreeValue(` and 406 `js_dup(` call sites in `quickjs.c`, plus 50 `*_mark` and 56
   `*_finalizer` functions. Every one of them encodes an ownership decision a Rust port must re-express.
   (`measured`, grep counts.)
5. **The compiler is single-pass to bytecode** (no AST): the parser emits phase-1 bytecode with 19
   temporary scope opcodes; `resolve_variables` (phase 2) and `resolve_labels` (phase 3, peephole +
   short opcodes) rewrite it, then `compute_stack_size`. There are 252 final opcodes (185 regular, 67
   short) plus 19 temporary ones. (`source-observed`, §3.)
6. **The interpreter** is one `JS_CallInternal` with computed-goto dispatch, recursive on the C stack for
   JS-to-JS calls, `alloca` frames, sentinel-return exceptions with catch offsets pushed on the value
   stack, and heap frames for generators/async resumed through the same function. NG removed its inline
   caches in PR #884 (Feb 2025) because they cost memory and were roughly neutral (mean 1.037x on the
   web-tooling benchmark). (`source-observed`, `documented`, §4.)
7. **Conformance.** At this exact sha test262.fyi gives NG 44,774/53,616 (83.5%) against Bellard's
   52,813 (98.5%). The gap is almost all Temporal (NG 0/4,605) and Intl (NG 23/3,382), which Bellard's
   unreleased master now ships (77k lines of "experimental AI coded" C). Excluding Temporal and intl402,
   NG passes 98.1% and Bellard 98.3%. (`measured` from published data, §7.)
8. **svelters uses a small surface**: about 25 rquickjs entry points (runtime limits, interrupt handler,
   promise rejection tracker, module declare/eval/load/write, persistent handles, job queue, GC, and raw
   ArrayBuffer/TypedArray/UTF-16 string calls). It uses nothing from quickjs-libc; timers are JS in
   `prelude.js` and Intl is ICU4X behind a JS shim. (`source-observed`, §6.)
9. **Churn.** NG changed `quickjs.c` in 232 commits over the last ~13 months (+10,041/−3,211 lines,
   about 18 commits a month, peaks of 47 and 36). Bellard made 120 commits (+10,236/−3,664). Of NG's 232,
   41 are memory-safety fixes (leak, use-after-free, overflow) that a safe-Rust port mostly would not
   need, 46 add features, 52 fix other bugs. About 25% of `quickjs.c` lines differ between NG and Bellard.
   (`measured`, §9.)
10. **Replaceable by crates with evidence:** regexp (`regress` 0.12 with `utf16`; Boa passes 1,875/1,879
    RegExp test262 tests with it), Unicode (`icu_casemap`/`icu_normalizer`/`icu_properties` 2.4, already in
    svelters' lockfile), Temporal (`temporal_rs`, Boa 4,603/4,605), float printing (`ryu-js`) and parsing
    (`fast-float2`). The parser could be replaced by `oxc_parser`, but in QuickJS the parser *is* the code
    generator, so that is a rewrite of 7.6k lines of emitter logic, not a swap. (§8.)

**Recommendation in one paragraph.** Port the object model, interpreter, bytecode passes, bytecode
reader/writer and the builtins that Svelte 5 exercises *faithfully* (same opcodes, same shapes, same
algorithms), because that is where QuickJS's speed-per-line and its test262 score live, and because a
faithful port lets the C engine serve as a differential oracle at every step. Replace regexp, Unicode,
Temporal/Intl and float conversion with crates. Port the compiler faithfully too, but *after* the
interpreter: start by loading bytecode written by C QuickJS-NG (`JS_WriteObject`, `BC_VERSION` 29) into the
Rust interpreter, so the first milestone needs no parser. Follow NG, not Bellard, as the base (§1.7).
Do not plan to "track upstream" commit by commit: at ~18 commits a month into a moving 65k-line file, a port
becomes a fork on day one; track test262 and NG's `tests/` instead, and cherry-pick semantics fixes by hand.

## 0. Method: attributing `quickjs.c` to subsystems

`quickjs.c` is ordered by subsystem, with header comments such as `/* Shape support */` and
`/* Array */`. I wrote a script (`qjs_census.py`, kept in the session scratchpad, not committed) with an
ordered list of 67 markers: header comments, or the first *definition* (not forward declaration) of a
subsystem's code. Markers must match in order; lines from one marker to the next go to that marker's
subsystem. Lines 1-26 (licence) are not counted. "code" counts non-blank, non-comment lines. A second
script counts C idioms per subsystem with regexes. Commands, run on the 4-core cloud container:

```
python3 -I qjs_census.py src/quickjs-ng/quickjs.c --ranges
python3 -I qjs_idioms.py  src/quickjs-ng/quickjs.c
```

Boundaries (all `@ 7a23d8e`; ranges are inclusive line numbers):

| Lines | Subsystem | Marker |
|---|---|---|
| 27-1678 | decls: types, structs, forward decls | `#include`, `vc()` |
| 1679-2204 | alloc: arena allocator, malloc wrappers | `js_trigger_gc` |
| 2205-3144 | runtime/context lifecycle, stack checks, job queue | `string_get` |
| 3145-4154 | atoms | `/* JSAtom support */` |
| 4155-4260 | classes | `/* JSClass support */` |
| 4261-5564 | strings (flat, StringBuffer, UTF-8, ropes) | `js_empty_string`, `/* Rope string support */` |
| 5565-6255 | shapes | `/* Shape support */` |
| 6256-6798 | object/function creation | `JS_NewObjectProtoClass` |
| 6799-7507 | free, finalizers, mark, cycle collector | `free_property` |
| 7508-7993 | memory usage stats | `/* Compute memory used ... */` |
| 7994-8679 | exceptions, backtraces, throw helpers, interrupts | `JS_GetGlobalObject` |
| 8680-12157 | property access core, exotic dispatch, fast-array conversion, globals | `JS_SetPrototypeInternal` |
| 12158-12358, 13773-15886 | conversions (ToPrimitive, ToNumber, ToString, number parsing) | `JS_ToPrimitiveFree`, `js_atof` |
| 12359-13772 | bigint arithmetic | `/* bigint support */` |
| 15887-17308 | operators (slow paths), for-in, iterator protocol | `js_compare_bigint` |
| 17309-18050 | interpreter support: fast arrays, closures, var_refs, classes | `/* Access an Array's ... */` |
| 18051-20985 | `JS_CallInternal` | its definition |
| 20986-21161 | call API (`JS_Call`, `JS_CallConstructor`) | `JS_Call` |
| 21162-22097 | coroutines: generators, async functions, async generators | `/* JSAsyncFunctionState` |
| 22098-24002 | lexer (JS and JSON tokens) | `/* JS parser */` |
| 24003-30287, 32570-32950, 37196-38107 | parser + emitter + scopes | `get_prev_opcode`, `js_parse_export`, `js_parse_directives` |
| 30288-32569 | modules | `js_new_module_def` |
| 32951-33606 | JSFunctionDef, bytecode dumps | `js_new_function_def` |
| 33607-35526 | `resolve_variables` (phase 2) | `add_closure_var` |
| 35527-36666 | `resolve_labels` (phase 3), pc2line | `/* the pc2line table ... */` |
| 36667-36916 | `compute_stack_size` | its comment |
| 36917-37195 | `js_create_function` | `add_module_variables` |
| 38108-38368 | eval API | `JS_EvalFunctionInternal` |
| 38369-40620 | bytecode/object serialization | `/* object list */` |
| 40621-64954 | builtins (25 sections, see §5) | `/* runtime functions & objects */` onward |

Results (`measured`):

| Group | Subsystems | Lines | % of quickjs.c |
|---|---|---:|---:|
| Object model | decls 1,652, alloc 526, runtime 940, atoms 1,010, classes 106, strings 1,304, shapes 691, objects 543, gc 709, memusage 486, errors 686, properties 3,478 | 12,131 | 18.7% |
| Numbers and operators | conversions 2,315, bigint 1,414, operators 1,422 | 5,151 | 7.9% |
| Interpreter | interp-support 742, `JS_CallInternal` 2,935, call API 176, coroutines 936 | 4,789 | 7.4% |
| Compiler | lexer 1,905, parser 7,578, funcdef 656, resolve_variables 1,920, resolve_labels 1,140, stack size 250, create_function 279 | 13,728 | 21.1% |
| Modules + eval API | 2,282 + 261 | 2,543 | 3.9% |
| Serialization | | 2,252 | 3.5% |
| Builtins | §5 | 24,334 | 37.5% |
| **Total** | | **64,928** (57,031 code) | |

Other files at `7a23d8e` (`wc -l`, `measured`):

| File | Lines | Role |
|---|---:|---|
| `libregexp.c` (+ `-opcode.h` 73, `.h` 101) | 3,522 | regexp compiler + backtracking VM |
| `libunicode.c` (+ `.h` 172) | 2,074 | case mapping, normalization, char ranges, properties |
| `libunicode-table.h` | 5,160 | generated by `unicode_gen.c` (3,617) |
| `dtoa.c` (+ `.h` 87) | 1,619 | Bellard's float ↔ string with radix and fixed formats |
| `cutils.h` / `list.h` | 2,021 / 107 | DynBuf, UTF-8, bit utils / intrusive lists |
| `quickjs-libc.c` (+ `.h` 87) | 5,059 | `std`, `os`, timers, workers, event loop |
| `qjs.c` / `qjsc.c` | 762 / 673 | REPL/runner, bytecode compiler CLI |
| `run-test262.c` / `api-test.c` | 2,384 / 2,320 | test drivers |
| `builtin-*.js` → `builtin-*.h` | 440 JS | builtins written in JS, shipped as bytecode (§5) |

## 1. Value representation and object model

### 1.1 JSValue

- Tags (`quickjs.h:179-200`, `source-observed`): negative tags have a refcount: `BIG_INT` −9, `SYMBOL`
  −8, `STRING` −7, `STRING_ROPE` −6, `MODULE` −3, `FUNCTION_BYTECODE` −2, `OBJECT` −1. Non-negative:
  `INT` 0, `BOOL` 1, `NULL` 2, `UNDEFINED` 3, `UNINITIALIZED` 4, `CATCH_OFFSET` 5, `EXCEPTION` 6,
  `SHORT_BIG_INT` 7, `FLOAT64` 8. Bellard's tags are identical (`quickjs.h:77-94 @ 7d1c13c`).
- Three layouts (`quickjs.h:202-396`): `JS_CHECK_JSVALUE` (pointer type, compile-time ownership check
  only, "does not produce working code"); `JS_NAN_BOXING` (`uint64_t`, tag in the high 32 bits, floats
  offset by `JS_FLOAT64_TAG_ADDEND`); and the default struct `{ JSValueUnion u; int64_t tag; }`, 16
  bytes. NaN-boxing is enabled only when `INTPTR_MAX < INT64_MAX`, i.e. 32-bit hosts
  (`quickjs.h:173-177`). So on the targets we ship, every value is two words. (`source-observed`)
- `JSValueConst` is `#define JSValueConst JSValue`: the borrow/own distinction is a naming convention,
  enforced only by the check build (`quickjs.h:202-222`). This is exactly the information a Rust port turns
  into types (`&Value` vs `Value`, or a rooted handle). (`source-observed`; the mapping is `inferred`.)
- Short BigInts are inline `int32_t` payloads (`SHORT_BIG_INT`); NG unified on 32-bit limbs
  (commit `ecf9b1a`, 2026-05-21) while Bellard uses 64-bit limbs on hosts with `__int128`
  (`quickjs.h:66-71 @ 7d1c13c`). (`source-observed`)

### 1.2 Where the refcount lives (NG ≥ 0.16.0)

- `JSMallocBlockHeader` (`quickjs.c:286-298`) is an 8-byte header before every allocation: arena
  bookkeeping (`block_idx`/`free_next`, `block_size_idx`) plus `gc_obj_type:7`, `mark:1` and
  `int ref_count`. `JS_REF_COUNT(p)` is `js_rc(p)->ref_count`, with `js_rc` subtracting
  `offsetof(..., user_data)` from the object pointer (`quickjs.c:500-509`). `JSGCObjectHeader` in the
  object body is now just a `list_head` (`quickjs.c:462-467`). (`source-observed`)
- It came in "Add arena allocator" `9de2921` (2026-06-28), adapted from Bellard's "custom malloc for small
  blocks (11% faster on bench-v8)"; first released in v0.16.0 (2026-07-31). The commit's table shows
  V8-bench geomean 1511 → 1783 (NG → NG+arena) vs Bellard 1764. (`documented` in the commit message;
  `source-observed` with `git log -S'define JS_REF_COUNT(p)'`.) This is the layout change that broke the
  quickjs-jit fork's patches (svelters `docs/notes/quickjs-jit-cranelift.md` §6).
- The arena serves ≤ 512-byte blocks in 31 size classes from 4 KB arenas (`quickjs.c:262-317,
  1705-1725`); `ASan` and `FORCE_GC_AT_MALLOC` builds bypass it. (`source-observed`)

### 1.3 Strings, ropes, atoms

- `JSString` (`quickjs.c:696-710`): `len:31`, `is_wide_char:1` (Latin-1 or UTF-16 storage), `hash:28`,
  `kind:2`, `atom_type:2`, `hash_next`, `first_weak_ref` (symbols can be weak targets). `kind` is NORMAL
  (chars follow the header), SLICE (parent + byte offset; used above 1,024 bytes,
  `JS_STRING_SLICE_LEN_MAX`) or INDIRECT (external pointer) (`quickjs.c:688-692, 720-737`).
- Ropes are a separate value type, `JSStringRope {len, is_wide_char, depth, left, right}`
  (`quickjs.c:716-722`), not used under 512 chars, rebalanced at depth 60 (`quickjs.c:226-233`). NG
  ported them from Bellard on 2026-01-29 (`e85ae37`). (`source-observed`)
- Atoms: `JSAtom` is a `uint32_t`. Bit 31 tags an integer key (`JS_ATOM_TAG_INT`, `quickjs.c:3147`), so
  array indices are atoms without a table entry. Atoms are interned `JSString`s in a runtime hash table
  (`JSRuntime.atom_hash/atom_array`, `quickjs.c:341-347`); 240 are predefined (`quickjs-atom.h`). Symbol
  and private names are atoms with `atom_type` SYMBOL/GLOBAL_SYMBOL. (`source-observed`)

### 1.4 Shapes and properties (hidden classes)

- Yes, hidden classes. `JSShape` (`quickjs.c:1108-1123`) holds `proto`, a property hash table and an
  array of `JSShapeProperty {hash_next:26, flags:6, atom}` in one allocation (`hash_table[]` then
  `prop[prop_size]`, `prop_hash_end()` at `quickjs.c:5581`). Values live in the object's separate
  `JSProperty *prop` array, parallel to the shape's property list. (`source-observed`)
- Shapes are shared through a **global hash keyed by (proto, property sequence)**, not a transition tree:
  `find_hashed_shape_prop` hashes the parent shape's hash with the new atom and flags and compares the
  candidate's whole property list (`quickjs.c:6058-6087`). Shapes are refcounted GC objects; an object
  that is modified while its shape is shared gets a private copy (`js_shape_prepare_update`).
- Property lookup `find_own_property` (`quickjs.c`, inline, 29 call sites) hashes the atom into the
  shape's table and walks the `hash_next` chain. The interpreter inlines it for `get_field`
  (`quickjs.c:19559-19600`), walking `shape->proto` until an exotic object forces the slow path. On the
  Svelte signals workload this was 7.8% of time (svelters `quickjs-jit-cranelift.md` §5). (`source-observed`)
- `JSProperty` (`quickjs.c:1081-1097`) is a union: value, getter/setter pair, `JSVarRef*` (global
  lexical bindings and module exports), or a lazy "autoinit" (realm and init id packed into the low 2
  bits of a pointer). (`source-observed`)

### 1.5 Objects, arrays, functions, frames

- `JSObject` (`quickjs.c:1125-1210`): flag bits, `class_id`, `shape`, `prop`, `first_weak_ref`, and a
  union `u` with one member per class family (bytecode function, C function, bound function, typed array,
  map, promise, proxy, generator, regexp ...). A source comment gives "byte sizes: 40/48/72" for the
  three value layouts. There are 66 built-in class ids (`quickjs.c:128-196`) and 8 exotic
  method tables. (`source-observed`)
- Fast arrays: `fast_array` flag with `u.array.u.values` + `count`, used for `Array`, `arguments` and
  typed arrays; converted to ordinary properties by `convert_fast_array_to_array` (`quickjs.c:10206`) on
  holes, non-index keys or non-default attributes. (`source-observed`)
- `JSFunctionBytecode` (`quickjs.c:855-890`): flags, `byte_code_buf`, `vardefs`, `closure_var`,
  counts, `cpool`, realm, filename/line/column, pc2line table, optional source. Several pointers are
  "self pointers" into the same allocation. (`source-observed`)
- Closures: `JSVarRef` (`quickjs.c:466-497`) is either *open* (`pvalue` points into a live stack frame
  slot; holds `stack_frame` + index) or *detached* (`value` held inline after `close_var_refs` on return).
  A var_ref into a suspended coroutine's frame keeps the coroutine alive (`is_coro`, fixed in `7955cfd`,
  2026-07-23, a use-after-free). (`source-observed`)
- `JSStackFrame` (`quickjs.c:427-449`): `prev_frame`, `cur_func`, `arg_buf`, `var_buf`, `var_refs`,
  `cur_pc`, `cur_sp` (only for suspended generators), `cur_gc_obj` (owner of a heap frame). Ordinary frames
  are `alloca`'d inside `JS_CallInternal`. (`source-observed`)

### 1.6 JSRuntime vs JSContext

- `JSRuntime` (`quickjs.c:337-425`) is the heap and the thread: allocator state, atom table, class
  array, GC lists, shape hash, current exception, stack limits, interrupt handler, promise hooks, **job
  queue** (`job_list`), module loader callbacks, SAB functions. One runtime is single-threaded.
- `JSContext` (`quickjs.c:558-620`) is a realm: global object, `class_proto[]`, constructors, cached
  shapes (array, arguments, regexp result), RNG state, interrupt counter, loaded modules, and the
  optional `compile_regexp`/`eval_internal` hooks. Objects from several contexts of one runtime mix freely.
  (`source-observed`)

### 1.7 NG vs Bellard: which base to follow

| Aspect | NG `7a23d8e` | Bellard `7d1c13c` |
|---|---|---|
| Releases | v0.11 (2025-10-28) … v0.16.2 (2026-08-20, = rquickjs 0.14.0), v0.17.0 (2026-09-18) | 2025-04-26, 2025-09-13, 2026-06-04 |
| quickjs.c lines | 64,954 | 64,898 |
| Lines differing (`diff -d`) | 16,487 NG-only / 16,431 Bellard-only (≈25%) | |
| Intl, Temporal | absent (svelters supplies Intl) | unreleased master: `intl/` + `temporal/` = 77,228 lines, "experimental AI coded" (`97f64ef`, 2026-10-10), behind `CONFIG_TEMPORAL_INTL` |
| Weak refs | eager per-target record list, cleared at free | `weakref_count` + `gc_remove_weak_objects` GC phase (`quickjs.c:6924 @ 7d1c13c`) |
| BigInt limbs | 32-bit everywhere | 64-bit with `__int128` |
| Short opcodes | always on (252 final opcodes) | `SHORT_OPCODES` macro (250) |
| Extra builtins | `DOMException`, `CallSite`, `performance`, `Error.prepareStackTrace`-style hooks, JS-hosted builtins | Intl/Temporal (master) |
| Platform | MSVC/Windows, CMake + Meson, WASI reactor, `quickjs-libc` 5,059 lines | Makefile, Cosmopolitan, libc 4,403 lines |
| Testing | `api-test.c` (37 tests), 106 files in `tests/`, CI with gc-stress, TSan, Valgrind | 7 libFuzzer targets in `fuzz/` |
| Embedding API | 264 `JS_EXTERN` functions; what rquickjs and svelters bind to | different names in places (`JS_NewClassID`, BOOL vs bool) |

NG regularly ports Bellard's work (8 commits titled "Port …", 25 whose message names Bellard since
2025-10, e.g. ropes, arena, register-based regexp `5f2fb55`), and at least one Bellard
commit is carried with Bellard's authorship kept (`83bd236`, JSON.parse source text access, author Fabrice
Bellard). (`measured` with `git log --grep`.)

**Which base:** NG (`inferred`). It is what svelters runs today (rquickjs 0.14.0), so the Rust port can be
checked against the engine already in production. Its embedding API is the one svelters' code knows. It has
the larger test and CI net (gc-stress, sanitizers). And it already absorbs Bellard's performance work.
Bellard's tree is worth reading for its fuzz targets and as the origin of algorithms. Do not port its Intl
or Temporal code: it is a third of a new engine again, and crates cover both (§8).

### 1.8 MicroQuickJS, briefly

Bellard's MicroQuickJS (`mquickjs.c` 18,410 lines at `6d4d7eb`) is a different design: a *tracing,
compacting* GC instead of refcounting, one-word values, WTF-8 strings, bytecode executable from ROM, a
non-recursive single-pass parser, and a JS subset "close to ES5" (README, `documented`). It is not a base
for svelters (Svelte 5 needs Proxy, classes, async, ES2020+). But it is evidence from QuickJS's own author
that the QuickJS compiler and interpreter design works without refcounts, which matters for the GC choice in
the ownership notes. (`inferred`)

## 2. Memory management

- **Allocation-triggered GC.** `js_trigger_gc` runs a full cycle collection when
  `malloc_size + size > malloc_gc_threshold`, then sets the threshold to 1.5x the live size
  (`quickjs.c:1679-1698`); the first threshold is 256 KiB (`quickjs.c:2333`). It is called when an
  object is created, counting its property array too (`quickjs.c:6142`, `60984dc`). (`source-observed`)
- **Refcount free path.** `JS_FreeValueRT` decrements the header refcount. At zero, strings, ropes,
  bigints and symbols are freed at once. Objects and function bytecode move to `gc_zero_ref_count_list`
  and are drained by `free_zero_refcount` in `JS_GC_PHASE_DECREF`, so freeing a long chain does not recurse
  on the C stack (`quickjs.c:7111-7213`). (`source-observed`)
- **Cycle collector** (`JS_RunGC`, `quickjs.c:7363-7493`), a trial-deletion scheme over
  `rt->gc_obj_list`, which holds every object, function bytecode, shape, var_ref, async function state and
  context:
  1. `gc_decref`: for each GC object, decrement the refcount of each child (`mark_children` dispatches on
     the GC type and the class's `gc_mark`); objects that reach 0 move to `tmp_obj_list`; set `mark=1`.
  2. `gc_scan`: objects still on `gc_obj_list` are externally reachable. Re-increment their children and
     move any child at refcount 0 back to the live list (transitive), then restore the counts of the
     objects still on `tmp_obj_list`.
  3. `gc_free_cycles` (phase `REMOVE_CYCLES`): free the objects and bytecode left on `tmp_obj_list`. Freed
     objects whose refcount is still non-zero (referenced from the garbage) are parked on
     `gc_zero_ref_count_list` and released at the end.
  (`source-observed`)
- **Finalizers and re-entrancy.** `free_object` sets `free_mark`, frees properties and shape, clears weak
  refs, then calls the class finalizer `(JSRuntime*, JSValueConst)` (`quickjs.c:7050-7095`). Finalizers
  get no `JSContext` and cannot run JS. During cycle removal other garbage objects are "zombies";
  `JS_IsLiveObject` checks `free_mark` (`quickjs.c:7495-7507`). (`source-observed`)
- **Weak refs.** A target (object or non-registered symbol) has `first_weak_ref`, a list of
  `JSWeakRefRecord {kind: MAP | WEAK_REF | FINALIZATION_REGISTRY_ENTRY}` (`quickjs.c:628-642`). When the
  target is freed, `reset_weak_ref` (`quickjs.c:63362-63441`) removes WeakMap/WeakSet records, sets
  `WeakRef` targets to undefined, and enqueues each registry callback with `JS_EnqueueJob`, unless the
  callback or held value is itself garbage in this cycle. WeakMap values are marked through the key's
  record list (`mark_weak_map_value`, `quickjs.c:7239-7253`), so the ephemeron rule is approximated by
  ordinary refcounting of the value. (`source-observed`)
- **GC stress.** `FORCE_GC_AT_MALLOC` runs a full GC on every allocation and routes all blocks to the
  backing malloc (`quickjs.c:271-281, 1679-1686`). NG runs it in CI (`gc-stress.yml`, added `4e8e822`,
  2026-07-13); `gc-stress-skip.txt` skips only `tests/test_builtin.js` as "too allocation-heavy".
  (`source-observed`)
- **Limits.** `JS_SetMemoryLimit` sets `malloc_limit` (`quickjs.c:2435`); allocation failure becomes
  an uncatchable OOM exception (NG `90aa2c4`, 2026-10-08). `JS_SetMaxStackSize` sets the C stack limit
  checked by `js_check_stack_overflow` in calls, the parser and module code. (`source-observed`)

Port note (`inferred`): the collector is small and easy to port. The 1,402 free and 406 dup sites,
the 50 mark functions and the zombie rules are what make "refcount + cycle collection" expensive in Rust.
A tracing GC removes the dup/free sites but needs rooting at every place C code holds a `JSValue` across an
allocation. A faithful refcount port keeps the call sites but makes each one a `Drop`/`Clone` decision. That
choice belongs to the ownership and GC notes; this census only sizes it.

## 3. Parser and compiler

- **Single pass, no AST.** The recursive-descent parser (`js_parse_*`, 473 references) emits bytecode as it
  parses (`emit_op`, `emit_label`, `quickjs.c:24029-24166`). Variable references are emitted as
  temporary opcodes (`scope_get_var`, `scope_put_var`, `scope_make_ref`, `enter_scope`/`leave_scope`,
  private-field and optional-chain forms, `source_loc`) that never reach final bytecode
  (`quickjs-opcode.h:275-299`). (`source-observed`)
- **Passes** (`js_create_function`, `quickjs.c:37032-37050`):
  1. phase 1, parse + emit;
  2. `resolve_variables` (`quickjs.c:35111`, ≈1,900 lines with helpers): maps scope opcodes to
     `get_loc`/`get_arg`/`get_var_ref`/global access, builds closure variable lists, handles `with`,
     direct `eval` (captures every variable), Annex B function hoisting and module variables;
  3. `resolve_labels` (`quickjs.c:35751`): label resolution, jump shortening, peephole optimisation
     (`code_match`, `skip_dead_code`), short opcode selection (`put_short_code`), pc2line table;
  4. `compute_stack_size` (`quickjs.c:36721`) by abstract interpretation of stack effects.
  (`source-observed`)
- **Opcodes** (`quickjs-opcode.h @ 7a23d8e`, counted with grep, `measured`): 185 regular `DEF`s (incl.
  `invalid` and `nop`), 67 short opcodes (`push_0..7`, `get_loc0..3`, `call0..3`, 8-bit jumps, ...), 19
  temporary `def`s, 30 operand formats. 252 final opcodes, so the dispatch table has 256 entries.
- **Serialization** (`quickjs.c:38369-40620`): `JS_WriteObject`/`JS_ReadObject` handle bytecode,
  modules and data (for workers and structured clone) with 21 tags (`BC_TAG_*`), atoms re-interned on read,
  LEB128 integers, an object-reference table, and `BC_VERSION` 29 (`quickjs.c:38501`). Bytecode is
  trusted: NG's only fuzz target (`fuzz.c`) feeds `JS_ReadObject`, and svelters checksums its cache before
  loading (`svelters-host/src/bytecode.rs:19`). (`source-observed`)

C idioms (`measured`, `qjs_idioms.py`): the parser has 310 `goto`s (40.9 per kloc), mostly
`goto fail` error exits; the lexer 95 (49.9 per kloc). Error propagation is `__exception int` return
codes (104 functions marked `__exception`). It is not setjmp-based anywhere. (`source-observed`)

## 4. Interpreter

- **Structure.** `JS_CallInternal` (`quickjs.c:18051-20985`) checks interrupts, dispatches non-bytecode
  callees through `class_array[id].call`, `alloca`s args + locals + operand stack + var_ref slots in one
  block, links the frame into `rt->current_stack_frame`, and runs `for(;;) SWITCH(pc)`. With
  `DIRECT_DISPATCH` the dispatch is a 256-entry computed-goto table (`quickjs.c:18077-18090`); otherwise
  a `switch`. (`source-observed`)
- **Calls recurse.** `OP_call*` call `JS_CallInternal` again on the C stack (e.g. `quickjs.c:18551,
  18588`). There is no frame trampolining, so deep JS recursion is C recursion, bounded by the stack-size
  check. (`source-observed`)
- **Exceptions.** Functions return the `JS_EXCEPTION` sentinel, and the pending exception sits in
  `rt->current_exception`. In the interpreter `goto exception` builds a backtrace if needed, then pops the
  operand stack looking for a `JS_TAG_CATCH_OFFSET` value pushed by `OP_catch`. Offset 0 marks a for-of
  iterator to close; otherwise the exception is pushed and execution jumps to the handler
  (`quickjs.c:20935-20962`). Uncatchable errors (OOM, InternalError since `db0fe52`, interrupts) skip
  handlers. (`source-observed`)
- **Generators and async.** `async_func_init` heap-allocates the frame (`JSAsyncFunctionState`, with args,
  locals and stack in one `js_malloc` block; `quickjs.c:21163-21203`). `async_func_resume` re-enters
  `JS_CallInternal` with `JS_CALL_FLAG_GENERATOR` and a fake non-object `func_obj` that carries the state
  pointer. The prologue restores `pc`/`sp` from the frame and jumps to `restart` (`quickjs.c:18093-18115`).
  `OP_await`/`OP_yield` save `pc`/`sp` and return (`done_generator`). So suspension is "return from the
  interpreter with the frame on the heap"; it is not a separate stack. (`source-observed`)
- **Proxy and Reflect** (`quickjs.c:51908-53123`, 1,216 lines) are ordinary exotic-method classes; every
  trap goes through `JS_Call`. Svelte 5's `$state` uses Proxy, which was the slowest workload on every
  engine measured in svelters (`quickjs-jit-cranelift.md` §5). (`source-observed`, `documented`)
- **Property access path.** Bytecode `get_field` inlines the own-property walk (§1.4) and falls back to
  `JS_GetPropertyInternal` for getters, exotic objects and primitives. There is no inline cache.
- **Inline caches were removed** in NG PR #884 "Remove inline caches" (merged 2025-02-06, merge commit
  `7de6d46`; the PR body is "Refs: #876"). Issue #876 ("NG is slower compared to Bellard version") holds
  the reasoning. On the web-tooling benchmark ICs averaged 1.0367x (range 0.89x typescript to 1.54x
  prettier). They cost memory ("We're trading memory for CPU", bnoordhuis), and for run-once code "the IC
  infrastructure is pure overhead". Removing them cut the memory gap to Bellard to 0%, with about 2% fewer
  instructions. (`documented`, github.com/quickjs-ng/quickjs/pull/884 and /issues/876, read 2026-10-25.)
- **Interrupts.** `js_poll_interrupts` decrements `ctx->interrupt_counter` (initial 10,000) and calls the
  host handler at zero (`quickjs.c:8657-8676`); NG now passes the context (`876442b`). (`source-observed`)
- **Measured profile** (prior work, svelters `quickjs-jit-cranelift.md` §5, stock NG 0.16.2): on the Svelte
  signals workload, `JS_CallInternal` 71% self, `find_own_property` 7.8%, `JS_FreeValue` 3%,
  `map_find_record` 2.4%, GC about 2%. (`documented`)

C idioms (`measured`): `JS_CallInternal` has 197 `goto`s (67 per kloc, the densest region apart from
`compute_stack_size`), computed goto, `alloca`, and raw `sp[-n]` pointer arithmetic on the operand stack.
Stable Rust has no computed goto. The options (a `loop { match }` that LLVM turns into a jump table, or a
tail-call threaded design) are the interpreter note's subject. (`inferred`)

## 5. Builtins by size

All in `quickjs.c @ 7a23d8e`, from the attribution script (`measured`):

| Builtin | Lines | Notes |
|---|---:|---|
| TypedArrays + ArrayBuffer + DataView + SAB | 3,305 | resizable, immutable, Float16, base64 lives separately |
| Array | 1,899 | fast-array paths, sort |
| String (+ iterator, Annex B) | 1,768 | |
| RegExp builtins (libregexp not included) | 1,691 | `exec`, `@@replace`, `@@split`, string iterator |
| Iterator (array iterator, helpers, concat, zip glue) | 1,575 | |
| Map / Set / WeakMap / WeakSet (+ iterator, groupBy) | 1,580 | hashed records, set methods, upsert |
| Object | 1,286 | |
| Date (two sections) | 1,221 | own date parser; no time-zone database |
| Promise | 1,172 | plus 324 lines of async-iterator prototypes |
| base64 / hex (Uint8Array) | 1,084 | |
| Proxy | 1,049 | |
| JSON | 1,032 | parser lexer is in the lexer section |
| DisposableStack (explicit resource management) | 655 | |
| Atomics | 593 | `CONFIG_ATOMICS` |
| Math | 541 | `sumPrecise`, xorshift RNG |
| misc runtime (C function helpers, `JS_NewCFunction*`) | 442 | |
| WeakRef + FinalizationRegistry | 421 | |
| intrinsics init (`JS_AddIntrinsicBaseObjects`) | 386 | |
| Function | 362 | |
| Number | 295 | |
| Error, AggregateError | 289 | |
| URI functions | 274 | |
| BigInt (builtin object; arithmetic is 1,414 more) | 248 | |
| DOMException | 224 | NG only |
| CallSite | 170 | NG only |
| Reflect | 167 | |
| Symbol | 126 | |
| Boolean, Generator, eval, global, performance | 155 | |

- **Self-hosted builtins.** `Array.fromAsync`, `Iterator.zip` and `Iterator.zipKeyed` are written in JS
  (`builtin-*.js`, 440 lines), compiled to bytecode headers and loaded lazily through
  `JS_AUTOINIT_ID_BYTECODE` (`quickjs.c:8940-9013`). A port can use the same mechanism to write
  cold builtins in JS instead of Rust. (`source-observed`)
- **Intl is absent**: 23/3,382 intl402 tests at this sha (test262.fyi). svelters provides `Intl` itself
  through ICU4X (`svelters-host/src/quickjs/intl.rs:1`). (`measured`, `source-observed`)
- **Support libraries.** libregexp: compiler plus backtracking bytecode VM over 8- and 16-bit strings.
  NG replaced its VM with Bellard's register-based one in `5f2fb55` (2026-06-27); 45 regexp opcodes.
  libunicode: case conversion and folding, NFC/NFD/NFKC/NFKD, `CharRange` set algebra, script, general
  category and binary properties for `\p{}` and `v`-flag sets, from 5,160 generated table lines. dtoa.c:
  Bellard's own exact float printing/parsing with radix, fixed and fraction formats
  (`dtoa.h:40-85`), not David Gay's. (`source-observed`)
- **Modules** (2,282 lines): `JSModuleDef`, host normalize/loader callbacks (with import-attribute
  variants, `JSRuntime.u`), export resolution with cycle handling, top-level await
  (`js_execute_async_module`, async evaluation timestamps), JSON modules (`bb7a32d`), and module identity
  keyed by specifier and attributes (`8ef0e71`). (`source-observed`)
- **Job queue.** `rt->job_list` of `JSJobEntry {ctx, job_func, argc, argv[]}`; `JS_EnqueueJob` and
  `JS_ExecutePendingJob` run one job at a time (`quickjs.c:2488-2550`). Promise reactions,
  FinalizationRegistry callbacks and async module steps all use it. (`source-observed`)

## 6. quickjs-libc, and what svelters actually uses

- **Workers share nothing but SharedArrayBuffer memory.** Each `os.Worker` runs a new `JSRuntime` on its
  own thread (`worker_func`, `quickjs-libc.c:4080-4120`). Messages are serialized with
  `JS_WriteObject2(..., JS_WRITE_OBJ_SAB | JS_WRITE_OBJ_REFERENCE)` (`quickjs-libc.c:4263`) and sent over a
  pipe. SAB payloads are `malloc`'d with an atomic refcount header (`JSSABHeader`, `js_sab_alloc/free/dup`,
  `quickjs-libc.c:3955-3995`), and only the pointers are passed (`sab_tab`). No object, string or atom
  crosses runtimes. (`source-observed`)
- **Timers.** `os.setTimeout`/`setInterval` keep a list of `JSOSTimer` in the per-runtime
  `JSThreadState` (`quickjs-libc.c:183-202, 2471`), and `js_os_poll` runs them from a select/poll loop.
  (`source-observed`)
- **svelters uses none of quickjs-libc.** No `js_std_`/`js_os_` symbol appears in
  `crates/svelters-host`; `setTimeout` is JS in `src/prelude.js:53`. (`source-observed @ 0aa41697`)
- **svelters' engine surface** (`crates/svelters-host/src`, rquickjs 0.14.0 with features `std`,
  `loader`; `source-observed @ 0aa41697`):
  - runtime: `Runtime::new`, `set_memory_limit`, `set_max_stack_size`, `set_interrupt_handler`,
    `set_host_promise_rejection_tracker`, `run_gc`, `is_job_pending`, `execute_pending_job`
    (`quickjs.rs:207-251, 640-707`);
  - context and modules: `Context::full`, `set_loader` with a custom `Resolver`/`Loader` and
    `ImportAttributes`, `Module::declare`, `.eval()` returning a promise, `Module::write`/`Module::load`
    for the bytecode cache, `JS_GetVersion` in the cache stamp (`quickjs.rs:369-469, 1542-1566`,
    `bytecode.rs:44-61`);
  - values: `Value`, `Object`, `Array`, `Function`, `Exception`, `BigInt::from_i/from_u`, `Type::*`,
    `Persistent::save` handles kept across calls (`quickjs.rs:95, 174`), `Coerced`, `Opt`, `Args`;
  - raw C API: `JS_NewArrayBuffer`, `JS_NewArrayBufferCopy`, `JS_GetArrayBuffer`, `JS_IsArrayBuffer`,
    `JS_DetachArrayBuffer`, `JS_SetImmutableArrayBuffer`/`JS_IsImmutableArrayBuffer`,
    `JS_NewTypedArray`, `JS_GetTypedArrayType`/`Buffer`, `JS_NewStringUTF16`, `JS_ToCStringLenUTF16`,
    `JS_ToCStringLen2`, `JS_HasException` (`quickjs/buffers.rs`, `quickjs/conv.rs`).
  This list is the embedding API a Rust engine must offer on day one to replace rquickjs in svelters
  (`inferred`).

## 7. Test assets and conformance

- **test262 at this sha, from test262.fyi** (data generated 2026-10-25, test262 `d53ebc8`, engines
  `qjs_ng` = `7a23d8e`, `qjs` = `7d1c13c`; `measured` from `https://data.test262.fyi/index.json` and
  `built-ins.json`):

| Scope | Total | QuickJS-NG | Bellard QuickJS | Boa `9871775` | V8 15.8.47 |
|---|---:|---:|---:|---:|---:|
| all | 53,616 | 44,774 (83.5%) | 52,813 (98.5%) | 51,252 (95.6%) | 52,377 (97.7%) |
| language | 23,726 | 23,288 | 23,285 | 23,281 | 23,088 |
| built-ins | 23,823 | 18,917 | 23,553 | 22,880 | 23,350 |
| built-ins/Temporal | 4,605 | 0 | 4,605 | 4,603 | 4,605 |
| intl402 | 3,382 | 23 | 3,373 | 2,673 | 3,345 |
| all minus Temporal and intl402 | 45,629 | 44,751 (98.1%) | 44,835 (98.3%) | | |

  NG's other large built-in gaps are Atomics (273/389; `Atomics.waitAsync` is skipped in `test262.conf`),
  Promise (643/732) and ShadowRealm (0/64). In the language suite NG (23,288) is level with Bellard (23,285)
  and Boa, behind JSC (23,452) and SpiderMonkey-experimental (23,397).
- **NG's own configuration.** `test262.conf` (284 lines) skips 30 features (Intl-*, Temporal,
  ShadowRealm, decorators, import-defer, source-phase imports, `Atomics.waitAsync`, tail calls,
  legacy-regexp) and excludes 222 paths. `test262_errors.txt` lists **40 known failures** in that
  configuration. The test262 submodule is pinned at `5ef1e57`. `test262-fast.conf` (132 lines) is the
  quick CI set. (`source-observed`)
- **Other assets.** `api-test.c`, 2,320 lines and 37 test functions, exercises the C API; it is the best
  spec for a Rust embedding API's semantics. `tests/` holds 106 files, mostly regression tests named
  `bugNNNN.js`. `fuzz.c` fuzzes only `JS_ReadObject`, while Bellard's `fuzz/` has 7 libFuzzer targets
  (eval, compile, bytecode, JSON, regexp, regexp-compile, module export). The CI workflows include
  `gc-stress.yml`, `tsan.yml` and `valgrind.yml`. (`source-observed`)

## 8. Porting difficulty, coupling and replacements

C-idiom density per subsystem (`measured`; per kloc of the subsystem's lines; "rc" counts
`JS_FreeValue(RT)`, `js_dup`, `JS_DupValue`, `js_free(_rt)`):

| Subsystem | Lines | goto/kloc | rc ops/kloc | Other C idioms |
|---|---:|---:|---:|---|
| decls + alloc + runtime | 3,118 | 0-3 | 4-46 | header-before-object (`js_rc`), intrusive lists (39 list ops), unions |
| atoms + strings | 2,314 | 9-11 | 14-32 | bitfields, flexible arrays, 8/16-bit dual storage, slices pointing into parents |
| shapes + objects | 1,234 | 9 | 12-33 | negative-offset hash table in one allocation, per-class union `u` |
| gc | 709 | 0 | 40 | intrusive lists (23 list ops), zombie objects, phase flags |
| properties | 3,478 | 24 | 32 | exotic dispatch through function-pointer tables, autoinit packed pointers |
| conversions + operators | 3,737 | 35-39 | 29-57 | `sp[-n]` operand pointers in slow paths |
| interpreter + coroutines | 4,789 | 26-67 | 36-74 | computed goto, `alloca`, catch offsets on the value stack, fake `func_obj` for resume |
| lexer + parser | 9,483 | 41-50 | 2-6 | `goto fail` error exits, mutable parse state, emitter fused into parser |
| resolve passes + stack size | 3,310 | 16-76 | 0-24 | in-place bytecode rewriting, label relocation lists |
| modules | 2,282 | 19 | 42 | graph with cycles (DFS indices), async evaluation state |
| serialization | 2,252 | 51 | 15 | untrusted-input parsing (bytecode is trusted) |
| builtins (all) | 24,334 | 43 | 50 | `JS_CFUNC_*` tables (1,073 entries), `magic` ints selecting behaviour |

Ranking (`inferred` from the sizes and densities above and the coupling read in §1-5). Size is lines,
coupling is how many other subsystems change when this one changes, and risk is the chance that a
port gets subtle semantics or performance wrong.

| Rank (port first) | Subsystem | Size | Coupling | Risk | Port or replace | Crate candidates (crates.io, 2026-10-25) |
|---|---|---|---|---|---|---|
| 1 | Value, heap header, atoms, strings, ropes | 3.9k | very high (everything) | high: decides the GC/ownership model | port the design; the ownership model comes from the GC note | none |
| 2 | Shapes, objects, properties, fast arrays | 4.7k | very high | high: perf (7.8-10.8% in `find_own_property`) | port faithfully | none |
| 3 | GC + finalizers + weak refs | 1.2k core (plus every dup/free site) | very high | high | redesign per the GC note; keep weak-ref semantics and the "jobs, not callbacks" rule | none fits as is |
| 4 | Bytecode format + reader/writer | 2.3k | high | medium | port faithfully: it is the bridge to the C compiler | none |
| 5 | Interpreter + coroutines + call API | 4.8k | high | high: 71% of time | port faithfully, then optimise | none |
| 6 | Conversions, operators, BigInt | 5.2k | medium | low-medium: pure functions | port faithfully (short BigInt inline matters) | `num-bigint` 0.5.1 only for heap BigInts, optional |
| 7 | Float ↔ string | 1.6k (`dtoa.c`) | low | low | replace for shortest printing, port the rest | `ryu-js` 1.0.3, `fast-float2` 0.2.4 (both used by Boa 0.22) |
| 8 | Builtins used by Svelte (Object, Function, Array, Error, Symbol, Promise, Map/Set/Weak*, Proxy/Reflect, String, JSON, Iterator, WeakRef) | ≈15k | medium (internal API) | medium | port faithfully; cold ones may be self-hosted JS | none |
| 9 | Compiler: lexer, parser/emitter, resolve passes | 13.7k | medium (emits the opcode set) | high: Annex B, eval, classes, destructuring | port faithfully *after* rank 5; consider `oxc_parser` 0.155 only as a later front end | `oxc_parser`, `boa_parser` 0.22 |
| 10 | Modules | 2.3k | medium | medium (TLA, cycles) | port faithfully; the loader API mirrors svelters' needs | `oxc_resolver` is resolution only (svelters already uses it) |
| 11 | RegExp engine | 3.5k + 1.7k builtins | low (string storage) | medium | replace first, port later only if needed | `regress` 0.12.0 with `utf16` (Boa: 1,875/1,879 RegExp tests) |
| 12 | Unicode tables and algorithms | 2.1k + 5.2k generated | low | low | replace | `icu_casemap`, `icu_normalizer`, `icu_properties` 2.4.0 (already in svelters' `Cargo.lock`) |
| 13 | TypedArrays, Atomics, DataView, base64 | 5.0k | low-medium | medium (resizable buffers, detaching) | port faithfully; needed for svelters' buffer ABI | none |
| 14 | Date | 1.2k | low | low | port; Temporal via crate if wanted | `temporal_rs` 0.2.6 (Boa: 4,603/4,605) |
| 15 | Intl | 0 in NG | none | n/a | keep svelters' ICU4X shim | ICU4X |
| 16 | quickjs-libc | 5.1k | none for svelters | n/a | do not port | n/a |

Crate notes:

- **regress.** Boa 0.22.0 depends on `regress ^0.12.0` with feature `utf16`, `ryu-js ^1.0.3`,
  `fast-float2 ^0.2.4`, `icu_normalizer ~2.3` (with `utf16_iter`), `icu_casemap ~2.3`, `num-bigint ^0.5.1`,
  `temporal_rs ^0.2.6` and `xsum ^0.1.6` (`documented`, crates.io dependency API for boa_engine 0.22.0).
  The default-feature docs of `regress` 0.12.0 show only `&str` matching; UTF-16 matching is behind the
  `utf16` feature (`documented`, docs.rs feature list). Not checked: regress's speed against libregexp,
  and its support for QuickJS's Latin-1 (8-bit) string storage. A port with Latin-1 strings may need to
  widen them or add a Latin-1 path (`hypothesis`).
- **Unicode.** ICU4X already sits in svelters' dependency tree (`Cargo.lock` lists `icu_casemap` and
  `icu_properties` 2.4.0). Using it for case mapping, normalization and properties drops 7k lines of C
  plus a table generator. (`source-observed`, `inferred`)
- **Parser.** `oxc_parser` 0.155.0 is fast and maintained, but QuickJS's parser and code generator are one
  thing: replacing it means writing an AST-to-phase-1-bytecode emitter that repeats 7.6k lines of
  decisions (destructuring, class fields, private names, `using`, function-name inference, eval scope
  capture). svelters' own compiler does not use oxc (`crates/svelters-compiler/Cargo.toml @ 0aa41697`), so
  there is no shared-AST win today. (`source-observed`, `inferred`)

## 9. Upstream churn

`git log --since=2025-10-01 --numstat -- quickjs.c`, grouped by author month (`measured`; NG's history
was deepened with `git fetch --shallow-since=2025-08-01`, so no graft commit is counted):

| Month | NG commits | NG +/− | Bellard commits | Bellard +/− |
|---|---:|---|---:|---|
| 2025-08 (committed in window) | 1 | +288/−10 | | |
| 2025-10 | 12 | +264/−124 | 21 | +1,410/−1,133 |
| 2025-11 | 4 | +167/−27 | 16 | +2,013/−1,120 |
| 2025-12 | 7 | +46/−65 | 7 | +114/−70 |
| 2026-01 | 36 | +2,206/−695 | 0 | |
| 2026-02 | 15 | +318/−85 | 0 | |
| 2026-03 | 33 | +1,680/−922 | 15 | +577/−222 |
| 2026-04 | 9 | +2,400/−196 | 0 | |
| 2026-05 | 11 | +81/−24 | 7 | +1,550/−348 |
| 2026-06 | 10 | +914/−301 | 12 | +498/−171 |
| 2026-07 | 47 | +1,241/−446 | 0 | |
| 2026-08 | 16 | +165/−127 | 0 | |
| 2026-09 | 22 | +204/−109 | 21 | +584/−318 |
| 2026-10 (to the 23rd) | 9 | +67/−80 | 21 | +3,490/−282 |
| **Total** | **232** | **+10,041/−3,211** | **120** | **+10,236/−3,664** |

- NG authors: Saúl Ibarra Corretgé 91, Ben Noordhuis 66, Divy Srivastava 18, then a long tail.
- By subject (NG, 232 commits): 41 memory-safety fixes (leak, use-after-free, double free, overflow,
  over-read, type confusion), 52 other fixes, 46 "Add/Implement/Support" features, 25 optimisations,
  6 "Port" commits. (`measured`, keyword regex on subjects; categories overlap a little.)
- Cost of "tracking upstream" (`inferred`): a faithful Rust port would have to re-apply about 18 commits
  a month by hand into code that no longer matches line for line. In practice that works only for the
  ~45% of commits that change semantics (features and non-memory fixes). The memory-safety fixes (18%)
  mostly do not apply to safe Rust. The optimisations (11%) need re-deriving, not copying. A port should
  therefore pin a base (`7a23d8e` or v0.17.0), import NG's `tests/` and test262 configuration, and treat
  later upstream commits as a reading list filtered by test262 deltas. That is close to what NG itself
  does with Bellard's tree.

## Recommendation

**Port faithfully** (same algorithms, same data layouts in spirit, same opcodes):

1. value representation, atoms, strings with ropes, shapes, objects, properties and fast arrays;
2. the bytecode format and `JS_ReadObject`/`JS_WriteObject` (BC_VERSION 29 compatibility at first);
3. the interpreter, coroutines and call path;
4. conversions, operators and BigInt;
5. the builtins Svelte 5 uses, then the rest except Intl/Temporal;
6. the compiler (lexer, parser/emitter, `resolve_variables`, `resolve_labels`, `compute_stack_size`);
7. modules and the job queue.

**Redesign, guided by other notes:** ownership and GC. Keep QuickJS's observable rules: finalizers cannot
run JS, FinalizationRegistry callbacks are jobs, weak targets are cleared exactly once, and OOM and
interrupt exceptions are uncatchable.

**Replace with crates:** regexp (`regress`, `utf16`), Unicode (ICU4X), float printing and parsing
(`ryu-js`, `fast-float2`; keep a port of `dtoa.c`'s fixed and radix modes if they don't cover them), Temporal
(`temporal_rs`) if wanted, Intl (svelters' existing ICU4X shim). Do not port quickjs-libc.

**Suggested port order** (each step testable against C QuickJS-NG `7a23d8e` as an oracle):

1. Values, heap and GC skeleton, atoms, strings, shapes, objects, property get/set/define/delete. Test with
   Rust unit tests and a minimal intrinsics set.
2. Bytecode reader for C-written bytecode and the interpreter for the regular and short opcodes; run
   bytecode produced by `qjsc`/`JS_WriteObject` from C NG. This gives a running engine with no parser.
   Differential check: same bytecode, same output, in both engines.
3. Object, Function, Array, Error, Symbol, Promise + job queue, Map/Set/WeakMap, Proxy/Reflect, String,
   JSON, Iterator, generators and async, then a run of the Svelte 5 runtime from C-compiled bytecode
   (signals, proxy) to compare with svelters' existing benchmarks.
4. Compiler port, checked by comparing its bytecode dump with C NG's on NG's `tests/` and test262 (the
   opcodes are the same, so the output should be identical before any optimisation divergence).
5. Modules, the rquickjs-shaped embedding API from §6, and svelters integration behind a feature flag.
6. The remaining builtins (TypedArrays/Atomics in full, Date, base64, DisposableStack), crates for
   regexp/Unicode, and test262 at NG's configuration (target: NG's 40 known failures or fewer).

## Open questions for the user

1. **Base: NG or Bellard?** Recommendation: NG at a pinned sha (`7a23d8e`, v0.17.0+), because svelters
   runs NG and its API, tests and CI are what we'd check against. Read Bellard for algorithms and fuzz
   targets.
2. **Track upstream, or fork at the port?** Recommendation: fork at the port. Track test262 and NG's
   regression tests, and review upstream commit subjects monthly; don't promise commit parity (§9).
3. **Bytecode compatibility with C QuickJS-NG?** Recommendation: yes for the first milestones (it lets the C
   compiler bootstrap the Rust interpreter and gives a differential oracle), then free to diverge once the
   Rust compiler passes the same tests.
4. **Intl and Temporal in the engine?** Recommendation: no. Keep svelters' ICU4X Intl shim, and add
   `temporal_rs` only when an app needs Temporal. Bellard's 77k-line AI-coded version is not a porting
   target.
5. **Regexp: crate or port?** Recommendation: start with `regress` (`utf16`), and port libregexp (3.5k lines,
   self-contained) only if measurements on Svelte-app regex use show a gap.

## First steps in this repository

1. Commit the census scripts (`tools/census/qjs_census.py`, `qjs_idioms.py`) with this marker table, so the
   numbers can be re-run on any NG sha.
2. Add `docs/research/notes/quickjs-census-ranges.tsv` generated by the script (line range → subsystem) to
   track how a later NG sha moves.
3. Write a tiny C harness (outside the engine) that builds NG `7a23d8e`, compiles a JS file with
   `JS_WriteObject`, and dumps bytecode (`JS_DUMP_BYTECODE_FINAL`); that is the oracle for steps 2 and 4
   of the port order.
4. Vendor NG's `tests/`, `test262.conf`, `test262_errors.txt` and `api-test.c` as the first acceptance
   suite, with the test262 submodule pinned at `5ef1e57`.
5. Sketch the hocus embedding API from the §6 list (about 25 entry points) as a Rust trait, implemented
   first over rquickjs, so svelters can switch engines behind it.
