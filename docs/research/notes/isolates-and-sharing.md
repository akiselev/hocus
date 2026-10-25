# Isolates, workers and sharing between them

Researcher note for the hocus design study (brief: `docs/research/BRIEF.md`, 2026-10-25). Topic: how
engines run several heaps and threads, what can be shared between them, and what hocus should
build so that workers are "fully integrated" without slowing the UI thread.

Sources read, with their shas: QuickJS-NG `7a23d8e` (scratchpad clone); Bellard QuickJS `7d1c13c`;
tc39/proposal-structs `8fca66e` (2024-12-17); tc39/proposals `07002d4` (2026-10-14); V8 `main`
`5f114fe1` (2026-10-25: selected files from the GitHub mirror, and a commit-only clone since
2024-06); Firefox `main` `efd2d1e6` (SpiderMonkey files); WebKit `main` `3f000ecf` (`JSLock.h`);
graaljs `dd265d5`; hermes `393a539`; svelters `0aa41697`; cadatomic `75b6a47`. Measurements were
run in this cloud container (4 cores, shared with seven other researchers, load average 1.3–2.5):
**cloud, not a gate.**

## 1. Summary and recommendation

**Findings**

1. **Every production JS engine is share-nothing between threads for ordinary JS objects.** One
   heap per isolate (V8), per VM (JSC), per runtime (SpiderMonkey, QuickJS, Hermes), per context
   (GraalJS). Workers talk by structured clone, transfer and `SharedArrayBuffer` (§2). What engines
   do share between heaps is **immutable data**: V8's read-only space and shared string table,
   SpiderMonkey's permanent atoms, well-known symbols and self-hosted stencil, GraalJS's code
   cache on a shared `Engine`, Hermes's bytecode buffer (§2.2). `documented` / `source-observed`.
2. **The JS Shared Structs proposal is stalled, and its only implementation is being removed.**
   It has been at Stage 2 since the October 2024 TC39 meeting, with no meeting since
   (tc39/proposals `07002d4`). V8 made `--harmony-struct` and `--shared-string-table` read-only
   (off) on 2026-10-20 (`1a037b51`), and removed `Atomics.Mutex` and `Atomics.Condition` on
   2026-10-22 (`ba3b6eee`), "as part of removing --harmony-struct". V8 keeps the shared heap itself
   for Wasm shared-everything threads (`[wasm][shared]` commits through 2026-10-23).
   `source-observed`. Why it was removed is not public (bug 572988458: not found).
3. **Full shared mutable heaps cost the single-threaded path.**
   - CPython's free-threaded build, even with biased reference counting and immortal objects, is
     1% (macOS aarch64) to 8% (x86-64 Linux) slower on pyperformance (Python 3.15 docs).
   - OCaml 5's parallel minor GC is 3.5% slower than stock OCaml on sequential code (ICFP 2020),
     and OCaml has no hidden classes to transition.
   - JSC's 2017 concurrent-JS design predicted shape transitions about 7× costlier and accesses to
     non-thread-local objects about 2×. It was never shipped (not found).
   `documented`.
4. **QuickJS-NG today (measured, cloud, not a gate):**
   - **Isolate cost:** one runtime plus context costs about 120 µs and 107 KB.
   - **Clone cost:** its object serializer clones plain records at about 640–880 ns each.
   - **Load cost:** loading an 874 KB bundle (the svelte@5.57.2 compiler) into a fresh runtime
     costs about 100–128 ms from source, or about 8 ms and 4.6 MB from bytecode. Every worker pays
     this again, because nothing is shared between runtimes (§3).
5. **svelters already has most of the sharing vocabulary on the Rust side**:
   - `Shared<[T]>` is an `Arc` view;
   - `Buffer<T>` is a move;
   - `Store<S>` is `Send + Sync` state that any number of hosts observe and any thread writes,
     coalesced into each host's checkpoint;
   - the export schema is process-global.

   What is missing is engine-side sharing of code and atoms, plus a cross-isolate identity rule
   for host objects (§5). `source-observed`.

**Recommendation.** Build "fully integrated workers" from three layers that keep the UI thread's
hot path free of atomics and keep process isolation (SEC3) possible:

| Layer | What | v1? |
|---|---|---|
| **(a) Share-nothing isolates** | one heap, one GC, one principal per runtime; a fast typed clone plus transfer, `postMessage` compatible | **v1** |
| **(b) A shared immutable image** | frozen atoms, function templates (bytecode), module records, constants and builtin-name tables in one `Arc<SharedImage>` per isolate group, immortal (no refcount traffic), mmap-able for process isolates | **v1: the template/atom split in the core types; the image itself in v1.5** |
| **(c) Rust-owned shareable host objects** | `Send + Sync` Rust objects (stores, `Shared<[T]>`, tables, services) reachable from every isolate; one `Arc` count per isolate wrapper, so atomics happen only at the boundary | **v1** (svelters already has them; hocus gives them identity across isolates) |
| (e) Ownership transfer | only for classes with detach semantics (ArrayBuffer, `Buffer<T>`, MessagePort) | v1 for those; general subgraphs: no |
| (d) A shared JS heap (shared structs) | a concurrent-safe shared space, global safepoints, shared write barriers | **no**; revisit only if TC39 reaches 2.7 and an engine ships |

Svelte reactivity across isolates should go through **(c) stores**, not shared `$state`. A worker
writes a Rust `Store`, and the UI isolate's effects see it at its next checkpoint. This is
asynchronous, glitch-free within each isolate, and identical in thread and process placement (§6).

## 2. How engines do multiple heaps and threads

### 2.1 Units of isolation

