# Verification and method: how to test, measure and organise the hocus port

Date: 2026-10-25. Topic owner: researcher "verification and method" (one of eight).
Scope: test262, QuickJS-NG as a differential oracle, fuzzing, benchmarks, the C→Rust porting
method, the repository scaffold and the first batch of lanes.

Pinned sources read for this note (all shallow clones in the session scratchpad
`/tmp/claude-0/-home-user/db6d0663-2392-5b80-95a4-80368735124c/scratchpad/src/`):

| Source | Sha | Date |
|---|---|---|
| quickjs-ng (port candidate and oracle; `QJS_VERSION` 0.17.0, `quickjs.h:1463`) | `7a23d8e` | 2026-10-24 |
| test262, at the sha QuickJS-NG pins as its submodule (`git ls-tree HEAD test262`) | `5ef1e572` | 2026-07-29 |
| Bellard QuickJS | `7d1c13c` | 2026-10-20 |
| Boa | `64c209f` | 2026-10-25 |
| Nova | `877ef16` | 2026-10-18 |
| Fuzzilli | `be2811d` | 2026-10-13 |
| svelters / cadatomic / cadabra3 / ecadabra (`/home/user/...`) | `0aa41697` / `75b6a47` / `7bf8908` / `39187fc` | today |

Grades: `documented`, `source-observed`, `measured`, `inferred`, `hypothesis` (BRIEF.md). Every
timing below was taken on this 4-core, 15 GB container shared with seven other researchers, and is
**cloud, not a gate**.

## Summary of findings

1. **test262 is about 53.6k test files; ~48.7k matter without Intl and staging.** At `5ef1e572`:
   23,809 `built-ins`, 23,724 `language`, 3,357 `intl402`, 1,483 `staging`, 1,086 `annexB` and
   116 `harness` (measured). Without intl402 and staging, 5,584 tests are `async`, 830 are
   `module`, 4,725 are negative tests (4,629 at parse time), 112 use `$262.agent` and 195 use
   `$262.createRealm` (measured). Temporal alone is 4,603 tests, and QuickJS-NG passes none of them
   (test262.fyi, 2026-10-25).
2. **QuickJS-NG's own runner is a ready-made reference result.** I built `run-test262` from
   `7a23d8e` and ran it with NG's `test262.conf` on its pinned test262. The result was 82,220
   executions (both strict modes), **40 failures, all of them listed in `test262_errors.txt`**,
   4,802 excluded and 5,736 skipped. It took **51 s on 3 threads** (measured, cloud, not a gate). That is
   the top of hocus's pass-rate ladder: the same pins, the same configuration, and at most the same
   40 failures.
