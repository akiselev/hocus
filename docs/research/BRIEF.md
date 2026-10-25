# Research brief: a Rust port of QuickJS for svelters and presto

Date: 2026-10-25. Working name of the engine and repository: **hocus** (rename is cheap).

## The ask (user, 2026-10-25)

"Lets port quickjs to rust so that we can do a native integration with svelters and the new
presto renderer. We need to research everything from how to translate ownership without unsafe
everywhere, copy and patch/cranelift jit, integration with svelters so we can better optimize the
gui loop, sharing types and objects between isolates so workers are fully integrated, rust GC
options, and so on. Do a thorough research loop and lets figure out where to get started."

## Context every researcher must know

- **svelters** (`/home/user/svelters`) is a Rust Svelte 5 compiler plus a native runtime that runs
  stock svelte@5.57.2 on native backends through a QuickJS-NG host (`crates/svelters-host`,
  rquickjs 0.14.0 = QuickJS-NG 0.16.2) and a generic backend C ABI (`crates/svelters-abi`).
- **presto** is the new GUI toolkit/renderer replacing egui for cadatomic (it was called "tessel"
  in the proposal). The proposal is `/home/user/cadatomic/docs/research/2026-10-25-gui-toolkit/`
  (`README.md`, `architecture.md`, `compiler-and-js.md`, `plan.md`), with evidence notes in
  `/home/user/cadatomic/docs/research/notes/2026-10-25-own-gui-toolkit/` (`round2/js-host-local.md`,
  `round2/zero-serdes-js.md`, `js-runtimes.md`, ...). JS runs on the UI thread at event time; a
  compositor thread owns wgpu.
- **Prior engine research in svelters** (read it, don't redo it):
  - `/home/user/svelters/plans/research/QUICKJS-RUNTIME.md` (module loader, bytecode cache, engines);
  - `/home/user/svelters/docs/notes/quickjs-jit-cranelift.md` (longbridge/quickjs-jit, a Cranelift
    JIT for QuickJS-NG: 20x on a monomorphic numeric kernel but a **tie or ~25% slower on the real
    Svelte 5 signals/proxy runtime**; V8 was ~20x QuickJS on Svelte signals, ~27x on app glue; perf
    on stock qjs: `JS_CallInternal` 71% self, `find_own_property` 7.8%);
  - `/home/user/svelters/plans/research/REACTIVITY.md`, `RUNTIME.md`, `HMR.md`, `ARCH-SECURITY.md`
    (isolates ISO1–ISO3: serializable op batches, per-isolate handles).
- **Recorded decisions this work reopens:** "stock QuickJS-NG; no rquickjs fork" (user,
  2026-10-21) and the GUI proposal's Q4 "don't fork QuickJS now". The user is now asking for a
  full Rust port, which supersedes the fork question; the research must say honestly what a port
  buys and what it costs, using the measurements above.
- QuickJS-NG source (shallow clone, HEAD of master today) is at
  `/tmp/claude-0/-home-user/db6d0663-2392-5b80-95a4-80368735124c/scratchpad/src/quickjs-ng`
  (`quickjs.c` is 64,954 lines). Clone other repositories you need into
  `/tmp/claude-0/-home-user/db6d0663-2392-5b80-95a4-80368735124c/scratchpad/src/<name>` with
  `git clone --depth 1`. Network access (GitHub, the web) works.
- Machine: 4 cores, 15 GB RAM, shared with the other researchers. Builds and benchmarks are
  allowed only when small and when they answer a question you have; say "cloud, not a gate" next
  to any timing, and prefer published or source-derived numbers.
- Toolchain: rustc 1.97.0 (`rust-version = "1.95"` across the user's repositories).

## How to write your note

- Write exactly one file: `/home/user/hocus/docs/research/notes/<your-topic>.md`. Don't edit
  anything else, and don't edit any other repository.
- Plain, short sentences. Tables where they help. Lead with a summary of findings and a
  recommendation; then the evidence.
- **Every material claim cites its source**: a URL (with date or version), a repository path at a
  commit sha (`file.c:123 @ abc1234`), or a measurement you ran (command and machine). Grade each
  claim as `documented` (stated by the project's docs/paper), `source-observed` (you read it in
  code), `measured` (you ran it), `inferred` (your reasoning from cited facts), or `hypothesis`.
  Never present a guess as a fact; a search that found nothing is reported as "not found".
- Give numbers with their provenance and version. Prefer primary sources (papers, repos, project
  blogs, issue trackers) over summaries.
- End with: open questions for the user (each with a recommendation), and the concrete first steps
  you'd take in this repository.
- Your final message back is a 15–30 line summary of the note's findings and recommendation.