| Engine | Unit with its own heap and GC | Threads | Cross-unit objects | Grade, source |
|---|---|---|---|---|
| **V8** | `Isolate` (heap, GC, one thread at a time; `Locker` for hand-off). Isolates are grouped into `IsolateGroup`s, each with its own sandbox and pointer-compression cage. Per group: "total heap usage of isolates in a group cannot exceed 4 GB", and "it's not going to be possible to pass shared JS objects across IsolateGroup boundary" | one mutator per isolate | read-only space shared by isolates; shared string table "across all isolates in a process (enabled by default when the V8 Sandbox or shared isolates are used)", with locked access; shared space/heap with client isolates (experimental) | `documented`: `include/v8-isolate.h:208-231`, `docs/objects/strings.md:85-86`, `src/heap/read-only-heap.h` @ `5f114fe1` |
| **JSC** | `VM` (heap); `JSLock` makes a VM usable from several threads, one at a time ("lock before doing anything that allocates a JavaScript data structure") | one at a time per VM | none; Bun/WebKit workers are separate VMs | `source-observed`: `Source/JavaScriptCore/runtime/JSLock.h:33-51` @ `3f000ecf` |
| **SpiderMonkey** | `JSRuntime` with one `JSContext` per thread; a worker runtime has a `parentRuntime` "guaranteed to outlive this one and whose permanent data may be used by this one" | one per runtime | permanent atoms, static strings, well-known symbols and the self-hosted stencil are shared with the parent; `SharedImmutableStringsCache` shares script source text between threads behind one lock | `source-observed`: `js/src/vm/Runtime.h:316-322, 474-477, 873-898`; `SharedImmutableStringsCache.h:26-35` @ `efd2d1e6` |
| **QuickJS / NG** | `JSRuntime` (atoms, classes, shapes, GC list, job queue, SAB hooks, interrupt); several `JSContext` realms share one runtime and pass objects freely | one thread per runtime | none. `os.Worker` (quickjs-libc) starts a thread with a new runtime and context, and messages are `JS_WriteObject2(..., JS_WRITE_OBJ_SAB \| JS_WRITE_OBJ_REFERENCE)` bytes. SABs are `malloc`ed with an atomic refcount header. `postMessage` takes one argument: no transfer list | `source-observed`: `quickjs.c:337-428`; `quickjs-libc.c:3950-3994, 4080-4136, 4248-4300, 4370` @ `7a23d8e` |
| **Hermes** | `Runtime`; a JSI-based `Worker` extension runs each worker "on its own thread with its own runtime"; bytecode handed to a worker "is referenced (not copied) by `BCProviderFromBuffer` for the life of the worker runtime" | one per runtime | the bytecode buffer (read-only) | `documented`: `doc/superpowers/specs/2026-07-28-worker-arraybuffer-input-design.md` @ `393a539` |
| **GraalJS** | `Context`; "share-nothing": "Concurrent access to JavaScript objects is not allowed", but "Concurrent access to Java objects is allowed" | contexts used by one thread at a time | host (Java) objects shared freely; parsed and compiled code shared between contexts of one explicit `Engine` ("never any code sharing happening between two separate engine instances") | `documented`: `docs/user/Multithreading.md:16-21` @ `dd265d5`; graalvm.org embed-languages (GraalVM 25.4 docs, 2026-10-25) |
| **Deno** | one V8 isolate per `Worker` ("each instance of `Worker` is run on a separate thread"); `structuredClone`, `postMessage` with transfer (ArrayBuffer, MessagePort, streams) | — | as V8 | `documented`: docs.deno.com web platform APIs (read 2026-10-25) |
| **Bun** | a worker is "a new JavaScript instance running on a separate thread while sharing I/O resources". Fast paths skip structured clone for pure strings and for flat objects of primitives. Bun's own numbers against Node 24.6: 648 ns vs 1.19 µs for an 11-character string, 1.26 µs vs 304 µs for 3 MB | — | as JSC (the 3 MB number implies the string payload is not copied; how is not stated) | `documented`: bun.com/docs/runtime/workers (read 2026-10-25) |

**The pattern.** GraalJS states it most clearly, and it is the model this note recommends: JS
objects belong to one context, while host objects (Java there, Rust here) may be shared and
synchronized by the host. `inferred`.

### 2.2 What engines share between heaps

| Shared thing | Engine | How it stays safe | Grade, source |
|---|---|---|---|
| Read-only roots (oddballs, root maps, builtin strings) | V8 | one read-only space, written once at setup, then immutable; `ReadOnlyHeap::Contains(addr)` tests membership | `source-observed`: `read-only-heap.h` @ `5f114fe1` |
| Internalized strings | V8 | one process-wide shared string table, locked; a "shared" bit on strings (`strings.md:70`). V8 deprecated the `--shared-string-table` flag on 2026-10-20 (`1a037b51`), so this path is also in flux | `source-observed` |
| Permanent atoms, static strings, well-known symbols | SpiderMonkey | frozen at runtime init, "can be freely accessed with no locking necessary" (`Runtime.h:877`) | `source-observed` |
| Compiled code templates (stencil) | SpiderMonkey | a `JS::Stencil` "is not associated with any particular Realm and may be generated off-main-thread", and may be "instantiated into any Realm … and used multiple times" (`JSStencil.h:8-13, 144-146`); the self-hosted stencil is used directly by worker runtimes (`Runtime.h:477`) | `source-observed` |
| Code cache | GraalVM | explicit shared `Engine`; contexts per thread | `documented` |
| Bytecode file | Hermes | read-only buffer referenced by each runtime | `documented` |
| Off-heap binaries > 64 bytes | Erlang/OTP 29 | refcounted, "stored outside all process heaps", "referenced by any number of ProcBins from any number of processes" | `documented`: erlang.org binary handling (OTP 29.1.2) |
| Global immutable terms (`persistent_term`) | Erlang | reads are lock-free and do not copy. An update or erase of a complex term makes every process scan its heap, and processes still holding the term copy it | `documented`: erlang.org persistent_term (OTP 29.1.2) |
| Immortal objects shared between subinterpreters | CPython 3.12+ | refcount pinned at `_Py_IMMORTAL_REFCNT`, so incref and decref are no-ops. "Without a shared GIL, two running interpreters could not safely share any objects" otherwise. A naive implementation cost 2–4% | `documented`: PEP 683 |

