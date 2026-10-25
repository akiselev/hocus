# Decisions

The decisions in force for hocus. This file wins over every other document here. Each entry names
who decided and when. Research and plan files explain why; they don't decide.

## In force

- **The repository and its name** (user, 2026-10-25): "start a new repo: lets port quickjs to
  rust". Working name `hocus` (D9 confirms or renames).
- **GitHub repository** (user, 2026-10-25): the user created `akiselev/hocus` (D10, done).

## Waiting on the user (2026-10-25)

The questions, with recommendations and reasons, are in `docs/research/README.md` §5:

| # | Question | Recommendation |
|---|---|---|
| D1 | Fork quickjs-oxide, or our own heap? | decide after the batch 1 bake-off |
| D2 | Native Svelte reactivity (reactive objects, Rust reactive core) behind a capability? | yes, gated on X1 and Svelte's tests |
| D3 | hocus is a new engine beside QuickJS-NG, not a fork of it | yes |
| D4 | Port base QuickJS-NG `7a23d8e` | yes |
| D5 | GC: finalize at sweep; 1 ms / 0.25 ms pause gate at 120 Hz; 4 GiB per isolate; own collector | yes |
| D6 | No shared mutable JS heap, no Shared Structs; Rust shared objects and a shared immutable image | yes |
| D7 | Stable Rust for the product; nightly only for Miri, sanitizers, fuzzing | yes |
| D8 | No Intl/Temporal in the engine; no JIT in year one; V8 if V8-class speed is needed | yes |
| D9 | Name `hocus`; licence MIT (or undeclared until decided) | hocus, MIT |
