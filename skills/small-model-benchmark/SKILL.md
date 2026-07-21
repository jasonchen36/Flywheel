---
name: small-model-benchmark
description: "Benchmarks small-model coding and review performance. Use when comparing Haiku/Flash-Lite quality on representative tasks."
---

# /small-model-benchmark
---
description: Run the small-model readiness benchmark pack conceptually and use it to judge whether Haiku/Flash-class models are being routed safely
---



# Small Model Benchmark Skill

Use this when you want to evaluate whether the weak-model setup is ready for real work.

Load:
- `${HOME}/SMALL_MODEL_BENCHMARK_PACK.md`
- `${HOME}/SMALL_MODEL_ROUTING.md`

## Workflow

### 1. Read the benchmark pack

Identify:
- route/escalation canaries
- bounded-helper canaries
- pass criteria

### 2. Score the current setup

For each canary, record:
- route chosen
- helper used
- whether evidence discipline was preserved
- whether escalation happened when needed

### 3. Summarize the result

Use:

```text
SMALL MODEL BENCHMARK

Passed:
- <canaries>

Failed:
- <canaries>

Highest-Risk Gap:
- <one issue>

Recommendation:
- <ship / patch more / use stronger model for X>
```