**What survives across this table.** Every shared thing is immutable for as long as it is shared,
and its lifetime is decoupled from per-object counts: frozen at init, an epoch, or one count per
heap. The few counted cases (Erlang binaries, SAB headers) count per heap reference, not per JS
reference. `inferred`.

### 2.3 Shared mutable objects: the attempts

| Attempt | Design | Outcome | Grade, source |
|---|---|---|---|
| **V8 shared structs** (Chromium dev trial since M108, flag `JavaScriptExperimentalSharedMemory`) | `SharedStructType`, `SharedArray`, `Atomics.Mutex/Condition` in a shared space of the isolate group; client isolates; global safepoints; shared-heap write barriers; atomic field stores ("Use atomic memcpy with incremental marking in shared heap", `5e30b8e4`, 2026-06-15) | `lockAsync`/`waitAsync` removed 2025-11-11 (`5ce45006`). The `--harmony-struct` flag was made read-only (off) and its tests skipped 2026-10-15, reverted, then relanded 2026-10-20 (`1a037b51`). Mutex and Condition removed 2026-10-22 (`ba3b6eee`). The shared heap stays for Wasm (`--wasm-shared` implies `--shared-heap`, `flag-definitions.h:1841`) | `source-observed` @ `5f114fe1` |
| **JSC "Concurrent JavaScript: It can work!"** (Pizlo, 2017-08-30) | whole heap shared; segmented butterflies; transition-thread-locality inference in spare pointer bits | Predicted cost: "at most one arithmetic instruction" on most accesses; transitions "about 7x costlier"; non-TTL accesses about 2×; per-object locking about 22 cycles per operation, "too expensive". Unshipped: the post says it is "not yet possible to evaluate", and no later implementation was found | `documented`: webkit.org/blog/7846 |
| **CPython free-threading** (PEP 703; supported build since 3.13) | biased refcounting: the owning thread (`ob_tid`) uses a non-atomic local count, other threads an atomic shared count; immortal and deferred-refcount objects; mimalloc | 1–8% single-thread overhead on pyperformance (3.15 docs); PEP 703 measured 5–6% for one thread | `documented`: PEP 703; docs.python.org/3/howto/free-threading-python.html (3.15.1) |
| **OCaml 5 domains** | per-domain minor heaps, a shared non-moving major heap with mostly-concurrent mark and sweep, a stop-the-world parallel minor GC | ParMinor 3.5% and ConcMinor 4.9% slower than stock on sequential code. The stop-the-world minor collector "outperforms the concurrent minor collector in almost all circumstances" and keeps the C API (no read barrier) | `documented`: Sivaramakrishnan et al., ICFP 2020, arXiv 2004.11663v3 §1.3, §6 |

## 3. QuickJS-NG specifics a port inherits

All `source-observed` at `7a23d8e` unless marked.

- **Per-runtime state that blocks sharing.**
  - `JSRuntime` owns the atom hash and array (`quickjs.c:343-349`), the class array (`:353`), the
    shape hash (`:417-421`), the GC object lists (`:358-361`), the SAB functions (`:412`) and
    `can_block` (`:414`).
  - A `JSAtom` is an index into that runtime's `atom_array`.
  - Atoms below `JS_ATOM_END` (240 predefined, `quickjs-atom.h`) are constants that skip
    refcounting (`__JS_AtomIsConst`, `:3154`). All others are refcounted with a plain `++`
    (`JS_DupAtomRT`, `:3379-3388`).
- **Bytecode is bound to a realm.**
  - `JSFunctionBytecode` carries `JSContext *realm` (`:882`), atom operands from the runtime's
    table, and a `cpool` of `JSValue`s (`:883`).
  - The serializer re-interns atoms on read, which is why loading bytecode into a new runtime
    costs milliseconds (below).
- **Shapes hold the prototype.** `JSShape` has `JSObject *proto` (`:1121`) and lives in the GC
  heap, so a shape cannot be shared between heaps as is. A shareable layout would have to split
  "keys and flags" from "proto".
- **Refcounts are non-atomic** and live in the allocator block header (comment at `:1126-1127`).
  Nothing in the core is thread-safe except Atomics' global waiter mutex (`:62698-62799`).
- **`os.Worker`** (`quickjs-libc.c`):
  - **Startup:** builds a fresh runtime and context per thread, calls
    `JS_SetCanBlock(rt, true)` for the worker, and loads the module from its file again
    (`:4080-4136`).
  - **Messages:** `JS_WriteObject2` output plus a table of SAB pointers whose counts are bumped
    atomically (`:4263-4296`); the receiver runs `JS_ReadObject` and calls `onmessage`
    (`:2698-2745`).
  - **Missing:** no transfer list and no `structuredClone` (neither found in `quickjs.c`, `quickjs.h`
    or `quickjs-libc.c`).

