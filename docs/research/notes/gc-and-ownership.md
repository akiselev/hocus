# GC and ownership for hocus: translating QuickJS's pointer graph into Rust

Researcher note, 2026-10-25. Topic: how a Rust port of QuickJS-NG should own its object graph, which
garbage collector it should use, and where the `unsafe` code should live.

Sources read (shallow clones, HEAD on 2026-10-25, under the scratchpad `src/`):
QuickJS-NG `7a23d8e`, gc-arena `5ea967a`, piccolo `ce709eb`, Nova `877ef16`, Boa `64c209f`,
rust-gc `b7405c9`, dumpster `6551a2c`, shredder `266de5a`, safe-gc `6a184a8`, shifgrethor
`ab1873f`, josephine `1f0563b`, mmtk-core `ed89711` (0.33.0), mmtk-v8 `7009cfb`, wasmtime
`ffff2e2` (sparse checkout). svelters `0aa41697` and cadatomic `75b6a47` for the local context.
Grades: `documented`, `source-observed`, `measured`, `inferred`, `hypothesis` (BRIEF.md).

## Summary

1. **Use a tracing GC, not reference counting.** QuickJS's model is a refcount on every value plus
   a trial-deletion cycle collector. The collector walks the *whole* GC heap in one pause. I measured
   that pause on QuickJS-NG `7a23d8e`: 29–72 ns per live object, so **5–7 ms for 100k live
   objects and ~190 ms for 1M** (measured, cloud, not a gate). One 120 Hz frame is 8.3 ms. That
   design can't meet presto's budget once a UI heap passes roughly 15–30k objects (inferred). An
   incremental tracing collector removes both costs: the pause, and the refcount traffic.
   - The traffic is 3% self time in `JS_FreeValue` on Svelte signals (svelters note).
   - QuickJS's source has 1,480 `JS_FreeValue` lines and 1,607 `goto fail/exception` lines, many of
     which exist only to release references (source-observed).