3. **The C engine can be an oracle at three seams, not only at the end.** This is cadabra3's
   layer-by-layer Parasolid method applied to QuickJS:
   - *Leaf seam.* Port libregexp, libunicode and dtoa, link them back into NG through their C ABI,
     and run NG's own test262 run on them. This works before hocus has a VM (inferred).
   - *Bytecode seam.* NG's bytecode output is deterministic: two compiles gave byte-identical
     files for a small script and for the 118 KB Svelte 5.57.2 runtime bundle (measured). So the VM can be
     tested on NG-compiled bytecode, and the compiler can be tested by running its output on NG's
     VM. This decouples the compiler and VM lanes (inferred).
   - *Observable seam.* For every corpus program, record the printed output, the completion value,
     the exception class and the microtask results. Compare those between engines, not internal
     order (cadabra3's "tests check outputs").
   - *Product seam.* svelters already has 50 fixtures with a byte-exact `expected.oplog`, behind an
     engine trait whose only implementation is QuickJS (`crates/svelters-host/src/engine.rs:1-7`).
4. **Instruction counts are a cloud-safe performance gate; wall time is not.** The same `qjs`
   workload took 129–182 ms quiet and 203–336 ms with three competing processes. Under cachegrind
   it counted **exactly 1,490,697,009 instructions in all four runs**, quiet or loaded (measured).
   gungraun (the renamed iai-callgrind, 0.20.0 on crates.io, 2026-09-26) is the Rust harness for
   this.
5. **Fuzzilli can drive a Rust engine on stable Rust.** It needs a REPRL loop (QJS's patch is 318
   lines) and SanitizerCoverage `trace-pc-guard` hooks. rustc 1.97 stable emits those hooks with
   `-Cpasses=sancov-module` (measured). I found no existing Rust-engine Fuzzilli target (not
   found). Fuzzilli's built-in differential mode compares two runs of *one* binary with different
   flags (source-observed). A hocus-vs-NG differential is therefore our own replay tool.
6. **Porting method: a hand port by subsystem, not c2rust.** rav1d's c2rust route started 3.8%
   slower than C, peaked at 11% during the safe rewrite and ended under 6%. It took more than 20
   person-months against an estimate of 7 (documented, memorysafety.org 2024-09). c2rust keeps the
   C ownership model, and changing that model is the point of hocus. The fish shell's incremental
   port (57k lines of C++ became 75k lines of Rust in about two years) shows that keeping the old
   test suite green at every step is what makes a long port safe (documented, 2024-12-28).
7. **quickjs.c splits into lanes by name prefix.** A heuristic census found 1,611 function bodies
   covering 56,404 of 64,954 lines. `JS_CallInternal` alone is 2,934 lines. The parser and
   compiler are about 12.3k lines, and the builtins about 17k (measured, heuristic). The ledger
   should list every C function, as cadabra3's `porting/ledger.tsv` does.

## Recommendation

- **Pin the oracle to the port base.** Port from one QuickJS-NG release tag (recommendation:
  v0.17.0, `6d46d07`). Build the oracle from that exact source with `cc` in `hocus-oracle-sys`,
  not through rquickjs, and pin test262 to the submodule sha of that tag. NG's `test262.conf`,
  `test262_errors.txt` and exclude lists then apply unchanged.
- **Gates are relative to the oracle, staged by rungs** (§1.6). A rung passes when hocus passes
  every test in it that NG passes, minus a short, reviewed list of departures.
- **Verify at every seam** (§2.2). Never gate on bytecode identity or internal traces. Use them
  only as triage probes and as a metric in the ledger.
- **Tiers as in the sibling repositories:**
  - G0 (lane): fmt, clippy, boundaries, ledger check, and the lane's own test262 directories and
    corpus slice.
  - G1 (consolidation): the full test262 run against the ratchet, the full differential corpus, the
    instruction-count ratchet and a GC-stress subset.
  - G2 (daily, rented server): GC stress over all of test262, Miri, sanitizers, fuzzing campaigns,
    wall-clock benchmarks and the coverage-to-ledger map.
- **Batch 0** (the coordinator alone): the scaffold, the pins and the ledger bootstrap.
- **Batch 1** (six lanes, all of which can run before the hocus core exists): `oracle`
  (NG FFI and the test262 runner), `observe` (the differential corpus and triage), `bytecode`
  (NG bytecode format), `regexp` (with libunicode), `numbers` (dtoa) and `bench`. A seventh,
  optional design-spike lane, `core-spike`, depends on the GC and ownership notes (§7).

---

## 1. test262

### 1.1 Structure and size

Counts are `.js` files excluding `*_FIXTURE.js` at test262 `5ef1e572`. They were made with `find`
and `grep` over the frontmatter in this container (measured).

| Directory | Tests | Largest subdirectories |
|---|---:|---|
| `test/built-ins` | 23,809 | Temporal 4,603; Object 3,411; Array 3,082; RegExp 1,879; TypedArray 1,446; String 1,223; TypedArrayConstructors 738; Promise 729; Iterator 654; Date 594; DataView 561; Function 509; Atomics 389; Set 383; Proxy 311 |
| `test/language` | 23,724 | expressions 11,102 (class 4,059; object 1,170; dynamic-import 1,005; async-generator 623); statements 9,347 (class 4,367; for-await-of 1,234; for-of 751); module-code 599; literals 534; eval-code 347 |
| `test/intl402` | 3,357 | ECMA-402 |
| `test/staging` | 1,483 | proposals, often engine-specific (NG excludes it, except explicit-resource-management) |
| `test/annexB` | 1,086 | legacy web compatibility |
| `test/harness` | 116 | tests of the harness include files |
| **total** | **53,575** | test262.fyi counts 53,616 at its own revision `d53ebc8` |

Without intl402 and staging there are 48,735 files (measured):

| Metadata | Files | Meaning (`INTERPRETING.md` @ `5ef1e572`) |
|---|---:|---|
| `flags: [async]` | 5,584 | load `doneprintHandle.js`; the test calls `$DONE()`; the runner drains jobs and waits for `Test262:AsyncTestComplete` |
| `flags: [module]` | 830 | run as module code; needs a module loader for `_FIXTURE.js` files |
| `flags: [onlyStrict]` / `[noStrict]` / `[raw]` | 665 / 2,530 / 32 | everything else runs twice, in strict and in sloppy mode |
| `negative:` | 4,725 | parse 4,629, resolution 34, runtime 40 |
| `CanBlockIsFalse` / `CanBlockIsTrue` | 2 / 7 | the agent's `[[CanBlock]]` |
| uses `$262.agent` | 112 | worker agents with SharedArrayBuffer broadcast (Atomics) |
| uses `$262.createRealm` | 195 | a fresh realm (cross-realm feature) |
| uses `$262.evalScript` / `IsHTMLDDA` | 31 / 35 | global script evaluation; the `document.all` emulation |
| includes `detachArrayBuffer.js` | 303 | `$262.detachArrayBuffer` |
| most-used includes | | `propertyHelper.js` 4,963; `testTypedArray.js` 2,082; `temporalHelpers.js` 1,489; `compareArray.js` 1,483; `isConstructor.js` 617; `asyncHelpers.js` 354 |
| no `features:` line | 17,104 | baseline-language tests |

The most common feature tags are destructuring-binding (6,637), async-iteration (4,971), class
(4,788), Temporal (4,611), generators (4,119), TypedArray (2,521), default-parameters (2,269),
class-fields-public (2,055), Symbol.iterator (1,848) and class-methods-private (1,709) (measured).

The host must provide (`INTERPRETING.md:38-115`, documented):
- `print`;
- `$262` with `createRealm`, `detachArrayBuffer`, `evalScript`, `gc`, `global`, `IsHTMLDDA`,
  `AbstractModuleSource`;
- `$262.agent` with `start`, `broadcast`, `getReport`, `sleep`, `monotonicNow`, and inside the
  agent `receiveBroadcast`, `report`, `leaving`, `sleep` and `monotonicNow`.

### 1.2 The runners

| Runner | Isolation | Timeouts | Expectations / ratchet | Notes |
|---|---|---|---|---|
| QuickJS-NG `run-test262.c` (2,384 lines) | one fresh `JSRuntime` per test on N threads (`-t`, default ncpu-1), in process | none by default. An interrupt countdown of 150 polls is set only for tests that ask for it (`run-test262.c:1786-1790`) | `test262_errors.txt` (40 lines) is the known-failure list. `-u` rewrites it; `-E` runs only those tests | `-a` runs both modes. `JS_SetDumpFlags(rt, JS_DUMP_LEAKS)` checks every test for leaks (`:1751`). Agents run on pthreads (`:595-880`). The config file has `[features]` (name, `=skip`, `=!tcc`) and `[exclude]` lists. A `-N` mode runs tests prepared by test262-harness and eshost (source-observed, `run-test262 -h`) |
| NG `test262-fast.conf` | | | | excludes slow tests: generated property-escape regexps (417), long URI tests, Unicode identifier tests (source-observed) |
| Boa `boa_tester` | in process, rayon `par_iter`, `catch_unwind` per test (`tests/tester/src/exec/mod.rs:22,275`) | "TODO: timeout" (`:283`) | results JSON is compared against `boa-dev/data` `test262/refs/heads/main/latest.json` and posted on PRs (`.github/workflows/test262.yml:36-56`) | per-ES-edition statistics (`edition.rs`); feature ignore list in `test262_config.toml`; test262 pinned there by `commit = "d86b229…"` |
| Nova `test262_runner.rs` (1,106 lines) | one `nova_cli` **process per test**, both modes (`:190-211`) | 10 s wall clock (`:163`) | `expectations.json` (7,797 non-passing tests: 7,572 FAIL, 162 CRASH, 41 UNRESOLVED, 22 TIMEOUT) and `metrics.json` (pass 40,692 of 53,616); the run fails if counts differ | `skip.json` skips annexB and intl402 wholesale; `--nogc` mode |
| test262-harness and eshost (npm) | process per test through eshost host adapters | configurable | none | NG supports its output format with `-N`; useful for running node/V8 as a third vote (documented, NG help text) |

**Lesson for hocus (inferred).** Run in process, one fresh runtime per test, catch panics like
Boa, and keep a process-per-test fallback mode like Nova for crash triage. Use Nova's two files:
an expectations file listing every test that does not pass with its status, and a metrics file
with counts. Add QuickJS's leak check as a GC-heap-empty check at runtime teardown.

Wall-clock timeouts break cadatomic's rule "tests wait on events, not the wall clock"
(`cadatomic/CLAUDE.md`, FQ-2, FQ-4 and FQ-13). Replace them with an **instruction budget**, as Boa's
fuzzer does (`instructions_remaining(1 << 16)`, `tests/fuzz/fuzz_targets/vm-implied.rs:14`). Also
give the runtime a **host clock hook**, so `monotonicNow`, `sleep` and `Date.now` read a virtual
clock in main-thread tests (inferred). Agent and Atomics tests (112 + 389) depend on real thread
timing. They run in their own step with a real clock and are gated in G2 only (inferred).

### 1.3 How engines track pass rates

- **test262.fyi** runs many engines daily. Its data endpoint `https://data.test262.fyi/`
  (`meta.json` generated 2026-10-25 03:26 UTC, test262 `d53ebc8`, "macOS 15.3.1 with M4") gives
  these totals out of 53,616 (documented, fetched today):

| Engine (version) | Pass | % | built-ins | language | intl402 |
|---|---:|---:|---:|---:|---:|
| JSC (324620) | 52,975 | 98.8 | | | |
| QuickJS, Bellard (`7d1c13c`) | 52,813 | 98.5 | 23,553 / 23,823 | 23,285 / 23,726 | 3,373 / 3,382 |
| SpiderMonkey 161 | 52,790 | 98.5 | 23,514 | | |
| V8 15.8.47 | 52,377 | 97.7 | 23,350 | 23,088 | 3,345 |
| LibJS | 52,393 | 97.7 | 23,329 | | |
| Boa (`9871775`) | 51,252 | 95.6 | 22,880 | 23,281 | 2,673 |
| Kiesel 0.5.0-dev | 50,830 | 94.8 | 23,470 | | |
| **QuickJS-NG (`7a23d8e`)** | **44,774** | **83.5** | **18,917** | **23,288** | **23** |
| XS | 43,639 | 81.4 | | | |
| Nova (`877ef16`) | 41,246 | 76.9 | 16,822 | 22,662 | 26 |

  NG's gap is almost all features it does not have. It passes 0 of 4,605 Temporal tests, against
  4,605 for Bellard's QuickJS and 4,603 for Boa. It passes 23 of 3,382 intl402 tests and 643 of
  732 Promise tests (documented, `built-ins.json`). On the language directory NG (23,288) matches
  Bellard's QuickJS (23,285) and Boa (23,281). Full-suite times on the M4 were NG 129 s,
  QuickJS 146 s, Boa 300 s and V8 550 s (documented, `meta.json` `timeTaken`).
- **Boa** keeps history in the `boa-dev/data` repository and a conformance page at
  `boajs.dev/conformance`. Its benchmarks page (`boajs.dev/benchmarks`) runs the V8 v7 suite
  nightly against v8-jitless, sm-jitless, kiesel, duktape and quickjs (documented, fetched today).
- **Nova** commits `metrics.json` and `expectations.json`, so the history is the git log
  (source-observed).

**For hocus (inferred).** Keep `ratchets/test262.tsv`: per test262 directory, the total, the pass
count and NG's pass count. Keep `ratchets/test262-expect.tsv`: every non-passing test with its
status. Each consolidation appends one row (date, sha, pass per rung) to
`docs/test262/history.tsv`. A private artifact page can chart it. No public dashboard until the
user decides (public work is the user's decision).

### 1.4 What a Svelte-first engine needs first

I counted constructs in the unbundled Svelte 5.57.2 client runtime: 92 files and 17,033 lines
under `svelte/src/internal/{client,shared}`, from the copy in the scratchpad
`jit/deps/node_modules`. These are regex counts, so the numbers are approximate (measured,
heuristic).

| Construct | Uses | Construct | Uses |
|---|---:|---|---:|
| arrow functions | 599 | `class` | 10 |
| spread/rest `...` | 150 | private `#field` | 9 |
| `?.` | 137 | `Reflect.` | 8 |
| `for (const … of …)` | 86 | `new Proxy` | 5 |
| `??` | 82 | `WeakMap` | 5 |
| `try {` | 56 | `queueMicrotask` | 5 |
| `async` / `await` / `Promise` | 47 / 23 / 43 | `function*` | 1 |
| `new Set` / `new Map` | 44 / 31 | `Symbol(` / `Object.getOwnPropertyDescriptor` | 21 / 35 |
| BigInt, Atomics, `Intl.`, `new Date`, `with`, labels | 0 | `JSON.` / `structuredClone` | 3 / 2 |

So the Svelte runtime needs, in order: closures and objects, destructuring and spread, classes
with private members, iterators and for-of, Map and Set, Proxy and Reflect, Symbol, and
Promise/async/microtasks. It does not need BigInt, Intl, Date, Atomics or annexB (inferred). App
code and the svelters prelude may use more. The observe lane's corpus will show it.

### 1.5 A staged pass-rate ladder

Sizes are file counts at `5ef1e572` (measured). Most files run twice, so executions are about 1.9×
the file count. Directories alone don't separate features: `language/expressions/object` holds
generator and async tests too. The runner should filter by directory *and* `features:` tags.

| Rung | Content | Files | Gate |
|---|---|---:|---|
| R0 | `test/harness` (assert.js, propertyHelper.js, compareArray.js…) | 116 | all of NG's passes |
| R1 | language core: everything in `language/` except class, generators, async, for-await-of, dynamic-import, module-code, import/export, using/await-using, yield | 10,358 | ≥ 95% of NG's passes in the rung, then 100% |
| R2 | core built-ins: Object, Function, Array, String, Number, Boolean, Math, JSON, Error, NativeErrors, AggregateError, Symbol, Reflect, Proxy, Map, Set, WeakMap, WeakSet, globals, URI functions, the iterator prototypes | 11,136 | as R1 |
| R3 | the Svelte rung: language class, generators, async, for-await-of, modules, dynamic import (13,188), plus Promise, Generator/Async prototypes, Iterator (1,607) | 14,795 | 100% of NG's passes; **"Svelte-ready" = R0–R3 + the svelters fixtures** |
| R4 | RegExp, Date, TypedArray family, ArrayBuffer, DataView, BigInt, WeakRef, FinalizationRegistry, DisposableStack, Uint8Array base64 | 5,898 | 100% of NG's passes |
| R5 | Atomics, SharedArrayBuffer and agents (493), annexB (1,086), using/await-using (178), cross-realm, the rest | ~2,000 | parity: hocus fails at most NG's 40 executions plus the departures |
| out of scope | Temporal (4,603), ShadowRealm (64), intl402 (3,357), staging (1,483) | | NG has none of them. Reconsider with Bellard's QuickJS as the oracle (it passes Temporal and intl402) |

## 2. QuickJS-NG as a differential oracle

### 2.1 Which build is the oracle

- svelters uses rquickjs 0.14.0, which bundles NG 0.16.2 (BRIEF.md). The NG tags are v0.16.2
  `1ab8676` and v0.17.0 `6d46d07`. HEAD is `7a23d8e` (`git ls-remote`, measured).
- svelters' bytecode cache is stamped with the NG version, because the bytecode format moves with
  the version (`crates/svelters-host/src/bytecode.rs:11-13`, documented). NG's format version is
  `BC_VERSION 29` (`quickjs.c:38501`, source-observed).
- **Recommendation (inferred):** use one source tree as both the port base and the oracle. Vendor
  it in `hocus-oracle-sys` and compile it with the `cc` crate. Then build flags are ours to set:
  `FORCE_GC_AT_MALLOC` for GC stress (`CMakeLists.txt:43,249-252`), `DIRECT_DISPATCH=0` to match a
  switch-dispatch port (`quickjs.c:51-55,18073`), and dump options. The rquickjs build stays the
  product engine in svelters until hocus replaces it.

### 2.2 Oracles at four seams

cadabra3 tests its kernel against Parasolid layer by layer. Its rule is that tests check outputs
"not Parasolid's internal order, trace values or bit patterns" (`cadabra3/CLAUDE.md`). The same
shape works here:

| Seam | What hocus replaces | How the oracle runs | Gate (an output check) |
|---|---|---|---|
| **Leaf** | libregexp (3,522 lines), libunicode (2,074 plus a 5,160-line table), dtoa (1,619) | (a) differential unit tests: Rust and C get the same inputs through FFI; (b) **Theseus mode**: the Rust crate exports the C ABI (`lre_compile`, `lre_exec`, `js_dtoa`…), NG links it in place of the C file, and NG's own `run-test262` runs the RegExp, Number and String directories | same match results and captures and the same strings; NG's run gives the same 40-failure result |
| **Bytecode** | the compiler (parser, emitter, resolver: about 12.3k lines), separately from the VM | NG compiles to bytecode (`JS_WriteObject`, `qjsc -b`), hocus's VM runs it. hocus compiles, and NG's VM runs it (`JS_ReadObject`) | the same observable result in both directions. Byte identity of the bytecode is a *metric* in the ledger, not a gate |
| **Observable** | the whole engine | the same program on hocus and on NG, observation records compared | records equal after normalisation (§2.4) |
| **Product** | svelters' `JsEngine` | the svelters fixtures run with `engine=quickjs` and `engine=hocus` | the op log is byte-identical to `expected.oplog` (`crates/svelters-host/tests/fixtures.rs:1-6`) |

**Measured: NG bytecode is deterministic.** `qjsc -b -C` on the same file twice gave identical
bytes for `appmix.js` (1,229 B, or 836 B with `-ss`) and for the Svelte 5.57.2 runtime IIFE
bundle (118,270 B of source, 361,043 B of bytecode). I used `qjsc` 0.17.0 built from `7a23d8e`.
The bundle was another researcher's `scratchpad/jit/bench/bundle.js`, read-only.

This only works if hocus can read and write NG's bytecode format. That is a reason to make the
first front end a faithful port of QuickJS's single-pass parser and emitter (inferred). An
oxc-based front end would lose the bytecode seam but keep the observable and product seams
(open question 3).

### 2.3 The corpus

| Corpus | Size | Where | Why |
|---|---|---|---|
| test262 at the pin | 48.7k files | `pins.toml` | the spec's authority; the main gate |
| NG's own tests | 100 `.js` files (`test_builtin.js`, `test_language.js`, `bug*.js`…) plus `api-test.c` (2,320 lines) and `lre-test.c` | quickjs-ng `tests/` @ pin | QuickJS-specific regressions. Port them first (§5.4) |
| svelters host tests | 34 JS files | `crates/svelters-host/tests/js/` @ `0aa41697` | host-binding semantics |
| svelters fixtures | 62 directories, 50 with `expected.oplog` | `fixtures/` @ `0aa41697` | the product seam: Svelte 5 runtime, compiled components and the event script |
| the Svelte runtime and bench workloads | w-signals, w-proxy, w-appmix, w-props | svelters `docs/notes/quickjs-jit-cranelift.md` §5. The files were in another machine's scratchpad and are not in the repo, so they must be regenerated and committed | hot paths; checksums already matched across engines there |
| big JS programs | acorn (~4,350 cases) and `svelte/compiler` (~3,680 cases) running *as JS* on both engines, with ASTs compared | svelters `tools/oracle/README.md` corpora | a heavy, real-world string, regexp and object load with JSON outputs |
| generated programs | Fuzzilli corpus samples (§3) | the G2 fuzzing server | finds what no hand-written corpus has |

svelters' `experiments/native-compiler/conformance` (628 passing runtime-runes samples) runs on
jsdom in node (`run.mjs:1-20`, source-observed). It would need a DOM in hocus, so it is not a
first-wave corpus (inferred).

### 2.4 What is compared

The `hocus-oracle` crate defines one **observation record** per run:
- stdout lines, from `print` and `console.log`;
- the completion value, serialised by a canonical walker written in ES5 so it runs early on hocus.
  It shows type, own keys in spec order, property attributes, prototype identity by intrinsic
  name, and `-0` and NaN;
- the uncaught exception: constructor name and `message`;
- unhandled rejections;
- the order in which jobs ran, from a `print` in each callback;
- for modules, the namespace keys.

Normalised or not compared (inferred, from the cadabra3 rule and NG's behaviour):
- `stack` strings and line/column numbers (compared only in NG-specific tests);
- `Function.prototype.toString` of native functions;
- `Math.*` results beyond the spec's exactness requirements (implementation-approximated
  functions such as `sin` and `exp`). They are compared within 1 ulp unless the program is a
  test262 test;
- `Math.random`, `Date.now` and timezones: the host hooks are fixed by the harness (fixed seed,
  virtual clock, UTC);
- GC timing: WeakRef and FinalizationRegistry results only after an explicit `$262.gc()`.

Error *messages* are kept but compared softly. A mismatch is a warning class, because svelters
or app code may show them (hypothesis: no svelters test depends on exact messages; not checked).

Never compared: bytecode, opcode traces, property-table layout, atom numbering. These are
**triage probes**. When records differ, the triager can run both engines with opcode tracing and
find the first differing instruction. That is debugging, not a test.

### 2.5 Triage

Each disagreement gets one class and an owner. The categories copy parasolid-re's "reproduce /
candidate departure" (`parasolid-re/CLAUDE.md`) and cadabra3's `testdata/r6f4a/DEPARTURES.tsv`:

| Class | Decided by | Action |
|---|---|---|
| **hocus bug**: test262 or the spec agrees with NG | test262 test, spec text | fix in hocus |
| **NG bug, reproduce**: NG deviates, but svelters or app code may depend on it, or it is a deliberate web-compat choice (NG excludes `S11.13.1_A6_T*` as "V8 has the same spec non-compliance", `test262.conf` `[exclude]`) | the coordinator | hocus copies NG; a row in `departures.tsv` with `reproduce` |
| **NG bug, candidate departure**: NG deviates from the spec, and nothing depends on it (the 40 known failures; e.g. `language/expressions/object/computed-property-name-topropertykey-before-value-evaluation.js`) | the coordinator; the user if it changes svelters behaviour | hocus follows the spec; a row with `depart`, and a request upstream |
| **implementation-defined**: messages, `Math` precision, `toString` of natives | the spec's "implementation-defined" wording | normalise in §2.4 |
| **nondeterministic program** | — | fix the corpus entry |

Third votes for disputed cases: node 22 (V8, in the cloud image) and engine262, "an
implementation of ECMA-262 in JavaScript" whose first goal is "100% Spec Compliance" (documented,
github.com/engine262/engine262). Both run as subprocesses. The `observe` lane can **pre-triage the
corpus with NG against V8 before hocus exists**, so departures are known before the port meets
them (inferred).

## 3. Fuzzing

| Target | Tool | What it finds | Tier |
|---|---|---|---|
| parser: bytes → parse result | cargo-fuzz / libFuzzer, plus Boa-style grammar-aware generation with `arbitrary` (Boa's `parser-idempotency`: generate an AST, print it, parse twice, compare; `tests/fuzz/README.md`) | panics, hangs, non-idempotent parse | G0 smoke (60 s) when the parser changes; G2 hours |
| parse acceptance, differential | compile only, on hocus and on NG | SyntaxError parity (test262 has 4,629 parse-negative tests) | G2 |
| bytecode reader | cargo-fuzz; NG's `fuzz.c` does exactly this for `JS_ReadObject` (51 lines) | NG says untrusted bytecode "is equivalent to executing untrusted native code" (`SECURITY.md:7-14`, documented). hocus can do better: a reader in `#![forbid(unsafe_code)]` plus a verifier, so a fuzz crash is always a bug | G0 smoke, G2 |
| regexp compile and exec, differential | cargo-fuzz on (pattern, flags, input) against C `lre_*` | match and capture divergence | G0 smoke in the regexp lane, G2 |
| dtoa and number↔string | random and edge-case doubles against C; exhaustive over float32 (2^32 inputs) is feasible on G2 (hypothesis: a few minutes on 48 cores) | formatting divergence | G1 sample, G2 exhaustive |
| whole engine | **Fuzzilli** with a hocus profile | crashes, panics, debug-assert failures | G2, a weekly campaign |
| whole engine, differential | replay Fuzzilli's saved corpus through `hocus-diff` (hocus against NG) | semantic divergence | G2 |
| GC | GC stress: a full collection before every allocation, NG's `FORCE_GC_AT_MALLOC` (`quickjs.c:88,1682`). NG runs it on every PR with ASan over `make test`, skipping `tests/test_builtin.js` (`.github/workflows/gc-stress.yml`, `gc-stress-skip.txt`) | missing roots, premature frees | G1: R0 and NG `tests/`; G2: all of test262 |
| the unsafe core | **Miri** on the unit tests of `hocus-gc` and the value/heap modules, with `-Zmiri-tree-borrows` too | UB, aliasing violations, leaks. Miri "has no access to most platform-specific APIs or FFI", needs nightly, and Tree Borrows is "even more experimental" (documented, rust-lang/miri README) | G2 |
| the unsafe core | ASan and TSan (`-Zsanitizer`, nightly) on test262 R0–R3; loom for the cross-isolate sharing primitives once they exist | memory errors and data races | G2 |

**Fuzzilli details** (source-observed at `be2811d`):
- Targets exist for JSC, SpiderMonkey, V8, XS, Duktape, JerryScript, njs, QJS, QTJS and Serenity
  (`Targets/`).
- The QJS target is one 318-line patch against Bellard's `2788d71`. It adds the REPRL file
  descriptors 100–103, a `--reprl` loop and `__sanitizer_cov_trace_pc_guard`. The profile has
  `timeout: 250` ms and `maxExecsBeforeRespawn: 1000` (`Profiles/QjsProfile.swift`).
- For hocus, `hocus-cli --reprl` is about 150 lines of Rust plus Fuzzilli's `coverage.c` compiled
  with `cc`. It needs a `fuzzilli()` builtin for the startup crash checks.
- Measured: `rustc 1.97.0 -Cpasses=sancov-module -Cllvm-args=-sanitizer-coverage-level=3
  -Cllvm-args=-sanitizer-coverage-trace-pc-guard` on stable emits undefined references to
  `__sanitizer_cov_trace_pc_guard` and `_init`, which `coverage.c` defines. Coverage-guided
  Fuzzilli therefore works on stable, without ASan (hypothesis until a full run).
- Fuzzilli's differential mode (`processArgsReference`, `Profile.swift:17-18,44`; V8's
  "Dumpling" profile) runs one binary twice with different flags (`main.swift:581-587`). Use it for
  hocus's own tiers (interpreter against a later JIT), not for hocus against NG.
- Fuzzilli is Swift. It runs on the rented server, not in lanes (inferred).
- cargo-fuzz "requires the nightly compiler since it uses the `-Z` compiler flag to provide
  address sanitization" (documented, rust-fuzz book). Lanes may run `cargo +nightly fuzz` smoke
  runs only if `pins.toml` pins a nightly date (open question 4).

## 4. Benchmarks

### 4.1 Suites

| Suite | Content | Why | Source |
|---|---|---|---|
| **Svelte workloads** (primary) | w-signals (the real Svelte 5.57.2 runtime: 100 `$.state`, 100 `$.derived`, 100 `render_effect`, 10×5 `$.set` + `flushSync`), w-proxy (deep `$.proxy` over 200 rows), w-appmix (shapes, closures, templates, filter/map/join), w-props (monomorphic kernel) | what the port must win. On stock NG 0.16.2, `JS_CallInternal` was 71% self time and `find_own_property` 7.8%; V8 was ~20× faster on signals and ~27× on app glue | svelters `docs/notes/quickjs-jit-cranelift.md:265-322` (measured there, loaded host ±25%) |
| keyed list | `bench/keyed-bench` (1,000 rows × 3 widgets, `script.json` clicks), via svelters-host's log backend | the product loop: JS plus the op layer | svelters `bench/keyed-bench`, `docs/notes/p5-integration.md:59-67` |
| **bench-v8 v7** | Richards, DeltaBlue, Crypto, RayTrace, EarleyBoyer, RegExp, Splay, NavierStokes | the number QuickJS reports. Bellard's 2026-06-04 release is "42% faster than the previous release based on the bench-v8 (aka v8-v7) score". Boa's nightly comparison uses it too | bellard.org/quickjs (fetched today); shipped in `quickjs-extras-2026-06-04.tar.xz`; boajs.dev/benchmarks |
| NG `tests/microbench.js` | per-operation microbenchmarks | instruction-count regressions per opcode family | quickjs-ng `tests/`, `make microbench` (`Makefile:140`) |
| startup and memory | runtime and context creation, loading the Svelte bundle's bytecode, `-m` statistics | QuickJS claims "less than 300 microseconds" for a whole runtime life cycle (documented). The measured NG test262 average: 173,022 B allocated per test, 703 atoms, 221 objects | `run-test262 -m` (measured) |
| later | JetStream 3 (released 2026-03-31, documented by blog.chromium.org) shell-runnable subsets; Web Tooling Benchmark (acorn, babel…) | broader JS | not verified that JetStream 3 runs in a plain shell |

### 4.2 Measuring in the cloud and on a quiet machine

Measured on this container, with `qjs` 0.17.0 Release and `appmix.js` (300 rounds):

| Condition | Wall time, 6 runs (ms) | Instructions (cachegrind `--cache-sim=no`, valgrind 3.22) |
|---|---|---|
| quiet-ish (load ≈ 2.2) | 129, 133, 182, 145, 142, 130 | 1,490,697,009 ×2 |
| 3 competing `qjs` loops | 252, 203, 336, 296, 335, 288 | 1,490,697,009 ×2 |

Wall time spread 41% with no load change and doubled under contention. The instruction count did
not move by one. So:
- **Gate on instruction counts** (gungraun, formerly iai-callgrind; rust-lang/rust PR 150080
  records the rename). Count both NG (through `hocus-oracle-sys`) and hocus on the same workload
  under the same valgrind, and track **hocus/NG instruction ratios**. Instructions miss cache and
  branch effects (rav1d's slowdowns included mispredicted branches and spills, memorysafety.org
  2024-09-10). So the ratio is a regression gate, not a speed claim (inferred). Any change in
  instruction count is real, so the ratchet can be tight: fail on more than +1% (inferred).
- **Wall-clock claims come only from a quiet machine**: the G2 rented server or the user's
  machine. Use interleaved engine rounds and the median of medians, as the JIT note's protocol
  did (64 warm-up batches, 16 timed). Every cloud number is marked "cloud, not a gate", as the
  sibling repositories require (`cadatomic/CLOUD.md` §4, `cadabra3/docs/plan/gates.md`).
- Seeds: `Math.random` is fixed and there is no ASLR-dependent hashing. NG has none (hypothesis,
  consistent with the identical counts above).

## 5. The C→Rust porting method

### 5.1 Evidence from earlier ports

| Port | Method | Outcome | Source |
|---|---|---|---|
| rav1d (dav1d, AV1 decoder) | c2rust, then a manual safe rewrite; dav1d's tests in CI "from the beginning" | estimated 7 person-months, took "more than 20 … with a team of 3"; 3.8% slower after transpiling, 11% at the peak, under 6% at the end; the main work was thread sharing (`Mutex`, `DisjointMut`), pointers turned into indices and stack zeroing | memorysafety.org 2024-09-09 and 2024-09-10 (documented) |
| libbzip2-rs, libzstd-rs | c2rust, then cleanup; differential fuzzing ("same input bytes … same output bytes"); Miri | good for the library; c2rust drops conditional compilation ("we only get the unix version"); goto and switch fallthrough give messy code; the tests were ported "later than we should have" | trifectatech.org 2025-03-10 (documented) |
| fish shell (C++) | hand port by component through autocxx, shipping at every step | 57k lines of C++ became 75k lines of Rust, about 2 years, 200+ authors; "keeping fish working at each step" kept the end-to-end tests usable | fishshell.com 2024-12-28 (documented) |
| c2rust applied to QuickJS | — | not found (web search today) | — |

### 5.2 What quickjs.c looks like to a porter

Measured on `7a23d8e`:
- `quickjs.c` has 64,954 lines; `quickjs-libc.c` 5,059; libregexp 3,522; libunicode 2,074 plus a
  5,160-line table; dtoa 1,619; `cutils.h` 2,021.
- There are 252 opcodes (`quickjs-opcode.h`) and 240 predefined atoms (`quickjs-atom.h`).
- 2,243 `goto`, no `setjmp`, 97 `#if` and 192 `#define` lines.
- Builtins are also partly JS: `builtin-array-fromasync.js` and `builtin-iterator-zip*.js` are
  compiled to `.h` bytecode.

Heuristic subsystem split, by function-name prefix. My script found 1,611 function bodies covering
56,404 lines (`scratchpad/tools/funcs.py`):

| Subsystem | Lines | Functions |
|---|---:|---:|
| parser and tokenizer | 6,958 | 70 |
| compiler, emitter, resolver, modules | 5,340 | 94 |
| interpreter (`JS_CallInternal`, one function) | 2,934 | 1 |
| conversions, operators, numbers | 4,020 | 149 |
| Array and iterators | 3,704 | 84 |
| Object, Function, Error, Symbol, Global, Math, Date, JSON | 3,731 | 145 |
| atoms and strings core | 3,378 | 110 |
| objects, shapes, properties | 3,032 | 82 |
| TypedArray, ArrayBuffer, DataView, Atomics | 2,504 | 59 |
| runtime, context, public API | 2,422 | 130 |
| Promise, async, generators, jobs | 2,087 | 72 |
| bytecode reader and writer (bjson) | 1,936 | 61 |
| GC, refcount, memory | 1,883 | 72 |
| String and RegExp builtins | 1,376 | 28 |
| Map, Set, WeakRef, FinalizationRegistry | 1,327 | 38 |
| Proxy and Reflect | 998 | 29 |
| unclassified (e.g. `add_eval_variables`, `get_lvalue`, `js_call_c_function`, `build_for_in_iterator`) | 8,774 | 387 |

### 5.3 c2rust for QuickJS: not recommended (inferred)

1. c2rust's output keeps the C ownership model: raw pointers plus manual
   `JS_FreeValue`/`JS_DupValue` refcounting. Replacing that model (GC, handles, no unsafe
   everywhere) is the whole purpose of the port. rav1d spent most of its 20 person-months on exactly
   that refactor, starting from transpiled code.
2. NG's interpreter uses computed-goto dispatch unless `DIRECT_DISPATCH` is 0
   (`quickjs.c:51-55`). It has 2,243 `goto`s, and c2rust handles these by emitting loop and label
   state machines (trifectatech).
3. Conditional compilation collapses to one configuration.
4. We don't need a Rust copy of QuickJS as an oracle. The C engine links directly through
   `hocus-oracle-sys`.

c2rust may still help with *data*: the Unicode tables (`libunicode-table.h`), the opcode and atom
tables, and dtoa's constant tables. Generators are better for those (§5.4).

### 5.4 The recommended method: hand port by subsystem, C as the oracle

1. **Tests first (red/green, as in cadabra3).** For each subsystem, enable the test262 rung and
   corpus slice that cover it before writing code, and record the failing state in the ratchet.
   NG's `tests/*.js` and `lre-test.c` come over first. They are JS and run unchanged on the hocus
   runner.
2. **Faithful at the function level, idiomatic at the type level.** A ported function keeps the
   order of observable operations: property gets, `ToPrimitive` calls and Proxy trap order.
   test262 checks these orders through Proxies (NG's own failures include trap-order cases such as
   `set-mutable-binding-idref-with-proxy-env.js`). Ownership changes at the type boundary:
   refcounted pointers become handles or GC references, and `JS_FreeValue` disappears or becomes a
   scope. The ownership note covers how; this note only requires that observable order stays and
   the oracle checks it.
3. **Generated tables, not hand-copied ones.** Opcodes, atoms, the Unicode tables and the
   intrinsic lists come from generator scripts that read the pinned C headers. One lane owns each
   generator; others never edit the output. This mirrors svelters' "generated files: rerun the
   generator, don't hand-merge".
4. **A porting ledger, as in cadabra3.** `porting/ledger.tsv` has one row per C function:
   - `c_file`, `c_function`, `c_lines@sha`, `subsystem`;
   - `status`: `todo | ported | verified | dropped | replaced`;
   - `rust_path`;
   - `verified_by`: test262 rungs and directories, corpus ids, fuzz targets;
   - `departures`;
   - `notes`.

   `porting/check.py` fails when a C function has no row, a `rust_path` item is missing, or a
   `verified` row has no `verified_by`. This copies cadabra3's `porting/check.py`, which checks
   rows, statuses and paths (`cadabra3/porting/README.md`). Traceability lives in the ledger,
   never in identifiers or comments (cadabra3: "RE traceability goes in the porting ledger, never
   in the code"). The QuickJS MIT notice (Bellard, Gordon, Noordhuis, Ibarra Corretgé, `LICENSE`)
   goes in `THIRD_PARTY_NOTICES.md`.
5. **A coverage map for scoping lanes.** A G2 job builds NG with `--coverage`, runs test262 one
   top-level directory at a time, and writes `porting/coverage.tsv` mapping C function to test262
   directories. A lane that takes functions gets their tests as its gate (hypothesis: about 70
   directory runs at about 1–3 min each with coverage).

### 5.5 Splitting the work into lanes without conflicts

- **Vocabulary is coordinator-owned:** `Value`, `Atom`, `JsString`, `Shape`, `Object`, `Realm`,
  the handle and GC API. Lanes own modules, never vocabulary (cadabra3: "One type per concept…
  Lanes may own modules, never vocabulary"). The core is the serial critical path: objects, shapes,
  atoms, the GC and the interpreter skeleton.
- **Leaves and tools run in parallel from batch 1** (§7), because the Theseus and bytecode seams
  let them be verified without the hocus core.
- **Builtins split by test262 directory.** One lane owns one builtin family. Its scope is the
  ledger rows for those C functions, and its gate is that test262 directory against NG's passes.
  Each builtin is a module that registers its function list. The only shared file is the
  intrinsics table, which takes additive merges that keep every row.
- **The interpreter has one owner** until the dispatch skeleton lands. After that it splits by
  opcode family: arithmetic and conversions, property access, calls and frames, control flow and
  exceptions, generators and async. Each family is gated by the opcodes' instruction-count
  benchmarks and the language rungs.
- **Merge order** follows `cadatomic/CLOUD.md` §1: infrastructure first, then the vocabulary,
  then the leaves, then the lanes that touch the intrinsics table and `Cargo.lock`.

## 6. Repository scaffold

### 6.1 Crates

| Crate | Content | `unsafe` |
|---|---|---|
| `hocus` | the facade: embedding API (later) | forbid |
| `hocus-bytecode` | generated opcode table, NG bytecode format reader and writer, verifier, disassembler | forbid |
| `hocus-compiler` | the parser, emitter and resolver. It emits `hocus-bytecode` data with a per-compile atom table, so it needs no heap and can be tested against NG on its own (inferred) | forbid |
| `hocus-regexp`, `hocus-unicode`, `hocus-num` | the libregexp, libunicode and dtoa ports, plus a `capi` feature that exports the C ABI for Theseus mode | forbid (except in `capi`) |
| `hocus-gc` | heap, handles, tracing | allowed, Miri-gated |
| `hocus-vm` | values, atoms, strings, shapes, objects, the interpreter, jobs and realms | allowed in listed modules only |
| `hocus-builtins` | one module per builtin family | forbid (aim) |
| `hocus-cli` | `hocus run`, `--reprl`, `observe` | forbid |
| `hocus-test262` | the runner with an `Engine` trait, implemented for hocus and NG | forbid |
| `hocus-oracle-sys` | the vendored NG source at the pin, built with `cc`; features `gc-stress`, `dump` | FFI |
| `hocus-oracle` | the observation record, canonical serializer, diff and triage CLI, corpus manifest; node and engine262 subprocess adapters | forbid |
| `hocus-bench` | gungraun instruction-count benches, the wall-clock harness and the workloads | — |
| `fuzz/` (outside the workspace) | cargo-fuzz targets | — |

Repository files:
- `pins.toml`: the quickjs-ng tag and sha, test262 (the submodule sha of that tag), svelte 5.57.2
  and the svelters sha, node, fuzzilli, the Miri/sanitizer nightly date, valgrind;
- `porting/` (`ledger.tsv`, `bootstrap.py`, `check.py`, `coverage.tsv`);
- `ratchets/`, one table each, moved only by `scripts/bless <table> "<reason>"` as in ecadabra's
  `ratchets/*.tsv`:
  - `test262.tsv` (per directory: total, NG pass, hocus pass);
  - `test262-expect.tsv` (every non-passing test and its status);
  - `oracle.tsv` (corpus: agree, departure, open);
  - `bench-ir.tsv` (instructions per bench, for hocus and NG);
  - `unsafe.tsv` (unsafe blocks per crate; it may only fall, like cadabra3's architecture
    ratchet);
- `departures.tsv`;
- `corpus/manifest.toml`: pointers to pinned sources, not copies;
- `scripts/cargo.sh` and `scripts/check.sh --fast|--batch|full`; `scripts/boundaries.sh` (no `cc`
  or C outside `-sys` and `capi`; forbid-unsafe crates; no NG types outside the oracle);
- `CLAUDE.md`, `CLOUD.md`, `STATUS.md` with a replaceable handoff block, `docs/fix-queue.md`.

### 6.2 Tiers

| Tier | Who, when | Runs | Budget, 4 cores |
|---|---|---|---|
| G0 lane | each lane, once at its end | fmt, clippy `-D warnings`, boundaries, `porting/check.py`, the lane's unit tests, the lane's test262 directories against the ratchet, its corpus slice, its fuzz target for 60 s (if nightly is pinned), its instruction-count benches | ~10–20 min |
| G1 batch | the consolidation, once | G0 for all crates; **full test262** (NG: 51 s on 3 threads; an early hocus interpreter may be 10–50× slower — hypothesis — so use a `release` + debug-assertions profile), checked against both test262 ratchets; the full differential corpus; the svelters fixture op logs once the engine is plugged in; all instruction-count benches against `bench-ir.tsv`; GC stress on R0 and NG `tests/` | ~45 min |
| G2 daily | the rented server (`scripts/remote/daily-check`, as in the siblings) | GC stress over all of test262; Miri; ASan and TSan; cargo-fuzz campaigns; a Fuzzilli campaign (weekly); exhaustive dtoa; wall-clock benchmarks; the coverage-to-ledger map; agent and Atomics tests with a real clock; a test262-HEAD canary (informational) | ~1–2 h |

"Record, don't fix" holds at consolidation, and a failure after a tier is a fix-queue row
(`cadatomic/docs/plan/gates.md`). CI is local and nightly. There are no GitHub Actions
(`cadatomic/CLAUDE.md`, user 2026-10-25).

## 7. The first batches

**Batch 0** (the coordinator alone, one session): the workspace skeleton and §6 files, `pins.toml`
with v0.17.0 and test262 at its submodule sha, and `porting/bootstrap.py` generating
`ledger.tsv` (1,611 or more `quickjs.c` rows, plus libregexp, libunicode, dtoa and cutils).
Gate: `check.sh --fast` is green; `check.py` passes; the row count matches the census.

**Batch 1** (six lanes, plus an optional seventh). None needs the hocus core:

| Lane | Scope (owns) | Gate |
|---|---|---|
| `oracle` | `hocus-oracle-sys` (NG at the pin, `cc`, `gc-stress` feature); `hocus-test262` (frontmatter parser, TOML config with NG's features and excludes, `$262` and agents, module loader, async `$DONE`, instruction budget, virtual clock hook, expectations and metrics, per-directory TSV); the NG `Engine` implementation | reproduces run-test262 on the pin: **82,220 executions, 40 failures = the errors file, 4,802 excluded, 5,736 skipped**, or explains each difference per test; two runs give identical output |
| `observe` | `hocus-oracle` (observation record, ES5 canonical serializer, diff, triage CLI); `corpus/manifest.toml`; NG and node subprocess adapters; `departures.tsv` seeded | NG-vs-NG runs are deterministic over the corpus; every NG-vs-V8 disagreement in NG `tests/`, svelters `tests/js` and the acorn and svelte samples is classified (§2.5) |
| `bytecode` | `hocus-bytecode`: opcode and atom generators from the pinned headers; NG bytecode reader, writer and verifier; disassembler; fuzz target | read then write is **byte-identical** for all corpus bytecode from pinned `qjsc -b` (NG `tests/`, test262 harness, the Svelte bundle); fuzz smoke with no panic outside `Err` |
| `regexp` | `hocus-regexp`, plus the libunicode parts it needs (`hocus-unicode` tables generator), and the `capi` Theseus shim | differential tests against `lre_compile`/`lre_exec` (regexp bytecode identity as a metric, results as the gate); NG linked with the Rust regexp passes `built-ins/RegExp`, `language/literals/regexp` and `annexB/built-ins/RegExp` exactly as stock NG does |
| `numbers` | `hocus-num`: dtoa, atod, radix and `toFixed`/`toPrecision`/`toExponential` paths, and the `capi` shim | 10^8 random doubles plus edge cases equal C; NG linked with the Rust dtoa passes `built-ins/Number`, `parseFloat`, `parseInt` and `language/literals/numeric` exactly as stock NG does |
| `bench` | `hocus-bench`: regenerate and commit the four Svelte workloads (rolldown, svelte 5.57.2), bench-v8 v7, `microbench.js`, the keyed-bench driver; gungraun binary benches of NG; the wall-clock harness for the quiet machine | `bench-ir.tsv` NG baseline; instruction counts identical across two runs; checksums match node |
| `core-spike` (optional) | a prototype of the value, heap and handle API per the GC and ownership notes, with Miri on its unit tests | a design note plus a compiling prototype; nothing lands in `hocus-vm` without the user's design decision |

Expected conflicts: the root `Cargo.toml` members, `Cargo.lock` and `pins.toml`. They are
additive, so keep every row. `hocus-unicode` is owned by `regexp`. `numbers` must not touch it.

**Batch 2 (sketch).**
- `vm-core`: values, atoms, strings, shapes, objects, the GC, and the interpreter skeleton running
  NG-compiled bytecode with minimal builtins, aiming at R0.
- `compiler`: tokenizer and parser, gated on "NG runs hocus bytecode with the same observation";
  byte identity tracked.
- `builtins-object-function`: R2 starts.
- `runner-hocus`: the hocus `Engine` implementation and REPRL.

**Batch 3 onward.**
- One lane per builtin family; the interpreter splits by opcode family.
- The svelters `JsEngine` implementation for hocus, through a request to svelters, because
  svelters sessions own svelters (`svelters/CLAUDE.md`).
- R3 ("Svelte-ready") as the milestone, with the instruction ratio against NG on the four Svelte
  workloads reported at each consolidation.

## Open questions for the user

1. **Port base and oracle version.** v0.17.0 (newer than svelters' 0.16.2) or v0.16.2 (the same
   as svelters today)? *Recommendation: v0.17.0, one source for port and oracle, built from source.
   The product seam doesn't care, because `expected.oplog` is the truth there.*
2. **Out-of-scope features.** Should Temporal, Intl and ShadowRealm be dropped from the ladder,
   with Bellard's QuickJS (which passes Temporal and intl402) as a later oracle for them?
   *Recommendation: out of scope until R5. Svelte doesn't use them.*
3. **Front end.** A faithful port of QuickJS's single-pass parser and emitter, which keeps the
   bytecode seam, or an oxc-based front end? *Recommendation: faithful first. Revisit after R3,
   when hocus's own bytecode diverges anyway.*
4. **Nightly toolchain** for Miri, sanitizers and cargo-fuzz only, pinned by date in `pins.toml`,
   never for building the product? *Recommendation: yes, G2 always; lanes only for 60 s fuzz
   smoke runs.*
5. **Fuzzilli** (Swift) on the rented daily server, as a weekly campaign? *Recommendation: yes,
   from batch 3, when hocus runs scripts.*
6. **Departures.** Who approves a `depart` that changes observable behaviour svelters may see?
   *Recommendation: the coordinator for test262-only cases; the user when svelters fixtures or app
   behaviour change.*
7. **Batch 1 size**: six lanes (seven with `core-spike`). *Recommendation: six. Run `core-spike`
   only after the GC and ownership decision.*
8. **Dashboard.** Keep pass-rate history private (a TSV in the repository and a private artifact)
   rather than public. *Recommendation: private.*

## Concrete first steps in this repository

1. Commit `CLAUDE.md`, `CLOUD.md` and `STATUS.md`, adapted from cadatomic's: tiers, "record, don't
   fix", "tests wait on events" and the departures rule.
2. Commit `pins.toml` (quickjs-ng v0.17.0 `6d46d07` and its test262 submodule sha; svelte 5.57.2;
   svelters `0aa41697`; valgrind 3.22; the nightly date).
3. Commit `porting/bootstrap.py` (start from `scratchpad/tools/funcs.py` and replace the heuristic
   prefixes with an explicit subsystem map), `porting/ledger.tsv` and `porting/check.py`.
4. Write `scripts/check.sh` with `--fast`, `--batch` and full, `scripts/bless`, and empty
   `ratchets/*.tsv` with headers naming their sources.
5. Write the batch 1 brief and the six lane files from §7. Push `claude/b1-<lane>` branches and
   start the sessions after the user approves the size.

## Sources

- quickjs-ng @ `7a23d8e`: `run-test262.c` (`:89-94` modes, `:595-880` agents, `:1751` leak dump,
  `:1786-1790` interrupt), `test262.conf`, `test262-fast.conf`, `test262_errors.txt`,
  `gc-stress-skip.txt`, `fuzz.c`, `Makefile:57-59,128-141`, `CMakeLists.txt:39-43,193-252`,
  `.github/workflows/{ci,gc-stress}.yml`, `SECURITY.md:7-14`, `quickjs.h:1276,1463`,
  `quickjs.c:51-55,88,18073,38501`, `LICENSE`.
- test262 @ `5ef1e572`: `INTERPRETING.md`, `harness/`, `test/` (counts measured).
- Boa @ `64c209f`: `test262_config.toml`, `tests/tester/src/exec/mod.rs`, `tests/fuzz/README.md`,
  `tests/fuzz/fuzz_targets/vm-implied.rs`, `tests/insta-bytecode/README.md`,
  `.github/workflows/test262.yml`.
- Nova @ `877ef16`: `tests/test262_runner.rs`, `tests/metrics.json`, `tests/expectations.json`,
  `tests/skip.json`.
- Fuzzilli @ `be2811d`: `Targets/README.md`, `Targets/QJS/`, `Sources/Fuzzilli/Profiles/`,
  `Sources/FuzzilliCli/main.swift`, `Sources/Fuzzilli/Execution/Execution.swift`.
- test262.fyi data: `https://data.test262.fyi/`, `meta.json`, `built-ins.json` (generated
  2026-10-25 03:26 UTC).
- bellard.org/quickjs (fetched 2026-10-25); boajs.dev/benchmarks (fetched 2026-10-25).
- memorysafety.org/blog/porting-c-to-rust-for-av1 (2024-09-09);
  memorysafety.org/blog/rav1d-performance-optimization (2024-09-10);
  trifectatech.org/blog/translating-bzip2-with-c2rust (2025-03-10); fishshell.com/blog/rustport
  (2024-12-28).
- rust-fuzz book (cargo-fuzz setup); github.com/rust-lang/miri README; github.com/engine262/engine262
  README; crates.io API for gungraun 0.20.0, iai-callgrind 0.16.1, cargo-fuzz 0.13.2, rquickjs
  0.14.0, oxc_parser 0.155.0, boa_engine 0.22.0, c2rust 0.22.1 (queried 2026-10-25);
  rust-lang/rust PR 150080 (the gungraun rename); blog.chromium.org 2026-03 (JetStream 3).
- svelters @ `0aa41697`: `docs/notes/quickjs-jit-cranelift.md`, `crates/svelters-host/src/{engine,bytecode}.rs`,
  `crates/svelters-host/tests/fixtures.rs`, `fixtures/`, `tools/oracle/README.md`,
  `experiments/native-compiler/conformance/run.mjs`, `bench/keyed-bench`, `CLAUDE.md`.
- cadabra3 @ `7bf8908`: `CLAUDE.md`, `porting/README.md`, `porting/ledger.tsv`,
  `docs/plan/gates.md`. cadatomic @ `75b6a47`: `CLAUDE.md`, `CLOUD.md`, `docs/plan/gates.md`,
  `pins.toml`. ecadabra @ `39187fc`: `ratchets/*.tsv`. parasolid-re: `CLAUDE.md` (reproduce /
  candidate departure).
- Measurements, this container (4 cores, shared, rustc 1.97.0, valgrind 3.22, cloud, not a gate):
  - `run-test262 -c test262.conf -t 3` (43,034 tests, 30 errors, 29 s) and `-m -a -t 3` (82,220,
    40 errors, 51 s), built from `7a23d8e` with CMake Release in `scratchpad/vm-bench/ng-build`;
  - `qjsc -b` determinism;
  - the appmix wall-clock and cachegrind runs (`scratchpad/vm-bench/appmix.js`);
  - the rustc sancov symbol check (`scratchpad/vm-bench/sancov`);
  - the `funcs.py` census;
  - the test262 and Svelte-runtime greps.