**Measurements** (cloud, not a gate; QuickJS-NG `7a23d8e`, `gcc -O2`, programs in this
session's scratchpad `bench-iso/bench.c` and `load.c`, two runs each):

| Measured | Result |
|---|---|
| `JS_NewRuntime` + `JS_NewContext` + free, 2,000× | 118–124 µs each |
| an extra context in an existing runtime, create + free | 94 µs (so intrinsics setup, not the runtime, is most of the cost) |
| empty runtime / runtime plus one context | 29 KB, 241 atoms / 107 KB, 589 atoms (40 KB), 106 shapes, 187 objects |
| `JS_WriteObject` + `JS_ReadObject` (`OBJ_REFERENCE`) of N records `{id, x, y, label, on}` | N=10: 3.2 + 5.6 µs; N=1,000: 157 + 613 µs; N=10,000: 2.1 + 6.0 ms. 640–880 ns per record; reading is 3–4× writing |
| compile the svelte@5.57.2 compiler bundle (874,217 bytes source) | 100–128 ms; 8,805 atoms, 3,229 functions, 540 KB of bytecode, 9.4 MB runtime malloc |
| read its bytecode (3.18 MB with debug info) into a fresh runtime | 7.7–8.5 ms, 4.6 MB malloc per runtime |

What follows (`inferred`):
- **Share-nothing workers that reuse the bytecode cache** pay about 8 ms and 4.6 MB per worker for
  a bundle this size, and the full 100+ ms without the cache. The Svelte client runtime plus a
  first-party bundle is a fraction of the compiler's size; how big a fraction is not measured.
- **With a shared image (§4 b), the per-worker cost falls to runtime plus realm setup** (about
  120 µs today) plus closure creation for the modules a worker evaluates.
- **Context setup itself could be shared later:** intrinsic objects copied from a frozen realm
  snapshot, as V8 does with its startup snapshot. 10–30 µs is a `hypothesis`.

## 4. Design options for hocus

"Fully integrated workers" is read here as six properties (`inferred` from the brief and from
svelters' isolate plans):
1. a worker starts cheaply with the same module graph and the same Svelte and svelters runtimes,
   with no reparse;
2. it sees the same Rust exports and types;
3. values cross cheaply, and big data crosses without a copy;
4. Rust objects keep their identity across isolates;
5. reactive state crosses isolates;
6. the same code runs in thread or process placement.

### 4.1 The options

| | (a) Share-nothing + fast clone | (b) Shared immutable image | (c) Rust-owned shareable host objects | (d) Shared JS heap (shared structs) | (e) Ownership transfer of subgraphs |
|---|---|---|---|---|---|
| What crosses | copies (typed clone), transferred buffers, SABs | nothing at run time: code, atoms, layouts and constants are shared by construction | references to `Send + Sync` Rust objects | references to shared JS objects | whole object graphs, by move |
| Prior art | every engine (§2.1), structured clone (HTML) | V8 read-only space, SM permanent atoms and stencils, Graal `Engine`, Hermes bytecode, CPython immortals | GraalJS Java objects, svelters `Shared`/`Store`, Erlang refc binaries | V8 shared structs (being removed), JSC 2017 (unshipped), CPython 3.13+, OCaml 5 | ArrayBuffer transfer, Swift `sending` (SE-0430, Swift 6.0), Rust `Send` moves |
| GC needs | none beyond per-heap GC; a serializer that allocates with precomputed shapes | the GC skips image objects (address-range or page bit, as `ReadOnlyHeap::Contains`); no barrier, since the image never points into a heap; the image lives on an `Arc` per isolate | wrapper finalizers drop one `Arc` count; host objects must not hold JS values strongly (cycles across isolates are uncollectable), only isolate-tagged handles traced by the owning isolate | thread-safe allocation in a shared space; global safepoints (V8 moved them to `IsolateGroup`, `3197b8a8`, 2026-07-01); a write barrier enforcing shared→shared only; tear-free field stores; thread-safe string transitions | a closed subgraph (no edges out except shareables) plus proof that the sender lost every reference: a type system (Swift regions) or a detach that neuters the sender |
| Object-model needs | none | split realm-free templates from closures; split layouts from prototypes; a two-range atom space; per-runtime feedback and constant materialisation | a per-isolate wrapper cache keyed by `Arc` pointer (so `===` holds inside an isolate) | sealed fixed-layout objects; realm-local prototypes plus a registry keyed by source location (proposal ATTACHING-BEHAVIOR) | detach semantics per class; JS cannot neuter arbitrary objects (frozen objects, proxies, closures) |
| Cost on the UI thread's fast path | none | none if image membership is a tag bit the code already tests (QuickJS already branches on `__JS_AtomIsConst`) | none: JS-side dup and free touch the wrapper, which is local; the `Arc` count moves only at wrapper creation, finalisation and send | yes: barrier checks on every store, shared-GC pauses coupled across isolates, atomic stores on shared objects; 1–8% in CPython, more with shape transitions (JSC estimate) | none for detachable classes; a general version needs either the clone cost or region tracking |
| Process placement (SEC3) | yes: bytes | yes: a host-produced, position-independent image file mapped read-only (as Hermes and V8 snapshots) | partly: an `Arc` cannot cross a process. The same object must be reachable as a capability id with asynchronous or mirrored access (ISO1's `get_prop` mirror) | **no** | buffers only, as copies or shared-memory segments with a copy fallback (ARCH-SECURITY §5, ISO1) |
| One principal per runtime (SEC-O7) | kept | kept: the image is read-only code, and an extension's own modules go in its own layer | kept if host objects are reached only through grants (`Cap::Export`) and per-principal handle spaces (R-C) | broken unless the shared heap is per principal | kept |
| Gives integrated workers? | properties 3, 6 | properties 1, 2 (with svelters' process-global schema) | properties 3, 4, 5 | 3, 4 (JS-level), not 6 | 3 (big buffers) |

### 4.2 Verdicts

- **(a) is the base, and nothing replaces it.** It is also the only option that works unchanged
  for process isolates and for engine-exploit containment (ARCH-SECURITY §1.4).
- **(b) is where the startup win is**:
  - **Measured:** 8 ms and 4.6 MB per worker for an 874 KB bundle with today's bytecode cache;
    100+ ms without it.
  - **Fast-path cost:** none, if the core types are split correctly from the start.
  - **Precedent:** what SpiderMonkey does for workers today.
  - **The hard part is atoms (§5.2).**
- **(c) is where "integration" comes from.** The Rust side owns anything that must be shared and
  mutable, with Rust's `Send + Sync` as the type check, as GraalJS does with Java objects.
  svelters already ships the pieces (§6).
- **(d) is not worth it for hocus:**
  - **Cost:** it slows the UI isolate (barriers, coupled GC pauses), and it rules out process
    placement.
  - **No proven design:** the only JS implementation of the semantics is being removed (V8,
    October 2026).
  - **Blocking:** the proposal's synchronous `Atomics.Mutex.lock` cannot be used on a UI thread
    that must not block ("the main thread cannot block", `CHROMIUM-DEV-TRIAL.md:54-55`), and V8
    dropped the async variants in 2025.
- **(e) applies only to classes with detach semantics.** These are ArrayBuffer and typed-array
  buffers, `Buffer<T>` (svelters' move), MessagePort, and later a Rust-side `Table`. A general
  "move this object graph" needs the clone anyway, or a region type system JS does not have
  (`inferred` from SE-0430's whole-region rule).

### 4.3 The Shared Structs proposal, in detail

`documented` from proposal-structs `8fca66e` unless marked.

- **Status.**
  - Stage 2: README line 3, and tc39/proposals `07002d4` lists it under Stage 2 with notes from
    2023-03 to 2024-10.
  - The Oct 2024 meeting item is titled "Structs and shared structs for stage 2". No later
    meeting is listed.
- **Semantics.**
  - **Structs:** sealed, fixed layout, one-shot initialisation, non-generic methods.
  - **Shared structs** additionally:
    - have a `null` prototype and "cannot contain instance methods";
    - may hold only primitives and other shared structs;
    - go to other agents without copying.
  - **Design principle 2:** "There are no references from shared objects to non-shared objects"
    (README:27).
  - **Memory model:** field accesses are unordered but never tear; `Atomics.load/store/
    exchange/compareExchange` gain struct-field overloads.
  - **`SharedArray`:** a fixed-length shared struct.
  - **`Atomics.Mutex`:** non-recursive, unlocked through `UnlockToken` (`using`).
  - **`Atomics.Condition`:** `wait`, `waitFor`, `notify`.
- **Known issues.**
  1. **No methods.** "Functions are deeply unshareable" (README:368). The fix under discussion has
     two parts:
     - realm-local prototypes;
     - an agent-cluster-wide registry keyed by **source location**, which makes duplicating a
       declaration non-semantics-preserving for bundlers (ATTACHING-BEHAVIOR:75, 187).
  2. **No progress since 2024**, and **V8 is removing its implementation** (§2.3).
  3. **Blocking locks on a UI thread** are disallowed on the web, and the async lock was removed.
  4. **Shared strings** need thread-safe string transitions; V8 deprecated its shared string table
     flag in the same change.
  5. **Synchronisation:** data races are allowed by design; the proposal is "an escape hatch for
     expert programmers".
- **For hocus:**
  - Do not implement the proposal's syntax or semantics.
  - Rust-defined shared types (§5.4) give Svelte code the same capability with Rust's checks.
  - If TC39 moves the proposal to 2.7 and an engine ships it, hocus can map `shared struct` onto
    the same Rust shared-object machinery later (`hypothesis`).

### 4.4 SharedArrayBuffer, Atomics, structured clone

- **QuickJS-NG has SAB and Atomics**, with host-pluggable SAB allocation
  (`JS_SetSharedArrayBufferFunctions`, `quickjs.h:1142`) and `JS_SetCanBlock` (`:1195`).
  - `os.Worker` sets `can_block` only on workers (`quickjs-libc.c:4118`).
  - The UI runtime must keep `can_block = false`, so `Atomics.wait` throws there, as on the web
    main thread (`inferred`).
- **svelters refuses SABs** crossing into Rust (`crates/svelters-host/src/quickjs/buffers.rs:314`),
  and ARCH-SECURITY keeps SABs out of isolates for side-channel reasons (§1.3).
  - Recommendation: SAB for first-party workers only, behind a grant for extension principals.
  - Keep SAB out of every API svelters exposes: `Shared<[T]>` and `Buffer<T>` already cover
    zero-copy without data races (`exports/buffers.rs:1-17`).
- **Structured clone semantics** are the HTML spec's StructuredSerializeWithTransfer (not re-read
  here).
  - hocus should implement it natively, with the transfer list QuickJS-NG lacks, so
    `postMessage`/`structuredClone` are web-compatible.
  - Bun's fast paths show what is achievable: pure strings and flat primitive objects skip the
    general algorithm.
  - A typed Rust-side clone that writes slots by precomputed shape should beat the 640–880 ns per
    record measured above. 100–200 ns is a `hypothesis` to measure.

## 5. Proposal for hocus

### 5.1 Cross-isolate value kinds

Every JS value is in exactly one class. The class is decided by the value's kind (and, for host
objects, by a Rust trait), never by a runtime analysis of reachability.

| Class | Values | Crossing | Notes |
|---|---|---|---|
| **Immediate** | `undefined`, `null`, booleans, numbers, small ints | copy | free |
| **Immutable payload** | strings, BigInts | copy, or share the payload | strings over a threshold get an `Arc`'d payload, shared by reference (Erlang's 64-byte rule, Bun's 3 MB result). The local string header stays per heap; the atomic count moves only at send and finalisation |
| **Image** | atoms, function templates, module records, frozen constants, well-known symbols, builtin layouts | nothing to do: the same id or pointer in every isolate of the group | immortal while the group's `Arc<SharedImage>` lives; never refcounted per use |
| **Shareable host object** | Rust `T: Shareable` (`Send + Sync + 'static`): `Store<S>`, `Shared<[T]>`, tables, services, presto handles exposed to workers | **share**: the receiver gets its own wrapper of the same `Arc`, and `===` holds within an isolate | thread placement: direct; process placement: becomes a capability id with mirrored or asynchronous access (§5.5) |
| **Transferable** | ArrayBuffer and views, `Buffer<T>`, MessagePort | **move** when listed in `transfer`, otherwise copy | the sender's object is detached |
| **Shared bytes** | SharedArrayBuffer | share (`Arc` plus `Atomics`) | first-party workers only; an extension needs a grant |
| **Local** | every other JS object: plain objects, arrays, Map/Set, Date, RegExp, closures, Proxies, Promises, class instances | **copy** by structured clone, or refused (functions, proxies, symbols, as in HTML) | the only kind with non-atomic counts or local GC metadata, and the only kind the UI hot path touches |

**No `Shareable` marker is exposed to JS in v1.** A host class is shareable because its Rust type
says so. JS code sees ordinary objects whose `postMessage` behaviour is "same object" instead of
"copy", like the shared-structs dev trial ("This is the same sharedBox … not a copy").

### 5.2 The shared image and atoms

- **Two atom ranges.**
  - A `JSAtom`-equivalent gets a tag: QuickJS already uses bit 31 for integer atoms (`quickjs.c:3147`).
  - **Image atoms** (bit 30 set) index the group's frozen table. All 240 predefined atoms and the
    348 builtin names that context setup interns today become image atoms.
  - **Local atoms** index the runtime's own refcounted table.
  - Equality stays an integer compare. The "is it immortal" test stays one bit test, replacing
    `__JS_AtomIsConst`'s compare.
- **The image is frozen when built.**
  - The host builds it once per isolate group, from the release bundle (Svelte runtime, svelters
    runtime, first-party modules): SpiderMonkey's permanent-atom model, not V8's locked
    process-wide table.
  - Interning a string at run time probes the frozen image hash (lock-free, read-only) and then
    the local table. That is one extra probe on the miss path only (`inferred`).
- **Layers.**
  - **Base and per-bundle layers:** each is an immutable `Arc<ImageLayer>`.
  - **Joining:** a runtime attaches to the layers that exist when it is created.
  - **HMR modules and dev overlays** compile into the runtime's local heap, which matches
    QuickJS-NG today. Re-interning a late layer into a runtime that already holds some of its
    strings as local atoms would make two ids for one string. The rule therefore prevents it:
    late layers are not attached to existing runtimes (`inferred`).
  - **Freeing:** a layer is freed when the last runtime holding its `Arc` drops it. This avoids
    Erlang `persistent_term`'s global scan.
- **Function templates.**
  - **Shared part:** the bytecode, image atom operands, constant descriptors, closure-variable
    descriptors, line tables and source map ids. These are immutable, `Send + Sync`, and contain
    no realm.
  - **Per-closure part:** the closure object carries the realm, home object and variable
    references. Per-realm side tables hold materialised constants that have identity (template
    objects, RegExp objects) and inline-cache or feedback state, in the spirit of V8's split
    between `SharedFunctionInfo` and `FeedbackVector` (not re-read here, `inferred`).
  - **For the JIT note:** code embeds no per-runtime addresses; it loads them from a per-runtime
    table, so compiled code can be shared too (`hypothesis`).
- **Layouts.** Object literal and class layouts in the image hold keys and flags, not prototypes.
  A runtime's shape is (layout, proto), so prototypes stay per realm (§3).
- **Process isolates.** The image is position-independent (offsets, not pointers) and produced
  only by the host from locked source, so a sandboxed process can map it read-only. This is also
  the bytecode-cache format (Q2), and it satisfies SEC-O12: only host-produced bytecode is read.

### 5.3 The Rust API

A sketch. Names are provisional; types follow svelters' existing exports where they exist.

```rust
/// Isolates that may share an image and host objects. Never crosses a process.
pub struct IsolateGroup { image: Arc<SharedImage>, /* wrapper registry, worker threads */ }

impl IsolateGroup {
    pub fn new(image: Arc<SharedImage>) -> Self;
    /// One principal per runtime (ARCH-SECURITY SEC-O7). `Placement::Thread | Process`.
    pub fn spawn(&self, principal: PrincipalInfo, placement: Placement, entry: ModuleId)
        -> Result<IsolateHandle, SpawnError>;
}

/// `!Send`: an isolate's runtime and every `Local` value stay on its thread.
pub struct Isolate { /* the hocus runtime */ }
pub struct Local<'iso> { /* a GC-managed value of this isolate */ }

/// Rust objects every isolate of the group may reference. The trait bound is the check.
pub trait Shareable: Send + Sync + 'static {
    /// How a reference looks in process placement (a capability id plus a mirror schema).
    const REMOTE: RemoteShape = RemoteShape::Mirrored;
}
impl<S: StoreState> Shareable for Store<S> {}
impl<T: Copy + Send + Sync + 'static> Shareable for Shared<[T]> {}

/// What `postMessage` produces: `Send`, and serialisable to bytes for process placement.
pub struct Message {
    bytes: Box<[u8]>,                        // the cloned graph, typed (no atoms: strings or image ids)
    shared: Vec<Arc<dyn Shareable>>,         // host references, re-wrapped on receipt
    sab: Vec<Arc<SabBuffer>>,                // SharedArrayBuffers
    moved: Vec<Transfer>,                    // detached buffers, Buffer<T>, ports
}
impl IsolateHandle {
    pub fn post(&self, msg: Message);        // non-blocking; delivered at the receiver's next turn
}
```

- **Typing.** `Local<'iso>` being `!Send` and `Message: Send` are the type-level form of the value
  classes. Rust code cannot move a local JS value to another thread except through `Message`.
- **Identity.** On receipt, `shared[i]` becomes a wrapper from the receiving isolate's wrapper
  cache, keyed by `Arc::as_ptr`. Sending the same store twice yields the same JS object
  (`inferred` design).

### 5.4 Shared types defined in Rust, for JS

A worker that needs shared mutable state uses a Rust type, not a JS shared struct. svelters'
`#[svelters::store]` already declares one: typed fields, `RwLock` state, per-field dirty bits,
any-thread writes (`crates/svelters-host/src/exports/reactive.rs:315-361`). Its locks are the
Rust ones, so JS cannot see a torn or racy field. `Shared<[T]>` covers immutable bulk data;
`Buffer<T>` covers moved mutable bulk data. A `Table` (columns of typed arrays, MQ5) would be the
next one. This is design principle 2 of the shared-structs proposal (no shared→local edges), with
Rust enforcing it. `inferred`.

### 5.5 Process placement (SEC3) stays possible

- **(a) and (e):** `Message.bytes` and moved buffers already go over a pipe; SABs cannot, so a
  process isolate gets none.
- **(b):** the image file is mapped read-only.
- **(c):** a `Shareable` becomes a capability id in the receiver's handle space (R-C).
  - Reads come from a mirror fed by change batches, as ISO1's `get_prop` mirror is.
  - Calls become asynchronous messages, gated on the host side (R-B).
  - Hence rule **C-1**: a host member visible to workers is a `query` (a mirrored read), a
    `command` (an asynchronous call), or an `observe`. It is never a synchronous mutation that
    returns data. This is the mode metadata ARCH-SECURITY §5 already asks every export member to
    carry (E0/E3 row), so the rule costs nothing new.
- **(d)** is the only option that cannot cross a process, which is one more reason to leave it
  out.

## 6. svelters and Svelte reactivity across isolates

**The svelters plans fit this design.**
- **ISO1** is a proxy backend over a transport trait that "carries bytes or `svelters_abi::Value`
  only, never `Arc`/pointers", with handles minted per isolate (ARCH-SECURITY §5, ISO1 row;
  TRACKS ISOLATES).
- **Process placement** is "a transport plus an isolate-host binary, not a redesign, provided
  the protocol never relies on shared memory" (ARCH-SECURITY §1.4).
- **QUICKJS-RUNTIME item 9** already chose "a separate `Runtime` per thread, messages as
  `svelters_abi::Value`. No shared JS objects".

All of this is option (a). (b) and (c) add to it without touching the transport: the image is
below the transport, and host objects in process placement are capability ids.

**Can a worker's `$state` drive UI on the main isolate?** There are two meanings.

1. **The worker owns the UI.** It renders a root through ISO1/ISO2 (an extension's panel, or a
   first-party panel moved off the UI thread). Its `$state` drives its own effects, which emit op
   batches. The UI thread applies them at the next checkpoint.
   - Planned: ISO2's compiler target already makes calls Promises and sync handlers errors
     (TRACKS ISO2).
   - Latency: at least one batch hop; in the same frame only if the batch arrives before
     `BeginUiFrame`.
   - The presto proposal keeps first-party JS on the UI thread for that reason (README Q6).
2. **The UI owns the components, the worker computes the state.** The state lives in a Rust
   `Store`.
   - Writes: the worker writes it with ordinary assignments to store fields, through the store's
     JS wrapper in the worker isolate.
   - Wake-up: each observing host has a `StoreSub` with atomic dirty bits and is woken once
     (`reactive.rs:207-262`).
   - Delivery: at its checkpoint the UI isolate folds the dirty fields into its dirty set and
     delivers them in one `__svelters_changed` batch (`core/reactive.rs:1-17`).
   - UI side: `$derived` and effects re-run there.
   - Status: this works today across hosts in one process (`StoreCell.subs` is a
     `Vec<Weak<StoreSub>>`).
   - Process placement: a remote subscription carries the same change batches.

**What "shared signals" therefore means.** There is no synchronous dependency graph across
isolates. Each isolate keeps its own glitch-free graph. Cross-isolate edges are store fields
propagated at checkpoints, and are eventually consistent. Making the graph cross isolates
synchronously would put the UI thread's reactive reads behind locks or a shared heap, which is
(d). `inferred`.

**Two risks seen in the current store code** (`source-observed`, `inferred` consequence):
- **The UI can block on a worker.** `Store` keeps its state under `std::sync::RwLock`, and
  `update`/`write` run the caller's closure while holding the write lock
  (`exports/reactive.rs:340-361`). A worker that computes inside `update` blocks the UI thread's
  reads for that time.
- **Reads can tear across fields.** `take_changes` reads each field through its getter "under the
  read lock" (`core/reactive.rs:8`, `:216`), so a checkpoint can see field A from one worker
  write and field B from the next.

Recommendation for svelters (via cadatomic's request tables): stores written by workers publish
immutable snapshots (an `ArcSwap`-style pointer swap, or a seqlock with a version per write), so
the UI thread never waits and reads all fields of a checkpoint from one version.

## 7. Open questions for the user

| # | Question | Recommendation |
|---|---|---|
| **IS-1** | What does "fully integrated workers" need to cover: first-party compute workers, third-party extension isolates, first-party panels off the UI thread? | All three on one mechanism, (a)+(b)+(c). Panels off the UI thread stay the exception (presto Q6) |
| **IS-2** | Implement JS shared structs, or a shared JS heap? | **No.** V8 is removing its implementation (2026-10); the proposal has been at Stage 2 since 2024-10 and cannot take blocking locks on a UI thread. Use Rust shared types (§5.4). Revisit if it reaches Stage 2.7 and an engine ships it |
| **IS-3** | `SharedArrayBuffer` and `Atomics.wait` for workers? | Yes for first-party workers; the UI runtime keeps `can_block = false`; extension principals only with a grant; never across a process |
| **IS-4** | Share the image across principals? | Yes for first-party code (read-only). An extension's own modules go in its own layer, never shared with another principal |
| **IS-5** | Make the shared image the bytecode-cache format (svelters Q2)? | Yes: one host-produced, position-independent format, which also satisfies SEC-O12 |
| **IS-6** | Change svelters `Store` to lock-free snapshot publishing? | Yes, before workers write stores (§6). Filed as a request, not done from hocus |
| **IS-7** | Expose a JS-visible "shareable" predicate (like the proposal's `Reflect.canBeShared`)? | Not in v1; the clone and transfer rules plus host types are enough |

## 8. First steps in this repository

1. **Do now, in the core value and code types** (cheap now, breaking later, in the spirit of
   ARCH-SECURITY §5):
   - an atom id with an image tag bit;
   - a `FunctionTemplate` with no realm and no runtime pointers, separate from the closure;
   - per-realm tables for materialised constants and feedback;
   - layouts separate from prototypes;
   - an `Isolate` that is `!Send`, and a `Local<'iso>` lifetime;
   - one principal per runtime;
   - a GC that can skip a region by a bit test.

   Coordinate these with the GC and ownership notes.
2. **Baseline benches** (from this note's two programs, kept with the engine): runtime plus realm
   creation, clone per record, bundle load from source and from bytecode, per-runtime memory.
   Run them on a quiet machine before claiming wins.
3. **v1 messaging:**
   - a native structured clone with a transfer list (`postMessage`, `structuredClone`);
   - ArrayBuffer, `Buffer<T>` and MessagePort transfer;
   - SAB behind a host switch;
   - `IsolateGroup::spawn` with `Placement::Thread`.
4. **v1 host sharing:** `Shareable` over svelters' `Store`/`Shared`/`Emitter`, a per-isolate
   wrapper cache keyed by `Arc` pointer, and the C-1 rule (workers see only query, command and
   observe members).
5. **v1.5 shared image:** base layer from the release bundle; workers attach at spawn. Acceptance:
   worker startup with the base bundle under 1 ms, and per-worker memory a small fraction of
   today's 4.6 MB for the same bundle (targets to confirm by measurement).
6. **Later:** realm snapshots (sharing intrinsics setup), the mmap'd image for `Placement::Process`
   (SEC3), and shared JIT code if the JIT note's design keeps code free of runtime addresses.

## Sources

- QuickJS-NG `7a23d8e`: `quickjs.c:337-428, 696-708, 855-885, 1108-1128, 3147-3161, 3379-3400,
  62698-62860`; `quickjs.h:1095-1100, 1142, 1195, 1266-1285`; `quickjs-libc.c:94, 3936-4045,
  4080-4136, 4248-4300, 2690-2750, 4370`; `quickjs-atom.h` (240 `DEF`s).
- Bellard QuickJS `7d1c13c`: `doc/quickjs.texi:35` (runtime life cycle "less than 300
  microseconds").
- tc39/proposal-structs `8fca66e`: `README.md`, `ATTACHING-BEHAVIOR.md`,
  `CHROMIUM-DEV-TRIAL.md`, `CODE-SHARING-IDEAS.md`. tc39/proposals `07002d4`: `README.md`
  (Stage 2 table, structs row and notes links).
- V8 `5f114fe1` (2026-10-25): `include/v8-isolate.h:208-292`, `docs/objects/strings.md:70-86`,
  `src/heap/read-only-heap.h`, `src/flags/flag-definitions.h:1317-1338, 1841, 4334-4345`,
  `src/objects/js-struct.h`. Commits: `ba3b6eee`, `1a037b51`, `e8ac4914`, `1775b0c9`,
  `5ce45006`, `36471eea`, `3197b8a8`, `5e30b8e4`, `a88b5701`.
- SpiderMonkey (Firefox `efd2d1e6`): `js/src/vm/Runtime.h`, `js/src/vm/SharedImmutableStringsCache.h`,
  `js/public/experimental/JSStencil.h`.
- JSC (WebKit `3f000ecf`): `Source/JavaScriptCore/runtime/JSLock.h`. F. Pizlo, "Concurrent
  JavaScript: It can work!", webkit.org/blog/7846 (2017-08-30).
- GraalJS `dd265d5`: `docs/user/Multithreading.md`; graalvm.org/latest/reference-manual/embed-languages
  (GraalVM 25.4, read 2026-10-25).
- Hermes `393a539`: `doc/superpowers/specs/2026-07-28-worker-arraybuffer-input-design.md`.
- Deno: docs.deno.com/runtime/reference/web_platform_apis (read 2026-10-25). Bun:
  bun.com/docs/runtime/workers (read 2026-10-25).
- CPython: PEP 683, PEP 703 (peps.python.org), docs.python.org/3/howto/free-threading-python.html
  (3.15.1), docs.python.org/3/library/concurrent.interpreters.html (added 3.14; shareable
  `None`, `bool`, `bytes`, `str`, `int`, `float`, `tuple`; others pickled; `memoryview` and
  `Queue` share mutable data).
- OCaml: Sivaramakrishnan et al., "Retrofitting Parallelism onto OCaml", ICFP 2020, arXiv
  2004.11663v3.
- Erlang/OTP 29.1.2: erlang.org/doc/system/binaryhandling.html; erlang.org/doc/apps/erts/persistent_term.html.
- Swift: SE-0430 `sending` (implemented in Swift 6.0), building on SE-0414 region-based isolation.
  Rust: `std::marker::{Send, Sync}` (doc.rust-lang.org).
- svelters `0aa41697`: `plans/research/ARCH-SECURITY.md` (§0, §1.3, §1.4, §3.1, §3.6, §5, SEC-O7,
  SEC-O12), `plans/research/TRACKS.md:388-409`, `plans/research/QUICKJS-RUNTIME.md:262`,
  `crates/svelters-host/src/exports/{buffers.rs:1-17, 338-370, reactive.rs:207-361, schema.rs:152}`,
  `crates/svelters-host/src/core/reactive.rs:1-17, 216`, `crates/svelters-host/src/quickjs/buffers.rs:314`.
- cadatomic `75b6a47`: `docs/research/2026-10-25-gui-toolkit/{README.md (Q6), architecture.md §2,
  compiler-and-js.md §3 (worklets), §5}`.
- Measurements: this session's scratchpad `bench-iso/bench.c` and `bench-iso/load.c`, built with
  `gcc -O2 -D_GNU_SOURCE` against QuickJS-NG `7a23d8e`; input `svelte-compiler-5.57.2.js` (the
  svelte@5.57.2 `compiler/index.js`, 874,217 bytes). Cloud container, 4 cores, shared: cloud, not
  a gate.