2. **The object graph lives in one per-isolate GC heap with precise roots.** Nothing scans the
   machine stack.
   - Roots are: the interpreter's frame slots, which are a heap-owned register file; a LIFO handle
     stack for Rust builtins (V8's `HandleScope` and `Local` model); and a global root table for
     host-held references.
   - All of these are precise, so the collector may later move objects (compaction, a nursery).
3. **Two reference kinds, both checked by the borrow checker** (prototype compiles; the two misuse
   cases are rejected, measured, rustc 1.97.0):
   - `Gc<'n, T>` / `Value<'n>`: unrooted and zero-cost. Valid only while a shared `&NoGc` borrow
     lives. GC needs `&mut Cx`, so the borrow checker forbids GC while one exists. This is gc-arena's
     brand, scoped to a region rather than to a whole `mutate` call.
   - `Local<'s, T>`: a rooted handle-stack slot that survives GC. Every function that may GC (any
     function that can call JS) takes and returns `Local`s.

   This drops Nova's `bind`/`unbind` transmute discipline: Nova has 1,036 `unsafe {` blocks, mostly
   lifetime transmutes, plus a custom lint crate. It also drops gc-arena's "mutation xor collection"
   rule, which forces a stackless VM.
4. **Collector: an incremental, non-moving mark-sweep first** (Lua 5.4 / gc-arena style). It is paced
   by allocation debt and driven by presto's frame scheduler:
   - small steps (≤0.25 ms) inside JS execution;
   - deadline-bounded steps in the gap after each commit (V8 and Blink idle tasks);
   - finishing the cycle before the window goes idle.

   One write-barrier funnel serves incremental marking now and generational card marking later. A
   generational nursery, compaction and MMTk come later and are optional.
5. **The `unsafe` lives in one crate, `hocus-gc`.** Target: about 30–60 `unsafe` blocks, all in that
   crate. Everything else is `#![forbid(unsafe_code)]`, except the JIT and possibly the interpreter's
   dispatch loop, which are audited separately.
   - For scale: piccolo, a full Lua VM on gc-arena, has 24 `unsafe` blocks in 18.6k lines
     (source-observed).
6. **Isolates.** One heap per isolate, and handles are `!Send`. Values cross isolates by structured
   clone. Atoms, strings and bytecode can live in a read-only shared space that needs no barrier and
   no counting. A shared mutable heap is out of scope.
7. **JIT.** Keep the same rule quickjs-jit already uses: at a safepoint every live GC value is in a
   frame slot. Then a JIT needs no stack maps at first. Later, Cranelift's user stack maps give
   spill-and-reload at safepoints, which supports a moving GC.

**Recommendation:** build `hocus-gc` as described in §7: incremental mark-sweep, precise roots,
`Local`/`Gc<'n>` handles and one barrier funnel. Don't depend on gc-arena, Nova's heap, boa_gc,
dumpster, Alloy or MMTk. Borrow their ideas, listed in §1.

**The trade-off:**
- We lose QuickJS's prompt, deterministic freeing. That matters little: the spec leaves finalization
  timing to the implementation, and svelters already keeps explicit destroy paths.
- Every builtin that can call JS must root its arguments. That costs about what `js_dup` costs today.
- We write and own a collector, about 3–5k lines (gc-arena is 5.6k).

## 1. The design space

| Design | Example(s) | Safety model | Roots | Moving | Incremental / generational | Write barrier | Fit for hocus |
|---|---|---|---|---|---|---|---|
| Raw pointers + refcount + cycle collector | QuickJS-NG | none (C) | refcounts; trial deletion finds them | no | no / no; full-heap pause | none (refcount *is* the bookkeeping) | the thing being replaced |
| Branded lifetime, mutation xor collection | gc-arena (piccolo, Ruffle, fabricator) | `'gc` brand + `unsafe trait Collect` (derived) + `Lock`/`RefLock` barriers | only the arena root; GC only between `mutate` calls | no | incremental (Lua 5.4 style), not generational | forward/backward barrier through `&Mutation` | ideas yes, crate no: forces a stackless VM |
| Typed indices in per-type vectors, scoped roots | Nova | `GcScope`/`NoGcScope` lifetimes; `bind`/`unbind` are `transmute`s | realms, `Scoped` list, VM frames; GC at any `GcScope` call | yes (compacting) | no / no; stop-the-world | none needed (STW) | closest model; ergonomics and unsafe count are the warning |
| `Rc`-like with root counting | rust-gc, boa_gc | safe API, unsafe core | per-pointer root counts (rust-gc) or ref − internal counts (boa) | no | no / no | none | keeps refcount traffic; STW |
| `Rc` + cycle detection | dumpster, bacon-rajan-cc | safe API, unsafe core | refcounts + dirty set | no | no | none | the "refcount in Rust" answer; still pauses |
| Concurrent, guarded | shredder | guards on access | handle counting | no | concurrent | — | unmaintained since 2022-01 |
| Index arenas, zero unsafe | safe-gc | `#![forbid(unsafe_code)]` | `Root<T>` table | no | no | none | proof of concept; slow access path |
| Pinned roots on the stack | shifgrethor, Josephine | `letroot!` + `Pin`; Josephine wraps SpiderMonkey | intrusive root list | with pinning | — | — | research; ideas only |
| Compiler-integrated conservative GC | Alloy (rustc fork + BDWGC) | conservative stack scan; finalizer safety analysis | conservative | no | no | none | needs a rustc fork; conservative stack scanning rules out moving |
| GC framework | MMTk (OpenJDK, Ruby, Julia, V8 experiment) | binding traits over raw addresses | binding enumerates slots and roots | plan-dependent | Immix, GenImmix, StickyImmix, ConcurrentImmix (SATB), LXR | per plan; binding emits fast paths | later option behind our trait |
| Wasm GC in a sandboxed heap | Wasmtime (null, DRC, copying) | refs are u32 indices into a GC heap region; barriers via `GcStore` | Cranelift user stack maps | copying collector: yes | no / no | DRC: inline RC barriers; copying: none | model for compressed refs and JIT stack maps |

### 1.1 gc-arena (and piccolo, Ruffle)

- **Safety rests on three things** (documented, gc-arena README @ `5ea967a`):
  - an `unsafe trait Collect` that is almost always derived;
  - a generative `'gc` lifetime that keeps pointers inside `mutate` callbacks;
  - "mutation xor collection": `Arena::mutate` must return before `Arena::collect` can run, "so we
    know that there are no untraceable objects on the stack".
- **`Gc<'gc, T>` is a zero-cost `Copy` newtype around `*const T`.** Every allocation is a separate
  `alloc::alloc`, linked into a global list through `GcHeader { next, tagged_vtable }`. The colour
  and flag bits sit in the vtable pointer's low bits (source-observed, `src/gc_ptr.rs:55,212-222`).
- **Collection is incremental mark-and-sweep, "very similar to … PUC-Rio Lua"** and tuned for low
  pause time. It is paced by allocation debt, with `Pacing::DEFAULT` = sleep 0.5, mark 0.1,
  trace 0.4, keep 0.05, drop 0.2, free 0.3 (source-observed, `src/metrics.rs:102-186`).
  - The stability rule: each object's work per cycle must stay below 1.0 unit, for example
    `mark+trace+keep < 1.0`.
  - There is no moving, no compaction and no multi-threading (documented, README).
- **Interior mutability goes through `Lock`/`RefLock`.** A write takes `&Mutation<'gc>` and runs a
  forward or backward barrier (source-observed, `src/context.rs:30-80`, `src/lock.rs`).
  - Derived `Collect` refuses `Cell`/`RefCell` holding `Gc`, so a safe derive can't break the
    barrier rule (documented, `src/collect.rs:5-22`).
  - Held `Gc` pointers must not be read in `Drop` (rule 2).
- **The cost, in piccolo's design:** the VM had to be "stackless", with Rust callbacks reified as
  `Sequence` state machines. The author calls this "certainly … limiting". The downside is "having to
  do this transform **manually**" (documented, kyju.org blog, 2024-05-01).
  - `Executor::step` takes a fuel budget of about one unit per Lua instruction, and GC runs between
    steps (documented, same post).
- **Unsafe:** 101 `unsafe {` blocks in 5,646 lines of gc-arena, and 24 in piccolo's 18,621 lines
  (source-observed, grep).

**For hocus:** QuickJS is recursive C. `JS_CallInternal` recurses for every JS→native→JS edge:
getters, Proxy traps, `valueOf`, sort comparators, `Array.prototype.map` callbacks. Porting it to a
stackless trampoline is a rewrite, not a port (inferred). GC could run only between host tasks, so
one long task that allocates (parsing a big JSON, building a 1M-row list) could never collect
(inferred). We should take gc-arena's pacing, its `Lock` barrier funnel and its derive rules, but
not its rooting model.

### 1.2 Nova

- **Data-oriented heap:** one vector per heap type (objects, arrays, strings, …), and handles are
  `u32` indices (documented, `nova_vm/src/heap/README.md`).
- **The GC is stop-the-world, tracing and compacting.** "Most `Value`s that remain in the heap move
  during garbage collection" (documented, `GARBAGE_COLLECTOR.md`).
- **Roots:** realms, a list of "scoped" values, and the VMs waiting on calls.
- **Safepoints:**
  - the interpreter checks `agent.check_gc()` before *every instruction* (source-observed,
    `engine/bytecode/vm.rs:304`);
  - the trigger is 2 MiB allocated since the last GC (source-observed, `execution/agent.rs:952`).
- **Two lifetimes enforce rooting.** `GcScope<'gc,'scope>` (may GC) and `NoGcScope` (shared borrow,
  no GC).
  - Values are `Value<'gc>`.
  - Before a call that may GC, values are `unbind()`-ed to `'static` and passed on; afterwards they
    are `bind()`-ed again or read back from `Scoped` (documented, `GARBAGE_COLLECTOR.md`: "At the
    beginning of a function, bind all parameters … Yes, this is annoying … You **must** still do it,
    or bugs will seep through!").
  - `unbind` is a safe method whose body is `core::mem::transmute` (source-observed,
    `engine/context.rs:395-401`). Misusing it is not caught by the compiler. Nova ships `nova_lint`
    (`gc_scope_comes_last`, `immediately_bind_scoped`, …) to catch the common mistakes.
  - Because handles are bounds-checked indices, a stale handle reads the *wrong object*, not freed
    memory. Nova is memory-safe there, but not GC-correct (inferred).
- **Unsafe:** 1,036 `unsafe {` blocks in 158,888 lines. The context lines mention `transmute` 294
  times and `core::mem::` 285 times (source-observed, grep; mostly lifetime transmutes).

**For hocus:** Nova's idea that the borrow checker enforces "root before a GC point" is the right
one. The friction comes from passing *unrooted* values across GC-capable calls. Making rooted
handles the calling convention for GC-capable functions removes `unbind` entirely (§7).

### 1.3 boa_gc and rust-gc

- **boa_gc** is a thread-local, stop-the-world mark-sweep (source-observed, `core/gc/src/lib.rs`).
  - `GcHeader { ref_count, non_root_count }`: cloning a `Gc` bumps `ref_count`.
  - Collection first traces the heap to count *internal* references (`trace_non_roots`). An object
    with `ref_count > non_root_count` is a root (source-observed, `internals/gc_header.rs`,
    `lib.rs:225-300`).
  - That is QuickJS's trial-deletion trick, applied as a root finder. It keeps the per-clone
    counting traffic.
  - The default threshold is 1 MiB (source-observed, `lib.rs`).
  - boa_engine has only 177 `unsafe {` blocks in 156,786 lines, because a `Gc` is used like an `Rc`
    (source-observed). The price is the counting traffic and full-heap pauses.
- **rust-gc** keeps a per-box `roots` count, plus a "rooted" bit in every `Gc` that is flipped when
  the `Gc` moves into or out of the heap (source-observed, `gc/src/gc.rs:56-107`, `lib.rs:147-332`).
  - It is stop-the-world.
  - Alloy's paper measured it at roughly 3× slower than Alloy on Binary Trees, with a 4× larger heap
    (documented, Hughes & Tratt 2025, §8.2).

### 1.4 dumpster, shredder, safe-gc

- **dumpster** "extends reference-counted garbage collection (such as `std::rc::Rc`) with a
  cycle-detection algorithm" (documented, README @ `6551a2c`).
  - A decrement that doesn't reach zero marks the allocation "dirty".
  - Collection walks from the dirty set and counts "unaccounted-for references" (source-observed,
    `unsync/collect.rs:181-245`). This is synchronous trial deletion, as in Bacon–Rajan.
  - It has `unsync` and `sync` variants. It shows that `Rc` plus a cycle collector *can* have a safe
    API: 100 `unsafe {` blocks inside, none for users.
- **shredder:** a concurrent collector with guarded access and one global collector (documented,
  README). Last commit 2022-01-16 (source-observed).
- **safe-gc:** zero `unsafe` and `#![forbid(unsafe_code)]` (documented). "Not … a particularly
  high-performance garbage collector."
  - Every `Heap::get` does a `TypeId` hash lookup, a downcast and a heap-id assert
    (source-observed, `src/lib.rs:857-872,974-980`).
  - It is the cleanest demonstration that index handles plus `&Heap`/`&mut Heap` access are fully
    safe.

### 1.5 shifgrethor and Josephine

- **shifgrethor:**
  - Roots are created by `letroot!` and pinned (`Pin`), so they drop in stack order.
  - `Gc<'root, T>` proves rooting. `GcStore` fields become `Gc` only through derived accessors.
  - It supports moving collectors "as long as they implement a pinning mechanism".
  - The author calls it an experiment that "likely has glaring bugs" (documented, README @ `ab1873f`).
- **Josephine** wraps SpiderMonkey. Mutable access to JS-managed data requires `&mut JSContext`,
  and it statically keeps compartments apart (documented, README @ `1f0563b`, 2018).
- **What we take from them:** use the context as the access token (`&mut` for anything that may GC,
  `&` to read). From shifgrethor we take the idea that rooting should be a typed proof, not a
  convention.

### 1.6 Alloy (Hughes & Tratt, OOPSLA 2025)

- **Design:** a GC for Rust built on a modified rustc and standard library with BDWGC. It uses
  conservative stack scanning, and `Gc<T>` is `Copy` (documented, arXiv 2504.01841v4,
  DOI 10.1145/3763179, §2–3).
- **Its contribution is finalizers.** Rust `Drop` impls become finalizers, with three supports:
  - *finalizer safety analysis*, which rejects unsound destructors;
  - *finalizer elision*, which drops a finalizer when the GC frees the memory anyway;
  - *premature finalizer prevention*, which inserts fences.
- **Results** (documented, §8.2):
  - a 5% wall-clock overhead on its suite;
  - typically a larger average heap footprint;
  - a worst case of 3.35× attributed to finalizers (§5);
  - on Binary Trees: about 1.3× faster than `Rc<T>`, about 3.5× slower than an arena, and about 3×
    faster than rust-gc;
  - BDWGC as an allocator alone costs 2–26% against modern allocators.
- **For hocus:** it needs a compiler fork, and conservative scanning rules out moving and precise JIT
  stack maps. Its lesson applies anyway: **finalizers are the expensive and unsafe part of a Rust
  GC**. hocus heap objects should have no `Drop`. Host cleanup should be an explicit class hook that
  runs after sweep and can't touch dead objects (inferred).

### 1.7 MMTk

- **What it is:** "a framework for the design and implementation of memory managers", in Rust
  (documented, mmtk-core README, 0.33.0).
  - Plans: NoGC, SemiSpace, GenCopy, GenImmix, MarkSweep, PageProtect, Immix, Lisp2, OVC,
    StickyImmix, **LXR** and **ConcurrentImmix** ("Concurrent non-moving immix using SATB")
    (source-observed, `src/util/options.rs:29-57`).
- **The binding API is a set of traits over raw addresses** (source-observed, `src/vm/`):
  - `ObjectModel`;
  - `Scanning::scan_object`, `scan_roots_in_mutator_thread` and `scan_vm_specific_roots`;
  - `Collection::stop_all_mutators`, `resume_mutators`, `block_for_gc` and `spawn_gc_thread`.
  - mmtk-core itself has 589 `unsafe {` blocks in 63k lines (source-observed).
- **Users:**
  - OpenJDK, Ruby and Julia are maintained bindings (documented, README).
  - Julia's docs list StickyImmix as the default plan and say moving is "not supported yet"
    (documented, docs.julialang.org v1.14-dev `gc-mmtk`).
  - mmtk-v8 says it "currently only supports `linux-x86_64`". Its last commit is 2025-01-07
    (source-observed).
- **LXR** is a reference-counting Immix with occasional SATB tracing for cycles and copying only in
  short pauses. Its field-logging barrier has 1.6% mutator overhead. It reports 4% better throughput
  than G1 and 43% better than Shenandoah on 17 workloads in a moderate heap (documented, Zhao,
  Blackburn & McKinley, PLDI 2022, arXiv 2210.17175).
- **For hocus:** MMTk is the way to get a generational, moving or concurrent collector without
  writing one. But it assumes GC worker threads and a stop-the-world handshake, and the binding is
  unsafe by nature. No published pause data for small, UI-sized heaps was found (not found).
  - Design `hocus-gc`'s internal object model (a header with a type id, precise slot enumeration, a
    root enumeration callback) so an MMTk plan could later sit behind it. Don't start there.

### 1.8 Wasmtime (Wasm GC)

- **`VMGcRef` is "not actually a pointer, but a compact index into a Wasm GC heap"**, or an inline
  `i31` (documented, `runtime/vm/gc/gc_ref.rs:106-128`).
  - It is deliberately not `Clone`/`Copy`, so that clones, writes and drops go through
    `GcStore::clone_gc_ref`, `write_gc_ref` and `drop_gc_ref`, the barrier funnel. The escape hatch
    is `unchecked_copy`.
- **There are three collectors** (source-observed, `vm/gc/enabled/drc.rs:1-45`, `copying.rs:1-11`):
  - null;
  - DRC, deferred reference counting with no cycle collector, which counts only stores into globals
    and tables;
  - a Cheney copying collector that "does not require any read or write barriers".
  - Under DRC, stack references are over-approximated in an intrusive list and corrected at GC from
    stack maps.
- **Barriers are emitted inline by Cranelift codegen** (source-observed,
  `crates/cranelift/src/func_environ/gc/drc.rs:460-760`): the DRC read barrier pushes onto the
  over-approximated root list, and the write barrier does the increment and decrement. The copying
  collector emits none (`copying.rs:399-422`).
- **Cranelift user stack maps:** the CLIF producer spills live GC values to stack slots at each
  safepoint and records them. "Currently all non-tail call instructions are considered safepoints"
  (documented, `cranelift/codegen/src/ir/user_stack_maps.rs:1-30`).

**For hocus:**
- **Compressed 32-bit references into one per-isolate region** give two things:
  - smaller `Value`s: V8 measured "up to 43%" heap reduction with pointer compression, at 64-bit
    performance after optimisation (documented, v8.dev/blog/pointer-compression, 2020-03-30);
  - containment: a stale reference stays inside the isolate's heap (inferred from Wasmtime's
    design).
