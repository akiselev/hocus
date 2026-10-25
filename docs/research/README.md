# hocus: a JavaScript engine in Rust for svelters and presto

Date: 2026-10-25. Status: **research done, proposal for the user. Nothing is built.**

**The question** (user, 2026-10-25): port QuickJS to Rust so that svelters and the presto renderer
get a native integration. Research how to translate ownership without `unsafe` everywhere,
copy-and-patch and Cranelift JITs, the svelters integration and the GUI loop, sharing types and
objects between isolates so workers are fully integrated, and the Rust GC options. Then decide
where to start.

**The evidence** is eight notes in `notes/`, written to `BRIEF.md`. Every claim in them is graded
(`documented`, `source-observed`, `measured`, `inferred`, `hypothesis`) and cited. The scripts and
raw results behind the measurements are in `artifacts/`. **Every timing is from a shared 4-core
cloud VM: cloud, not a gate.** Trust ratios more than absolute times.

| Note | Answers |
|---|---|
| `notes/quickjs-census.md` | what QuickJS-NG is made of, subsystem by subsystem, and what to port, replace or drop |
| `notes/rust-engines-prior-art.md` | Boa, Nova, Brimstone, quickjs-oxide and other Rust engines; the Bun, Ladybird, rav1d and tsgo ports |
| `notes/gc-and-ownership.md` | Rust GC designs, QuickJS's refcounting, the pause budget presto needs, and a GC API for hocus |
| `notes/interpreter-design.md` | dispatch in Rust, register vs stack bytecode, values, shapes, inline caches, calls, strings |
| `notes/jit.md` | copy-and-patch, Cranelift, CacheIR, AOT from the svelters compiler; V8/JSC tiers measured on Svelte code |
| `notes/isolates-and-sharing.md` | how engines share across threads, Shared Structs, and a sharing design for workers |
| `notes/svelters-presto-integration.md` | what owning the engine enables for svelters and presto, the embedding contract, the benchmark gate |
| `notes/verification-and-method.md` | test262, QuickJS-NG as an oracle, fuzzing, benchmarks, the porting method, the repo scaffold and first batches |

## 1. The short answer

1. **Speed alone does not justify the port.** It must be justified by integration.
   - On the real Svelte 5.57.2 runtime, V8 is about 19–23× QuickJS-NG on signals. Its interpreter
     alone (Ignition, with inline caches) is only 1.5–2.3× (`jit.md` §0, `artifacts/jit/results.txt`).
   - So the best interpreter we can write gets about 1.5–2× QuickJS on Svelte code
     (`interpreter-design.md`). A baseline JIT adds up to about 4× (JSC's baseline). The rest of
     the gap is V8-class optimizing tiers: years of work.
   - No Rust JS engine is faster than QuickJS today. On bench-v8, Boa is 0.20–0.33× QuickJS-NG,
     Brimstone 0.16–0.39× and Nova 0.06–0.12× (`interpreter-design.md`, javascript-zoo data). The
     one at parity, quickjs-oxide, is 1.5–2.8× *slower* than QuickJS on the Svelte workloads
     (`rust-engines-prior-art.md`, measured).
   - **The bar is "at least parity with QuickJS-NG, aiming at 1.5×"**, measured against QuickJS-NG
     plus the cheap wins already planned for it (templates, ids, raw C ops, svelters' own JS
     reactivity core), not against today's stock path (`svelters-presto-integration.md` §4).
2. **What only an engine we own can do**, ranked by value for cadatomic
   (`svelters-presto-integration.md` §0):
   1. **`$state` objects without `Proxy`:** ordinary objects with a reactive bit, with dependency
      tracking on the engine's own property path. QuickJS is 6× behind V8 on Svelte proxies, and
      no interpreter or JIT trick closes that gap (`interpreter-design.md` Q4).
   2. **A reactive core in Rust:** `$.get`, `$.set`, dirty marking and the flush as engine
      intrinsics; effect bodies stay JS. This targets the 71% of signals time spent in call
      overhead. Estimated 5–10×, a `hypothesis` until X1 below measures it.
   3. **GC driven by presto's frame clock.** QuickJS's cycle collector pauses about 29–72 ns per
      live object: 5–7 ms at 100k objects and about 190 ms at 1M (`gc-and-ownership.md`, measured).
      That pause can land mid-frame. A 120 Hz frame is 8.3 ms.
   4. **Interrupts that unwind** (`finally` runs), time-budgeted microtask drains and event-time
      dispatch.
   5. **HMR that frees replaced modules.** QuickJS keeps them until the context dies, so svelters
      forces a full reload every 200 swaps.
   6. **A debugger and profiler designed in**, including an effect profiler keyed by compiler
      source locations.
   7. **Workers that start almost free:** atoms, compiled code and object layouts shared read-only
      between isolates. Today every QuickJS runtime reloads the Svelte compiler bundle: about 8 ms
      and 4.6 MB each from bytecode (`isolates-and-sharing.md`, measured).
   8. One language in the build, memory safety by construction, no C toolchain for wasm and iOS.
3. **The design the notes converge on.** It is a port of QuickJS's *semantics* (builtins,
   compiler, module system, test assets) onto a *new memory model*, not a line-by-line port. The
   memory models don't match: tsgo's lesson is that a faithful port only works when they do
   (`rust-engines-prior-art.md`). §2 has the design.
