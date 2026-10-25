# Research artifacts

Scripts and raw results behind the measurements in `../notes/`, copied from the research session's
scratchpad on 2026-10-25. Paths inside the scripts point at that scratchpad and must be adjusted to
rerun. All timings are from a shared 4-core cloud VM: cloud, not a gate. Third-party benchmark
sources (bench-v8, the bundled Svelte runtime) are not copied; the batch 1 `bench` lane commits
them properly.

| Folder | What | Note |
|---|---|---|
| `census/` | `quickjs.c` subsystem attribution and C-idiom counts; function census | quickjs-census, verification-and-method |
| `gc/` | QuickJS GC pause vs heap size (`gcpause.mjs`); the safe GC API prototype (`api.rs`) | gc-and-ownership |
| `isolates/` | runtime creation, serializer and bundle-load costs on QuickJS-NG (C) | isolates-and-sharing |
| `jit/` | V8/JSC tier-by-tier runs on the Svelte workloads (`run.sh`, `entry.js`, `results.txt`) | jit |
| `interpreter/` | opcode-counting patch to QuickJS-NG `7a23d8e`, opcode mixes per workload, a dispatch toy | interpreter-design |
| `rust-engines/` | bench-v8 scores for Bellard QuickJS, QuickJS-NG and quickjs-oxide; the Svelte signals entry | rust-engines-prior-art |