- **Funnel every heap store through one barrier entry point.**

## 2. Per-design properties

| | Safety | Roots | Moving | Incr./gen. | Barrier | Finalizers | Interior mutability cost | Measured overhead (cited) |
|---|---|---|---|---|---|---|---|---|
| QuickJS-NG | C | refcount + trial deletion | no | no / no | n/a | class finalizer runs at refcount 0, or during cycle free (zombies visible) | n/a | GC ~2%, `JS_FreeValue` 3% self (svelters note) |
| gc-arena | safe API; unsafe derive-able trait | arena root + `DynamicRootSet`; GC only outside `mutate` | no | yes / no | `Lock`/`RefLock` + `&Mutation` | `Finalization` phase API, `resurrect` | `Lock` = `Cell` + barrier | none published (not found) |
| Nova | memory-safe indices, GC-correctness by lifetimes + lint | scoped list, realms, frames | yes | no / no | none | WeakRef/FinalizationRegistry in heap | `&mut Agent` for writes | "performance is acceptable, but it is not fast" (README) |
| boa_gc | safe API | ref − internal counts | no | no / no | none | `Finalize` trait | `GcRefCell` borrow flags | none published here |
| rust-gc | safe API | root counts flipped on move | no | no / no | none | `Finalize` | `GcCell` flags + root flag | ~3× slower than Alloy (Alloy §8.2) |
| dumpster | safe API | refcounts + dirty set | no | no | none | `Drop` | `RefCell` | none fetched (blog moved) |
| safe-gc | no unsafe | `Root<T>` | no | no | none | plain `Drop`, can't see the GC | `&mut Heap` | slow by design (README) |
| Alloy | needs rustc fork | conservative | no | no | none | Drop as finalizer + FSA/elision | none (`Copy` Gc) | 5% over suite; up to 3.35× from finalizers |
| MMTk | binding unsafe | binding enumerates | plan | yes / yes | plan barrier | binding hook | VM's | LXR barrier 1.6% (PLDI'22) |
| Wasmtime | indices into GC heap | Cranelift stack maps | copying: yes | no | DRC inline / none | host data table | `GcStore` calls | not found |

## 3. QuickJS's own model, and refcounting in Rust

### 3.1 What QuickJS-NG does today (source-observed, `quickjs.c @ 7a23d8e`)

- **The refcount and GC flags now sit in the allocator block header.** `JSMallocBlockHeader {
  block_idx, block_size_idx, gc_obj_type:7, mark:1, ref_count }` lies 8 bytes before every
  allocation and is reached by `js_rc(p)` (`:286-298`, `:463-509`). The object body keeps only the
  GC list link. This is the layout change that broke quickjs-jit's inline DUP/FREE (svelters note
  `quickjs-jit-cranelift.md:152-158`).
