# Branch: `test-last-know-working`

This branch is intentionally preserved.

It captures the **last known working configuration** that allowed the Codex
mechanism to finish in-flight work without interruption. With that setup,
Signposter could keep running until the roadmap completed rather than stopping
when token limits were hit.

That behavior was **reported to OpenAI and subsequently fixed**.

## What this branch contains

- H052 roadmap manifests (`docs/roadmaps/h052-*`) aligned with GitHub issue bodies
- The planner/roadmap state used during that validation run

## What this branch is not

- Not the active development line — use `main` for current work
- Not intended for merge into `main` (roadmaps and audits are operator-local)
