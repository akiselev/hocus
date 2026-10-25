# What an engine we own enables for svelters and presto, and the integration design

Researcher note for the hocus study (`../BRIEF.md`). Date: 2026-10-25. Mostly local code reading;
nothing was built or benchmarked for this note.

**Sources, at these shas** (all `file:line` references below are at them):

| Tree | Sha / version | Short name |
|---|---|---|
| svelters | `0aa4169` (master, 2026-10-25) | `sv:` |
| cadatomic | `75b6a47` | `ca:` |
| QuickJS-NG | `7a23d8e` (master, 2026-10-24; **not** the 0.16.2 svelters ships) | `qjs:` |
| svelte | 5.57.2, the npm tarball's `src/` (fetched with `npm pack svelte@5.57.2` into the scratchpad) | `svelte:` |

Short paths: `host/` = `sv:crates/svelters-host/src/`, `nr/` = `sv:packages/native-runtime/src/`,
`gui/` = `ca:docs/research/2026-10-25-gui-toolkit/`, `r2/` =
`ca:docs/research/notes/2026-10-25-own-gui-toolkit/round2/`.

Grades: `documented`, `source-observed`, `measured` (by someone, with where), `inferred`,
`hypothesis` (BRIEF.md). All timings quoted here come from earlier notes. Most were taken on heavily
loaded machines (load average 11–33), so trust ratios more than absolute numbers. None is a gate.

---

## 0. Summary and recommendation

**Findings**

1. **The boundary is not where the time goes.**
   - In P5's 1000-row fill, QuickJS spends 1,479 ms in JS. Host/ABI work is 16 ms and toolkit calls
     are 423 ms (`measured`, `sv:docs/notes/p5-integration.md:76`).
   - A perfect FFI saves about 0.5 µs per op at most (`inferred`, `gui/compiler-and-js.md:41-48`).
     Over 11,000 ops that is about 5 ms of the 1,479.
   - So the FFI-shaped wins an owned engine offers are each worth under 1% of measured JS time:
     native node values (§2a), compiler-emitted bytecode (§2d) and Intl (§2j). They also have cheap
     QuickJS equivalents already planned (W-C1, W-C3, W-J1).
2. **What is slow is the Svelte runtime running in an interpreter.**
   - Real Svelte 5 signals run about 20x faster on V8 than on QuickJS-NG 0.16.2, app glue about 27x
     and `$state` proxies about 6x (`measured`, loaded machine,
     `sv:docs/notes/quickjs-jit-cranelift.md:299-313`).
   - `perf` on the signals workload shows `JS_CallInternal` at 71% self time: frame setup and
     dispatch for many tiny calls (`measured`, same note `:316`).
   - Batched, one state change costs 5.5–7 µs (`measured`, `sv:plans/research/REACTIVITY.md:686`).
3. **What only an engine we own can do**, ranked by expected value for this app:
   1. **Reactive objects without `Proxy` (§2c).** `$state` objects and arrays get a header bit, and
      reads record dependencies on the engine's own property fast path. No trap call and no
      invariant re-check. This targets w-proxy, where QuickJS is 6.2x behind V8.
   2. **An intrinsic reactive core (§2b).** `$.get`/`$.set`, dirty marking and the flush run in Rust;
      effect bodies stay JS closures, traced by the GC. This targets the 71% call overhead.
   3. **GC driven by the frame clock (§2e).** Incremental and idle-time collection with a pause
      budget. QuickJS-NG's collector is a synchronous full cycle collection, triggered inside
      whatever allocation crosses the threshold (`qjs:quickjs.c:1680-1697, 7483-7494`).
   4. **Interrupts that unwind (§2f).** This removes the FX6 recovery hack. Microtask lanes and
      time-budgeted drains come with it.
   5. **Freeing replaced module records (§2h).** HMR could then stop forcing a full reload every
      200 swaps.
   6. **A debugger and profiler designed in (§2i)**, including a per-effect profiler keyed by
      compiler source locations.
   7. **Heap snapshots and code shared across isolates (§2g, §2k).** Useful, but small for this app
      today: about 13 ms of startup and about 55 KB of shared bytecode.
4. **Much of item 3.2 can be tried on stock QuickJS first.**
   - RUNTIME.md's own JS core is 2–4x faster than Svelte's signals on qjs for most workloads, and
     10x on write+flush (64 vs 6 µs). On idle trees it is 77x (`measured`,
     `sv:plans/research/RUNTIME.md:54-59`). Phase 2 has not started.
   - A Rust reactive core called through raw `rquickjs-sys` C functions is also possible today.
     QuickJS C functions are cheaper to enter than JS functions (`documented`,
     `r2/zero-serdes-js.md:30-35`).
   - The port's value must be measured against **QuickJS-NG plus those**, not against today's stock
     path.

**Recommendation**

- Treat hocus as a **second engine behind svelters' existing `JsEngine` seam**, never a replacement
  of it:
  - one new adapter in svelters-host, beside `quickjs.rs`;
  - capability-gated intrinsics reached through built-in modules, with JS fallbacks, so that the
    same compiled component JS runs on Node, QuickJS and hocus;
  - a differential test tier that runs svelters' own suites and the 56 op-log goldens on both
    engines.
- Gate the port on a benchmark set in which the bar is "QuickJS-NG + own JS core + raw C ops"
  (§4).
- Before building engine internals, run the cheap experiment **X1** (a Rust reactive core on stock
  QuickJS, §6). It tells us how much of the reactivity win needs the engine at all.

---

## 1. Today's boundary

### 1.1 The `JsEngine` abstraction

- **Where it sits** (`source-observed`):
  - `host/engine.rs:1-7`: an engine-agnostic core (`core.rs`: the handle table, name caches, the
    event queue, timers, checkpoints and op forwarding) talks to one engine through
    `trait JsEngine` (`host/engine.rs:191-309`).
  - `quickjs.rs` is the only implementation.
  - The engine is built by a factory: `Host::new` calls `Host::with_engine(backend, cfg,
    Rc::new(|core| QjsEngine::new(core)))`, and `with_engine` is `pub(crate)` (`host/lib.rs:229-240`).