- **`JS_FreeValueRT` decrements, and at zero frees at once** (`:7195-7203`). An object at zero is
  moved to `gc_zero_ref_count_list`, which is drained iteratively (`free_zero_refcount`,
  `:7111-7126`). Iterative draining avoids recursion depth when long chains are freed.
- **The cycle collector runs three full passes over `gc_obj_list`** (`JS_RunGC`, `:7483-7493`):
  1. `gc_decref` subtracts internal edges (`:7373-7393`);
  2. `gc_scan` restores the counts of everything reachable from a nonzero count (`:7412-7430`);
  3. `gc_free_cycles` frees the rest (`:7432-7472`).

  It is not incremental, and its cost grows with the whole GC heap, live or dead.
- **The trigger is allocation-driven.** It fires when `malloc_size + size > malloc_gc_threshold`.
  The threshold starts at 256 KiB and is reset to 1.5× live after each GC (`:1679-1697`, `:2333`).
  So a pause can land in the middle of any allocation, in the middle of a frame.
- **FinalizationRegistry callbacks are queued as jobs** from `reset_weak_ref` when the target is
  freed (`:63362-63440`, `JS_EnqueueJob` at `:63435`). With refcounting that is often right at the
  last `JS_FreeValue`. Inside a cycle collection, a callback is skipped if its callback or held value
  belongs to the cycle.
- **The refcount design shapes code elsewhere:**
  - "delay instantiation to avoid creating cycles for every javascript function" (`:17695`);
  - "the field could be removed in the getter", hence `js_dup(func)` (`:9179`);
  - "if 'p' is a prototype, it can be freed in the called function", hence a dup/free pair around
    every exotic call (`:9224`, `:9236`).
- **The cost of the bookkeeping in source size:** 1,480 lines contain `JS_FreeValue`, 406
  `js_dup(`, 192 `JS_FreeAtom` and 1,607 `goto fail`/`goto exception` (grep).

**Measured GC pause** (QuickJS-NG `7a23d8e`, `qjs` Release build, `std.gc()` = `JS_RunGC`):
- Setup: a live graph of N plain objects with 2 cross edges each, kept alive by an array; the minimum
  of 5 runs. The machine is the shared 4-core cloud box at load ≈2.5, two runs (cloud, not a gate).

| live GC objects | full GC pause | per object | freeing the same graph as cycle garbage |
|---|---|---|---|
| 10k | 0.29–0.54 ms | 29–54 ns | 0.9–1.8 ms |
| 100k | 5.4–7.2 ms | 54–72 ns | 24–30 ms |
| 1M | 188–199 ms | 188–199 ns | 590–637 ms |

The script is `scratchpad/gcnote/gcpause.mjs`.

- **Inferred:** at 120 Hz a 1 ms GC pause allows only about 15–30k live GC objects under QuickJS's
  design. A Svelte app with a few thousand components and long reactive lists passes that.
  - The presto design does not budget GC yet. The js-host note says the host has "No
    `set_gc_threshold`; GC only via `collect_garbage` … plus QuickJS's own triggers"
    (`round2/js-host-local.md:67-68`).

### 3.2 What changes if hocus traces instead

| Behaviour | QuickJS refcount | hocus tracing | Does it matter? |
|---|---|---|---|
| When unreachable acyclic objects die | at once | at the next sweep | Memory peaks are higher (Alloy saw larger average heaps, §8.2). Mitigate with pacing; tune the sleep factor. |
| Class finalizers (host wrappers) | prompt, deterministic | at sweep, batched | svelters already treats FinalizationRegistry timing as unspecified and keeps explicit destroy paths (`QUICKJS-RUNTIME.md:260`). presto's wrappers clear a slot and queue the rest (`compiler-and-js.md:424-427`). Fine. |
| FinalizationRegistry | callback job often queued within the same task | queued after a cycle completes | Spec-conformant either way: liveness and cleanup scheduling are host-defined (documented, ECMA-262 WeakRef/FinalizationRegistry, `HostEnqueueFinalizationRegistryCleanupJob`). Tests that `await` cleanup need `gc()` hooks. |
| `WeakRef.deref` | the target lives exactly until its refcount reaches 0 | `KeepDuringJob` set as the spec says, cleared at the job end | simpler under tracing |
| Refcount traffic | inc/dec on every copy, 3% self + inlined | none; a barrier only on heap stores | a win |
| Error paths | every early return must free | nothing to free | most of the 1,607 cleanup gotos disappear (inferred) |
| Cycles (closures ↔ prototypes) | collector pause, lazy-prototype hacks | free | the lazy `prototype` stays as a memory optimisation, no longer a necessity |
| Pause | full heap, not incremental | incremental steps | the main reason (§4) |

### 3.3 Is "Rc in Rust plus a cycle collector" viable and safe?

**Viable, and safe at the API level.** dumpster and bacon-rajan-cc show it can be done
(documented). The cost for a JS engine is high, because it keeps both of QuickJS's costs: refcount
traffic and a cycle collector that has to pause. The problems:

1. **Traffic.** Every `Value` copy into a frame slot or a property is an increment, as QuickJS
   pays today. A JIT has to inline the inc/dec, as quickjs-jit did (`quickjs-jit-cranelift.md:78-86`).
2. **Interior mutability.** JS objects are aliased and mutated. With `Rc` that means
   `RefCell`/`Cell` everywhere (inferred):
   - borrow-flag checks;
   - panics when a `Drop`, a finalizer or a re-entrant JS call hits an object that is already
     borrowed.
3. **Drop recursion.** Long chains overflow the Rust stack unless freeing is a worklist, as QuickJS's
   is. That means a custom `Rc`, not `std::rc` (inferred from `free_zero_refcount`).
4. **A cycle collector needs to see the internal counts.** You end up writing your own `Rc` with
   `unsafe` inside, as dumpster does (100 blocks).
5. **Moving is impossible** while raw `&T` borrows come from `Rc` derefs (inferred).
6. **Threads:**
   - Atomic counts are expensive.
   - CPython's free-threading needed biased reference counting (`ob_tid`, a non-atomic
     `ob_ref_local` for the owning thread, an atomic `ob_ref_shared` for others), immortal objects
     (`ob_ref_local = UINT32_MAX`; inc/dec do nothing) and deferred counting for functions, code
     objects and modules, which are freed only by the GC.
   - It also needs two stop-the-world pauses per cycle collection, and a non-generational collector
     in the free-threaded build.
   - Measured single-thread cost: 6% (Skylake) and 5% (Zen 3); multi-thread: 8% and 7%
     (documented, PEP 703, pyperformance 1.0.6).

The research literature agrees with tracing: an existing RC implementation in a JVM was about 30%
slower than tracing on average, and gets to parity only with heavy optimisation, namely deferral,
coalescing and backup tracing (documented, Shahriyar, Blackburn & Frampton, "Down for the Count?",
ISMM 2012). LXR is where that line of work ends: RC plus SATB tracing, which is a GC project in its
own right.

## 4. GUI-loop requirements (presto)

### 4.1 Budgets (documented, `cadatomic/docs/research/2026-10-25-gui-toolkit/architecture.md @ 75b6a47`)

- **The targets (§1):**
  - click to photon at 120 Hz: p50 12–18 ms, p99 ≤ 30 ms;
  - a JS-driven change: at most +1 frame over a native one, p99 ≤ 33 ms;
  - one changed label: ≤ 0.5 ms of UI-thread CPU;
  - idle: **0 frames and 0 timer wakeups (counted)**;
  - under load, compositor animations keep display rate while JS runs for 100 ms.
- **Scheduling (§10.2):**
  - the UI frame starts at `S_ui = T − (latch + margin) − est_gpu − est_comp_cpu − 50 µs − est_ui`,
    where `est_ui` is the p90 of a 64-sample ring;
  - urgent frames run at once for discrete input after idle;
  - slow UI enters pipelined mode, capped at 2× the interval.
