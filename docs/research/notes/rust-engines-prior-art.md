# Prior art: JavaScript engines in Rust, and ports of C engines to Rust

Researcher note for the hocus study (brief: `docs/research/BRIEF.md`). Date 2026-10-25.
Grades: `documented`, `source-observed`, `measured`, `inferred`, `hypothesis`.
Every timing below ran on a shared 4-core cloud VM (Intel Xeon @ 2.80 GHz, load average 1.3 to 3.1
from other researchers): **cloud, not a gate**.

## Summary

1. **Someone has already built "QuickJS in safe Rust", and it is fast on the classic benchmark.**
   quickjs-oxide (pocket-nexus, MIT, 0.4.1, first commit 2026-07-11) is about 133k lines of engine
   Rust with `#![forbid(unsafe_code)]`. It targets QuickJS 2026-06-04 behaviour and passes 97.5% of
   the test262 variants QuickJS runs (`documented`). It uses NaN-boxed `u64` values with 32-bit
   arena indices, a register VM and a non-moving mark-sweep collector. It was written with Claude
   (152 commits carry Claude co-author trailers) (`source-observed`). On bench-v8 I measured it
   **level with Bellard's QuickJS and about 15% ahead of QuickJS-NG** (`measured`). Two other
   AI-era "QuickJS in Rust" projects exist (quickrs-core and Lewin671/quickjs-rust). Both use `Rc`.
   The first adds a trial-deletion cycle collector. The second never frees cycles.
2. **The benchmark win does not carry over to Svelte.** On the real Svelte 5.57.2 runtime
   (signals, deriveds and effects, plus `$.proxy` deep reactivity), quickjs-oxide took **1.5 to
   2.2 times** QuickJS's time on signals and **about 2.8 times** on proxy (`measured`). So "safe
   Rust can match QuickJS" is shown for bench-v8 and not yet for our workload.
3. **The mature pure-Rust engines are too slow to adopt for the GUI loop.** Boa is the most mature
   (95.6% of test262, 318 contributors, MIT/Unlicense). On Boa's own nightly bench-v8 its score is
   345 against QuickJS's 1,765, about 5 times slower (`documented`, boa-dev/data 2026-10-25).
   On the Svelte bench I measured Boa at about 7× QuickJS's time on signals and about 11× on
   proxy (`measured`). Nova is 1.0, MPL-2.0 and passes 76.9% of test262. Its author says it is "not a fast engine"
   (`documented`). Brimstone is a single-author, V8-shaped engine (MIT, >97% of the language) with
   a semispace collector "written in *very* unsafe Rust" (`documented`).
4. **Nova is the deepest work on "ownership without unsafe everywhere".** It also shows what that
   costs. Its handles are 32-bit indices into per-type arenas, and GC compacts them. The borrow
   checker enforces rooting through `GcScope`/`NoGcScope` reborrowing. The price is about 9,400
   `bind`/`unbind`/`scope`/`reborrow` calls in 159k lines, a custom dylint lint crate, and a design
   its author calls "profoundly un-Rusty" and "unsound or incomplete" today (`source-observed`,
   `documented`). An index heap turns use-after-free into a logic bug: a stale handle can silently
   read another value of the same type. It is never memory unsafety. quickjs-oxide makes the same
   trade with runtime checks: one `&mut Heap`, an explicit root stack, and a GC-stress mode.
5. **The large ports say: port faithfully, use a language-independent test suite as the oracle,
   and choose the memory model on purpose.**
   - **Bun** went from Zig to Rust: 535k lines in 11 days with about 64 Claude agents. It was
     driven by `PORTING.md` and `LIFETIMES.tsv`, with adversarial review. It ended with 4% of the
     code `unsafe`, 19 regressions and 2 to 5% faster (`documented`).
   - **Ladybird** ported the LibJS front end, 25k lines, in two weeks. The bytecode was
     byte-identical and test262 showed 0 regressions (`documented`).
   - **fish** took about 14 months by hand, incrementally (`documented`).
   - **rav1d** went through c2rust and then a refactor. It took more than 20 person-months
     against an estimated 7, and stayed about 5 to 6% slower than C. A $20k bounty concluded
     without a published parity result (`documented`).
