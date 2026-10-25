# JIT compilation for hocus: copy-and-patch, Cranelift and the alternatives, judged against Svelte workloads

Researcher note, 2026-10-25. Topic: should hocus (a Rust port of QuickJS-NG) get a JIT? Which kind,
when, and what must the interpreter be designed to allow now? This note builds on
`/home/user/svelters/docs/notes/quickjs-jit-cranelift.md` (svelters `0aa41697`, "the svelters JIT
note" below). That note measured longbridge/quickjs-jit, a two-tier Cranelift JIT for QuickJS-NG: 20x
on a monomorphic kernel and a **tie** (0.93–1.06x) on the real Svelte 5 runtime. Its results are
not repeated here.

Local sources: QuickJS-NG `7a23d8e` (2026-10-24, version 0.17.0) in the scratchpad `src/quickjs-ng`;
svelte@5.57.2 from npm; svelters `0aa41697`; cadatomic `75b6a47`. Grades follow BRIEF.md:
`documented`, `source-observed`, `measured`, `inferred`, `hypothesis`.

## Summary

1. **New measurement: where the 15x goes.** I ran three Svelte-style workloads on V8 and JSC with
   their tiers switched off one at a time, next to QuickJS-NG 0.17.0 (measured, cloud, not a gate;
   §0). On the real Svelte 5 signals runtime, QuickJS-NG = 1.0x:

   | Tier stack | V8 (node 22) | JSC (bun 1.4.4) |
   |---|---:|---:|
   | Interpreter with inline caches only | 1.5x (Ignition) | 1.05x (LLInt) |
   | + baseline JIT | 2.8x (Sparkplug) | 4.2x (Baseline) |
   | + optimizing JITs | 14.3x (TurboFan) | 16.6x (DFG + FTL) |

   - An IC-equipped interpreter closes only 2–15% of the gap, counted on a log scale.
   - The baseline JIT closes 23–49% of it, and the optimizing tiers close the remaining 49–62%.
   - The optimizing tiers speculate and inline. They are also the most expensive part to build
     and maintain (inferred: V8, JSC and SpiderMonkey each run several of them with large teams).
   - The proxy and app-glue workloads show the same order (§0).
2. **This explains quickjs-jit's tie.** Its Tier 1 calls generic C helpers and still recurses
   through `JS_CallInternal` for every JS call. Its Tier 2 guards shapes, but its bounded inlining
   did not pay on these workloads. JSC's Baseline gets 4x over an interpreter of QuickJS's speed
   because it has inline polymorphic ICs, call ICs and JIT-to-JIT calls on a VM stack (inferred
   from §0, §3).
3. **Copy-and-patch** is the right technique for a baseline tier.
   - Paper numbers: it compiles ~100x faster than LLVM -O0, and its code is 14% faster than
     LLVM -O0's (Xu & Kjolstad, OOPSLA 2021).
   - Deegen's LuaJIT Remake compiles 19.1M bytecodes/s, about 91 bytes of machine code per
     bytecode. Its baseline JIT is only 33% slower than LuaJIT's tracing JIT.
   - CPython's copy-and-patch JIT is a cautionary tale. It was 4–12% faster in 3.15, with an LLVM
     build-time dependency. PEP 836 was withdrawn on 2026-10-19 after the Steering Council's ultimatum.
   - From Rust it works today on stable `rustc --emit=obj` plus the `object` crate (logicaffeine).
   - Rust's guaranteed tail calls (`become`) and `preserve_none` ABI are nightly-only, with
     stabilization hoped for in 2027. Stencils must be checked at build time instead.
4. **Cranelift is the wrong tool for a baseline tier.** Its compile speed is about 7x slower than
   Winch (inferred from the TPDE paper). It has no deopt or OSR support, and its releases are
   monthly and breaking. 0.137.0 (2026-10-20) requires rustc 1.97 (measured, crates.io).
   - Cranelift is a reasonable backend for a later optimizing tier.
   - It now has user stack maps (for a moving GC) and `try_call` exceptions.
5. **SpiderMonkey's design is the one to copy.**
   - CacheIR: inline caches as small linear programs. The Baseline Interpreter, the Baseline JIT,
     the Portable Baseline Interpreter and weval all share them.
   - Warp transpiles CacheIR into SSA, so the optimizing tier needs no separate type-inference
     system.
   - One IC language serves every tier, including the interpreter on iOS and wasm.
6. **AOT from the svelters compiler** is a complement, not a substitute.
   - Emitting engine bytecode directly saves parse time only. Bytecode is already cached.
   - A typed AOT path in the style of Static Hermes needs sound types. The Svelte runtime and user
     components don't have them. Static Hermes is still unshipped: Hermes V1 (2025) excludes it.
   - The useful AOT is **weval-style**: compile the bundled bytecode ahead of time with the same
     stencils as the baseline JIT, ICs still at run time. weval measured 2.17x over an IC
     interpreter in SpiderMonkey, with no runtime code generation, so it works on iOS.

**Recommendation.**

- **Build no JIT in year one.**
- **Design the interpreter so a JIT can be added later.**
  - JS-to-JS calls on a VM stack, not on the Rust stack.
  - A fixed, documented frame layout that holds every live value.
  - An IC slot operand on every property, call and arithmetic op.
  - ICs in a CacheIR-style IC language, interpreted at first.
  - Opcode bodies as shared helper functions with one ABI.
  - Bytecode liveness and pc maps.
  - A code-memory and platform capability layer.
- **Ship interpreter ICs first.** Expect 1.2–1.6x on Svelte code (§4).
- **Build a copy-and-patch baseline tier next, and only on a trigger.** The trigger is that JS
  execution is the bottleneck in presto frames after the interpreter work (§6). Expect 2–4x over the
  QuickJS-NG baseline, in a few months of work.
- **Treat an optimizing tier as a separate, later decision.** That is where V8's remaining 4–5x
  lives, and it costs years. V8 (deno_core) stays the lever if that throughput is ever needed.

## 0. Measurement: tier-by-tier decomposition on Svelte workloads

**Question.** The svelters JIT note gives V8 ≈ 20x QuickJS on Svelte signals. How much of that comes
from inline caches in the interpreter, how much from a baseline JIT, and how much from optimizing
tiers? Both V8 and JSC can switch their tiers off, so the gap can be decomposed on the same JS.

**Setup** (measured, cloud, not a gate):
- Machine: 4-core Intel Xeon @ 2.80GHz, Linux 6.18 VM, shared with seven other researchers. Load
  average 1.0–3.5 during the runs.
- Engines:
  - QuickJS-NG 0.17.0 at `7a23d8e`, `qjs` built with CMake Release (`-O3`, gcc 13.3).
  - node v22.22.0 (V8 12.4.254.21).
  - bun 1.4.4 (JSC).
- Workloads: svelte@5.57.2 `src/internal/client/index.js`, bundled with
  `bun build --format=iife --conditions=production` (118 KB). Each workload is modelled on the
  svelters note's workload of the same name. They were rewritten here because those files are not
  on this machine, so absolute numbers differ from that note's.
  - `signals`: 100 `$.state`, 100 `$.derived(a+2b+c)` and 100 `$.render_effect`. One call is
    10 × (5 `$.set` + `$.flush()`).
  - `proxy`: `$.proxy` over 200 `{id,text,done}` rows, one render effect per row, and a derived count
    that iterates the proxy. One call is 10 × (toggle + edit + flush).
  - `appmix`: non-reactive glue over four object shapes: closure dispatch, template strings and
    `filter/map/join`.
- Protocol (the svelters note's shape): 640 warm-up calls, then 16 timed batches of 10 calls; the
  value is a batch's median ms per 10 calls. There were 5 interleaved rounds per engine
  configuration. The table gives the **minimum over the 5 rounds**: the host was shared, and QuickJS's
  per-batch time is bimodal (16–30 ms; probably cycle-collector pauses, not verified). Checksums
  matched across all engines.
- Tier switches:
  - V8 `--no-opt --no-maglev --no-sparkplug`: Ignition only.
  - V8 `--jitless`: Ignition with no codegen at all.
  - V8 `--no-opt --no-maglev`: + Sparkplug.
  - V8 `--no-opt`: + Maglev.
  - JSC `BUN_JSC_useJIT=0`: LLInt only.
  - JSC `BUN_JSC_useDFGJIT=0`: LLInt + Baseline.

**Results** (ms per 10 calls, lower is better; the factor is speed relative to QuickJS-NG):

| Engine / tiers | signals | × | proxy | × | appmix | × |
|---|---:|---:|---:|---:|---:|---:|
| QuickJS-NG 0.17.0 (interpreter, no ICs) | 16.94 | 1.00 | 52.83 | 1.00 | 8.51 | 1.00 |
| JSC LLInt only (interpreter, monomorphic ICs) | 16.08 | 1.05 | 80.03 | 0.66 | 10.39 | 0.82 |
| V8 Ignition only (interpreter, full IC system) | 11.30 | 1.50 | 42.81 | 1.23 | 5.18 | 1.64 |
| V8 `--jitless` | 11.76 | 1.44 | 44.51 | 1.19 | 5.36 | 1.59 |
| V8 Ignition + Sparkplug (baseline) | 6.13 | 2.76 | 30.34 | 1.74 | 3.91 | 2.18 |
| V8 + Maglev (mid-tier) | 6.17 | 2.75 | 30.74 | 1.72 | 4.03 | 2.11 |
| JSC LLInt + Baseline JIT | 4.04 | 4.19 | 24.02 | 2.20 | 2.85 | 2.98 |
| V8 full (+ TurboFan) | 1.19 | 14.26 | 15.79 | 3.35 | 1.31 | 6.49 |
| JSC full (+ DFG + FTL) | 1.02 | 16.62 | 8.48 | 6.23 | 0.93 | 9.17 |

Median-of-rounds values agree within ±25%, except QuickJS signals (median 26.9 ms). Using medians
would *raise* every engine's ratio over QuickJS. Raw output: scratchpad `jit/results.txt`;
harness: Appendix A.

**Reading it** (inferred):
- **Interpreter ICs alone are worth 1.2–1.6x** (V8 Ignition). That interpreter has V8's
  hidden classes, feedback vectors and IC handlers, but no JIT code.
  - JSC's LLInt caches per instruction, e.g. `get_by_id` holds one cached structure and offset
    (documented, WebKit blog, below); that its caches are weaker than the JITs' is the blog's
    own statement. It runs at QuickJS speed or slower.
  - This matches the svelters note's estimate of 1.1–1.3x for interpreter ICs. It also matches
    PBL's 1.26x on Octane (§3.5).
- **A baseline JIT adds 1.3–1.8x** in V8's case, where Sparkplug calls the same IC handlers.
  - It adds **3.3–4.0x** in JSC's case, where Baseline brings inline polymorphic ICs and call
    linking.
  - So the shape of the baseline tier matters more than its existence. Helper-call-per-opcode code
    (quickjs-jit Tier 1, Sparkplug) gains less than code with inline IC fast paths (JSC Baseline,
    Deegen).
- **Maglev added nothing** on these workloads. I did not find out why; it may not trigger within
  640 calls.
- **The optimizing tiers add 3–5x on signals and appmix.** That is V8 6.17 → 1.19 ms and JSC
  4.04 → 1.02 ms. On a log scale it is about half or more of the total gap. It needs speculation,
  inlining of the tiny `get`/`set` calls, and deopt.
- **Proxies are slow everywhere.** No tier gets more than 6x on the proxy workload. This supports
  the svelters advice to keep hot widget plumbing on `$state.raw` and signals.
- `--jitless` ≈ Ignition only, so regexp compilation is irrelevant here.

**Caveat.** The engines differ in more than JIT tiers: object model, GC, builtins. The JSC row
pair (LLInt vs Baseline, same object model) and the V8 row pair (Ignition vs Sparkplug) isolate the
baseline-JIT effect cleanly. The QuickJS-vs-Ignition step mixes ICs with V8's whole object model,
so it is an upper bound for "add ICs to QuickJS".

## 1. Copy-and-patch

### 1.1 The paper (Xu & Kjolstad, OOPSLA 2021)

- **Technique.** A library of binary "stencils" is compiled ahead of time by clang/LLVM from C++
  templates. Each stencil has "holes" left as relocations for constants, jump targets and called
  functions. At run time the compiler copies stencil bytes and patches the holes. There is no IR,
  no register allocation and no instruction selection at run time (documented,
  https://2021.splashcon.org/details/splash-2021-oopsla/40/Copy-and-Patch-Compilation-A-Fast-Compilation-Algorithm-for-High-Level-Languages-and).
- **Numbers (abstract).**
  - On TPC-H query compilation it generates code "two orders of magnitude faster than LLVM -O0
    and three orders of magnitude faster than higher optimization levels".
  - The code "runs an order of magnitude faster than interpretation and 14% faster than LLVM -O0".
  - Its WebAssembly compiler generates code 4.9–6.5x faster than V8's Liftoff baseline (documented,
    same URL).
  - These are the authors' numbers on their own workloads.

### 1.2 Deegen and LuaJIT Remake (Haoran Xu)

Source: Xu & Kjolstad, "Deegen: A JIT-Capable VM Generator for Dynamic Languages", arXiv
2411.11469v2 (https://arxiv.org/html/2411.11469v2), and LLVM Dev Meeting 2023 slides
(https://www.llvm.org/devmtg/2023-10/slides/techtalks/Xu-Deegen-LLVM-Based-Compiler.pdf).

- **The interpreter is generated from semantic descriptions.** Each bytecode's semantics is
  written once as a C++ function. Deegen lowers it through LLVM IR, optimizes it and emits one
  native function per bytecode (documented, §3, §6.2).
  - Handlers chain by guaranteed tail calls, in continuation-passing style.
  - It uses LLVM's GHC calling convention, so six VM state values stay pinned in registers: "GHCcc
    supports guaranteed-tail-call, allowing us to do continuation-passing" (documented, §6.1).
- **The baseline JIT is generated from the same descriptions by copy-and-patch** (documented,
  §7.2).
  - The object files of the per-bytecode code are parsed into stencils for the main path and the
    IC stubs.
  - Runtime constants ("burn in runtime constants") become holes.
  - With interpreter dispatch removed, "the JIT fast path simply falls through to the next
    bytecode".
- **Inline caching in baseline code** (documented, §7.1).
  - Each IC site has a self-modifying code region that starts as a jump to the miss handler.
  - New IC stubs are copy-and-patched at run time and chained from that branch. Small stubs are
    placed inline ("inline slab optimization").
  - Polymorphic IC is modelled in LLVM by repurposing the `CallBr` node, because "LLVM is simply not
    designed for this".
  - Calls use a "direct/closure dual-mode call IC". A generic IC mechanism lets users express
    arbitrary IC semantics (§5.2).
  - The interpreter uses monomorphic ICs with optional quickening (§6.1). IC is "the only
    high-level optimization in our baseline JIT" (§8).
- **Results** (documented, §8.3; 44 benchmarks, i7-12700H, GC disabled in all engines because LJR has
  none):

  | Result | Value |
  |---|---|
  | Interpreter vs PUC Lua | 179% faster |
  | Interpreter vs LuaJIT's interpreter | 31% faster |
  | Baseline JIT vs PUC Lua | 360% faster |
  | Baseline JIT vs LuaJIT's optimizing tracing JIT | 33% slower, faster on 13/44 benchmarks |
  | Compile throughput | 19.1M Lua bytecodes/s, 1.62 GiB/s of machine code, 91 bytes per bytecode on average |
  | Size of bytecode semantics | 1,500 logical lines (42 definitions, expanded to 255 variants) vs LuaJIT's 4,500 lines of x64 asm |

- **Limitations** (documented, §3): x86-64 only, GC-agnostic, no concurrent compilation, and no C
  API compatibility. The optimizing tier is "future work".
- **For hocus** (inferred):
  - Deegen is the existence proof that *one* semantic description can drive both a fast
    interpreter and a baseline JIT with inline ICs.
  - It is also the closest analogue of JSC Baseline's gain in §0.
  - It depends on two LLVM features that stable Rust does not expose: guaranteed tail calls and a
    custom calling convention (§1.4).

### 1.3 CPython's JIT (3.13–3.16)

- **Design.** PEP 744 copy-and-patch: stencils are compiled from the interpreter's micro-op
  definitions by LLVM at CPython *build* time. It was experimental in 3.13 and 3.14 (documented,
  https://peps.python.org/pep-0774/).
- **Measured speedups.**
  - 3.13/3.14: "the original JIT was often slower than the interpreter"; early tracing "yielded no
    speedups initially in 3.13 and 3.14" (documented, Ken Jin, https://blog.python.org/2026/03/jit-on-track/,
    2026-03-23; PEP 836).
  - 3.15 alpha: about 11–12% faster than the tail-calling interpreter on macOS AArch64 and 5–6% on
    x86-64 Linux. Individual benchmarks range from "a 20% slowdown to over 100% speedup" (same blog).
  - PEP 836's appendix (2026-06-16..27): M3 Pro 1.126x, AmpereOne 1.073x, i5-8400 1.069x, Windows
    Ryzen 1.047x. The PEP sums up "about 4-12% geometric mean" across Tier 1 platforms (documented,
    https://peps.python.org/pep-0836/).
- **Problems the team names** (documented, Ken Jin, https://fidget-spinner.github.io/posts/faster-jit-plan.html,
  2025-11-08):
  - The monomorphic inline caches give poor trace-selection data.
  - Reference counting forces spills, because any decref can run `__del__`.
  - Naive stencils "without much optimizations" don't yield much. Branch layout added extra jumps.
  - The fixes were incremental: branch inversion about +1%, top-of-stack caching about +0.5%,
    refcount elimination.
- **The LLVM build-time dependency** (documented):
  - The build needs a specific LLVM (16, then 19; LLVM 20/21 in 3.15).
  - PEP 774 proposed checking in pre-generated stencils (about 7.2 MB for six platforms, per its
    discussion thread). It was deferred on 2025-03-14 until the JIT shows more performance gains
    (PEP 836's account), then withdrawn together with PEP 836
    (https://discuss.python.org/t/pep-774-removing-the-llvm-requirement-for-jit-builds/78548).
  - PEP 836 says supporting multiple LLVM versions "is required".
- **Whether it pays** (documented):
  - On 2026-06-05 the Steering Council asked for a Standards Track PEP within six months, or the JIT
    would leave `main`
    (https://www.linuxcompatible.org/story/python-316-alpha-1-released-whats-new-and-the-jit-debate-ahead/).
  - PEP 836 ("JIT Go Brrr", 2026-07-02) set a bar: at least 20% geomean for JIT + free-threading by
    the first 3.17 beta. It admitted: "We do not have a strong pulse on whether the JIT currently
    benefits larger real-world workloads".
  - Its authors withdrew it on 2026-10-19 for lack of "personal bandwidth"
    (https://discuss.python.org/t/pep-836-jit-go-brrr-the-path-to-a-supported-jit-compiler-for-cpython/108010/57).
  - The Faster CPython team had lost its main sponsor in 2025 (Ken Jin's blog).
- **Lessons for hocus** (inferred):
  1. Copy-and-patch of *generic* opcode bodies gains little over a good interpreter. CPython's
     baseline was already a specializing interpreter with inline caches (PEP 659), so the JIT
     removes only dispatch. Gains come from the inline IC fast paths and from call handling.
  2. Making a C toolchain a build dependency is a real cost. A Rust project can avoid it (§1.4).
  3. A JIT needs a measured, product-level win and more than one maintainer per stage, or it dies
     politically even when it works technically.

### 1.4 Copy-and-patch in a Rust project

- **Stencils from Rust are feasible on stable today.** The `logicaffeine-forge` crate is an example
  (documented, https://github.com/Brahmastra-Labs/logicaffeine/blob/main/crates/logicaffeine_forge/README.md):
  - Its `build.rs` compiles a `#![no_std]` file of 94 stencil functions with `rustc --emit=obj`
    and no linker.
  - It reads code and relocations with the `object` crate, normalized across ELF, Mach-O and COFF.
  - Holes are undefined extern symbols that LLVM cannot fold: `logos_hole_cont_N` for
    continuations, `LOGOS_HOLE_I64_N` for constants.
  - Two build gates: "leaf purity" (a relocation to a non-hole symbol is an error), and every
    continuation must decode as an unconditional `jmp`/`b`.
  - Its relocation kinds: Branch26, Page21, PageOff12, GotPage21, GotPageOff12, Rel32, GotRel32 and
    Abs64. It runs on x86-64 and aarch64 (Linux, macOS, Windows), and the crate is off on wasm32.
  - It publishes no performance numbers.
- **A nightly-ABI probe found the sharp edges** (documented, gandr-lang/gandr issue 515, 2026-10-17,
  https://github.com/gandr-lang/gandr/issues/515; rustc 1.98-nightly, LLVM 22.1.7):
  - It used `extern "rust-preserve-none"` stencils ending in `become`, plus `#![no_builtins]` so
    copies don't turn into `memcpy` calls.
  - On aarch64 Mach-O, operands stay GOT loads under every relocation model. The patcher must relax
    GOT loads to `movz`/`movk`/`adrp` itself.
  - x86-64 PIC routes continuations through the GOT, so stencils need `relocation-model=static`.
  - Identical stencils get merged into one symbol, leaving an empty stencil. The generator must
    refuse empty stencils.
  - The compiler assumed an extern static's address is non-null, which broke a zero immediate.
    Immediates were biased by one.
  - Branches have a ±128 MiB range on aarch64.
  - Result: 0.65–0.74 ns per step natively vs 53–59 ns in its interpreter loop, and about 0.15 µs
    to emit each command.
- **Rust language status** (documented):
  - `explicit_tail_calls` (`become`, tracking issue rust-lang/rust#112788) and `loop_match` are
    unstable. The 2026 project goal aims to "hopefully stabilize in 2027"
    (https://trifectatech.org/blog/tail-calls-project-goal/, 2026-04-14;
    https://goals.rust-lang.org/2026/tail-call-loop-match.html).
  - `extern "rust-preserve-none"` is unstable (`rust_preserve_none_cc`, tracking issue #151401).
- **Two ways to get stencils into hocus** (inferred):

  | Path | Build dependency | Tail-call guarantee | Risk |
  |---|---|---|---|
  | Stencils in Rust, compiled by our own rustc (`--emit=obj`, `object` crate) | none beyond rustc | build-time check that each continuation is a `jmp` (logicaffeine's gate); `become` later | LLVM codegen drift between rustc versions; a gate failure blocks the build, not users |
  | Stencils in C, compiled by clang (CPython/Deegen) | a pinned clang/LLVM | `musttail`, `preserve_none` in clang | the CPython LLVM-version problem |

  - The Rust path wins: the stencil compiler *is* the toolchain we already pin (rustc 1.97).
  - The generated stencil tables can also be checked in, as PEP 774 proposed, with a CI check that
    regenerates and diffs them.
- **Stencil semantics should be the opcode helper bodies** (inferred). Then the interpreter and
  the JIT share one definition, as in Deegen. Until tail calls are stable, the interpreter
  dispatches with `loop { match }`, and the stencils are the same bodies compiled as
  continuation-passing leaf functions.

### 1.5 Platform issues (any JIT)

| Platform | Rule | Source |
|---|---|---|
| macOS, Apple silicon | W^X per thread. `mmap(MAP_JIT)`, then `pthread_jit_write_protect_np` or the newer `pthread_jit_write_with_callback_np` with an allow-list (`com.apple.security.cs.jit-write-allowlist`, which then forbids `write_protect_np`). `sys_icache_invalidate` before running new code. A hardened runtime needs `com.apple.security.cs.allow-jit` and gets only **one** `MAP_JIT` region | documented, https://developer.apple.com/documentation/apple-silicon/porting-just-in-time-compilers-to-apple-silicon |
| iOS / iPadOS | No JIT for third-party apps. `pthread_jit_write_protect_np` "isn't available on iOS". BrowserEngineKit's EU exception applies only to browser engines | documented (same page); cadatomic `round2/js-host-local.md` @ `75b6a47` (Apple DTS quotes) |
| Windows | `VirtualAlloc(RW)` → `VirtualProtect(RX)` → `FlushInstructionCache`. With Control Flow Guard, executable pages are valid call targets by default unless the JIT passes `PAGE_TARGETS_INVALID` and registers entries with `SetProcessValidCallTargets`. Arbitrary Code Guard (`ProhibitDynamicCode`) forbids JIT entirely | documented, https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard, https://learn.microsoft.com/en-us/windows/desktop/api/memoryapi/nf-memoryapi-setprocessvalidcalltargets; ACG via a third-party write-up (ired.team) |
| Linux | `mmap(RW)` → `mprotect(RX)`. On aarch64 an explicit I-cache flush is needed, because "mprotect does not flush it" | documented, logicaffeine README |
| wasm32 | No runtime native code. A browser host could generate and instantiate wasm modules, but that is a different backend | documented, logicaffeine README (crate gated off wasm32); PBL header (below) |

**Consequence** (inferred): the interpreter is the product on iOS, on wasm and under ACG. Any JIT
is an accelerator that a capability probe can switch off. quickjs-jit already has such a probe
(svelters JIT note §1).

## 2. Cranelift

- **Current version:** `cranelift-codegen` 0.137.0 (2026-10-20), MSRV **1.97.0**. 0.136.x needs
  1.96 and 0.135.x needs 1.95 (measured: crates.io API, 2026-10-25).
  - Releases are monthly, in lockstep with Wasmtime (Wasmtime 50.0.0 on the same day).
  - quickjs-jit pinned 0.116, 21 releases behind (svelters JIT note).
  - A Cranelift tier means either a monthly API port or a pin that ages. Our `rust-version = "1.95"`
    already excludes the latest two releases.
- **Compile speed vs code quality** (documented, TPDE paper §6.2, arXiv 2505.22610,
  Wasmtime/Sightglass + PolyBench):
  - The TPDE back-end compiles 4.27x faster than Cranelift and 2.68x faster than Cranelift with its
    fast register allocator. It is 1.74x *slower* than Winch.
  - Its code runs 1.14x faster than Winch's and 1.64x slower than Cranelift's.
  - Implied ratios (inferred): **Winch compiles ~7.4x faster than Cranelift; Cranelift's code is
    ~1.9x faster than Winch's.**
  - In TPDE's Cranelift adapter, 37% of the time went into translating Wasm to CLIF.
  - Copy-and-patch is about another order of magnitude below single-pass compilers (Liftoff
    comparison in §1.1).
- **Winch** is Wasmtime's single-pass baseline compiler with no IR. Its docs say it prioritizes
  compile time over code quality (documented,
  https://docs.wasmtime.dev/examples-fast-compilation.html; aarch64 support from Wasmtime 35,
  https://bytecodealliance.org/articles/winch-aarch64-support).
  - It is Wasm-only. Its design (macro-assembler, value stack mirrored at compile time) is the
    Sparkplug pattern, not a reusable library for JS (inferred).
- **Deopt and OSR.** Cranelift has none (svelters JIT note §2). quickjs-jit's workaround:
  - The interpreter frame is the only frame.
  - At every helper call and exit, live values are written back to frame slots, so deopt means
    "store unboxed values, set pc/sp, return DEOPT".
  - OSR entry is a separate compiled entry block per loop header.
  - Inlined callees get runtime-owned "shadow frames" so they can be materialized (svelters JIT
    note §1).
  - This works. It costs stores at every boundary, which is one reason its Tier 2 gains little on
    call-heavy code (inferred).
- **Stack maps / safepoints** (documented, https://fitzgen.com/2024/09/10/new-stack-maps-for-wasmtime.html;
  `cranelift_codegen::ir::user_stack_maps` docs):
  - Since 2024, "user stack maps" make the frontend responsible. The CLIF producer marks values
    that need stack maps (`cranelift-frontend` automates it).
  - Live GC references are spilled to stack slots before each safepoint (every non-tail call).
    Each safepoint is annotated with those slots, and the values are reloaded after the call, so a
    *moving* GC works.
  - CVE-2022-24791 (missing stack maps in cold blocks) shows the earlier design's risk
    (https://www.wiz.io/vulnerability-database/cve/cve-2022-24791).
- **Exceptions:** Cranelift has `try_call` with exception tables (documented,
  `cranelift_codegen::ir::exception_table`). It was implemented for Wasm exception handling,
  March–August 2025 (https://cfallin.org/blog/2025/11/06/exceptions/).
  - A JS JIT could use it for `try/catch` across calls. quickjs-jit instead returns an EXCEPTION
    status and lets the interpreter unwind.
- **Fit** (inferred): Cranelift's niche in a JS engine is an *optimizing* tier: SSA in, decent
  register allocation out, with deopt kept in the frontend. That is what quickjs-jit Tier 2 is. For
  a baseline tier it is too slow to compile on the main thread, so quickjs-jit compiles in the
  background. It also adds an IR translation step that copy-and-patch and single-pass designs
  don't have.

## 3. Other options

### 3.1 dynasm-rs and hand-written templates

- `dynasm`/`dynasmrt` 5.1.0 (2026-07-22) is a proc-macro assembler (documented,
  https://docs.rs/dynasmrt/; version measured on crates.io).
  - It covers x64/x86 except AVX-512, aarch64 up to ARMv8.4 except SVE, and riscv32/64.
  - Wasmer's singlepass compiler depends on it (measured: crates.io dependencies of
    `wasmer-compiler-singlepass` 7.5.0).
- It is stable Rust and compiles as fast as a template JIT. Each opcode template is written once
  per architecture (inferred).
- For a Sparkplug-style tier, most templates are short: "load operands, call IC/helper, branch". Two
  architectures × ~250 opcodes is manageable (QuickJS-NG has 252 `DEF(` opcodes,
  `quickjs-opcode.h` @ `7a23d8e`, source-observed). Inline IC fast paths are what make it long.
- Ruby's YJIT and ZJIT (Rust) use their own assembler and backend, not Cranelift (documented, ZJIT
  launch post 2025-12-24, https://bernsteinbear.com/blog/launch-zjit/).
  - YJIT reports 10–20% speedups in production Rails (documented, RubyKaigi 2025,
    https://rubykaigi.org/2025/presentations/maximecb.html).
  - ZJIT is "faster than the interpreter, but not yet as fast as YJIT" at Ruby 4.0.

### 3.2 V8 Sparkplug (baseline)

Documented, https://v8.dev/blog/sparkplug (2021-05-27, V8 9.1):

- Sparkplug compiles from bytecode "in a single linear pass over the bytecode", with no IR.
  Today it makes two passes; the first finds loops.
- It "intentionally creates and maintains a frame layout which matches the interpreter's frame".
  So the debugger, profiler, unwinding and stack traces keep working, and "any OSR logic that
  works for the interpreter will work for Sparkplug".
- It maps code addresses to bytecode offsets two ways instead of storing the pc. The freed slot
  caches the feedback vector.
- "most Sparkplug code just calls into 'builtins'" ("basically just builtin calls and control
  flow"). It removes only "operand decoding and next-bytecode dispatch".
- Gains: Speedometer +5–10%; real sites 5–15% main-thread time.
- Later V8 numbers (Chrome 117, M2 MacBook Air): Sparkplug is +41% over Ignition on Speedometer
  and +45% on JetStream (documented, https://v8.dev/blog/maglev, 2023-12-05).
- On Svelte signals I measured +84% (§0).

### 3.3 V8 Maglev (mid-tier)

Documented, https://v8.dev/blog/maglev (2023-12-05, Chrome M117):

- Maglev is an SSA CFG compiler. A prepass computes loops and liveness. Graph building is abstract
  interpretation of the interpreter frame state.
- Feedback-driven nodes include shape checks and field loads, plus code dependencies (e.g. on
  constant globals).
- It reuses TurboFan's deopt and does single-pass forward register allocation, with tagged and
  untagged stack regions.
- It compiles "roughly 10x slower than Sparkplug, and 10x faster than TurboFan". Gains: JetStream
  +8.2%, Speedometer +6%, and 10% less energy on Speedometer (https://v8.dev/blog/holiday-season-2023).
- It gave nothing on §0's workloads.

### 3.4 JSC: LLInt → Baseline → DFG → FTL

Documented, Filip Pizlo, https://webkit.org/blog/10308/speculation-in-javascriptcore/:

- **LLInt** is written in offlineasm and "obeys the JIT ABI". Its `get_by_id` metadata holds "a
  cached structure ID and a cached offset". The "best inline caching implementation is the one
  shared by the JITs".
- **Baseline** is a template JIT with no OSR speculation. **DFG** speculates and can OSR-exit to
  Baseline or LLInt. **FTL** is the peak tier.
- **Tier-up thresholds:** LLInt → Baseline at 500 points, Baseline → DFG at 1,000 and DFG → FTL at
  100,000. Each call adds 15 points and each loop iteration 1. The thresholds are scaled by function
  size and by recompile history.
- **Speed:**
  - JetStream 2: "the Baseline and DFG are more than 2× better than the tier below them and the FTL
    is 1.1× better than the DFG".
  - Per-bytecode cost: LLInt 3.97 ns, Baseline 1.71, DFG 0.349, FTL 0.225.
  - Compile cost: DFG about 4x Baseline, FTL about 6x DFG.
- **OSR:** exit is "easy", entry is "hard", so OSR entry happens only at hot loops. The text rates
  speculation as "a bet with tiny benefit and enormous cost" when it fails: a DFG exit costs
  2,499 ns.
- For hocus: JSC's split, a slim interpreter plus a template baseline with full ICs, is what
  produced the 4x step in §0 (inferred).

### 3.5 SpiderMonkey: Baseline Interpreter, CacheIR, Warp

This is the most relevant prior art for hocus.

**CacheIR** (documented, https://firefox-source-docs.mozilla.org/js/cacheir.html):

- An inline cache is a short **linear program** of three kinds of ops:
  - *guards* (e.g. `GuardShape`);
  - *idempotent ops* (e.g. `LoadObject`);
  - exactly one *result op*, last, which is the only op allowed side effects.
- Operands are of three kinds:
  - *Ids*: values known only when the IC runs;
  - *Fields*: fixed per stub but different between ICs, e.g. a shape or slot offset;
  - *Imms*: constants baked into the CacheIR.
- A property get on a known shape is just: `GuardToObject; GuardShape; LoadFixedSlotResult`.
- Every dynamic op calls an IC chain.
  - The *fallback stub* performs the operation generically. It then asks a `CacheIRGenerator` to
    write CacheIR for the inputs it saw.
  - `BaselineCacheIRCompiler` turns that into a stub and prepends it to the chain.
  - Stub fields are stored in the stub, not in the code, "so one piece of jitcode can serve many
    stubs".
- **IC states:** Specialized; after six stubs, Megamorphic (more general CacheIR); Generic (stop
  attaching). Each stub counts its entries.
- **Adding an op** means a YAML definition (`CacheIROps.yaml`, code-generated), then the shared
  compiler, then the Warp transpiler, then the generator.

**Baseline Interpreter** (documented, Jan de Mooij, 2019-08-30,
https://hacks.mozilla.org/2019/08/the-baseline-interpreter-a-faster-js-interpreter-in-firefox-70/):

- It is generated by the same `BaselineCodeGen` template as the Baseline JIT, so the two share ICs
  (in `JitScript`) and frame layout. The interpreter frame adds only an explicit bytecode pc.
- Tier-up from the interpreter to the JIT "is a jump". The Baseline Interpreter's threshold is 10.
  The Baseline JIT's threshold rose from 10 to 100, so far less code is compiled.
- `JitScript` allocation is "at least 10 times faster than Baseline JIT compilation".
- Results:
  - Google Docs load: C++ interpreter 901 ms, + Baseline Interpreter 676 ms, + Baseline JIT 633 ms.
  - Speedometer: 31 → 52 → 69 points.
  - Page loads 2–8% faster.
- The Baseline Interpreter, i.e. ICs without native code per script, delivered 225 of the 268 ms
  saved on Google Docs load and 21 of the 38 Speedometer points (inferred from the numbers above).

**Warp** (documented, 2020-11-13, https://hacks.mozilla.org/2020/11/warp-improved-js-performance-in-firefox-83/):

- WarpBuilder builds MIR by **transpiling the baseline tiers' CacheIR**, one CacheIR op to its MIR
  ops. It replaced IonBuilder and the global Type Inference system.
- The WarpOracle snapshot is taken on the main thread. Building and optimization run off-thread.
- *Trial inlining* gives a callee a specialized ICScript per call site before Warp compiles it.
- Results: Google Docs load about 20% faster, Speedometer 10–12%, 8% less JS memory on tp6.
- One CacheIR op such as `Int32PowResult` serves `Math.pow` and `**` in every tier.

**Portable Baseline Interpreter (PBL)** (documented, Chris Fallin, 2023-10-11,
https://cfallin.org/blog/2023/10/11/spidermonkey-pbl/):

- PBL interprets JS opcodes *and CacheIR* in portable C++, with no code generation, for wasm/WASI.
  It keeps an auxiliary stack in the baseline frame format.
- Results: Octane 1.26x over the generic interpreter. Two real applications cut wall-clock CPU time
  by 42% and 17%.
- Simple operators like `Add` "were twice as slow" through interpreted ICs, so PBL uses ICs only
  for property accesses and built-in calls ("hybrid ICs").

**weval** (documented, Fallin & Bernstein, PLDI 2025, https://cfallin.org/pubs/pldi2025_weval.pdf;
slides https://cfallin.org/pubs/pldi2025_weval_slides_public.pdf, slide 17):

- weval partially evaluates the interpreter and the IC bodies *ahead of time*. A table of
  precompiled CacheIR sequences covers the common ICs.
- Results: "2.17x geomean (2.93x max) speedup over interpreter with ICs" and "2.53x geomean (4.18x
  max) speedup over generic interpreter". It is in production (`--enable-aot`).
- So on that suite, ICs alone gave 2.53 / 2.17 ≈ 1.17x, and AOT compilation of the bytecode gave
  the rest (inferred).

**For hocus** (inferred): CacheIR gives one IC representation for the interpreter (interpreted, as
in PBL), the baseline (compiled stubs or copy-and-patch), an AOT path (weval-style) and an
optimizing tier (transpiled, as in Warp). That removes the main design risk of adding tiers later.

### 3.6 Hermes and Static Hermes

- **Hermes** compiles to bytecode ahead of time and ships no JIT.
  - **Hermes V1** shipped as opt-in in React Native 0.82 (2025-10-08) and as the default in 0.84
    (2026-02-11) (documented; cited in cadatomic
    `docs/research/notes/2026-10-25-own-gui-toolkit/round2/zero-serdes-js.md` §3.5 @ `75b6a47`).
  - It "does not yet contain JS-to-native compilation (previously known as 'Static Hermes') or the
    JIT compilation that was presented during React Native EU 2023" (documented,
    https://reactnative.dev/blog/2025/10/08/react-native-0.82).
  - Expensify gains: TTI 2.5–7.6% faster, bundle load 3.2–9% faster.
- **Static Hermes** is "optional ahead-of-time native compilation of soundly typed JavaScript"
  that "requires sound types" (documented, RN EU 2023 deck,
  https://speakerdeck.com/tmikov2023/static-hermes-react-native-eu-2023-announcement?slide=14).
  - In 2024 it was "not ready for general use" (Chain React 2024 deck).
  - No public performance number for it was found. A third-party table (Perry v0.5.17 release
    notes) shows `shermes -typed -O` slower than node and bun on most rows (not verified).

### 3.7 Tracing (LuaJIT, TraceMonkey)

- **LuaJIT's tracing JIT** remains the peak bar for Lua. Deegen's baseline is 33% slower overall,
  with most of the loss on number-crunching (Deegen §8.3, documented).
- **TraceMonkey**, a JS tracing JIT, was removed from SpiderMonkey around Firefox 11 (2011), about
  67k lines (documented, Nicholas Nethercote,
  https://blog.mozilla.org/nnethercote/2011/11/23/memshrink-progress-report-week-23/).
  - Reasons: tracing suits "tight, branch-free loops" but was bimodal on other code. JägerMonkey
    with type inference was faster on average.
- **CPython's JIT** moved from tracing to a method frontend in PEP 836 (documented).
- **For Svelte code** (inferred): the work is short, branchy, polymorphic functions called through
  closures. That is tracing's worst case. Tracing is not a candidate.

## 4. What makes Svelte-style code fast

### 4.1 What the hot code does

Source-observed, svelte@5.57.2:

- `get()` (`src/internal/client/runtime.js:540`) reads `signal.f` (a flags bitfield). It tests
  module-level variables (`active_reaction`, `untracking`, `captured_signals`, `skipped_deps`,
  `read_version`). It compares `deps[skipped_deps] === signal` and pushes onto arrays.
- `set`/`internal_set`/`mark_reactions` (`reactivity/sources.js:149,189,354`), `update_reaction`
  (`runtime.js:220`) and `flushSync` (`reactivity/batch.js:1033`) follow the same pattern.
- The objects are a few shapes (Source, Derived, Effect). The calls are tiny and frequent, and
  closures run effects.
- `$state` objects go through Proxy traps (`proxy.js`: `get`, `set`, `has`, `ownKeys`,
  `defineProperty`, `deleteProperty`, `getOwnPropertyDescriptor`).

Source-observed, QuickJS-NG `7a23d8e`:

- `OP_get_field` does a hash lookup in the shape on every execution: `find_own_property` hashes
  the atom and walks a chain (`quickjs.c:6836`, `:19559`). There is no per-site cache.
- `OP_call` recurses into `JS_CallInternal` on the C stack (`quickjs.c:18542-18553`). Each call
  `alloca`s a fresh frame for args, locals and stack (`:18138-18151`).
- The svelters JIT note's profile matches: `JS_CallInternal` 71% self, `find_own_property` 7.8%.

### 4.2 Which technique addresses which cost, and how much of the gap it closes

| Technique | What it fixes in the Svelte hot path | Needs a JIT? | Evidence of size |
|---|---|---|---|
| Hidden classes + monomorphic property ICs | `signal.f`, `signal.rv`, `reaction.deps` loads become shape compare + slot load (no hash walk) | no | CPython 3.11's specializing interpreter (inline caches in bytecode, PEP 659) plus frame work: 1.25x on pyperformance (documented, https://docs.python.org/3/whatsnew/3.11.html). PBL: 1.26x Octane. weval-implied ICs: 1.17x. QuickJS-NG's own polymorphic ICs: 0.94–1.42x on web-tooling, removed for memory (svelters note §2) |
| Calls without native recursion (VM-stack frames, frame reuse) | the many tiny `get`/`set`/closure calls | no | CPython 3.11 "Inlined Python function calls": 1.7x on fib-style recursion, 1–3% on pyperformance. Lazy frames: 3–7% (documented, same page) |
| Quickening / superinstructions | dispatch count, operand decoding | no | CPython 3.14 tail-call dispatch: 1–5% once an LLVM 19 regression is excluded (documented, https://blog.nelhage.com/post/cpython-tail-call/). Deegen's interpreter is 31% over LuaJIT's with tail-call dispatch and register pinning |
| **Interpreter total (V8 Ignition: hidden classes, full IC system, fast builtins)** | all of the above | no | **1.2–1.6x over QuickJS-NG on §0's workloads** (measured). V8 `--jitless` is "around 40% slower" than full V8 on Speedometer 2.0, but 80% slower on Web Tooling (documented, https://v8.dev/blog/jitless) |
| Baseline JIT calling shared IC handlers (Sparkplug) | dispatch, operand decode | yes | +1.3–1.8x over Ignition (measured, §0). Speedometer +41% over Ignition (documented) |
| Baseline JIT with inline polymorphic ICs and call ICs (JSC Baseline, Deegen) | IC dispatch, call setup | yes | +3.3–4.0x over LLInt (measured, §0) |
| Speculative optimizing tier with inlining (TurboFan, DFG/FTL, Warp) | the call itself: `get` inlined into its callers, flags kept in registers, guards hoisted | yes | +3–5x over the baseline on signals and appmix, under 2x on proxy (measured, §0). Warp: Speedometer +10–12% over a baseline already present (documented) |
| Faster Proxy traps | `$state` proxies | no | No tier gets more than 6x over QuickJS on the proxy workload (measured). Best fixed by not using proxies in hot paths (svelters note §6) |

**Conclusion** (inferred from the table):

- Without native code, the ceiling is about 1.5x on Svelte code. That needs a V8-quality IC system
  and object model, and nothing less.
- About 3–4x needs a baseline JIT with inline ICs and call ICs.
- The remaining 4x to V8/JSC needs an optimizing tier.
- Interpreter ICs still come first, for three reasons:
  - Every later tier consumes them (CacheIR, feedback vectors).
  - They are the only gain available on iOS and wasm.
  - The `find_own_property` hash walk is pure waste on monomorphic code.

## 5. AOT from the svelters compiler

**Today.** svelters compiles `.svelte` to Svelte 5-shaped JS: `$.template_effect`, `$.get`/`$.set`
on signals, and `$n.*` native ops (CF2 milestone, svelters `0aa41697`,
`plans/milestones/CF2-template-effects.md`). QuickJS compiles that JS to bytecode, and svelters
caches the bytecode: cold load 95–120 ms, warm about 13 ms for 115 modules
(`plans/research/QUICKJS-RUNTIME.md:163` @ `0aa41697`). With our own engine there are four AOT
levels:

| AOT level | What the compiler would emit | Gain | Cost / risk | Verdict |
|---|---|---|---|---|
| A1. Engine bytecode directly | hocus bytecode instead of JS text | parse time only, which the bytecode cache already removes | ties the compiler to the engine's bytecode version | not worth it now (inferred) |
| A2. Facts as hints | object shapes of props and records, "this call is `$.get` on a Source", "this identifier is a signal" | ICs warm in one or two executions, so these facts only help an optimizing tier avoid warm-up and deopts | a hint channel in the bytecode format | reserve a bytecode section (inferred) |
| A3. Engine intrinsics for Svelte primitives | a `signal_get` opcode implemented natively | removes a call per `$.get`, the hottest operation | **forks Svelte semantics**: svelters runs stock svelte@5.57.2 (user, 2026-10-21) | needs a user decision; not recommended (inferred) |
| A4. Typed native AOT (Static Hermes) | native code from soundly typed JS | large on typed numeric code (claimed) | needs sound types, which neither the Svelte runtime (JSDoc only) nor user components have. Static Hermes is still unshipped after three years (§3.6) | no (inferred) |
| A5. AOT baseline (weval-style) | the bundled app and runtime bytecode compiled at build time with the baseline JIT's stencils into a native object linked into the app. ICs stay runtime data, interpreted or table-dispatched | weval: 2.17x over an IC interpreter (§3.5). Works on iOS, where code is signed in the binary | build step per target. Falls back to the interpreter for HMR and live-compiled code | **the promising one**, once a baseline tier exists (inferred) |

- **Porffor** is a from-scratch AOT JS → Wasm/C compiler. It calls itself a research project "not
  yet intended for serious use". It says it is fast against interpreters but "usually slower" than
  JIT engines (documented, https://github.com/CanadaHonk/porffor). It covers a subset of JS. It is
  not a path for a full engine, but it shows that AOT JS without types tops out near interpreter
  and baseline speed (inferred).
- **A5 suits presto** (inferred). The svelters loader already compiles `.svelte` live in release
  builds, for self-editing and hot reload. So A5 covers the shipped bundle, and live-edited code
  runs in the interpreter (or in the baseline JIT where JIT is allowed).

## 6. Staged recommendation

### 6.1 Design into the interpreter now (cheap now, expensive to retrofit)

1. **A VM stack of frames; no Rust recursion for JS → JS calls.**
   - `OP_call` on a bytecode function pushes a frame and continues in the same dispatch loop, as in
     CPython 3.11, PBL and Sparkplug.
   - Native builtins that call JS re-enter the loop explicitly.
   - This is the call-frame cost in §4.2. It also makes OSR and tier-up "a jump", and gives the GC
     precise roots (the GC note proposes a heap-owned register file;
     `gc-and-ownership.md` §Summary 2).
2. **A fixed, documented frame layout:** header (callee, `this`, new.target, env/var-refs, pc,
   IC-table pointer, flags), then args, locals and operand stack.
   - Every live JS value is in a frame slot at every call, allocation and exit.
   - This is quickjs-jit's rule, which lets a baseline JIT run with no stack maps and deopt with no
     metadata. It matches the GC note's JIT rule (`gc-and-ownership.md` §Summary 7).
   - Store the bytecode pc in the frame, not only in a Rust local. The JIT later replaces it with
     an address → pc map, as Sparkplug does.
3. **Keep stack bytecode (QuickJS's) and compute stack depth statically.** SpiderMonkey's baseline
   compiles stack bytecode fine, because "the JS parser emits bytecode for a stack-based VM"
   (documented, https://firefox-source-docs.mozilla.org/js/index.html), and the depth at each pc is
   derivable. Nothing forces a register-bytecode rewrite (inferred). Add:
   - an **IC index operand** (u16, with a wide form) on every property get/set, element access,
     call, `in`, `instanceof`, arithmetic and compare op;
   - per-function **liveness bitsets at loop headers and call sites**, computed when bytecode is
     emitted. A future optimizing tier needs them for deopt; a baseline tier doesn't.
4. **ICs as data in a CacheIR-style IC language.** Each IC site holds a short chain of stubs; each
   stub is a list of guard/load/result ops with per-stub fields (shape id, slot offset, getter).
   - Allocate the IC table lazily, after about 10 executions (SpiderMonkey's threshold), to avoid
     QuickJS-NG's memory regression.
   - Use PBL's "hybrid" rule: ICs only for property access, calls and builtins, not for `Add`.
   - Define the ops in one table, as `CacheIROps.yaml` does, so an interpreter, stencils and a
     transpiler are all generated from it.
5. **Shapes with stable slot offsets and a cheap shape id.** QuickJS's `JSShape` is hashed and
   shared. An IC needs "shape S ⇒ property at slot k" to hold until the shape changes, plus a
   token that invalidates it on descriptor change (quickjs-jit's patch 0017).
   - This is object-model work. The object-model researcher owns it; it is listed here because the
     JIT depends on it.
6. **Opcode bodies as shared helper functions with one ABI:**
   `fn(&mut Frame, operands…) -> Status`, with ownership, effect and reentrancy flags in one
   generated table (quickjs-jit's X-macro, as Rust).
   - The interpreter inlines the helpers. A Sparkplug-style tier calls them. A copy-and-patch tier
     uses them as stencil bodies.
7. **A code-memory and capability layer from day one,** even if unused: W^X, MAP_JIT, Windows CFG,
   an I-cache flush and a "JIT allowed" probe. Then no later tier changes the embedding API.
8. **Write barriers:** if the GC is incremental (the GC note's recommendation), barriers live in
   one helper that JIT code can call or inline. Refcount DUP/FREE disappear with a tracing GC,
   which removes the CPython JIT's main spill problem (§1.3).

### 6.2 Tiers, in order, with triggers

| Stage | What | Trigger to start | Expected gain (vs QuickJS-NG on Svelte code) | Effort (planning numbers) |
|---|---|---|---|---|
| S0 | §6.1 designed into the port | now | none by itself; it enables everything below | part of the port |
| S1 | Interpreter ICs (CacheIR-style, interpreted) + VM-stack calls + quickening of hot ops | as soon as the port runs Svelte | **1.2–1.6x** (§4.2) | 6–10 weeks (inferred) |
| S2 | **Copy-and-patch baseline JIT** on the interpreter frame: stencils from the shared helper bodies; inline fast paths for the IC'd ops and calls; IC stubs compiled to native when hot; synchronous compile at a call-count threshold | P5/presto profiling on real cadatomic panels shows **JS execution > 25% of frame time or > 4 ms p95 per frame on a 120 Hz target**, *and* > 50% of that JS time in bytecode functions (not builtins, proxies or host calls), after S1 | **2–4x** (Sparkplug 2.8x → JSC Baseline 4.2x in §0). Desktop only; iOS and wasm keep S1 | 3–5 months for x86-64 + aarch64 (inferred; Deegen and logicaffeine show the stencil pipeline is weeks, and the IC/call fast paths are the bulk) |
| S2b | AOT baseline of the shipped bundle (weval-style) with the S2 stencils | S2 done and iOS or mobile frame budgets are missed | up to about 2x over S1 on iOS (weval 2.17x) | 1–2 months on top of S2 (hypothesis) |
| S3 | Optimizing tier: Warp-style transpile of bytecode + CacheIR to SSA, trial inlining, deopt to the interpreter frame; **Cranelift** backend | S2 shipped and still JS-bound, with a product need above 4x | toward 10–15x | person-years. quickjs-jit's Tier 2 (59k agent-written lines) did not deliver it. Compare against switching the engine to V8 for that build first |

### 6.3 Copy-and-patch vs Cranelift for the first JIT tier

**Copy-and-patch** (inferred from §1, §2):

- Compile speed is the reason. A baseline tier compiles every warm function, so it should run
  synchronously on the UI thread at about 19M bytecodes/s (Deegen). Cranelift's ~7x-slower-than-
  Winch compile forced quickjs-jit to compile in the background with an `acquire_entry` handshake.
- Stencils come from the same helper bodies as the interpreter, so there is one semantic source.
- The stencil compiler is our pinned rustc, so there is no LLVM or clang dependency and no monthly
  Cranelift API port.
- Inline IC fast paths and patchable IC stub chains are natural (Deegen §7.1). Cranelift has no
  code patching.

**Risks:**

- Rust has no stable guaranteed tail calls. Mitigation: a build-time gate that every continuation
  is a `jmp`; `become` when stable (hoped for 2027).
- LLVM codegen drift between rustc versions. Mitigation: check in generated stencil tables and
  regenerate them in a CI job.
- Relocation handling on two ISAs and three object formats. logicaffeine and gandr have mapped it.

**Fallback:** if stencil generation from Rust proves too fragile, write the baseline tier by hand
with dynasm-rs (Sparkplug-style). It is stable Rust and fast to compile, at the price of templates
per architecture.

**Cranelift's place:** only S3, as the optimizing backend. User stack maps (for a moving GC) and
`try_call` now exist. Deopt stays our job, through the frame write-back that quickjs-jit already
uses.

## Open questions for the user

1. **Is throughput the problem?** The JIT stages are triggered by profiling, not scheduled.
   *Recommendation:* adopt the S2 trigger in §6.2. Measure on presto panels after S1 before
   committing a JIT lane.
2. **May hocus treat Svelte's runtime primitives as engine intrinsics (A3)?** This would change the
   "stock svelte@5.57.2" decision (user, 2026-10-21). *Recommendation:* no. Keep Svelte's JS as the
   source of truth, and let ICs and, later, inlining make `$.get` cheap.
3. **Is iOS a performance target or only a supported platform?** If it is a target, S2b (AOT
   baseline of the bundle) becomes the main JIT-shaped work, because iOS forbids runtime codegen.
   *Recommendation:* treat iOS as interpreter-only until S2 exists, then decide on S2b with numbers.
4. **Nightly Rust for stencils?** `become` and `preserve_none` would make stencils cleaner, but the
   user's repositories build on stable 1.95+. *Recommendation:* stay on stable with a build-time
   stencil gate. Revisit when `explicit_tail_calls` stabilizes.
5. **If S3-class speed is ever needed, our own optimizing JIT or V8 for that build?**
   *Recommendation:* V8 (deno_core) for that build. The engine-neutral op layer from the svelters
   notes keeps this option open. §0 shows the optimizing tier is the expensive 4x.

## First steps in this repository

1. Write `docs/design/frames-and-calls.md`: the frame layout, the VM-stack calling convention, and
   "every live value in a slot at every helper call". Agree it with the GC note's root design
   before any interpreter code.
2. Write `docs/design/bytecode.md` with the IC-index operand, the stack-depth rule and the liveness
   tables, and with reserved sections for compiler hints (A2) and AOT code (S2b).
3. Prototype the CacheIR-style IC language on two ops (`get_field`, `call`). Interpret it in the
   interpreter, and measure `signals`/`appmix` from Appendix A against QuickJS-NG `7a23d8e`.
   This is the S1 go/no-go.
4. Put Appendix A's harness into `bench/` as the standing JS microbaseline, with the V8/JSC tier
   rows. Every later tier is judged by where it lands between the "Ignition", "Sparkplug" and
   "JSC Baseline" rows.
5. Build a one-week stencil spike, outside the engine: compile three helper bodies (`get_loc`,
   `add_int`, `if_false`) with `rustc --emit=obj`, extract them with `object`, patch and run them on
   x86-64 Linux and aarch64 macOS. Record the relocation census. This settles whether S2 uses
   copy-and-patch or dynasm-rs.

## Appendix A: harness (measured runs in §0)

Files: scratchpad `jit/bench/entry.js`, bundled to `bundle.js` with
`bun build entry.js --outfile bundle.js --format=iife --target=browser --conditions=production`
(svelte@5.57.2 installed with `npm install --ignore-scripts` in a separate `deps/` directory). The
runner is `jit/run.sh`.

Commands:
- `qjs bundle.js <w>`;
- `W=<w> node [flags] bundle.js`;
- `W=<w> [BUN_JSC_useJIT=0 | BUN_JSC_useDFGJIT=0] bun bundle.js`.

The core of `entry.js`:

```js
import * as $ from '../deps/node_modules/svelte/src/internal/client/index.js';
function mkSignals() {               // 100 state, 100 derived, 100 render effects
  const N = 100, src = [], out = new Array(N).fill(0);
  $.effect_root(() => {
    for (let i = 0; i < N; i++) src.push($.state(i));
    for (let i = 0; i < N; i++) {
      const a = src[i], b = src[(i + 1) % N], c = src[(i + 2) % N];
      const d = $.derived(() => $.get(a) + 2 * $.get(b) + $.get(c));
      $.render_effect(() => { out[i] = $.get(d); });
    }
  });
  $.flush(); let t = 0;
  return () => { for (let r = 0; r < 10; r++) {
      for (let k = 0; k < 5; k++) $.set(src[(t * 7 + k * 13) % N], t + k);
      $.flush(); t++; }
    let s = 0; for (let i = 0; i < N; i++) s += out[i]; return s; };
}
// mkProxy: $.proxy over 200 {id,text,done}; derived count iterating the proxy; 200 render
//   effects; each call: 10 x (toggle p[i].done, edit p[j].text, $.flush()).
// mkAppmix: 400 objects of 4 shapes; handlers[kind](o) dispatch; filter/map/template/join.
// Driver: 640 warm-up calls; 16 batches of 10 calls; report the median batch (ms per 10 calls).
```