- **Failure handling (§17):** a JS long task is aborted by the interrupt watchdog at the budget.

**Derived pause bound (inferred):**
- A 120 Hz frame is 8.33 ms.
- **Any GC work inside a UI frame inflates `est_ui`** and moves `S_ui` earlier for every following
  frame. It also counts against the 0.5 ms "changed label" budget.
- **So a 120 Hz UI needs these bounds:**
  - **no GC pause above 1 ms**, about 12% of a frame;
  - **in-frame GC steps of ≤ 0.25 ms**, so a frame with an ordinary JS handler stays under the label
    budget;
  - the atomic phase (root scan and mark finish) under 1 ms.
- That is the same order as Riptide's "1 ms or less" goal. It is two orders below Hades's p99.9 of
  48 ms (below).
- **Most GC work should run in the UI thread's gap** between posting a commit and the next `S_ui`,
  which is up to several ms per frame on light frames.

### 4.2 How others schedule GC

- **V8 and Blink idle tasks.** Each idle task gets a deadline, the scheduler's estimate of the
  remaining idle time, up to "an upper bound of 50 ms" when nothing is animating.
  - V8 runs scavenges, incremental marking steps, full GCs, and full GCs with compaction in idle
    time.
  - Results: 43% of GC ran in idle time on a workstation (31% on a Nexus 6), and jank improved by
    about 7%. Gmail's heap is about 45% smaller when idle (documented, v8.dev "Getting garbage
    collection for free", 2015-08-07).
  - The design paper is Degenbaev et al., "Idle Time Garbage Collection Scheduling", PLDI 2016,
    shipped since Chrome 38 (documented, search summary of the abstract).
- **JSC Riptide** (documented, webkit.org, 2017-01-20):
  - concurrent, generational through sticky mark bits, non-moving;
  - a retreating-wavefront barrier, which reuses the existing generational barrier;
  - conservative stack scan;
  - 2 ms time slices with a minimum collector pause of 0.6 ms;
  - remaining pauses "tend to be 1 ms or less";
  - splay-latency 5× better.
- **Hermes Hades** (documented, `doc/Hades.md` on main, fetched 2026-10-25; reactnative.dev
  2021-10-26):
  - a young generation like GenGC's;
  - an old generation of segments with size-segregated free lists, marked and swept on one
    background thread;
  - an SATB barrier that pushes old values into a 128-entry buffer and takes a lock once per 128
    writes;
  - mark draining in 8 KiB chunks;
  - compaction of one segment per cycle, piggybacked on a young collection;
  - an *incremental* single-thread mode on 32-bit;
  - pauses: "48ms at p99.9 on 64-bit devices (34x faster than GenGC!)" and about 88 ms on 32-bit,
    against GenGC's 200 ms average and 1.4 s p99;
  - the cost: "more expensive write barriers, slower freelist based allocation … increased heap
    fragmentation".
- **Lua 5.4** (documented, manual §2.5):
  - incremental mode: pause 200, step multiplier 100, step size 13, meaning "steps of approximately
    8 Kbytes";
  - generational mode: minor multiplier 20, major multiplier 100;
  - finalizers run in reverse order of marking.

### 4.3 What hocus should do (inferred, from the above)

1. **Incremental mark with work-bounded steps,** on gc-arena's and Lua's debt model: each step
   processes about 8 KiB of mark work.
2. **A frame-aware driver API** for presto's UI loop (sketched in §7.5):
   - `gc_step(deadline)` after the commit is posted;
   - `gc_finish_before_idle(budget)` when the frame clock goes Idle, so idle stays at 0 wakeups and
     no GC timers are armed;
   - `gc_urgent_ok()` so urgent input frames skip in-frame steps.
3. **Allocation-debt steps inside long JS tasks,** so a 100 ms task still collects, plus a hard cap
   on step time.
4. **Lazy, incremental sweep.**
5. **A generational nursery only after measurement.** Svelte's short-lived allocations (closures,
   effect records, iterator results) may justify one later. The barrier funnel (§7.3) keeps that door
   open.
6. **Ephemeron (WeakMap) resolution in the atomic phase is a pause risk.** Bound it, or make it
   incremental. Hades also does it during a pause.

## 5. Sharing across threads and isolates

(Coordinates with the isolates note. This section covers only what the GC choice permits.)

| GC design | Per-thread heaps | Shared mutable heap | Read-only shared space |
|---|---|---|---|
| QuickJS refcount | yes: one `JSRuntime` per thread | no: counts are non-atomic | possible only with immortal objects (CPython's trick) |
| Rc + cycle collector in Rust | yes (`!Send` Rc) | needs `Arc`/biased RC (PEP 703: 5–8%) | immortal bit |
| gc-arena | yes: independent arenas, single-threaded only (documented) | no | `Gc::new_static`-style, per arena |
| Nova | one `Agent` per thread | no | no |
| hocus proposal | yes: one heap per isolate; `Local`/`Gc` are `!Send` | out of scope (needs a global safepoint or a concurrent GC; MMTk-class work) | yes: atoms, interned strings, bytecode and builtin shapes in an immutable shared region. References into it are never marked or freed and need no barrier |

What crosses isolates in the proposal:
- structured-clone byte batches, which are the ISO1–ISO3 op batches (`svelters/plans/research/ARCH-SECURITY.md`);
- `SharedArrayBuffer` storage, which is raw bytes outside any GC heap and holds no references;
- references into the read-only space.

Compressed references make the boundary checkable (inferred): an offset is only meaningful in its own
heap's base, so a reference leaked across isolates fails decode or bounds checks instead of
aliasing.

## 6. What a JIT later needs from the GC

From quickjs-jit (svelters note), Wasmtime/Cranelift and V8:

1. **Safepoints where every live reference sits in a known slot.**
   - quickjs-jit needs no stack maps: "all live values stay in frame slots that the cycle collector
     already scans" (`quickjs-jit-cranelift.md:78-86`).
   - hocus should keep the same invariant from the start: the interpreter's register file is a traced
     heap structure. Then a baseline JIT that writes values back to frame slots before every helper
     call needs no stack maps either.
   - An optimising tier that keeps references in registers needs Cranelift user stack maps, which
     spill at each safepoint, record the slots and reload after the call (documented).
2. **Barrier fast paths in generated code.** Keep the barrier test to one flag load and one branch:
   "is the collector marking (SATB), or is the target old and the value young (card)?" The slow path
   is a helper call (inferred). Wasmtime emits its DRC barriers inline, and its copying collector
   needs none (source-observed).
3. **Moving objects.** A JIT must not cache a raw address across a safepoint. With compressed
   references the JIT keeps the heap base in a pinned register, as V8 does with its root register
   (documented, v8.dev pointer compression).
4. **Inline allocation** by bump pointer in a thread-local buffer. That favours an Immix-style
   allocator over free lists later. Hades's free lists cost throughput (documented).
5. **Stable, exported layouts.** quickjs-jit pinned layouts with static asserts, and they still broke
   when NG 0.16 moved the refcount (svelters note). Wasmtime asserts its header offsets
   (`VM_GC_HEADER_*`, source-observed). hocus should export header and field offsets as constants
   from `hocus-gc` and assert them.
6. **No refcount inc/dec in JIT code at all.** That is one of the larger simplifications from
   dropping refcounts (inferred).

## 7. Proposal: the hocus object model and GC API

### 7.1 Crates and where the `unsafe` lives

- **`hocus-gc`**, the only crate with heap `unsafe`. It holds:
  - segment allocation (a reserved region; size-class blocks first, Immix lines later);
  - the object header;
  - compressed-reference decode;
  - `Gc<'n, T>` → `&'n T` deref;
  - the tracer and sweeper;
  - the handle stack;
  - weak and ephemeron processing;
  - the `unsafe trait Trace`, with a derive in `hocus-gc-derive`.

  Target: **30–60 `unsafe` blocks** (hypothesis, to be checked against the code). For calibration:
  gc-arena has 101, rust-gc 45 and boa_gc 89.
- **`hocus-vm`** (interpreter, builtins, objects, shapes): `#![forbid(unsafe_code)]`. If the dispatch
  loop needs unchecked indexing, that goes in a small `hocus-vm-fast` module, audited separately.
- **`hocus-jit`** (later): `unsafe` by nature; it consumes `hocus-gc`'s exported layout constants.

### 7.2 Types (sketch)

```rust
// hocus-gc: references and roots
/// 32-bit offset into this isolate's heap region (pointer compression); 0 = null.
#[derive(Copy, Clone, PartialEq, Eq)]
#[repr(transparent)]
struct RawRef(NonZeroU32);

/// Unrooted typed reference. Zero-cost, Copy. Valid only while `'n` (a shared borrow of the
/// context's NoGc token) lives; GC needs `&mut Cx`, so the borrow checker forbids GC meanwhile.
#[derive(Copy, Clone)]
pub struct Gc<'n, T: Trace> { raw: RawRef, _n: PhantomData<(&'n NoGc, *const T)> }