6. **Recommendation:** don't c2rust QuickJS, and don't adopt Boa or Nova. Port QuickJS's
   *semantics and front end* faithfully, as an AI-assisted structural port. Do not port its
   *memory model*: build a new heap in the quickjs-oxide/Nova shape (arena indices, a tracing GC,
   NaN-boxed `u64`). That shape is also what native node values, shared isolates and a later JIT
   need. Before writing code, run a one-week bake-off of **quickjs-oxide as a fork base** against
   a fresh port, gated on the Svelte workloads. It is the cheapest way to learn whether months of
   work already exist. Details are in [Recommendation](#recommendation).
7. **Name:** `hocus` is free on crates.io. `hocus-pocus` is taken, and the npm package `hocus` is
   a time tracker. On GitHub, `hocus-dev/hocus` (3.2k stars, archived 2024) and the popular
   `ueberdosis/hocuspocus` (Y.js backend) are both "hocus". The name is usable but crowded.
   Alternatives: `alakazam` and `incanto` (both free crates), or `praestigiae` (Latin "conjuring
   tricks", free).

## Comparison table

Lines are Rust lines (`find -name '*.rs' | xargs cat | wc -l`, generated tables included) at the
listed sha. "unsafe" counts lines that contain the word `unsafe`. test262 figures are from the
test262.fyi data, `gh-pages@482f9d8`, generated 2026-10-25 03:26 UTC, test262 `d53ebc8`, macOS M4.
Speed is the bench-v8 score (higher is faster) from boa-dev/data `@8cf6917`, run nightly on GitHub
`ubuntu-24.04` runners on 2026-10-25, unless marked *m* for my own runs.

| Engine | Lang | Rust lines (sha) | test262 total / language | bench-v8 score (QuickJS = 1765) | GC | unsafe lines (/kLOC) | Licence | Authors (last 12 mo) | Last release |
|---|---|---|---|---|---|---|---|---|---|
| QuickJS (Bellard) | C | 64,898 + 77k intl/temporal C (`7d1c13c`) | 98.50% / 98.14% | 1765; *m* 1144–1149 | refcount + cycle trial deletion | n/a | MIT | 1 | 2026-06-04 |
| QuickJS-NG | C | 64,954 (`7a23d8e`) | 83.51% (no Temporal/Intl) / 98.15% | *m* 974–983 | same | n/a | MIT | many | 0.16.2 (svelters pin) |
| **quickjs-oxide** | Rust | 132,687 engine (core 54k, front end 66k, regexp 12k) (`3a9ceb9`) | not on test262.fyi; self-reported 97.46% of QuickJS's scope, 79.8% of all variants | *m* 1054–1139 | non-moving mark-sweep over typed arenas, explicit root stack | 0 (`forbid`) | MIT | 9 (2 main) | 0.4.1, 2026-10-22 |
| Boa | Rust | 236,337 core (`64c209f`) | 95.59% / 98.12% | 345 | mark-sweep, `Gc<T>` with root counts, thread-local | 618 (2.6) | MIT / Unlicense | 91 | 0.22.0, 2026-08-28 |
| Nova | Rust | 159,207 `nova_vm` (`877ef16`) | 76.93% / 95.52% | none published; "not a fast engine" | compacting mark-compact, index handles, lifetime-checked rooting | 1,133 (7.1) | MPL-2.0 | 15 | 1.0.0, 2026-03-15 |
| Kiesel | **Zig** | n/a | 94.80% / 95.05% | 584 | Boehm (bdwgc) | n/a | MIT | 1 main (Linus Groh) | 0.5.0-dev build |
| Brimstone | Rust | 127,275 (`64d6698`) | not on test262.fyi; ">97% of the language" (README) | not published | Cheney semispace, V8-style `HandleScope` | 236 (1.9) | MIT | 1 | none tagged |
| yavashark | Rust | 260,691 (`42e4266`) | "~81% of the entire suite" (README) | not published | own crate `yavashark_garbage` | 437 (1.7) | MIT | 2 | none tagged |
| quickrs-core (eugenehp/quickjs-rs) | Rust | 65,944 core + 7k C API (`2e203eb`) | "not measured"; upstream's 86/86 tests | not published | `Rc` + per-cell lock + trial deletion | 23 core, 833 C API | MIT | 1 | 0.16.2, 2026-09-22 |
| Lewin671/quickjs-rust | Rust | 218,098 (`6663738`) | 42,672/42,672 of the NG-configured subset (self-reported) | not published | `Rc`; "a reference cycle is never freed" | 118, all in the CLI | MIT | 1 | binaries on GitHub |
| Starlight | Rust | 35,046 (`503e789`) | n/a | n/a | Immix | 322 | MIT | dead (2021) | none |
| Duktape | C | n/a | n/a | 486 | refcount + mark-sweep | n/a | MIT | inactive | 2.7.0 (2022) |

Sources for each row are in the sections below.

## Evidence

### test262 and speed data (shared by every row)

- test262.fyi publishes its data in `test262-fyi/data`, branch `gh-pages` (`482f9d8`).
  `meta.json` pins the engine versions it ran: boa `9871775`, nova `877ef16`,
  kiesel `0.5.0-dev.21+9850ecf6b`, qjs `7d1c13c`, qjs_ng `7a23d8e`, test262 `d53ebc8`, on
  "macOS 15.3.1 with M4". `index.json` has the per-directory pass counts out of 53,616 tests
  (`source-observed`). I computed the percentages. language/: boa 98.12%, nova 95.52%,
  kiesel 95.05%, qjs 98.14%, qjs_ng 98.15%. built-ins/: boa 96.04%, nova 70.61%, qjs 98.87%,
  qjs_ng 79.41%. The QuickJS-NG gap is mostly Temporal and Intl: NG passes 0 of the 6,715 Temporal
  tests, and Bellard's QuickJS passes 6,707 (`source-observed`).
- **Bellard's QuickJS and QuickJS-NG have diverged.** Bellard's tree now carries `intl/` and
  `temporal/` (77,228 C lines), and its 2026-06-04 Changelog claims "30% faster on bench-v8" plus
  "11% faster" from a small-block allocator (`source-observed`, `quickjs-bellard/Changelog
  @ 7d1c13c`). A faithful port has to pick one upstream. That choice belongs with the
  QuickJS-internals researcher.
- Boa's benchmark page draws from `boa-dev/data` (`bench/results/*.json`, workflow
  `.github/workflows/benchmark.yml`). The workflow downloads Bellard's latest binary QuickJS
  release and runs V8 `--jitless`, SpiderMonkey `--no-jit-backend`, Kiesel `releasefast`,
  Duktape 2.7.0 and `cargo run --release --bin boa` on `bench/bench-v8/combined.js`
  (`source-observed`). Latest scores, 2026-10-25: boa 345, quickjs 1765, kiesel 584,
  duktape 486, v8-jitless 3589, sm-jitless 1171; libjs's last entry is 840 (2026-01-16). Boa's
  ratio to QuickJS was 0.055 in 2024-04, 0.196 in 2025-10 and 0.195 in 2026-10. QuickJS jumped
  from 899 to 1765 across its 2026-06 release. Over the same months every engine's score rose
  (v8-jitless 2672 → 3589), so the runner hardware changed too. Only same-day ratios are meaningful
  (`source-observed`, computed from `score.json`).
- A third-party table (`github.com/ahaoboy/js-engine-benchmark`, run 2026-10-25, "ubuntu", no
  hardware given) has boa 215, quickjs 1201, quickjs-ng 646, kiesel 374, duktape 503, rquickjs 759,
  and lists Nova as unsupported (`documented`). Its QuickJS-NG figure is far below mine
  (974–983). I don't know why; the build flags are not stated.

### My measurements (cloud, not a gate)

Builds:
- QuickJS (Bellard) `7d1c13c`: `make qjs`, gcc 13.3 `-O2`.
- QuickJS-NG `7a23d8e`: `make` (CMake Release).
- quickjs-oxide `3a9ceb9`: `cargo build --release` (fat LTO, one codegen unit) on rustc 1.97.0.
  It declares `rust-version = "1.99"` and uses one 1.99 API (`fmt::NumBuffer` in
  `src/engine/value/primitive.rs:629`). In a scratch copy I replaced it with
  `value.to_string().into_bytes()`. Nothing else needed changing (`measured`).

**bench-v8** (`combined.js` from boa-dev/data), two rounds run in turn, one engine at a time:

| Engine | Round 1 | Round 2 | Richards | DeltaBlue | Crypto | RayTrace | EarleyBoyer | RegExp | Splay | NavierStokes |
|---|---|---|---|---|---|---|---|---|---|---|
| quickjs-oxide | 1054 | 1139 | 810 | 757 | 1101 | 1460 | 2274 | 310 | 2532 | 1616 |
| QuickJS (Bellard) | 1149 | 1144 | 828 | 668 | 1003 | 1285 | 1877 | 379 | 2619 | 2209 |
| QuickJS-NG | 983 | 974 | 598 | 694 | 848 | 1252 | 1696 | 323 | 1882 | 1790 |

The per-benchmark columns are round 2. quickjs-oxide is within noise of Bellard's QuickJS and about
15% ahead of QuickJS-NG. It is weaker on NavierStokes (floats) and RegExp (`measured`). This
matches its README: 0.94× QuickJS's time over V8 v7, 1.15× over Octane, 0.99× over AWFY
(`documented`, README `@3a9ceb9`).

**Svelte 5.57.2 reactivity.** I wrote a small entry
(`/tmp/claude-0/-home-user/db6d0663-2392-5b80-95a4-80368735124c/scratchpad/bench/rust-engines/sig/nm/entry.js`,
bundle `…/sig/w-sig.js`; not committed)
that drives `svelte/internal/client` directly:
- *signals*: 200 `$.state` sources, 200 `$.derived`, one derived sum, one `$.render_effect`, then
  3,000 rounds of 20 `$.set` and a `$.flush()`;
- *proxy*: a `$.proxy` array of 200 `{id,text,done}` rows read by an effect, then 1,500 rounds of
  two mutations and a flush.

I bundled it with `bun build --format=iife --conditions=browser --conditions=production` and added
a `queueMicrotask` shim for QuickJS. The checksums match across all engines.

| Engine | signals ms (3 runs) | proxy ms (3 runs) |
|---|---|---|
| quickjs-oxide | 663 / 989 / 881 | 2365 / 2147 / 2362 |
| QuickJS (Bellard) | 447 / 458 / 439 | 803 / 842 / 698 |
| QuickJS-NG | 478 / 432 / 462 | 959 / 811 / 851 |
| node 22.22.0 | 113 | 318 |
| Boa 0.22 (`64c209f`, `cargo build --release --bin boa`) | 3006 / 2997 | 8278 / 9142 |

quickjs-oxide is 1.5 to 2.2 times slower than QuickJS on signals and about 2.8 times slower on
proxy. Boa is about 7 times slower on signals and 10 to 11 times slower on proxy (`measured`). For
Boa that is worse than its 5× gap on bench-v8. The cause is not profiled. Candidates are its `Proxy` path, closure and
`Map`/`Set` costs, and GC frequency (`hypothesis`). This is the workload the GUI loop runs (svelters
`docs/notes/quickjs-jit-cranelift.md`: `JS_CallInternal` 71% self time on stock qjs), so it, not
bench-v8, should gate any engine choice (`inferred`).

### quickjs-oxide (pocket-nexus/quickjs-oxide @ 3a9ceb9)

- **What it is:** "A JavaScript engine written entirely in safe Rust, with zero `unsafe`, that
  targets the behavior of QuickJS 2026-06-04" (`documented`, README). `unsafe_code = "forbid"`
  appears at `Cargo.toml:88` and `#![forbid(unsafe_code)]` at `core/src/lib.rs:6`. The only
  `unsafe` lines in the tree are in a benchmark probe allocator (`source-observed`).
- **Who and how:** 924 commits since 2026-07-11, mostly by two people (659 and 261 commits). 152
  commit messages carry `Co-Authored-By: Claude …` trailers. The repo has `CLAUDE.md` and
  `AGENTS.md` (`source-observed`, blobless clone). 126 crates.io downloads (`documented`,
  crates.io API).
- **Not a transliteration.** The front end parses into a `FunctionIr` tree and emits
  "QuickJS-shaped stack code", resolving identifiers "as QuickJS's `resolve_scope_var` does". A
  second pass then lowers that stack code to 8-byte register instructions over a 256-register
  window. The stack depth serves as the register allocator (`documented`, `docs/architecture.md`).
  It keeps QuickJS's stack bytecode as an intermediate form, so a Ladybird-style differential check
  of bytecode against qjs is possible at that level (`inferred`).
- **Heap:** `Value` is a `#[repr(transparent)] u64`, NaN-boxed, whose payloads are 32-bit arena
  indices (`ObjectId`, `StringId`, …). These are newtypes "without a generation. A stale index is a
  logic error; the rooting rule, the debug liveness assertions and the stress mode are what catch
  it." Records sit in nine typed `Arena<T>`s (a `Vec` plus live and mark bitmaps). Property values
  and elements share one `ValueHeap` of size-classed ranges (`documented`, `docs/heap.md`). It has
  shapes with a transition tree, validity cells guarding prototype chains, and 16-byte inline
  caches.
- **Rooting rule:** "An allocating call … passes exactly one safepoint and roots its own arguments
  for that collection. Allocation takes `&mut Heap`, so a borrow of a record cannot survive a
  safepoint; an index can. Any index or heap `Value` held in a Rust local across a second
  allocating call must first be pushed on the root stack, held in a handle, or stored in a rooted
  object." The `gc-stress` feature collects before every allocating call, quarantines freed slots
  and verifies every edge after each collection (`documented`, `docs/heap.md`).
- **Deliberate departures:** `with` is a `SyntaxError`, nothing reads `Symbol.unscopables`, direct
  `eval` runs as indirect eval, a runtime has one realm, and nothing reads `Symbol.species`. It
  has no `std`/`os` modules, `Worker`, `qjsc` or C API (`documented`, README).
- **Gaps for us:** one realm per runtime and no workers mean the isolate story is still to be
  built. MSRV 1.99 is above the user's 1.95 (`rust-version`) and 1.97 (toolchain). Its proxy and
  signals speed trails QuickJS (above).

### The other "QuickJS in Rust" projects (2026)

- **quickrs-core** (`eugenehp/quickjs-rs @ 2e203eb`, crates.io `quickrs-core` 0.16.2,
  2026-09-22). A one-commit tree claiming to be "a pure-Rust reimplementation of the QuickJS-ng
  JavaScript engine (v0.16.2)". It has a drop-in C ABI ("All 264 `JS_EXTERN` entry points are
  exported", api-test.c 39/39). It passes upstream's 86/86 tests, and test262 is "not measured"
  (`documented`, README, PARITY.md).
  - Its GC: "`Gc<T>` is an `Rc`-backed handle … plus a trial-deletion cycle collector." Each cell
    wraps its value in a `Lock<T>` with `borrow()`/`borrow_mut()`. The cell type is `RefCell`, or
    atomics under the `sync` feature (`source-observed`, `crates/quickrs-core/src/gc.rs:1-110`).
  - `Value` is a 16-byte Rust enum (`value.rs:21`).
  - This is the "safe faithful port" shape: QuickJS's refcounting mapped to `Rc`, plus run-time
    borrow checks on every object access. No speed comparison with C is published.
- **Lewin671/quickjs-rust** (`@6663738`, 2026-10-23). Despite the name it is "a new design
  rather than a port". It reports 42,672/42,672 on the test262 subset QuickJS-NG's config runs,
  with 10,900 skipped (`documented`, `docs/conformance/burndown.jsonl`). "`ObjectRef` is
  `Rc<ObjectData>` … There is no tracing collector: a reference cycle is never freed"
  (`documented`, `docs/architecture.md`). That rules it out for a long-running GUI.
- **RustPorts** (rustports.org) lists a function-for-function `quickjs` port and calls matching
  "a tightly-tuned C engine like quickjs … the harder target and an open challenge". Its
  `github.com/rustports/quickjs` repository could not be cloned (authentication prompt, so private
  or missing). No numbers were found (`documented`; repo not found).
- **c2rust translations of QuickJS:** not found. Searches for "c2rust quickjs" returned none.
  Academic LLM translators top out far below QuickJS's size: Rustine's largest subject is 13,200
  LoC with a 93.9% assertion pass rate (arXiv 2511.20617, 2025-11-25). C2RustXW reports 78.87%
  average semantic correctness (arXiv 2603.28686, 2026-03-30) (`documented`).

### Boa (boa-dev/boa @ 64c209f, v0.22.0)

- **Size and licence:** 236k lines across `core/*`. `boa_engine` alone is 157,552. Version 0.22.0,
  edition 2024, `rust-version = "1.95.0"`, MIT or Unlicense (`source-observed`, `Cargo.toml`). It
  has 3,956 commits since 2018-08 and 318 authors, 91 in the last 12 months. Its tags are v0.21
  (2025-10-21) and v0.22 (2026-08-28) (`source-observed`, blobless history).
- **VM:** bytecode with a register file kept in the frame's stack window (`get_register`/
  `set_register` relative to `rp`, `core/engine/src/vm/mod.rs`). It has inline caches
  (`vm/inline_cache`). `JsValue` is NaN-boxed by default, with a `jsvalue-enum` feature for the
  old enum (`core/engine/src/value/inner.rs:5-10`) (`source-observed`).