4. **Where to start:** two cheap experiments decide the biggest unknowns before engine code is
   written, alongside the harness lanes that every path needs (§4):
   - the **quickjs-oxide bake-off** decides whether to fork it or build our own heap;
   - **X1**, a Rust reactive core on stock QuickJS, decides how much of the reactivity win needs
     our own engine at all.

## 2. The design

| Area | Decision (recommended) | Why | Note |
|---|---|---|---|
| **Base** | QuickJS-NG at `7a23d8e` (v0.17.0+), not Bellard's QuickJS. Fork at the port; track test262 and NG's regression tests, not individual commits | svelters runs NG; its API, tests and CI are what we compare against. Without Temporal and Intl, NG passes 98.1% of test262 and Bellard 98.3% | census |
| **Port faithfully** | object-model semantics, builtins Svelte needs, the bytecode format, the parser and compiler, the module system | the semantics are where QuickJS's value is; the front end is about 15k lines | census, prior-art |
| **Replace with crates** | regexp (`regress` with `utf16`), Unicode tables, float formatting (`ryu-js`, `fast-float2`), Temporal (`temporal_rs`, later) | mature crates with test262 evidence (Boa passes 1,875/1,879 RegExp tests with `regress`) | census |
| **Don't port** | quickjs-libc, Intl (svelters already has an ICU4X shim), Bellard's 77k-line "experimental AI coded" Temporal/Intl | — | census |
| **Memory** | **tracing GC, no refcounts.** Incremental, non-moving mark-sweep at first. Precise roots (frame slots, a handle stack, a root table), so objects may move later. 32-bit compressed references, 4 GiB per isolate | removes 1,402 `JS_FreeValue` and 406 `js_dup` sites and the goto-fail cleanup (about a third of the lines in sample functions); bounds pauses; prepares for a JIT | gc |
| **Safety** | **one audited `unsafe` kernel in `hocus-gc`** (target 30–60 blocks), `forbid(unsafe_code)` elsewhere, an `unsafe` count ratchet that only falls. The borrow checker enforces rooting: an unrooted `Value<'n>` cannot be held across a call that may GC, and a `Local<'s>` cannot escape its scope. A prototype compiles and rejects both misuses with no transmutes or lints | Nova's equivalent has 1,036 `unsafe` blocks, 9,400 bind/unbind calls and a custom lint crate, and its author calls it unsound; piccolo, a full Lua VM, has 24 | gc, prior-art |
| **Values** | 64-bit `Copy` NaN-box: int32 and f64 inline, a 32-bit heap reference in the payload | small, cheap to copy, fits compressed references | interpreter, gc |
| **Bytecode** | register plus implicit accumulator (Ignition style), 1-byte opcodes, wide prefixes, immutable, serializable, a feedback-slot index on every cached site | QuickJS's stack bytecode spends 31–41% of executed opcodes moving data, each with a refcount update (measured opcode mix). Stack-to-register translation removed 47% of executed instructions in Shi et al. (VEE 2005) | interpreter |
| **Bytecode seam** | **NG's stack bytecode stays the compiler's output and the oracle seam; a lowering pass turns it into hocus's register bytecode at load.** M0 runs bytecode written by C QuickJS-NG, so the VM is testable before the parser exists. The ported compiler must then emit byte-identical bytecode to `qjsc` | NG's bytecode output is deterministic (measured, including the Svelte runtime bundle). This reconciles the census and verification notes (keep the format) with the interpreter note (don't execute it) | synthesis (`inferred`) |
| **Calls** | JS→JS calls on a VM stack, never Rust recursion; one register file; zero-copy arguments; captured variables in heap scope objects | every QuickJS call recurses through `JS_CallInternal` with a heavy prologue; a Svelte signals call runs only 40 opcodes | interpreter, jit |
| **Objects** | hidden classes with transition trees, inline slots sized per allocation site, dictionary mode as fallback, V8-style element kinds | the standard design every fast engine uses | interpreter |
| **Inline caches** | monomorphic, then 4-way, then global, in a **lazily allocated** feedback vector. Cache stubs as data in a **CacheIR-style** language, so a later JIT reuses them | NG removed its caches (PR #884) over memory and run-once cost; lazy allocation fixes both. CacheIR serves every SpiderMonkey tier | interpreter, jit |
| **Dispatch** | handlers generated twice by one macro: tail-call threading on x86-64 and aarch64 (stable Rust, with a CI assembly check) and a portable `match` loop for wasm32, iOS and Miri | `become` is unstable (stabilisation hoped for 2027); a toolchain change silently cost Stitch 30% at Rust 1.92 | interpreter |
| **Strings** | Latin-1/UTF-16 with an ASCII bit (ASCII passes to Rust as `&str` with no copy), ropes, a cached UTF-8 copy for presto's text layer | — | interpreter |
| **GC and the frame** | no pause above 1 ms at 120 Hz; in-frame steps ≤ 0.25 ms; the rest in the gap after each frame is committed; a cycle finishes before the window goes idle. A frame-driver API for presto | V8 idle-time GC, JSC Riptide (~1 ms), Lua 5.4 incremental steps | gc |
| **Finalization** | at sweep, not at the last reference | the spec leaves timing to the host; svelters and presto keep explicit destroy paths | gc |
| **Isolates** | **v1:** share-nothing isolates with a native `structuredClone` and transfer list, plus `Send + Sync` Rust objects (stores, `Shared<[T]>` views, presto handles) reachable from every isolate, one `Arc` count per wrapper. **v1.5:** a shared immutable image (atoms, function templates, layouts), frozen once per process | no engine shares ordinary JS objects across threads; a shared mutable heap costs single-threaded code 1–8% (CPython free-threading, OCaml 5) | isolates |
| **Shared Structs** | **no.** Stage 2 since 2024-10; V8 turned the flag off on 2026-10-20 and removed `Atomics.Mutex`/`Condition` on 2026-10-22; its locks block, and a UI thread must not | — | isolates |
| **Core types now** | split what is shareable (code, layouts, process atoms) from what is per-runtime (realm, prototypes, IC state) from the first commit | the v1.5 image is cheap only if the split exists from the start | isolates |
| **JIT** | **none in year one.** Design it in: VM stack, every live value in a frame slot at safepoints (so no stack maps at first), IC slots, liveness and pc maps, a W^X platform layer | interpreter ICs close 2–15% of the V8 gap, a baseline JIT 23–49%, the optimizing tiers 49–62% (measured, log scale) | jit |
| **JIT tier 1, later** | **copy-and-patch baseline** with inline caches (Deegen's design), stencils compiled by stable `rustc --emit=obj`; started only if profiling shows JS above 25% of frame time (or 4 ms p95 per frame) after the IC stage | Deegen's baseline is within 33% of LuaJIT; CPython's JIT (4–12%, LLVM build dependency, PEP 836 withdrawn) is the cautionary case; quickjs-jit's helper-per-opcode Cranelift tier tied on Svelte | jit |
| **Cranelift** | only for a later optimizing tier, if ever. If V8-class speed is needed, use V8 (deno_core) behind svelters' `JsEngine` seam | no deopt or OSR, monthly breaking releases, 0.137 needs rustc 1.97 (> our 1.95) | jit |
| **AOT** | the useful form is weval-style: compile the shipped bundle at build time with the baseline stencils. That also covers iOS, where runtime codegen is forbidden. The compiler emits JS plus a typed hint section, not engine bytecode | parse time is already cached; Static Hermes-style typed compilation needs sound types Svelte doesn't have | jit, integration |
| **svelters seam** | hocus is a **second engine behind svelters' `JsEngine` trait**, beside QuickJS-NG, never a replacement of the trait. Intrinsics are reached through built-in modules with JS fallbacks, so the same compiled JS runs on Node, QuickJS and hocus. The adapter (`hocus.rs`) is svelters' to write | keeps the DOM conformance (628/774), the 56 op-log goldens and the other backends untouched | integration |
| **Minimal ES surface for "runs Svelte"** | modules, classes with private fields, extending builtins, Proxy/Reflect, Map/Set/WeakMap/WeakSet, Promise and async, async generators, WeakRef/FinalizationRegistry, typed arrays, BigInt, simple RegExp. Not needed first: `eval`, `new Function`, Atomics, SharedArrayBuffer, Intl | grep of the shipped Svelte runtime and native-runtime | integration, verification |

## 3. How the work is verified

The same pattern as cadabra3 against Parasolid: **QuickJS-NG is the layer-by-layer oracle, and
tests compare observable outputs, never internal order** (`verification-and-method.md`).

- **Four oracle seams:**
  1. Leaf libraries (regexp, Unicode, numbers) are linked back into NG through a C shim ("Theseus
     mode"), and NG's own test262 run must not change.
  2. Bytecode: the compiler is checked byte-for-byte against `qjsc`; the VM runs NG's bytecode.
  3. Observable output: printed output, exceptions and job order, compared between engines. Node
     and engine262 break ties. Disagreements are triaged as our bug, an NG bug we reproduce, an NG
     bug where we follow the spec (a registered departure), or implementation-defined.
  4. svelters: its fixtures with byte-exact `expected.oplog`, through the `JsEngine` seam.
- **test262 ladder,** each rung relative to NG (hocus passes every test NG passes): R0 harness
  (116 tests), R1 language core (~10.4k), R2 core builtins (~11.1k), **R3 the "Svelte rung"**
  (classes, async, generators, modules, Promise; ~14.8k), R4 RegExp, Date, TypedArray and BigInt,
  R5 Atomics, annexB and parity with NG's 40 known failures. NG's own run, reproduced here: 82,220
  executions, 40 failures, all in its known-errors file.
- **Benchmarks:** instruction counts (gungraun, the renamed iai-callgrind) are the cloud gate,
  tracked as a hocus/NG ratio; they were identical across four runs while wall time spread from
  129 to 336 ms. Speed claims come only from a quiet machine.
- **Fuzzing:** Fuzzilli works with a Rust engine on stable rustc 1.97 (coverage hooks measured),
  plus cargo-fuzz on the parser and bytecode reader, GC stress, Miri and sanitizers on the daily
  server.
- **Method:** hand port by subsystem with AI lanes, not c2rust. c2rust keeps C's ownership model,
  which is the thing we are changing; rav1d took 20+ person-months against 7 estimated. Bun's
  Zig→Rust port (about 1M lines in 11 days with parallel Claude agents, a porting guide and a
  per-field lifetimes table, the test suite as oracle) and Ladybird's JS front end (two weeks,
  byte-identical bytecode) show that test-gated agent ports at this scale work. A porting ledger
  has one row per C function (1,611 in `quickjs.c`).

## 4. Where to start

### Batch 0: the coordinator alone

- This repository: the research (done), `pins.toml`, `CLAUDE.md`, `STATUS.md` and the decision
  list `docs/plan/DECISIONS.md`.
- After the user's answers to §5: the workspace skeleton, the ratchet files, `scripts/check.sh`
  tiers, and `porting/ledger.tsv` generated from `quickjs.c` (gate: the row count matches the
  census).

### Batch 1: decide the unknowns and build the harness (no engine core yet)

| Lane | Does | Gate | Decides |
|---|---|---|---|
| `bench` | regenerate and commit the four Svelte workloads (signals, proxy, app glue, property kernel; svelte 5.57.2), bench-v8, NG's microbench; gungraun instruction-count benches of NG | instruction counts identical across two runs; checksums match node | the baseline every other lane measures against |
| `oxide-bakeoff` | profile quickjs-oxide (`pocket-nexus/quickjs-oxide @ 3a9ceb9`, MIT, 133k lines, `unsafe` forbidden) on the Svelte workloads; find why it is 1.5–2.8× slower; read its heap, rooting and embedding API against §2 | a written verdict with profiles: the gap's causes, and whether its heap can host presto nodes, reactive objects and a shared image | **fork oxide, or build our own core** (D1) |
| `x1-reactive` | a Rust reactive core (`$.state`/`$.derived`/effects/flush) called from stock QuickJS-NG through raw `rquickjs-sys` C functions, in a standalone harness in this repo; run Svelte's signal tests against it | Svelte's signal tests pass; the w-signals gain is measured against Svelte's JS core and svelters' own JS core | **how much of the reactivity win needs hocus** (D2) |
| `oracle` | `hocus-oracle-sys` (NG built from the pin), `hocus-test262` runner with an `Engine` trait implemented for NG | reproduces NG's run: 82,220 executions, 40 failures = its errors file | — |
| `bytecode` | `hocus-bytecode`: opcode tables generated from the pinned headers, NG bytecode reader, writer, verifier and disassembler | read → write is byte-identical for all corpus bytecode from `qjsc -b` | — |
| `core-spike` | `hocus-gc` prototype per `gc-and-ownership.md` §7: `Cx`/`NoGc`/`Value<'n>`/`Local<'s>`, compile-fail tests for both misuse cases, a stop-the-world collector, a collect-at-every-allocation stress mode, Miri on its tests, the three ported example functions | compiles; compile-fail tests fail as designed; Miri clean; `unsafe` count recorded | how the heap API feels on real code, for D1 |

The leaf ports (`regexp`, `numbers`, `observe`) wait for batch 2, because a quickjs-oxide fork
would bring its own.

### Batch 2 and on (sketch)

- **M0:** `vm-core` runs NG-compiled bytecode (lowered to register bytecode) with minimal builtins:
  test262 R0.
- **M1:** the compiler port (byte-identical to `qjsc`), builtins by family in parallel lanes,
  the hocus `Engine` for the test262 runner: R1–R2.
- **M2 "runs Svelte":** R3, the hocus `JsEngine` adapter in svelters (requested through svelters'
  table), svelters' fixtures and op logs on both engines, instruction ratios on the Svelte
  workloads at every consolidation.
- **M3 "worth it":** interpreter ICs, frame-clock GC, engine-native `$state` objects and the
  reactive intrinsics behind capabilities. The gate is §1's bar on a quiet machine, plus the GC
  pause gate in presto.
- **Later:** the shared immutable image for workers, the debugger, then the copy-and-patch
  baseline if its profile trigger fires.

## 5. Decisions for the user

Each has a recommendation. D3, D4 and D8 touch decisions recorded as the user's.

| # | Question | Recommendation |
|---|---|---|
| **D1** | Fork quickjs-oxide, or port onto our own heap? | **Decide after the one-week bake-off.** Fork only if profiling shows its Svelte gap is fixable in its design and its heap can carry presto objects; otherwise our own heap per §2, reading oxide for its solutions |
| **D2** | May hocus implement Svelte's reactivity natively (reactive objects instead of `Proxy`, a Rust reactive core), behind a capability, with Svelte's JS core kept as the differential reference and the DOM conformance on it? This departs from "stock svelte@5.57.2" on native backends | **Yes, behind a capability, gated on X1's numbers and on Svelte's own signal and proxy tests passing in-engine.** The JIT note argues the other way (keep Svelte's JS authoritative, rely on ICs); but without this, hocus's speed case is 1.5–2× at best, and this is the main thing only an owned engine can do |
| **D3** | Is hocus a fork of QuickJS-NG under the recorded "stock QuickJS-NG; no rquickjs fork" decision (user, 2026-10-21)? | **No: hocus is a new engine beside QuickJS-NG.** QuickJS-NG stays a supported svelters engine and the oracle; the recorded decision keeps describing the QuickJS backend exactly. Record it as "hocus added (user, date)" |
| **D4** | Port base: NG v0.17.0 at `7a23d8e`, or 0.16.2 (what svelters ships)? | **`7a23d8e`**, one source for port and oracle, built from source |
| **D5** | GC terms: finalization at sweep; pause gate 1 ms / 0.25 ms at 120 Hz; 4 GiB per isolate; our own collector rather than MMTk | **Yes to all four** (`gc-and-ownership.md` §10) |
| **D6** | Sharing: no shared mutable JS heap and no Shared Structs; Rust `Send + Sync` objects and a shared immutable image instead | **Yes** (`isolates-and-sharing.md` IS-1..IS-7) |
| **D7** | Toolchain: stable Rust 1.95+ for the product; nightly only for Miri, sanitizers and fuzzing, pinned by date | **Yes.** Tail calls through stable sibling-call optimisation with an assembly check; `become` when it stabilises |
| **D8** | Scope: no Intl or Temporal in the engine; JIT none in year one, with the copy-and-patch trigger in §2; V8 (deno_core) if V8-class speed is ever needed | **Yes** |
| **D9** | Name and licence | **`hocus`** (free on crates.io; npm has an unrelated `hocus` and GitHub an archived `hocus-dev/hocus`). Licence: **MIT**, since the port derives from MIT QuickJS-NG and must keep its notice anyway; or undeclared like cadatomic until you decide. Alternatives: `incanto`, `praestigiae`, `alakazam` |
| **D10** | GitHub repository | **Create an empty private `akiselev/hocus`.** The session's GitHub integration can't create repositories (403); once it exists, this history is pushed as is |

## 6. Gaps

- Nothing was measured on a quiet machine. The four Svelte workloads were rebuilt from the svelters
  note's descriptions; the `bench` lane commits the canonical versions.
- quickjs-oxide's Svelte gap is not profiled yet, and its test262 figure is self-reported.
- X1's gain, cadatomic panels' JS share of frame time, and the GC pause on a real cadatomic heap
  are not measured. Every gain marked `hypothesis` in the notes depends on them.
- QuickJS line references are at `7a23d8e`, not the 0.16.2 that svelters ships.
- Claims dated after 2026-10 (V8's shared-struct removal, PEP 836's withdrawal, the Bun port) come
  from primary sources the notes cite (V8 commits, PEP text, Bun's own post), read on 2026-10-25.