/// A JS value (NaN-boxed or tagged; the value-representation note decides). Heap variants carry
/// RawRef, so the value has the same `'n` brand.
#[derive(Copy, Clone)]
pub struct Value<'n>(u64, PhantomData<&'n NoGc>);

/// Rooted handle: a slot in the LIFO handle stack. Survives GC (the collector updates the slot if
/// it moves the object). `'s` is the handle scope's generative lifetime, not a borrow.
#[derive(Copy, Clone)]
pub struct Local<'s, T = JsValue> { slot: u32, _s: PhantomData<(*mut &'s (), T)> } // !Send

/// Long-lived root for host code (svelters/presto node wrappers): an entry in a root table,
/// removed on drop. Like gc-arena's DynamicRoot or V8's Global.
pub struct Global<T> { id: u32, _t: PhantomData<T> } // !Send

/// The tracing contract. Unsafe to implement by hand; derived for every heap type.
/// Heap types must not implement Drop (they can't touch other GC objects at sweep).
pub unsafe trait Trace { fn trace(&self, t: &mut Tracer); }

/// Heap fields. Cell semantics (copy in/out, never `&mut` into the heap) + the one barrier.
#[repr(transparent)]
pub struct Slot(Cell<u64>);
impl Slot {
    pub fn get<'n>(&self, _: &'n NoGc) -> Value<'n>;
    pub fn set<'n>(&self, owner: GcHeaderRef<'n>, v: Value<'n>, nogc: &'n NoGc); // barrier here
}
```

```rust
// hocus-vm: the context
pub struct Cx<'s> { agent: &'s mut Agent }   // Agent = heap + handle stack + frames + realm
impl<'s> Cx<'s> {
    pub fn nogc(&self) -> &NoGc;                                   // no GC while borrowed
    pub fn root<T>(&self, v: Gc<'_, T>) -> Local<'s, T>;           // &self: works inside nogc
    pub fn get<'n, T>(&'n self, l: Local<'s, T>) -> Gc<'n, T>;
    pub fn scope<R: Unrooted>(&mut self, f: impl for<'i> FnOnce(&mut Cx<'i>) -> R) -> R;
    pub fn escape<'i, T>(inner: Local<'i, T>, outer: &Cx<'s>) -> Local<'s, T>; // like V8's EscapableHandleScope
}
impl<'s, T> Local<'s, T> { pub fn set(self, cx: &Cx<'s>, v: Gc<'_, T>); } // reuse a slot in loops
```

**Calling convention:**
- *May GC* (it allocates, or can run JS): `fn f<'s>(cx: &mut Cx<'s>, a: Local<'s>, …) ->
  JsResult<'s, Local<'s>>`.
- *Never GC*: `fn g<'n>(nogc: &'n NoGc, a: Value<'n>, …) -> Value<'n>`.
- The interpreter's opcodes read and write **frame slots**, which are rooted by being in the frame.
  So the hot path creates no handles. `Local` traffic is confined to Rust builtins and to the slow
  paths.

**Prototype check (measured, rustc 1.97.0, `scratchpad/gcnote/proto/api.rs`, safe Rust with a Vec
heap, `#![forbid(unsafe_code)]`):**
- The `Cx`/`NoGc`/`Value<'n>`/`Local<'s>` shape compiles.
- Holding a `Value<'n>` across a call that may GC is rejected: `error[E0502]: cannot borrow *cx as
  mutable because it is also borrowed as immutable`.
- Returning a `Local` out of its handle scope is rejected: `error: lifetime may not live long enough`.
- Neither rule uses a transmute or a lint.

### 7.3 Heap layout and barrier

- **Every heap object starts with a header:** a type id, the mark bits and a size class (8 bytes), in
  the spirit of QuickJS-NG's merged header.
- **Fields are fixed-layout records of `Slot`s.** Variable-size storage (property arrays, array
  elements, closure var-ref arrays) is a separate GC `FixedArray` that is replaced on growth, as V8
  backing stores are. So nothing in the heap needs `RefCell` or borrow flags (inferred).