- **GC (`boa_gc`):** a "mark-sweep garbage collector that implements a `Trace` and `Finalize`
  trait". The heap is a thread-local `BOA_GC` (`core/gc/src/lib.rs`). Roots are found the rust-gc
  way. Each `GcHeader` has a `ref_count` and a `non_root_count`. A collection first traces
  heap-internal references to count non-roots, so any box with `ref_count > non_root_count` is a
  root (`core/gc/src/internals/gc_header.rs`). It needs no rooting API, but every `Gc` clone or
  drop touches a counter, and the GC is per thread (`source-observed`).
- **unsafe:** 618 lines in `core`: 293 in the engine, 180 in the GC, 96 in strings
  (`source-observed`).
- **Parser:** its own `boa_parser` (29.6k lines). Boa's `temporal_rs` crate, extracted from Boa,
  powers Chrome 144's Temporal through Diplomat bindings (`documented`, STL Rust meetup abstract,
  meetup.com/stl-rust/events/314701905). That makes Boa's ecosystem crates (temporal_rs, plus
  ICU4X) reusable by a port even if Boa itself is not.

### Nova (trynova/nova @ 877ef16, nova_vm 1.0.0)

- **Status:** 1.0 on 2026-03-15. "Entering the major release era" with SemVer, but "we do not aim
  to give LTS-like API stability guarantees" (`documented`, trynova.dev/blog/nova-1.0). Its README
  says: "The engine performance is acceptable, but it is not fast by any means". Arrays have no
  sparse storage. RegExp lacks lookaround and backreferences, and "is not compliant". There is no
  Promise subclassing (`documented`, README). test262 is 76.93% overall, 95.52% language and
  70.61% built-ins (test262.fyi). MPL-2.0 (`LICENSE.md`). It pins toolchain 1.99.1
  (`rust-toolchain.toml`). It has 38 authors, 15 in the last 12 months, most of them one person
  (`source-observed`). It was funded by NLnet NGI Zero Core (`documented`).