- **Methods, Rust → JS** (`host/engine.rs:191-309`):

  | Method | Calls into JS | Default |
  |---|---|---|
  | `load_module(provider)` | entry module through a `ModuleProvider` | required |
  | `load_entry` / `mount_root` / `root_call` | EM1 embedding (`__svelters_embed`) | "not supported" error |
  | `hot_update(provider, changed)` | H1 in-place module swap | `Reload` |
  | `dispatch(h, signal, args, deferred, flags)` | `__svelters_dispatch` | required |
  | `dispatch_sync(...) -> Value` | `__svelters_dispatch_sync` | required |
  | `dispatch_changed(batch)` | `__svelters_changed([h, prop, v, ...])` | required |
  | `dispatch_gone(h, reason)` | `__svelters_gone` | required |
  | `run_frame()` | `__svelters_internal.frame` | required |
  | `fire_timer(id)` | `__svelters_internal.fire` | required |
  | `run_jobs(max) -> JobStatus` | microtask drain, count-budgeted | required |
  | `settle_ops(batch)` | E1 promise settlement | error |
  | `release_buffers`, `buffer_stats` | E2 buffers | no-op |
  | `assets_changed`, `locale_changed` | AS1, IN1 | no-op |
  | `collect_garbage`, `take_errors`, `jobs_pending` | — | — |

- **Conversion is written once, over engine traits** (`source-observed`, `host/convert.rs:1-11`):
  `JsReader` (`:54-104`), `JsPromises` (`:105`) and `JsWriter` (`:121`) are implemented for
  QuickJS in `host/quickjs/conv.rs`. Callbacks go through `CallbackEngine`
  (`host/core/callbacks.rs:28-35`).
- **Modules** come through the engine-neutral `ModuleProvider`. Its cached bytecode is tagged by
  engine: `ModuleCode::Cached { engine, bytes }` (`sv:crates/svelters-modules/src/lib.rs:46-51`).
  The bytecode cache stamps the QuickJS-NG version (`host/bytecode.rs:41-55`).
- **Port surface for a second engine** (`source-observed`, `wc -l` over the files that name
  rquickjs):
  - `quickjs.rs` 1,619 lines, `quickjs/{conv,buffers,intl,platform}.rs` 1,340, `bytecode.rs` 341:
    about **3.3k lines**;
  - no other svelters crate names rquickjs (grep over `crates/`).

### 1.2 Every host op

All are rquickjs `Function::new` closures on plain objects (`source-observed`; also
`r2/js-host-local.md` §1.2):

- **Prelude natives,** private after the prelude runs (`host/quickjs.rs:886-982`): `log`,
  `schedule`, `cancel`, `frame_request`, `now`, `cancelAsync`, `cancelOp`, `streamAck`,
  `releaseBoxed`, `queueRelease`.
- **`globalThis.__svelters_host`** (`host/quickjs.rs:990-1442`):
  - tree: `create`, `insert`, `remove`, `destroy`;
  - name resolvers: `kindId`, `propId`, `signalId`, `methodId`, `slotId`, `propType`;
  - properties and calls: `setProp`, `getProp`, `call`, `construct`, `callStatic`, `typeChain`;
  - signals and observation: `listen`, `observe`, `link`, `unlink`;
  - embedding: `rootFault`, `rootInfo`, `rootEmit`;
  - persistence and styling: `persist`, `style`, `bounds`;
  - child attributes: `setChildAttr`, `getChildAttr`;
  - developer services and assets: `dx`, `assets`, `assetBytes`, `alloc`.
- **Platform ops** (`host/quickjs/platform.rs:38-95`): `clipboardWrite`, `clipboardRead`,
  `clipboardFormats`, `shellOpen`.
- **Intl natives** behind the IN1 shim (`host/quickjs/intl.rs:199-`): `current`, `canonicalize`,
  `resolve`, `supported`, `number`, `numberDigits`, `plural`, `pluralCategories`, `compare`, … The
  options cross as JSON (`:1-2`).
- **Entry points the host looks up on `globals()` by name on every call:** `__svelters_dispatch`,
  `__svelters_dispatch_sync`, `__svelters_changed`, `__svelters_gone`, `__svelters_internal`,
  `__svelters_embed`, `__svelters_hot`, `__svelters_assets_changed`, `__svelters_recover` (grep of
  `quickjs.rs`).
- **Not JS ops:** `call_sync` and `request_frame` are backend↔host C ABI members
  (`sv:crates/svelters-abi/src/lib.rs:261-281, 292-352`).

### 1.3 How values cross

- **JS → backend** (`source-observed`, `r2/js-host-local.md` §1.3–§1.4):
  - the JS value is read through `JsReader`;
  - the backend's slot descriptor drives coercion into a borrowed 24-byte `svl_value` tree
    (`svelters-abi/src/lib.rs:190-208`, size asserted);
  - ASCII strings are borrowed in place with `JS_ToCStringLen2`;
  - records are read field by field, two property lookups per field;
  - enums cross as strings and are looked up in a `HashMap`.
- **Handles** are JS numbers below 2^53 (`host/quickjs.rs:828`).
  - In JS, each handle has a `Native` object with about 20 fields and, eagerly, 8 `Map`s, 1 `Set`
    and 1 object (`nr/index.js:84-108`).
  - `$n.set` does a `plainData` proxy copy, two `Map` dedupe checks, and passes the prop as a
    **string** (`nr/index.js:617-646`).
- **Backend → JS:**
  - owned `svelters_abi::Value`s are decoded;
  - a handle in a result becomes a JS call to `__svelters_node(h)`;
  - records become fresh plain objects.
- **Callbacks** are `Persistent<Function>` entries in a `HashMap` (`host/quickjs.rs:92-99`). To the
  cycle collector these are untraced roots (`r2/zero-serdes-js.md` §2).
- **Re-entry** goes through a thread-local raw `JSContext` and a `RunningCtx` guard, because
  `Context::with` is not re-entrant (`host/quickjs.rs:65-89`). Each op upgrades a `Weak<Core>`
  (`:823`).

### 1.4 Measured costs (all quoted; loaded machines)

