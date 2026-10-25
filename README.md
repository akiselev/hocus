# hocus

A JavaScript engine in Rust, ported from QuickJS-NG, for
[svelters](https://github.com/akiselev/svelters) and the presto renderer in cadatomic.

**Status: research.** No engine code exists yet. Start with `docs/research/README.md` (the
synthesis, the design, the first batch and the decisions waiting on the user), then
`STATUS.md`.

The goal is not raw throughput. The research shows a Rust interpreter can reach about 1.5–2× QuickJS
on Svelte code at best. The goal is what an engine we own allows: reactive objects without `Proxy`,
a reactive core in Rust, a GC driven by presto's frame clock, unwinding interrupts, real HMR,
workers that share compiled code, and a mostly-safe engine with one audited `unsafe` kernel.
QuickJS-NG stays a supported svelters engine and is hocus's oracle.

Licence: not yet chosen (see `docs/plan/DECISIONS.md`, D9). Code ported from QuickJS-NG keeps its
MIT notice.