- **Data-oriented heap:** "homogenous arenas with hot data split apart from cold data, and
  optional data stored behind keyed indirections using the arena's associated 32-bit handle as the
  key … The arenas are additionally compacted during garbage collection" (`documented`, README,
  ARCHITECTURE.md).
  - `Value` is an ordinary Rust enum, asserted to be one word
    (`ecmascript/types/language/value.rs:398`).
  - Doubles whose low byte is zero are stored inline as a 7-byte `SmallF64`
    (`number/small_f64.rs:14`). Other doubles are `HeapNumber` handles, so float-heavy code
    allocates (`source-observed`).
  - It uses a stack-based bytecode VM (`documented`, ARCHITECTURE.md).
  - The only pointer passed around is `&mut Agent`.
- **The rooting API**, which is the most relevant prior art. Source: `GARBAGE_COLLECTOR.md`,
  `nova_vm/src/engine/context.rs`, `engine/rootable/scoped.rs`.
  - Handles are *unrooted*: "when the garbage collector runs, they are invalidated". Compaction
    moves records, so a handle the GC cannot reach points at the wrong slot afterwards
    (`documented`).
  - `GcScope<'a, 'b>` is a ZST holding `PhantomData<&'a mut GcToken>` and `PhantomData<&'b
    ScopeToken>`. Calling anything that may GC or run JS takes `gc.reborrow()`, an exclusive
    reborrow, which invalidates every `Value<'gc>` bound to the old borrow. `gc.nogc()` gives a
    shared `NoGcScope` for code that cannot GC (`source-observed`, `context.rs`).
  - To keep a value across a GC point, you `value.scope(agent, gc.nogc())`. That pushes it onto
    `agent.stack_refs` (a `RefCell<Vec<HeapRootData>>`) and returns a `Scoped<'scope, T>` index.
    Scoping allocates ("always includes allocating new heap space (though this is amortized)"), and
    duplicates are not deduplicated (`documented`, `source-observed`).
  - Rules every contributor follows: bind all parameters at entry, unbind only at the call site,
    rebind every return value with `.unbind().bind(gc.nogc())`, never unbind into a local, and
    "when in doubt, scope!" (`documented`). `Bindable` is an `unsafe trait` implemented with
    `transmute` between lifetimes (`context.rs:229-258`).
  - Cost in the tree: 4,246 `.unbind()`, 2,845 `.bind(gc|nogc…)`, 923 `.scope(agent…)` and 1,406
    `gc.reborrow()` calls in 159k lines. That is about one GC annotation every 17 lines
    (`measured`, grep `@877ef16`). Nova also ships `nova_lint`, a dylint crate, to enforce "the
    requirements of the engine, more specifically the garbage collector" (`source-observed`).
  - The author's own verdict:
    - Reborrowing is "by far the most complicated and hard-to-grasp parts of the codebase"
      (`documented`, trynova.dev/blog/worked-for-the-internet).
    - In 2026 he argued that handles should be *contravariant* ("write-only" until proven valid),
      which removes about 800 bind/unbind sites. He calls the result "profoundly un-Rusty",
      "unsound or incomplete" today, and backed by "runtime validity checks". A mistake "can cause
      a bounds-check panic or silently swap one JavaScript value for another of the same type"
      (`documented`, trynova.dev/blog/garbage-collection-is-contrarian, 2026-01-09).
    - He names `gc-arena` (invariant branded lifetimes, fully safe) as the alternative. It
      requires a stackless interpreter and blocks mutable heap access (`documented`, same post).