| What | Cost | Source |
|---|---|---|
| JS loop iteration (bench floor) | 144–189 ns | `sv:docs/notes/js-native-serdes.md` via `r2/js-host-local.md:241-262` |
| Plain JS→JS call in QuickJS-NG | 234–325 ns | `sv:docs/notes/egui-rust-exports.md:152-170` |
| rquickjs no-op native call, 3 args | 837 ns (est. 200–330 ns unloaded over the loop) | js-native-serdes Part B; `r2/zero-serdes-js.md` §2 |
| `setProp` by name / by integer id | 1,433 / 768 ns (2,235 / 1,607 after S1) | `r2/js-host-local.md:245, 263, 310-311` |
| Allocate `{x,y,w,h}` in JS | ~1.2 µs over the loop | js-native-serdes §4.3 |
| Read 4 fields through a `$state`-like `get` trap | +2.5 µs | same |
| Batching into typed arrays | slower than direct calls in QuickJS (1.2–1.4 vs 1.0–1.1 µs per op) | `sv:plans/research/EGUI.md:105` |
| One effect re-run per event / one batched update per event | 172–198 µs / 5.5–7.0 µs | `sv:plans/research/REACTIVITY.md:685-686` |
| Svelte write+flush vs a 150-line own core (qjs) | ~64 vs ~6 µs; 77x on 1000 idle effects | `sv:plans/research/RUNTIME.md:56-59` |
| P5 fill, 11k ops (GTK): JS / host-ABI / toolkit | 1,479 / 16 / 423 ms | `sv:docs/notes/p5-integration.md:76` |
| w-signals / w-proxy / w-appmix / w-props, ms per 10 calls, stock qjs 0.16.2 vs node 22 | 100.2 vs 4.86; 341 vs 55.0; 26.1 vs 0.98; 7.21 vs 0.19 | `sv:docs/notes/quickjs-jit-cranelift.md:299-302` (±25%) |
| Startup | engine 6 ms; 115-module graph 95–120 ms cold, ~13 ms from cached bytecode | `sv:plans/research/QUICKJS-RUNTIME.md:20-21, 163, 275` |

Notes on the table:
- The P5 keyed-bench is a weak engine comparison. Node/V8 through the TestHost took 450 ms to fill
  and 330 ms to update, against QuickJS's 1,479 and 320 ms. The Node figures include op-log
  building, and Svelte's `reconcile` is quadratic on reverse (`p5-integration.md:90`). So P5
  measures Svelte's algorithms as much as the engine (`inferred`).
- Cranelift JIT result: quickjs-jit tied with its own interpreter on Svelte workloads and lost to
  stock NG 0.16.2 by about 25% (`quickjs-jit-cranelift.md:304-312`).

### 1.5 What must stay engine-agnostic

| Must stay | Why / where |
|---|---|
| **The C backend ABI** and `convert.rs` | GTK, Qt, AppKit, WinUI, egui and presto backends never see an engine (`svelters-abi/src/lib.rs`, `convert.rs:11` "Nothing here names an engine type") |
| **`core.rs` checkpoint semantics** | The order is: settle E1, then the change batch, then events with a drain after each, then timers, then a final drain (`host/core.rs:1073-1173`). SCHED1's seeded schedules found ordering bugs here (`:1094-1100`, `:1135-1138`); hocus must reproduce the ordering exactly |
| **Compiled component JS** | "The emitted component JS is identical for both targets today" (`REACTIVITY.md:904`). Intrinsics must sit behind module imports with JS fallbacks, never in codegen that only hocus can run |
| **DOM conformance** | 628/774 on the jsdom DOM host under Node (`gui/compiler-and-js.md:82-89`). It measures compiler+runtime fidelity and stays on Node and Svelte's JS core |
| **Op-log goldens** | 56 `expected.oplog` files (`find`). The Node TestHost, the QuickJS host (`sv:crates/svelters-host/tests/fixtures.rs:1-5`) and the egui/GTK replays all check them. Op logs are "by JS-facing names, never by IDs" (`gui/compiler-and-js.md:117-118`), and hocus must produce them byte-identically |
| **ISO1 isolates** | A serializable op-batch protocol "provided the protocol never relies on shared memory" (`sv:plans/research/ARCH-SECURITY.md:162-165`) |
| **Determinism hooks** | The virtual clock comes through the `now` native (`host/quickjs.rs:923-932`). Seeded `Math.random` is JS (`:42-61`), so it is engine-neutral |

---

## 2. What owning the engine enables that the FFI cannot

Each item gives: today, what hocus could do, the estimated gain on a measured workload, whether
stock QuickJS can get most of it, and the fidelity risk.

### Overview

