# hocus: instructions for agents

hocus is a JavaScript engine in Rust: QuickJS-NG's semantics, builtins and compiler, ported onto a
new memory model (tracing GC, NaN-boxed values, register bytecode). It serves svelters (a second
engine behind svelters-host's `JsEngine` trait, beside QuickJS-NG) and cadatomic's presto renderer.

## Read first

1. `STATUS.md`: the handoff block.
2. `docs/plan/DECISIONS.md`: the decisions in force. It wins over every other document.
3. `docs/research/README.md`: the design and why. The notes under `docs/research/notes/` are the
   evidence; they are history once a decision is recorded.
4. In a batch: the batch's brief and lane file.

Status comes from the code, the tests and `STATUS.md`, never from research or plan files.

## Rules

- **QuickJS-NG is the oracle.** Tests compare observable output (printed values, exceptions, job
  order, op logs), never QuickJS's internal order. A disagreement is triaged: our bug, an NG bug
  we reproduce, an NG bug where we follow the spec (a row in `departures.tsv`), or
  implementation-defined. An oracle that says nothing is never a pass.
- **`unsafe` lives in the audited kernel only** (`hocus-gc` and modules listed in the boundary
  script); every other crate is `#![forbid(unsafe_code)]`. The `unsafe` count ratchet only falls.
- **Port semantics, not C idioms.** No refcounts, `goto fail` scaffolding, out-parameters, integer
  error codes or sentinel values. Names say what the code computes; the C function a piece of code
  came from goes in `porting/ledger.tsv`, not in identifiers.
- **Every C function is a ledger row** with its status (ported, replaced, dropped, pending).
- **Not implemented means not registered:** no placeholder builtins, opcodes or host APIs. Traits
  and crates only with a consumer.
- **Tests wait on events or the virtual clock, never the wall clock.** Cloud timings are "cloud,
  not a gate"; instruction counts are the cloud benchmark gate; speed claims need a quiet machine.
- **Pins** (`pins.toml`) move only in their own commit.

## Building

- Stable Rust, `rust-version = "1.95"`, toolchain rustc 1.97.0. Nightly only for Miri, sanitizers
  and fuzzing, pinned by date.
- Build and test as little as possible: `cargo check -p <crate>` while writing, the tests you
  touched when done. Research and docs work builds nothing.

## Other repositories

svelters, cadatomic, QuickJS-NG and quickjs-oxide are read-only from here. A change svelters needs
goes to svelters as a request (svelters sessions own svelters).

## Who decides

The user decides design, licence, anything public or paid, and reversals of recorded user
decisions. The coordinator decides routine questions and records lasting ones in
`docs/plan/DECISIONS.md` as "(coordinator, date)". Lanes never ask the user; they write the
question with a recommendation into their report.

## Git

Lanes push only their own branch; the coordinator merges `main`. Never force-push. No credentials
in committed files. Replace the handoff block in `STATUS.md` before a session ends.