- **unsafe:** 1,133 lines in `nova_vm`. The densest are typed arrays and shared data blocks
  (238), `data_block.rs` (86) and `script.rs` (55) (`source-observed`). The script code
  transmutes oxc AST lifetimes to `'static` and relies on "Caller promises that SourceTextModule
  is rooted" (`script.rs:60-75`, `source_code.rs:82-122`). The front end is oxc (below).
- **Users:** Andromeda (tryandromeda/andromeda, MPL-2.0) is a Deno-like runtime "powered by Nova
  Engine and Oxc". It pins `nova_vm` by git rev (`source-observed`, `Cargo.toml:44`).

### Brimstone, yavashark, Starlight, jsparagus

- **Brimstone** (Hans-Halverson/brimstone @ 64d6698, MIT, 2025):
  - "Bytecode VM, heavily inspired by the design of V8's Ignition. Compacting garbage collector,
    written in *very* unsafe Rust. Custom RegExp engine. Custom parser." It covers ES2026 and
    Temporal, everything except SharedArrayBuffer and Atomics, and claims ">97% of the ECMAScript
    language in test262". "Not ready for use in production" (`documented`, README).
  - Its GC is "A Cheney-style semispace garbage collector". `Handle<T>` points into a
    `HandleScope` block, like V8 (`source-observed`, `src/js/runtime/gc/garbage_collector.rs`,
    `handle.rs:27`).
  - 1,319 commits, 360 in the last year, all by one author (`source-observed`). This is the
    V8-handle-scope alternative to Nova's design. It is correct only by discipline, but keeps
    pointer-speed access.
- **yavashark** (@42e4266, MIT): 260k lines and 2 authors. It uses swc
  (`yavashark_swc_validator`). "~81% of the entire test262 suite". Its focus is now speed, with
  NaN-boxing and new object-model proofs of concept (`documented`, README).
- **Starlight** (Starlight-JS/starlight @ 503e789, last commit 2021-10-31) is dead. It aimed at
  JIT-grade speed and "not … 100% safety" (`documented`).