| # | Capability | Est. gain on measured workloads | On stock QuickJS? | Fidelity risk |
|---|---|---|---|---|
| a | Native node values | <1% of P5 JS time beyond W-C3/W-J1 | mostly (W-C3, W-J1, exotic class) | low |
| b | Intrinsic reactive core | w-signals 5–10x over Svelte's core (hypothesis); 2–5x over an own JS core | partly (X1: Rust core via raw C functions) | **medium**: effect ordering |
| c | Reactive objects, no Proxy | w-proxy 3–6x (hypothesis), V8 parity is 6.2x | **no** (needs a header bit and fast paths in property get/set) | medium: `$state` proxy semantics |
| d | Compiler-emitted bytecode / IR | startup ≤13 ms already; per-op ~intrinsic | mostly (bytecode cache, templates) | high if JS text is dropped |
| e | Frame-clock GC | p99 frame time; ~2% throughput | **no** (only a threshold and `JS_RunGC`) | none |
| f | Unwinding deadlines, microtask lanes | correctness and latency | partly (W-H1); unwinding **no** | low |
| g | Heap snapshots | ≤ ~13 ms per launch / isolate | **no** | low |
| h | HMR module replacement | removes forced reloads every 200 swaps | **no** (module records never freed) | low |
| i | Debugger and profiler | DX; needed to verify the perf work | needs a C patch (PR #1421) | none |
| j | Intl via ICU4X | small (done as IN1) | yes (done) | none |
| k | Shared code and shapes across workers | small now (~55 KB bytecode) | **no** | none if immutable only |

### (a) Native node values

- **Today** (`source-observed`):
  - every node has a JS `Native` with 10 eager collection objects (`nr/index.js:84-108`);
  - handles are numbers;
  - handle → node goes through a per-host `Map` inside a `WeakMap` (`nr/index.js:64-69`);
  - borrowed nodes go through `WeakRef` plus `FinalizationRegistry` (`nr/index.js:1306, 1332`);
  - handler closures sit in JS maps, and callbacks in Rust `Persistent`s, which are roots.
- **What hocus can do:**
  - a **host value kind**, `HostRef { class: u16, id: u32, gen: u32 }`, carried inline in the
    value. No allocation, and `===` works by value;
  - a class table entry that maps atoms to typed slots (`f64`, `i32`, `bool`, interned string id,
    handle) stored in presto's node slab;
  - `node.label = v` or an intrinsic `set_f64(node, P, v)` becomes a typed store plus a dirty bit;
  - per-node JS state (handlers, last values) moves into Rust columns that the GC traces through
    the class's `trace` hook. Handler cycles then die with the node, which is the design
    `r2/zero-serdes-js.md` §4.2 wants and stock QuickJS can only half-do.
- **Estimated gain** (`inferred`):
  - Per write: about 250–450 ns today (unloaded estimate), about 40–80 ns with option (a) raw C
    functions on stock QuickJS (`r2/zero-serdes-js.md` §5.1), and about 10–30 ns as a hocus
    intrinsic (`hypothesis`).
  - The step from option (a) to hocus saves about 30–50 ns × 11,000 ops ≈ 0.3–0.6 ms of P5's
    1,479 ms.
  - The allocation saving is real (10 objects per node), but lazy nodes (W-C3) take most of it on
    QuickJS.
- **Verdict:** a nice property, not a reason to port.
  - The value of owning it is **lifetime correctness**: traced handlers, no `Persistent` roots, and
    `WeakMap` keys with defined semantics.
  - Speed is not the reason.
- **Fidelity:** none, as long as the generated JS still works with the JS `Native` on other
  engines.

### (b) Engine-level Svelte reactivity

- **Today:** Svelte's JS core runs as written. Its hot path is `get` (`svelte:internal/client/runtime.js:540`), `set`
  (`reactivity/sources.js:149`), `update_reaction` (`runtime.js:220`), `execute_derived`
  (`deriveds.js:339`) and the batch flush (`reactivity/batch.js`) (`source-observed`).
  - They are short functions reading module-level `let`s (`active_reaction`, `untracking`, …), with
    flag bitfields and array `deps`/`reactions`.
  - That is the "many tiny calls" profile behind `JS_CallInternal` 71% (`measured`).
- **What hocus can do:**
  - `Source`, `Derived` and `Effect` become engine records (Rust structs in a slab). Their
    dependency edges are Rust vectors or intrusive links.
  - `$.get(s)`, `$.set(s, v)` and `$.untrack` become **intrinsics**: opcodes, or native calls that
    push no JS frame.
  - `flush` and `flushSync` run in Rust and call effect and derived closures directly.
  - The closures stay JS, and the GC traces them through their effect record.
  - The scheduler can stop at a frame deadline between effects and say so to presto.
- **Estimated gain** (`hypothesis`):
  - On w-signals, the remaining JS is the derived bodies (`a+2b+c`: three `get`s and arithmetic)
    and the effect bodies (an array store). If graph bookkeeping is ~80–90% of today's time,
    removing it gives **5–10x**.
  - Against an own **JS** core, which is already 2–4x (10x on write+flush) faster than Svelte's on
    qjs, the extra gain is perhaps **2–5x**.
  - Batched updates could fall from 5.5–7 µs to about 1 µs.
  - None of this is measured. X1 (§6) bounds it on stock QuickJS.
- **Fidelity: this is the risk.** RUNTIME.md lists the hard semantics (`:208-221`):
  - effect order: pre, render, user; tree order; init-time user effects deferred to `pop`;
  - unowned and disconnected deriveds;
  - `state_unsafe_mutation` and `effect_update_depth_exceeded`;
  - boundary error propagation;
  - writable `$derived`.
- **The mitigation already exists in design** (`documented`, `RUNTIME.md` "Conformance plan" 1–3):
  - the frozen 43-name contract (`@svelters/native-runtime/core`, RT1);
  - Svelte's `tests/signals` (~55);
  - a differential fuzzer that runs the same programs on two cores and compares effect, teardown
    and error logs.
- **A Rust core is a third implementation behind the same contract.** It keeps:
  - Svelte's public semantics;
  - the conformance score, which stays on Svelte's JS core under Node;
  - op logs, compared byte-for-byte.
- **What it risks:**
  - drift whenever upstream changes `batch.js`, which had 95 commits in 12 months
    (`RUNTIME.md:52-53`);
  - async mode, which stays out of scope (the compiler rejects it).
- **hocus can run both cores in one process.** Differential testing then needs no Node at all.

### (c) Proxy fast paths for `$state` deep reactivity

- **Today:**
  - `$.proxy` wraps objects and arrays in a `Proxy` with `get`/`set`/`has`/`ownKeys`/
    `defineProperty`/`deleteProperty`/`getOwnPropertyDescriptor` traps
    (`svelte:internal/client/proxy.js:118, 423`). Props use more proxies (`reactivity/props.js:94,
    166, 263`).
  - In QuickJS each trapped read (`qjs:quickjs.c:52318-52366`) does four things:
    1. looks up `"get"` on the handler (`get_proxy_method`);
    2. makes a full JS call into the trap;
    3. inside the trap, runs Svelte's `get(source)` (another call);
    4. then calls `JS_GetOwnPropertyInternal` on the target for the invariant check
       (`source-observed`).
  - Measured: +2.5 µs for 4 fields (`measured`). w-proxy is QuickJS's worst ratio among the app
    workloads (341 ms), and also slow on V8 (55 ms).
- **What hocus can do:**
  - give an ordinary object or `Array` a **reactive bit** in its header plus a side table of
    per-key sources;
  - `OP_get_field` / `get_array_el` on such an object do the normal shape lookup and record the
    key's source as a dependency (one predicted branch);
  - writes bump the source and its version;
  - `ownKeys` and `length` track a version source, as `proxy.js` does;
  - there is no `Proxy`, no trap call and no invariant check;
  - `Array.isArray`, `JSON.stringify` and spreads work natively, and `structuredClone` and
    `$state.snapshot` can read the backing store directly.
- **Estimated gain** (`hypothesis`): reads go from about 600 ns per field (loaded) to tens of ns, so
  w-proxy gains **3–6x**. V8's own proxy path is the bar, at 6.2x.
- **Fidelity:**
  - `proxy.js` is stable (9 commits in 12 months, `RUNTIME.md:53`), so a native port is tractable.
  - The observable surface to keep:
    - `is_state_proxy` / `STATE_SYMBOL`;
    - identity (the proxy is not the original object);
    - `$state.snapshot` output;
    - descriptor rules (non-configurable properties);
    - dev labels.
  - A user `Proxy` around a reactive object must still work.
  - This **cannot be done on stock QuickJS** without a fork. Exotic classes exist, but a plain
    `Array` cannot be made exotic from the public API (`inferred` from `r2/zero-serdes-js.md` §1.2,
    §1.8).

### (d) The compiler emitting engine bytecode or a typed IR

- **Today:**
  - the compiler already knows the static writes: 84% of `$n.set` calls (`gui/compiler-and-js.md:36`);
  - lowering separates `Op::Set` from `Op::SetDynamic`, and templates are planned (W-C2);
  - startup is about 13 ms from cached bytecode; `.svb` has reserved optional sections
    (`gui/compiler-and-js.md:176-177`).
- **What hocus can do:**
  - consume a **typed hint section** next to the JS: template tables, which identifiers are signals,
    prop ids, the static/dynamic split;
  - specialise on load: `$.get(x)` becomes a `get_signal` opcode, `$n.setp(n, P, v)` a typed store,
    and a template a native instantiate.
- **Do not emit engine bytecode from the compiler** (`inferred`):
  - it welds the compiler to one engine's format and version;
  - it breaks Node, QuickJS and the DOM conformance;
  - it removes the JS twin that HMR and debugging read.
- **Gain:** dispatch savings on the hot calls. They overlap with (b) and need it to matter.
  Startup gains are bounded by the 13 ms.

### (e) A GC scheduled by presto's frame clock

- **Today** (`source-observed`):
  - QuickJS-NG uses refcounting plus a synchronous cycle collector: `JS_RunGC` = `gc_decref` →
    `gc_scan` → `gc_free_cycles` (`qjs:quickjs.c:7483-7494`). It walks every GC object.
  - The collector runs inside whatever allocation pushes `malloc_size` past the threshold. The
    threshold then becomes 1.5x the heap (`:1680-1697`); the initial value is 256 KiB (`:2333`).
  - svelters calls `run_gc` only for tests and shutdown (`host/quickjs.rs:665-667`).
  - GC was about 2% of the signals profile (`measured`). Pause against heap size has not been
    measured; W-J0 plans it (`gui/plan.md`, W-J0).
- **What hocus can do:**
  - presto computes `S_ui` and knows when the UI phase is done (`gui/architecture.md` §10.2);
  - the engine exposes `gc_slice(deadline)` and `GcPolicy { idle_slice, max_pause, suppress_while:
    input_burst }`;
  - collection runs incrementally after the Commit and in idle time, never inside event dispatch
    unless memory is critical;
  - PrimJS documents the same idea: GC trigger timing "in combination with business … rendering
    and other key scenes, can suppress GC" (`documented`, quoted in `r2/zero-serdes-js.md` §3.6).
- **Gain:** p99 frame time, the "one changed label ≤ 0.5 ms" budget, and fewer fling hitches.
  Throughput changes little. The size of the gain is unknown until X3 (§6) measures QuickJS's
  pause.

### (f) Deadlines, interrupts and event-time dispatch

- **Today** (`source-observed`):
  - The interrupt handler is polled every 10,000 interpreter ticks (`qjs:quickjs.c:556,
    8657-8676`). It checks an atomic flag plus a wall-clock deadline (`host/quickjs.rs:220-232`).
  - A stop is uncatchable **and skips every `finally`**. Svelte's active reaction and batch state
    stay mid-update, so the host calls `__svelters_recover()` afterwards (FX6,
    `host/quickjs.rs:313-338`), and the job drain stops at an aborted job (FX6b, `:645-655`).
  - `call_sync` drains no microtasks (`run_jobs(0)`, `host/core.rs:1255`).
  - Job budgets count jobs, not time.
- **What hocus can do:**
  - **Terminate-with-unwind:** an interrupt that user `catch` cannot stop but that runs `finally`
    blocks in runtime-marked frames. That deletes FX6's recover path.
  - **Microtask lanes:** for example, E1 settlements before events, which `checkpoint` now gets
    only through call order (`host/core.rs:1094-1100`). Also per-realm queues, so one root's flood
    cannot starve another.
  - `drain_jobs(Budget { deadline, max })`.
  - A first-class "dispatch, then drain to a deadline" entry for presto's event-time dispatch
    (`gui/architecture.md` §11.1).
- **Gain:** correctness and latency, not throughput. W-H1 (the drain entry) can land on QuickJS;
  the unwinding interrupt cannot.

### (g) Heap snapshots for startup

- **Today:** QuickJS has none (`documented`, `QUICKJS-RUNTIME.md:270`). Startup is 6 ms for the
  engine and about 13 ms to evaluate a 115-module graph from bytecode.
- **What hocus can do:** snapshot after module evaluation and **before mount**. Host objects in the
  heap would tie a snapshot to presto's tree, so take it before any exist.
- **Gain:** perhaps 13 → 2–3 ms per launch (`hypothesis`). That matters more per isolate (one per
  extension) and per worker than for the main app.

### (h) HMR with real module replacement and `$state` kept

- **Today:**
  - H1 (in-place component swap) and H3 (`$state` kept) are **done on QuickJS** (`sv:STATUS.md:127-128`).
  - Versions are new module names (`?v=n`, `host/quickjs.rs:1462-1470`).
  - QuickJS frees resolved modules only in `JS_FreeContext` (`js_free_modules(ctx,
    JS_FREE_MODULE_ALL)`, `qjs:quickjs.c:2955-2970, 3035, 3073`; `source-observed`). This confirms
    what HMR.md:451 called "believed, unverified".
  - So the host forces a full reload, which loses all JS state, after 200 swaps or past half the
    memory limit (`host/core.rs:97-98`, `host/quickjs.rs:405-412`).
- **What hocus can do:**
  - free a superseded module record when no closure references it;
  - optionally rebind live import bindings to the new version, which is real ESM hot replacement.
    The `?v=n` alias game goes away.
- **Gain:** no forced reloads in long sessions and flat memory. The edit latency is already
  dominated by compile. This is a moderate developer-experience gain.

### (i) A debugger and profiler designed in

- **Today:**
  - no debugger; the one route is QuickJS-NG PR #1421, which needs a C patch (`QUICKJS-RUNTIME.md`
    §4 #6);
  - profiling is "partly" possible, by sampling from the interrupt handler (§4 #10);
  - `perf` sees only C frames.
- **What hocus can do:**
  - a DAP server: breakpoints, stepping, async stack traces, native source maps;
  - a sampling profiler driven by the frame clock;
  - an **effect profiler** that attributes each frame's JS time to the effect, derived or handler
    that ran, by the compiler's `source-loc` (`gui/compiler-and-js.md:171-172`);
  - an allocation and heap inspector.
- **Gain:** developer experience, and the instrument needed to prove items (b), (c) and (e).

### (j) Intl via ICU4X

- **Today:** IN1 is **done**: a JS shim (`host/intl.js`) over Rust natives in `svelters-intl`, with
  ICU4X 2.4.0 pinned (`sv:crates/svelters-intl/Cargo.toml:14-21`). Options cross as JSON.
- **What hocus can do:** make Intl objects native classes over the same crate.
- **Gain:** small. Formatting is not hot, and cadatomic's UI uses `Intl.Collator` once (grep). Do it
  late.

### (k) Workers sharing compiled code and shapes

- **Today:**
  - one runtime per thread; messages as `svelters_abi::Value` (`QUICKJS-RUNTIME.md` §4 #9);
  - `SharedArrayBuffer` refused across to Rust (`host/quickjs/buffers.rs:314`);
  - no JS workers in use: E1 offload runs Rust on rayon.
- **What hocus can do:**
  - an `Engine`-level `Arc` cache of immutable code (function templates, literal atoms) and frozen
    shapes, shared by isolates on any thread;
  - deeply frozen data (typed records, typed arrays) shared zero-copy.
- **Do not share mutable objects:** that breaks ISO1's process placement.
- **Gain:** small today (Svelte bytecode is 54–57 KB, `RUNTIME.md:62-66`). It grows with the number
  of extension isolates.

---

## 3. The integration contract

### 3.1 What hocus exposes (Rust API sketch; names are illustrative)

```rust
// Process-wide, Send + Sync: immutable code cache, atom table for literals, shared frozen shapes.
pub struct Engine { /* … */ }
impl Engine {
    pub fn new(cfg: EngineConfig) -> Arc<Engine>;
    pub fn compile(&self, src: &ModuleSource) -> Result<Arc<CodeBlob>, CompileError>; // shareable
}

// One per thread, !Send: heap, GC, job queues, realms.
pub struct Isolate { /* … */ }
impl Isolate {
    pub fn new(engine: &Arc<Engine>, cfg: IsolateConfig /* memory limit, stack, GcPolicy */) -> Isolate;
    pub fn realm(&mut self, opts: RealmOptions) -> RealmId;   // first-party, per-root, per-extension
    pub fn enter<R>(&mut self, realm: RealmId, f: impl FnOnce(&mut Cx<'_>) -> R) -> R; // re-entrant

    // Jobs and time
    pub fn drain_jobs(&mut self, b: Budget /* deadline, max jobs, lanes */) -> DrainStatus;
    pub fn interrupt_handle(&self) -> InterruptHandle;        // Send; set_deadline, terminate(unwind)
    // GC, driven by presto's frame clock
    pub fn gc_slice(&mut self, deadline: Instant) -> GcProgress;
    pub fn gc_full(&mut self);
    // Tooling
    pub fn snapshot(&self) -> Result<Snapshot, SnapshotError>;   // before host objects exist
    pub fn debugger(&mut self) -> Option<&mut Debugger>;          // DAP backend, dev builds
    pub fn profiler(&mut self) -> &mut Profiler;                  // frame-tagged samples
}

// Modules: svelters' ModuleProvider maps onto this one-to-one.
pub trait ModuleHost {
    fn resolve(&self, base: &str, spec: &str) -> Result<ModuleId, String>;
    fn load(&self, id: &ModuleId) -> Result<ModuleSource, String>;
}
pub enum ModuleSource { Js { text: String, map: Option<String>, hints: Option<Hints> },
                        Code(Arc<CodeBlob>), Native(NativeModule) }

// Host classes: typed slots in Rust storage, traced, finalizers only enqueue.
pub trait HostClass: 'static {
    const NAME: &'static str;
    fn slots() -> &'static [SlotDef];          // atom -> (slot id, SlotType::{F64,I32,Bool,StrId,Handle,Js})
    fn trace(&self, id: u64, t: &mut Tracer);  // e.g. a node's handler closures
    fn on_unreachable(id: u64, q: &FinalizeQueue);
}
pub struct HostRef { class: ClassId, id: u64 }  // a value kind; no JS object behind it

// Native functions with typed signatures: argument decoding generated, no FromJs per call.
let m = NativeModule::new("svelters:scene")
    .func("set_f64", |cx: &mut Cx, n: HostRef, p: PropId, v: f64| scene_set_f64(cx, n, p, v))
    .func("set_str", |cx: &mut Cx, n: HostRef, p: PropId, v: StrRef<'_>| /* borrowed, atom-aware */);

// Reactive core intrinsics (capability): Rust graph, JS closures.
pub struct Reactive<'cx> { /* source/derived/effect records, scheduler */ }
impl Reactive<'_> {
    pub fn flush(&mut self, until: Option<Instant>) -> FlushStatus;  // presto can bound it
}
```

### 3.2 How the existing `JsEngine` maps onto it

| `JsEngine` / host piece | hocus call | Change in svelters |
|---|---|---|
| `load_module`, `load_entry` | `ModuleHost` adapter over `ModuleProvider`; `realm.import(entry)` | `ModuleCode::Cached { engine: "hocus" }` and a hocus stamp in the bytecode cache |
| `dispatch`, `dispatch_sync`, `dispatch_changed`, `dispatch_gone`, `fire_timer`, `run_frame` | `cx.call(Global<Function>)` on the handles cached at load | none; also fixes the per-call `globals().get` lookup |
| `run_jobs(max)` | `drain_jobs(Budget { max, deadline: None })` | an optional time budget later |
| `settle_ops`, `release_buffers`, `buffer_stats` | promise resolve; `ArrayBuffer` over Rust memory | none |
| `hot_update` | `realm.replace_modules(changed)`; frees old records | the swap budget becomes optional |
| `collect_garbage` | `gc_full` | — |
| `JsReader`, `JsWriter`, `JsPromises`, `CallbackEngine` | one `HocusConv` over `Cx` | the second implementation of existing traits |
| prelude natives, `__svelters_host` ops | `NativeModule`s / global object | the same JS names, so native-runtime does not change |
| `RunningCtx` / thread-local raw ctx | not needed: `Cx` is passed into every native call | — |
| **New, optional** `JsEngine` methods with defaults | `fn caps(&self) -> EngineCaps` (snapshot, debugger, reactive_core, reactive_objects, host_values); `fn frame_idle(&mut self, deadline)` (GC slice); `fn dispatch_and_drain(...)` (W-H1, QuickJS too) | additive, as `hot_update` and `settle_ops` already are |

**Selecting intrinsics without forking the generated JS:**

- The module provider resolves `@svelters/native-runtime/core` to one of three files:
  - `core/svelte.js` (today's adapter, `nr/core/svelte.js:1-8`);
  - an own JS core (RUNTIME phase 2);
  - `core/native.js`, which re-exports from the built-in `svelters:reactive`.
- The choice is made per engine capability, at resolve time, so tree-shaking still works.
- Component code is unchanged on every engine.

### 3.3 Side by side during the port

- **Choosing the engine:** `HostConfig { engine: EngineKind::{QuickJs, Hocus} }` plus an
  environment override, `SVELTERS_ENGINE=hocus`. This needs `Host::with_engine` or an equivalent to
  be reachable (`host/lib.rs:237`).
- **Linking:** both engines link into one binary. hocus is pure Rust, so the `rquickjs-sys`
  duplicate-symbol rule (one QuickJS per process, `r2/js-host-local.md:16-17`) does not apply.
- **QuickJS-NG stays the default** until hocus passes §4's gate in two consecutive batches. It also
  stays the reference engine for differential runs.

**Differential test tiers** (proposal):

| Tier | What runs on both engines | Pass rule |
|---|---|---|
| D0 | `svelters-host/tests/*.rs`: batch, child_attrs, dx, fixtures, hmr, hooks, host, intl, modules, observe, reload, sched, serdes, started, sync_frame, types_ops | green on both |
| D1 | the 56 `fixtures/*/expected.oplog` | byte-identical on Node TestHost, QuickJS and hocus |
| D2 | native-runtime's 25 `test/*.test.mjs`, in-engine through a small `describe`/`it` shim (RUNTIME.md plans one for the signals suite) | same results as Node |
| D3 | Svelte's `tests/signals` (~55) in-engine, on Svelte's core and on the native core | identical logs |
| D4 | the cross-engine fuzzer: random signal graphs (RUNTIME.md conformance 3b) and random host-op programs; seeded SCHED1 schedules | identical effect, teardown, error and op logs |
| D5 | the benchmark gate (§4), with one harness for Node, QuickJS and hocus | §4 targets |

The DOM conformance (628/774) stays on Node and is not part of the engine gate. It guards the
compiler and runtime JS, which hocus does not change.

---

## 4. Benchmarks for the gate, and targets

**Workloads:**

1. **w-signals, w-proxy, w-appmix, w-props** from `quickjs-jit-cranelift.md:272-292`, with the same
   protocol: 64 warm-up batches, 16 timed, upper median, three interleaved rounds.
   - The scripts were in another machine's scratchpad (`/tmp/claude-1000/...`) and are **not
     present here** (`ls` failed).
   - They must be rebuilt from the description: Svelte 5.57.2 `internal/client` bundled to an IIFE;
     100 states, 100 deriveds and 100 render effects; 10 rounds of 5×`$.set` plus `flushSync`. That
     is the first task in §6.
2. **The P5 keyed-bench** (`bench/keyed-bench`): fill, update, reverse, swap and clear, with JS time
   split from host and toolkit time (`p5-integration.md:66-87`).
3. **cadatomic panels.** No JS timing for them was found (grep of `ca:docs`).
   - Candidates: Properties, TreePanel, ProblemsPanel, AgentChat and JobsPanel, from 26 `.svelte`
     files and about 2.7k lines.
   - Runes counted in `ca:ui`: 51 `$state`, 39 `$derived`, 15 `$effect`, 107 `{#if`, 34 `{#each`.
   - Measure mount, one-change and a 1k-row update in each.
4. **The batched-update microbench** (REACTIVITY §6: 10k changes in one checkpoint) and the **W-J0
   crossing microbench**.
5. **GC pause against heap size** (X3) and **startup** to first commit.

**Targets** that would make the port worth it.

All targets are against **QuickJS-NG with the own JS core and raw-C hot ops (W-J1)**, on a quiet
machine (`[local]`). They are proposals (`inferred` from §1.4).

| Workload | Today, stock qjs (loaded) | M1: runs Svelte | Port worth it (M2) |
|---|---|---|---|
| w-signals | 100.2 ms / 10 calls | ≥ 1.0x qjs, correctness only | ≥ 5x the bar; near node (4.86) |
| w-proxy | 341 | ≥ 1.0x | ≥ 3x the bar; ≤ node (55) |
| w-appmix | 26.1 | ≥ 0.8x | ≥ 1.5x (plain interpreter work; a JIT question, not this note's) |
| w-props | 7.21 | ≥ 0.8x | no target without a JIT tier |
| batched update per change | 5.5–7 µs | — | ≤ 1 µs |
| P5 fill / update, JS time only | 1,479 / 320 ms | ≥ 1.0x | ≥ 3x the bar |
| cadatomic panel one-change | not measured | — | JS ≤ 0.2 ms of the 0.5 ms budget (`gui/architecture.md:32`) |
| GC | not measured | — | no collection inside dispatch; p99 slice ≤ 1 ms at a cadatomic-sized heap |
| startup to first commit (JS part) | ~19 ms (6 + 13) | ≤ 1.5x | ≤ 10 ms with a snapshot |

**Decision rule** (proposal): continue past M2 only if three conditions hold:
- the reactive workloads (w-signals, w-proxy, batched update, panel one-change) reach ≥ 3x the bar;
- D0–D4 are green;
- test262 shows parity with QuickJS-NG on the M1 surface (§5).

If X1 alone gets w-signals to within 2x of that target on QuickJS, the remaining case for hocus
rests on (c), (e), (f), (h) and (i). Each of those must then be argued on its own merit.

---

## 5. The minimal ECMAScript surface for "M1 runs Svelte"

Method: grep over Svelte 5.57.2's `internal/client`, `internal/shared`, `reactivity`, `motion` and
`store` sources; svelters' `packages/{native-runtime,types,style}/src`; the host prelude; and
cadatomic's `ui/` (159 files) (`source-observed`, grep counts are of files unless marked).

**Required**

| Feature | Where it is used |
|---|---|
| ES modules: static import/export, `import.meta` (HMR `hot`), top-level await (the entry may use it, `host/quickjs.rs:356-360`) | everything |
| Classes: getters/setters, `extends` of built-ins (`SvelteMap extends Map`, `SvelteSet extends Set`, `SvelteDate extends Date`: `svelte:reactivity/map.js:53`, `set.js:47`, `date.js:42`) | Svelte, runtime |
| **Private fields and methods** (`this.#x`, `#m()`) | Svelte `reactivity/batch.js`, `dom/blocks/boundary.js`, `branches.js`, all of `reactivity/*` and `motion/*`; `nr/embed.js:66-120`, `nr/lists.js:135`; `@svelters/types` streams |
| **Proxy + Reflect** (all traps) | `proxy.js:118, 423`, `props.js:94, 166, 263`, `nr/index.js:1065` |
| Map, Set, WeakMap, WeakSet | 16 / 24 / 3 / 2 Svelte files; 8 / 8 / 5 / 0 runtime files |
| Symbol, with `iterator`, `asyncIterator`, `for`; `Symbol.dispose` (feature-detected) | Svelte (5 files use `iterator`), `@svelters/types/src/core.js:637, 708` |
| Promise, microtasks, async/await | Svelte `batch.js`, `deriveds.js`, `effects.js`, `runtime.js` (`tick`); host `settle_ops` |
| **Async generators and `for await`** | `svelte:internal/client/reactivity/async.js:245` (DEV); `@svelters/types/src/core.js:336` (E3 streams) |
| Optional chaining, `??`, logical assignment (`??=`) | 48 / 17 Svelte files |
| WeakRef, FinalizationRegistry | `nr/index.js:1306, 1332` (feature-detected, with a fallback) |
| Typed arrays incl. `BigInt64Array`, ArrayBuffer, BigInt | E2 buffers; `convert.rs` bigint |
| JSON, Date, `Object.hasOwn`, `Array.from`, `Object.defineProperty`, `getOwnPropertyDescriptor` | runtime |
| RegExp: simple patterns, `g`, `replace`/`replaceAll`; no lookbehind, named groups or `u`/`v` in the core paths found | `proxy.js:33, 373`, `async.js:50`, `shared/attributes.js:148` |
| `Error.stack`, `Error.prepareStackTrace` CallSites | source maps (`QUICKJS-RUNTIME.md` §4 #3) |
| Strict mode | modules |

**Provided by the host, not the engine:**
- prelude: `console`, `performance`, `queueMicrotask` (polyfilled through `Promise`), timers,
  `requestAnimationFrame`, `AbortController`/`AbortSignal` (`host/prelude.js:16-142`);
- a stub `EventTarget` (`nr/index.js:534`), which `$state.snapshot` tests with `instanceof`;
- `Intl` (IN1).

**Needed but has a fallback:** `structuredClone`. `$state.snapshot` calls it for `Date` and
non-plain objects inside `try` (`svelte:internal/shared/clone.js:110, 131`); QuickJS lacks it too.

**Not needed for M1** (zero hits in the runtime paths): `eval`, `new Function` (only Vite's
ModuleRunner uses them, and the Rust loader replaces it), `with`, sync generators except as the
basis of async ones, Atomics, `SharedArrayBuffer`, `Intl` in-engine, `URL`/`URLSearchParams` (only
`SvelteURL`), Temporal, decorators.

**M1 exit criteria** (proposal):
1. The prelude, native-runtime and Svelte 5.57.2 load through `ModuleProvider`.
2. D0 and D1 are green, with every op log byte-identical.
3. D3 (`tests/signals`) is green.
4. The w-* workloads run with checksums equal to Node's; speed is only recorded.
5. Test262 passes for the feature list above. The test262 scope itself belongs to another note.

---

## 6. First steps

These are concrete, in order. The steps that need svelters edits go to the coordinator as requests,
because this repository edits nothing else.

1. **Rebuild the w-* harness in hocus** (`bench/w-*`): Svelte 5.57.2 from npm, bundled to IIFEs, a
   shared batch driver, and runners for node, qjs (rquickjs 0.14) and later hocus. Checksums must
   match across engines. Timings are cloud, not a gate; quiet-machine runs are `[local]`.
2. **X1: a Rust reactive core on stock QuickJS.**
   - A small Rust crate implements the graph.
   - It is exposed through raw `rquickjs-sys` C functions (no fork), with JS effect closures, behind
     the 43-name `core/` contract.
   - Compare it with Svelte's core and a JS own-core prototype on w-signals and the batched-update
     bench.
   - This bounds §2b's gain and tells whether the gain needs hocus. About one week; it can run in
     this repository against svelters read-only.
3. **X3: QuickJS GC pause against heap size** at cadatomic-like heaps (10–200 MB) of Svelte
   objects. This bounds §2e.
4. **Write `hocus-embed`'s API** (§3.1) as a design doc with the `JsEngine` mapping (§3.2). Send
   svelters the additive `JsEngine` capability methods and a public engine selector as one request,
   through cadatomic's `docs/plan/04-kernel-requests.md` SV table.
5. **Build the differential harness first, before the engine:** D1 (op-log replay), plus a
   `describe`/`it` shim that runs native-runtime's tests and Svelte's `tests/signals` in an
   embedded engine. Prove the harness on QuickJS alone. Then hocus can enter it on day one of M1.
6. **Scope M1** to the §5 surface, and check that list against the test262 plan in the
   engine-scope note.

---

## 7. Open questions for the user

| # | Question | Recommendation |
|---|---|---|
| U1 | Gate the engine port on X1 (a native reactive core on stock QuickJS) and the §4 bar "QuickJS + own core + raw ops", not on today's stock numbers? | **Yes.** It is the honest comparison, and X1 costs about a week |
| U2 | May a Rust reactive core become the default for native backends, with Svelte's JS core kept as the differential reference and the DOM conformance on Svelte's core? | **Yes, behind a capability**, after D3 and D4 pass. This is a fidelity choice the user should own |
| U3 | May `$state` objects be engine-native reactive objects instead of `Proxy`s on hocus, with the observable surface kept (§2c)? | **Yes**, as the main engine-only win; prove it with Svelte's proxy tests in-engine |
| U4 | Should the compiler emit engine bytecode? | **No.** JS plus a typed hint section in `.svb`; every engine keeps running the same JS |
| U5 | Should isolates and workers share mutable JS objects? | **No.** Share immutable code and deeply frozen data only; ISO1's process placement depends on it |
| U6 | Does QuickJS-NG stay a supported engine? | **Yes, at least until hocus passes the gate in two consecutive batches**, and as the differential reference after that. The recorded "stock QuickJS-NG, no fork" decision then still describes the QuickJS backend exactly; hocus is an addition, not a fork |
| U7 | Who owns the svelters-host adapter (`hocus.rs`)? | **svelters lanes**, under svelters' rules; hocus ships only the engine and `hocus-embed` |

---

## 8. Gaps

- The w-* bench scripts are not on this machine; their numbers are from a loaded 12-core host
  (±25%).
- No measurement exists for: QuickJS GC pause against heap size, the cadatomic panels' JS time, the
  own JS core on the w-* workloads, or the unloaded W-J0 crossing variants. Every gain in §2 marked
  `hypothesis` depends on these.
- QuickJS line references are at master `7a23d8e`, not the 0.16.2 that svelters ships. The module
  freeing and GC trigger code was not re-checked at 0.16.2.
- Whether a plain `Array` can be given reactive semantics without a fork was reasoned from the
  public header notes in `r2/zero-serdes-js.md`, not from a probe.