- **Host data:** a Rust-owned payload (`regex` program, `Map` hash index, ArrayBuffer bytes) sits in a
  side table indexed by id. Its cleanup is an explicit class hook run after sweep, not a `Drop` of the
  heap object (Alloy's lesson; gc-arena rule 2).
- **One barrier, inside `Slot::set`.** It covers:
  - the SATB or Dijkstra barrier while marking;
  - card marking when a nursery exists;
  - a no-op otherwise.

  The JIT emits the same check (§6.2).

### 7.4 The collector, phase 1

- **Non-moving incremental mark-sweep:**
  - tri-colour, with an SATB barrier (bounded mark work, no root re-scan);
  - gc-arena/Lua pacing by allocation debt;
  - lazy sweep;
  - size-class segregated blocks inside a reserved 4 GiB region (compressed references).
- **Roots:**
  - the frame stack, including suspended generator and async frames, which are heap objects;
  - the handle stack;
  - the `Global` table;
  - realm intrinsics;
  - the job queue.
- **Weak handling:**
  - WeakRef, WeakMap/WeakSet ephemerons and FinalizationRegistry are processed in the atomic phase;
  - `KeepDuringJob` is cleared at each job end;
  - cleanup jobs are queued to the host.
- **Phase 2, on evidence only:** a bump-allocated nursery with evacuation (handles and frame slots
  make it possible), Immix lines, or an MMTk plan behind the same object model.

### 7.5 Frame-driver API (for presto's UI thread)

```rust
impl Agent {
    pub fn gc_step(&mut self, deadline: Instant) -> GcProgress;  // after commit, before next S_ui
    pub fn gc_finish_before_idle(&mut self, budget: Duration);   // when the frame clock goes Idle
    pub fn set_in_frame_step_cap(&mut self, cap: Duration);       // default 0.25 ms
    pub fn gc_stats(&self) -> GcStats;                            // pause histogram for §16 harness
}
```

## 8. Ergonomics: three QuickJS functions ported line by line, against the proposal

### 8.1 Property get (`JS_GetPropertyInternal`, `quickjs.c:9110`)

QuickJS (abridged):
```c
for(;;) {
    prs = find_own_property(&pr, p, prop);
    if (prs) {
        if ((prs->flags & JS_PROP_TMASK) == JS_PROP_GETSET) {
            JSValue func = JS_MKPTR(JS_TAG_OBJECT, pr->u.getset.getter);
            /* Note: the field could be removed in the getter */
            func = js_dup(func);
            return JS_CallFree(ctx, func, this_obj, 0, NULL);
        } ...
        return js_dup(pr->u.value);
    }
    if (p->is_exotic) { ... obj1 = js_dup(JS_MKPTR(JS_TAG_OBJECT, p));  /* p can be freed */
                        retval = em->get_property(ctx, obj1, prop, this_obj);
                        JS_FreeValue(ctx, obj1); return retval; }
    p = p->shape->proto; if (!p) return JS_UNDEFINED;
}
```

hocus:
```rust
pub fn get_property<'s>(cx: &mut Cx<'s>, obj: Local<'s, Object>, key: Atom,
                        this: Local<'s>) -> JsResult<'s, Local<'s>> {
    let cur = obj;                                       // a rooted cursor; reused, not re-pushed
    loop {
        // NoGc region: plain reads, no rooting, no refcounts.
        let step = {
            let nogc = cx.nogc();
            let p = cx.get(cur);
            match p.find_own_property(nogc, key) {
                Some(Prop::Data(v)) => return Ok(cx.root(v)),
                Some(Prop::Getter(Some(f))) => Step::CallGetter(cx.root(f)),  // replaces js_dup
                Some(Prop::Getter(None)) => return Ok(cx.undefined()),
                None if p.is_exotic(nogc) => Step::Exotic(p.class(nogc)),
                None => match p.proto(nogc) {
                    Some(proto) => { cur.set(cx, proto); continue; }
                    None => return Ok(cx.undefined()),
                },
            }
        };                                              // NoGc borrow ends here
        return match step {
            Step::CallGetter(f) => call(cx, f.into(), this, &[]),             // may GC: fine
            Step::Exotic(cls) => (cls.get_property)(cx, cur, key, this),       // `cur` is rooted
        };
    }
}
```

What changes:
- **The dup/free pairs and their comments vanish.** The getter function and `p` are rooted
  `Local`s, so "the field could be removed in the getter" and "p can be freed" can't happen.
- The fast path is the same shape as the C, with no handle traffic until the value escapes.
- **The one new rule:** decide inside the `nogc` block, then call outside it. The compiler enforces
  it.
- The interpreter's `OP_get_field` would call a variant that writes into a frame slot rather than
  returning a `Local`.

### 8.2 `Array.prototype.push` (`js_array_push`, `quickjs.c:44173`)

QuickJS fast path:
```c
if (unlikely(new_len > p->u.array.u1.size)) {
    if (expand_fast_array(ctx, p, new_len)) return JS_EXCEPTION;
}
for(i = 0; i < argc; i++)
    p->u.array.u.values[array_len + i] = js_dup(argv[i]);
p->u.array.count = new_len;
p->prop[0].u.value = js_uint32(new_len);
return js_int32(new_len);
```

hocus:
```rust
fn array_push<'s>(cx: &mut Cx<'s>, this: Local<'s>, args: Args<'s>) -> JsResult<'s, Local<'s>> {
    let fast = { let nogc = cx.nogc();                        // check the guards; produce plain data
                 cx.get(this).as_fast_std_array(nogc).map(|a| (a.len(nogc), a.capacity(nogc))) };
    if let Some((len, cap)) = fast {
        let new_len = len.checked_add(args.len() as u32).filter(|n| *n <= i32::MAX as u32)
                         .ok_or_else(|| cx.range_error("Array too long"))?;
        if new_len > cap { grow_elements(cx, this.cast(), new_len)?; }    // may GC: allocates
        let nogc = cx.nogc();
        let a = cx.get(this).as_fast_std_array(nogc).unwrap();
        for (i, v) in args.iter(cx).enumerate() {
            a.elements(nogc).slot(len as usize + i).set(a.header(), v, nogc); // barrier, no dup
        }
        a.set_len(nogc, new_len);
        return Ok(cx.root(Value::int(new_len as i32)));
    }
    generic_push(cx, this, args)                               // ToObject / Set / length path
}
```

What changes:
- `js_dup(argv[i])` becomes a barriered `Slot::set`.
- The allocation (`grow_elements`) is a GC point, so the array is re-read after it. The C code
  re-uses `p`, which works only because QuickJS never moves objects. Re-reading costs a load (inferred).
- `args` lives in the caller's frame (`Args<'s>` borrows rooted frame slots), so the arguments are
  never rooted one by one.

### 8.3 Closure creation (`js_closure` / `js_closure2`, `quickjs.c:17579-17700`)

QuickJS:
```c
func_obj = JS_NewObjectClass(ctx, func_kind_to_class_id[b->func_kind]);
if (JS_IsException(func_obj)) { JS_FreeValue(ctx, bfunc); return JS_EXCEPTION; }
var_refs = js_mallocz(ctx, sizeof(var_refs[0]) * b->closure_var_count);
for (i = 0; i < b->closure_var_count; i++) {
    switch (cv->closure_type) {
    case JS_CLOSURE_LOCAL: var_ref = get_var_ref(ctx, sf, cv->var_idx, false); break;
    case JS_CLOSURE_REF:   var_ref = cur_var_refs[cv->var_idx]; JS_REF_COUNT(var_ref)++; break; }
    if (!var_ref) goto fail;
    var_refs[i] = var_ref;
}
...
fail: JS_FreeValue(ctx, func_obj); return JS_EXCEPTION;
```

hocus:
```rust
fn closure<'s>(cx: &mut Cx<'s>, frame: FrameRef<'s>, bc: Local<'s, Bytecode>)
              -> JsResult<'s, Local<'s, Function>> {
    let n = { let nogc = cx.nogc(); cx.get(bc).closure_vars(nogc).len() };
    let refs = FixedArray::new(cx, n)?;                        // may GC
    for i in 0..n {
        let cv = { let nogc = cx.nogc(); cx.get(bc).closure_vars(nogc)[i] }; // Copy descriptor
        let vr = match cv.kind {
            ClosureKind::Local | ClosureKind::Arg => frame.var_ref(cx, cv.var_idx)?,  // may allocate
            ClosureKind::Ref | ClosureKind::GlobalRef => frame.parent_var_ref(cx, cv.var_idx),
        };
        let nogc = cx.nogc();
        cx.get(refs).slot(i).set(cx.get(refs).header(), cx.get(vr).into(), nogc); // no ++
    }
    let class = { let nogc = cx.nogc(); cx.get(bc).kind(nogc).function_class() };
    let f = Function::new_bytecode(cx, class, bc, refs)?;     // may GC; bc, refs rooted
    define_name_length(cx, f, bc)?;
    if needs_prototype(cx, bc) { define_lazy_prototype(cx, f)?; } // memory optimisation only now
    Ok(f)
}
```

What changes:
- There is no `fail:` label and no manual `JS_REF_COUNT(var_ref)++`.
- An early `?` return simply leaves garbage for the GC.
- The ordering rule is the same as before: re-read after each allocation.
- **Closed-over variables:** `JSVarRef` keeps its design: an "open" var-ref points at a frame slot,
  and a "closed" one owns the value (QuickJS `:470-497`). Here `pvalue` becomes `enum { Open(frame,
  idx), Closed(Slot) }` instead of a raw `JSValue*`. That removes an interior raw pointer into the
  stack, which a safe port needs anyway (inferred).

### 8.4 Ergonomics verdict (inferred)

- **A line-by-line port of QuickJS's C is not the right unit.** Each function keeps its structure:
  fast path, guards, slow path. The dup/free/goto-fail scaffolding, about a third of the lines in
  these three functions, disappears.
- **One discipline is new:** decide in a `nogc` block, act (allocate or call) outside it, re-read
  after. The compiler checks it.
- **Against the alternatives:**
  - Nova's `bind`/`unbind` noise, plus lints;
  - gc-arena's stackless rewrite;
  - Rc's `RefCell` borrow panics;
  - and, since refcounts are gone, no leak-on-error-path class of bugs.
- **Remaining risk:** handle-stack growth in long builtin loops, the same issue V8 has. Use
  `Local::set` to reuse slots, and `cx.scope` per iteration.

## 9. Recommendation and trade-offs

**Build `hocus-gc`: a precise, incremental, non-moving mark-sweep in one audited crate**, with:
- compressed 32-bit references;
- frame-slot and handle-stack roots (`Local<'s>`);
- zero-cost unrooted `Gc<'n>`/`Value<'n>` checked by the borrow checker;
- one barrier funnel;
- a frame-aware step API for presto.