- **jsparagus** (mozilla-spidermonkey/jsparagus @ e14ee38) was Mozilla's Rust parser and emitter
  meant to replace SpiderMonkey's front end. "Not on crates.io yet. The AST design is not stable
  enough" (`documented`). The daily job stopped on 2025-01-10, and there were 27 commits after
  2022 (`source-observed`). It is dormant, so don't build on it.
- **Kiesel** is written in Zig (`documented`, codeberg.org/kiesel-js/kiesel), not Rust. It uses
  bdwgc, and needs Rust only for Intl and Temporal. It is listed for its speed and conformance
  numbers only.

### Reusable Rust front ends

- **svelters does not depend on oxc.** The brief says it depends on `oxc_resolver`, but at
  `0aa4169` the workspace has no oxc crate. `crates/svelters-modules/Cargo.toml:18` says it
  resolves "with Node's ESM rules (not oxc_resolver: it forces serde_json's `preserve_order` on
  every crate in the build)". Only a probe script, `docs/notes/scripts/qjs-loader-probe`, uses
  `oxc_resolver = "11"`. svelters' JS parser is its own acorn port, `crates/svelters-compiler/src/
  acorn`, 16,657 lines (`source-observed`).
- **Who uses what:**
  - Nova: `oxc_parser`, `oxc_ast`, `oxc_semantic`, `oxc_ecmascript`, `oxc_regular_expression`,
    all pinned `=0.124.0`.
  - Boa: its own parser.
  - Brimstone: custom.
  - yavashark: swc.
  - quickjs-oxide and quickrs-core: their own, modelled on QuickJS.

  (`source-observed`.) oxc is now at 0.155.0 (2026-10-19), still pre-1.0 with frequent breaking
  releases: Nova lags 31 minor versions. swc_ecma_parser is at 46.1.2. biome_js_parser was last
  published to crates.io in 2024-03 (0.5.7) (`documented`, crates.io API).
- **Can a port use oxc and only emit bytecode?** Yes. Nova does exactly that (`source-observed`).
  The costs, from Nova's code:
  - The oxc AST has to outlive lazy function compilation. Nova keeps it alive with `unsafe`
    lifetime transmutes (`script.rs`, `source_code.rs`).
  - An arena AST costs memory that QuickJS's one-pass parser never allocates.
  - oxc's semantic model is not QuickJS's scope model. QuickJS's closure-variable resolution,
    Annex B function hoisting and direct-eval scopes would have to be re-derived over it
    (`inferred`).
  - QuickJS's front end runs from the tokenizer (`next_token`, `quickjs.c:23099`) through
    `js_parse_program` (`:37990`), including `resolve_variables`/`resolve_labels`/`ss_check`.
    That is roughly lines 22,600 to 38,200, about 15k lines or 24% of `quickjs.c @ 7a23d8e`
    (`source-observed`, approximate span).

  For a faithful port, translating that span keeps bytecode byte-identical with qjs, which is
  Ladybird's oracle (below). oxc would cost us that oracle in exchange for skipping about 15k lines
  that an AI-assisted port does quickly (`inferred`). The svelters compiler can keep its acorn
  port. The engine front end doesn't need to share it.

### Ports of large codebases to Rust, and their lessons

- **Bun, Zig to Rust (2026)** (`documented`, bun.sh/blog/bun-in-rust, 2026-07-08):
  - Scale: 535,496 lines of Zig. 11 days (May 3 to merged May 14), 6,778 commits, about 64
    Claude agents at peak, about $165,000 of tokens.
  - Method: "Do the rewrite that looks like we transpiled our Zig code to Rust." It was all at
    once, not incremental. A `PORTING.md` maps Zig patterns to Rust. A `LIFETIMES.tsv` gives a
    proposed lifetime for every struct field, reviewed adversarially. Each implementer had two
    adversarial reviewers in separate contexts. The compiler errors served as the work queue.
  - The oracle was the language-independent TypeScript test suite: 60,624 tests and 1.39M
    `expect()` calls, with "0 tests skipped or deleted".
  - Results:
    - 19 known regressions, all "code that's syntactically identical in both languages but
      semantically different": a side effect inside `debug_assert!`, slice-cast panics, bounds
      checks that ReleaseFast had dropped, and `comptime` format strings.
    - 128 bugs fixed; memory leaks closed through `Drop`.
    - About 4% of the code is in `unsafe` blocks, 78% of them one line.
    - 2 to 5% faster; binaries about 20% smaller.
- **Ladybird LibJS front end, C++ to Rust (2026)** (`documented`,
  ladybird.org/posts/adopting-rust, 2026-02-23):
  - Scope: lexer, parser, AST and bytecode generator, about 25,000 lines in about two weeks with
    Claude Code and Codex, "human-directed, not autonomous". The output has "a strong 'translated
    from C++' vibe" by design.
  - Verification: test262 52,898 with 0 regressions, the Ladybird suite 12,461 with 0
    regressions, every AST identical, all bytecode identical "byte-for-byte". Lockstep runs of
    both pipelines happened while browsing.
  - The VM and GC were not ported. In 2024 they had rejected Rust because the GC'd, deep-inheritance
    web object model fits Rust's ownership model poorly. Later newsletters report a Rust regex
    engine and Rust style and layout, but no GC port (`documented`, ladybird.org newsletters
    2026-03 and 2026-07; GC port not found).
- **fish 4.0, C++ to Rust** (`documented`, fishshell.com/blog/rustport):
  - Scale: 57K lines of C++ became 75K lines of Rust, by hand and incremental ("Fish of Theseus").
    autocxx glue had to be forked for `wstring`. The PR ran 2023-01-28 to 2023-02-19, the last
    C++ was removed in 2024-01, and 4.0 shipped 2025-02. They call it "14 months" of port and
    "almost 2 years of work".
  - Lessons: keep a working program at every step; `.unwrap()` and asserts caused many bugs.
    Speed is "usually slightly better", with memory at about 8M at rest against 7M.
- **rav1d, C to Rust via c2rust** (`documented`, memorysafety.org/blog/porting-c-to-rust-for-av1,
  memorysafety.org/blog/rav1d-perf-bounty):
  - Method: c2rust first, "functionally equivalent but unsafe Rust", then a manual refactor.
    "more than 20 person-months of effort with a team of 3" against an estimate of about 7.
  - Lessons: Rust's thread-safety model was "overly strict" for dav1d's design. "Pointers were
    refactored into integer indices", context structs were split to remove back-pointers, and
    unions became tagged enums.
  - Speed: about 6% slower, then "about 5% slower". A $20k bounty for parity ran from 2025-05-14
    and the program "has concluded" (2025-12-31). No parity result was found.
- **c2rust itself** is active: `immunant/c2rust @ 820a940` (2026-10-22), v0.22.1, 1,354 commits in
  the last 12 months (`source-observed`). Its output "closely mirrors the input C code"; generating
  safe, idiomatic Rust "ultimately requires manual effort" (`documented`, c2rust docs). DARPA's
  TRACTOR program (since 2024-08) funds LLM-plus-analysis C-to-Rust work (`documented`, secondary
  press). For QuickJS the hard part is exactly the part c2rust leaves alone: refcounting through
  `JS_FreeValue`, intrusive `list_head` lists, raw back-pointers, `computed goto` dispatch (`inferred`).
- **tsgo, TypeScript to Go** (`documented`, github.com/microsoft/typescript-go/discussions/411,
  Ryan Cavanaugh, 2025-03-05):
  - They kept the codebase "as compatible as possible" with the original, because both will be
    maintained for a long time. It is "a port that maintains the existing behavior".
  - Languages that demand rethinking memory "might be a better fit for a ground-up rewrite". Go's
    GC "nets us a very big win in reducing codebase complexity".
  - The lesson for us: a faithful port is cheapest when the target language's memory model
    matches the source's. Rust does not match QuickJS's refcounted, pointer-graph heap, so the
    heap is the one part that cannot be ported line for line (`inferred`).

**Lessons that apply to a QuickJS port (`inferred` from the above):**
1. **Use an oracle that doesn't care about the implementation language.** For us that is test262
   under QuickJS's `test262.conf`, QuickJS's `tests/`, and svelters' own suite. Ladybird adds
   byte-identical bytecode, which a structural port of the front end keeps.
2. **Port all at once with AI workflows,** not incrementally behind FFI. fish's incremental route
   took 14 months and needed forked binding tools. Bun's all-at-once route took 11 days, and
   quickjs-oxide took about 3.5 months. QuickJS is about 87k lines of C in total
   (quickrs PARITY.md: `quickjs.c` 64,943, libc 5,057, libregexp 3,522, libunicode 2,074,
   dtoa 1,619), a sixth of Bun's size.
3. **Write the memory-model mapping first,** as Bun's `PORTING.md`/`LIFETIMES.tsv`. For QuickJS
   that mapping *is* the architecture, so it must be designed, not transliterated (rav1d, tsgo).
4. **Expect regressions where the two languages read the same but mean different things:**
   `debug_assert!`, bounds checks, overflow, `wchar`. Grep for them in review (Bun, fish).
5. **c2rust buys a compiling start and nothing on the hard part.** rav1d's refactor tripled its
   estimate and still left a 5% gap.

## Recommendation

**Do an AI-assisted structural port of QuickJS's semantics and front end, onto a new arena heap.
Decide in week one whether to fork quickjs-oxide instead of starting empty.**

| Option | Verdict | Why (evidence above) |
|---|---|---|
| c2rust, then refactor | **No** | Output is unsafe C in Rust. The refactor is the whole job. For rav1d it took 3× the estimate and stayed 5% slower. QuickJS's refcount and pointer heap is exactly what must change. |
| Faithful hand port, refcounts kept (`Rc` + `RefCell`, or raw pointers + `Drop`) | **No** for the heap | quickrs-core shows the safe version: a borrow check on every object access and a 16-byte `Value`. Keeping raw pointers keeps C's bug class. Neither makes shared isolates or a JIT easier. |
| Structural port: QuickJS front end, bytecode, opcode semantics and builtins function by function, over a new arena/index heap with a tracing GC | **Yes** | It keeps the oracles (test262 at QuickJS's config, byte-identical stack bytecode against qjs, upstream `tests/`). quickjs-oxide shows the heap shape reaches QuickJS speed on bench-v8 in 100% safe Rust. Bun and Ladybird show AI workflows port code of this size in weeks. |
| Fork quickjs-oxide | **Evaluate first** | It is already about this design, MIT, and AI-maintained. Against it: 2-person bus factor, 5 spec departures, no realms or workers, MSRV 1.99, and 1.5–2.8× slower than QuickJS on Svelte reactivity. |
| Adopt or fork Boa | No | About 5× slower than QuickJS on bench-v8 (345 vs 1765), 7–11× on the Svelte bench (measured), and a thread-local `Gc` heap that is hard to share across isolates. Its crates (temporal_rs, ICU4X glue) are still worth reusing. |
| Adopt or fork Nova | No | 76.9% test262, a non-compliant RegExp, "not fast", MPL-2.0, toolchain 1.99.1, and a rooting discipline costing one annotation every 17 lines that its author calls unsound today. Reuse its ideas, not its code. |

What each choice means for the svelters goals:

- **Native node values.** presto nodes as JS objects without serialization need an object kind with
  a host payload, and a heap the host can address cheaply.
  - Arena engines (structural port, oxide, Nova) give every object a `u32` id. A presto node can
    hold an `ObjectId` and the engine can hold a node id, with no refcount traffic across the
    boundary (`inferred`).
  - Boa's `Gc<T>` makes every host-held reference a root counter (`source-observed`, rooting
    above).
  - QuickJS-NG today needs `JS_DupValue`/`JS_FreeValue` around every held value.
- **Engine-level signals.** Dependency tracking inside property access or a `$state` cell kind
  means editing the interpreter's hottest paths. That needs an engine we own and understand. A
  structural port or an oxide fork gives that. Nova and Boa would be forks of someone else's
  fast-moving engine (`inferred`).
- **Shared isolates.** A per-heap index space makes cross-isolate *object* sharing a copy, as
  structured clone already is. It also makes it natural to share the immutable parts: bytecode,
  atoms, shapes, compiled regexps. Those are segments addressed by index, put behind `Arc`.
  - quickjs-oxide already keeps "the static code units, their position tables and source text"
    in a `CodeStore` beside the heap (`documented`, `docs/heap.md`). It holds them through `Rc`,
    so sharing needs `Arc` (`inferred`).
  - QuickJS's design is the hardest to share: atomic refcounts would be needed on every value
    (`inferred`).
- **A JIT later.** NaN-boxed `u64` with 32-bit payloads matches what V8 calls pointer compression.
  JIT code loads an arena base once and addresses by index (`inferred`). A register VM (oxide) is
  a better JIT input than a stack VM (QuickJS, Nova) (`inferred`).

  Arenas are `Vec`s that move when they grow, so JIT code must reload bases after any allocation
  (`inferred`). The JIT crate would hold the project's `unsafe`, isolated like Bun's FFI blocks.

  svelters' Cranelift measurement says a baseline JIT gives roughly nothing on Svelte signals
  (`documented`, `docs/notes/quickjs-jit-cranelift.md`). The heap design, ICs and proxy and
  closure paths therefore matter more than the JIT for the GUI loop (`inferred`).

**How to root without Nova's cost:** use quickjs-oxide's rule. A single `&mut Heap` means a
borrow of a record cannot cross a safepoint. Indices held across a second allocation go on an
explicit root stack. A `gc-stress` feature collects at every allocation and verifies every edge.
Add a debug-only generation tag to ids, so a stale index panics instead of aliasing (`inferred`;
Nova's author lists generational handles as his own next check). Reserve lifetime-branded handles
in the Nova style for the public embedding API, where misuse is likeliest and annotations are few
(`hypothesis`).

## Open questions for the user

1. **Fork quickjs-oxide, or port onto our own heap?** Recommendation: a one-week bake-off before
   deciding. Fork if oxide reaches QuickJS-NG speed on the Svelte signals and proxy runs after a
   profiling pass, and its embedding API can carry presto host objects. Otherwise do the
   structural port and keep oxide as the reference design and benchmark. Either way, read its
   `docs/heap.md` before designing ours.
2. **Which upstream do we port: QuickJS-NG 0.16.2 (svelters' pin) or Bellard 2026-06-04?**
   Bellard's is about 15% faster on bench-v8 in my runs and has Temporal and Intl (+77k lines). NG
   has the API svelters uses. Recommendation: NG's semantics and API for the svelters
   integration. Take Bellard's 2026 performance changes (small-block allocator, micro-optimizations)
   as a later list.
3. **Is zero `unsafe` a requirement, or "unsafe only in named crates"?** Recommendation: forbid it
   in the engine core as oxide does, and allow it in the JIT and FFI crates only. Bun's 4% is an FFI
   artefact we don't have.
4. **Licence.** QuickJS, oxide, Boa and Brimstone are MIT; Nova is MPL-2.0. Recommendation: MIT,
   which keeps the oxide-fork and port-from-QuickJS options open.
5. **Name.** `hocus` is free on crates.io but shares a namespace with hocus-dev (archived, 3.2k
   stars) and hocuspocus (an active Y.js server). Recommendation: keep `hocus` if the JS
   overlap doesn't bother you. Otherwise use `alakazam` (magic-word family, free crate) or
   `praestigiae` (Latin family, free crate).
6. **MSRV.** oxide and Nova need Rust 1.99. Recommendation: keep `rust-version = "1.95"` for hocus
   and patch around newer APIs, as I did with one line for oxide.

## First steps in this repository

1. `hocus/bench/`: check in the bench-v8 `combined.js` runner and the Svelte reactivity bench from
   this note (`entry.js` plus the bundling command). Run qjs-ng, Bellard qjs and quickjs-oxide.
   Record results with sha, machine and "cloud, not a gate". This is the gate for every later
   decision.
2. Profile quickjs-oxide on the proxy and signals runs (`perf record`, with its `--ic-stats` and
   `--gc-stats`), to learn whether the 2–3× gap is a local fix or the design. One or two days.
3. Write `docs/design/porting.md` (Bun's `PORTING.md` idea). It should map QuickJS's `JSValue`,
   `JS_FreeValue`, `JSObject`/`JSShape`, atoms, `list_head` and `JSFunctionBytecode` onto the arena
   heap, with the rooting rule and stress mode. Also write a `LIFETIMES.tsv` covering every
   `JSRuntime`/`JSContext` field.
4. Set up the oracles before any engine code:
   - test262 at QuickJS-NG's `test262.conf`;
   - upstream `tests/`;
   - a qjs bytecode-dump differ for the front end (`qjs -d`-style dumps from both, compared
     function by function).
5. Then port in order: front end and bytecode (Ladybird's byte-identical gate), interpreter loop,
   objects/shapes/ICs, builtins by directory. Each step gates on the oracles and the two benches.

## Appendix: the Svelte reactivity bench entry

Bundle: `npm install svelte@5.57.2`, then `bun build entry.js --format=iife --target=browser --conditions=browser --conditions=production --minify-syntax`. Prepend `var print = globalThis.print || ((s) => console.log(s));` and a `queueMicrotask` shim (`(f) => Promise.resolve().then(f)`) for engines that lack it.

```js
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
```