Design the object model so a nursery or an MMTk plan can come later.

| We gain | We pay |
|---|---|
| bounded pauses (target ≤ 1 ms, steps ≤ 0.25 ms) instead of 5–7 ms per 100k objects | writing and owning a collector (3–5k lines; ~30–60 unsafe blocks to audit) |
| no refcount traffic (3% self in `JS_FreeValue`, plus inlined dups); simpler error paths | non-deterministic finalization and higher memory peaks |
| a safe engine body (`forbid(unsafe_code)` outside `hocus-gc`) without Nova's transmute discipline | rooting arguments at GC-capable calls; a "decide, then act" style in builtins |
| a moving GC and an optimising JIT stay possible (precise roots, stack-map-ready) | no conservative stack scanning shortcut. Every root must be precise, and debugging missed roots needs a stress-GC mode from day one (as quickjs-jit had) |
| per-isolate heaps with a read-only shared space | no shared mutable heap across workers |

**What we don't recommend, and why:**
- **gc-arena as a dependency:** its rooting model forces a stackless rewrite of QuickJS.
- **Nova's heap:** ergonomics, 1,036 unsafe blocks, and stop-the-world compaction.
- **boa_gc, rust-gc and dumpster:** they keep the refcount traffic and the stop-the-world pauses.
- **Alloy:** it needs a rustc fork and scans conservatively.
- **MMTk now:** too heavy for small single-threaded UI heaps, and an unsafe binding. Keep it as a
  phase-2 option.

## 10. Open questions for the user (each with a recommendation)

1. **Accept non-deterministic finalization**, so that host wrappers and FinalizationRegistry run at
   sweep and not at the last reference?
   *Recommendation: yes.* svelters and presto already keep explicit destroy paths. The spec leaves the
   timing to the host.
2. **Pause target:** is "no single GC pause above 1 ms at 120 Hz, in-frame steps ≤ 0.25 ms, the rest in
   post-commit gaps" the right budget?
   *Recommendation: adopt it as the gate,* measured in presto's deterministic simulator and on a
   quiet machine.
3. **Cap per-isolate heaps at 4 GiB** (compressed references)?
   *Recommendation: yes for UI isolates.* Revisit for compute workers if they need more; they could
   use 64-bit references behind a feature.
4. **No shared mutable heap between workers** in the first design (structured clone + SAB + a
   read-only space only)?
   *Recommendation: yes.* A shared heap is a concurrent-GC project, and the isolates note should
   confirm no use case needs it.
5. **Own the collector rather than adopt MMTk?**
   *Recommendation: own a small one first.* Spike MMTk's StickyImmix or ConcurrentImmix only if
   throughput or pauses miss the target after the pause harness exists.

## 11. First steps in this repository

1. **Create `crates/hocus-gc`** with `RawRef`, `Gc<'n,T>`, `Local<'s,T>`, `Global<T>`, `NoGc`,
   `Slot`, `unsafe trait Trace` and the derive. Start from `scratchpad/gcnote/proto/api.rs`.
   - Write the compile-fail tests (trybuild) for the two misuse cases first: use-after-GC and a
     `Local` escaping its scope.
2. **Implement stop-the-world mark-sweep** over size-class blocks in a reserved region. Count the
   `unsafe` blocks (a CI ratchet), and set `#![forbid(unsafe_code)]` in every other crate.
3. **Add a stress mode:** collect at every allocation and every safepoint, on the model of
   quickjs-jit's `STRESS_GC` and QuickJS's `FORCE_GC_AT_MALLOC`. Make it a test-suite axis before
   any builtin is ported.
4. **Port the three functions in §8**, plus `OP_get_field` writing into frame slots, against a
   minimal object, shape and array model. Check the ergonomics claim on real code.
5. **Make it incremental:** SATB barrier in `Slot::set`, debt pacing, lazy sweep, `gc_step(deadline)`.
   Add a pause histogram and gate on it in a headless harness that replays `w-signals.js` and
   `w-proxy.js` (svelters' P5 microbaselines).
6. **Re-measure the QuickJS baseline** on a quiet machine, with heaps of 10k, 100k and 1M objects
   (`scratchpad/gcnote/gcpause.mjs`), so hocus has a before and after.

## Sources

- QuickJS-NG `7a23d8e`, `quickjs.c` lines cited inline.
- gc-arena `5ea967a`: `README.md`, `src/{collect,context,gc_ptr,metrics,lock}.rs`.
- piccolo `ce709eb` README; blog https://kyju.org/blog/piccolo-a-stackless-lua-interpreter/ (2024-05-01).
- Nova `877ef16`: `GARBAGE_COLLECTOR.md`, `nova_vm/src/heap/README.md`,
  `engine/bytecode/vm.rs:304`, `engine/context.rs:395-401`, `execution/agent.rs:952`, `nova_lint/src`.
- Boa `64c209f`: `core/gc/src/{lib.rs,internals/gc_header.rs}`.
- rust-gc `b7405c9`: `gc/src/{gc.rs,lib.rs}`.
- dumpster `6551a2c`: `README.md`, `dumpster/src/unsync/collect.rs`.
- shredder `266de5a` README. safe-gc `6a184a8`: `README.md`, `src/lib.rs`.
- shifgrethor `ab1873f` README. josephine `1f0563b` README.
- Hughes & Tratt, "Garbage Collection for Rust: The Finalizer Frontier", arXiv 2504.01841v4
  (2025-09-30), PACMPL OOPSLA2, DOI 10.1145/3763179.
- mmtk-core `ed89711`: `README.md`, `src/util/options.rs`, `src/vm/{scanning,collection}.rs`.
- mmtk-v8 `7009cfb` README. Julia MMTk docs: https://docs.julialang.org/en/v1.14-dev/devdocs/gc-mmtk/.
- Zhao, Blackburn & McKinley, "Low-Latency, High-Throughput Garbage Collection", PLDI 2022,
  arXiv 2210.17175.
- Shahriyar, Blackburn & Frampton, "Down for the Count?", ISMM 2012 (abstract via ANU repository).
- wasmtime `ffff2e2`: `crates/wasmtime/src/runtime/vm/gc/{gc_ref.rs,enabled/drc.rs,enabled/copying.rs}`,
  `crates/cranelift/src/func_environ/gc/{drc,copying}.rs`, `cranelift/codegen/src/ir/user_stack_maps.rs`.
- V8: https://v8.dev/blog/free-garbage-collection (2015-08-07), https://v8.dev/blog/pointer-compression
  (2020-03-30); Degenbaev et al., PLDI 2016.
- WebKit Riptide: https://webkit.org/blog/7122/ (2017-01-20).
- Hermes: `doc/Hades.md` (facebook/hermes main, fetched 2026-10-25);
  https://reactnative.dev/blog/2021/10/26/toward-hermes-being-the-default.
- Lua 5.4 manual §2.5: https://www.lua.org/manual/5.4/manual.html.
- PEP 703: https://peps.python.org/pep-0703/.
- Local context:
  - svelters `0aa41697`: `docs/notes/quickjs-jit-cranelift.md`, `plans/research/QUICKJS-RUNTIME.md:260`;
  - cadatomic `75b6a47`: `docs/research/2026-10-25-gui-toolkit/{architecture.md §1,§10,§17; compiler-and-js.md:424-427}`
    and `docs/research/notes/2026-10-25-own-gui-toolkit/round2/js-host-local.md:67-68`.
- Measurements (cloud, not a gate):
  - `scratchpad/gcnote/build/qjs -m scratchpad/gcnote/gcpause.mjs` (QuickJS-NG `7a23d8e`, CMake
    Release, 4-core shared container, load ≈2.5);
  - `rustc --edition 2024 [--cfg fail_*] scratchpad/gcnote/proto/api.rs` (rustc 1.97.0).
  - Scratchpad = `/tmp/claude-0/-home-user/db6d0663-2392-5b80-95a4-80368735124c/scratchpad`.
