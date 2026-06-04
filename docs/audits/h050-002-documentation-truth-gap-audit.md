# H050-002 Documentation Truth Gap Audit

## Scope

This audit compares the current documentation against implemented Signposter
behavior. It records stale or misleading sections only. It does not rewrite the
docs, change code, or introduce new workflow concepts.

## Current Truth Baseline

Current implemented behavior includes:

- planner draft, validate, seed, next, run, advance, impact, side-task, step,
  mark, roadmap, and status surfaces;
- lifecycle status, lifecycle next, orchestrator planning, scheduler selection,
  and control-plane status surfaces;
- worktree planning/apply, run claim, prompt writing, backend execution attempt,
  report, gate, complete, PR plan, review, merge, integration, and cleanup;
- role policy, backend status, subagent planning, token usage, artifact safety,
  comment/report bounding, and manual artifact support;
- applied H049 roadmap completion and seeded H050 roadmap with issues #371
  through #450.

The docs should describe Signposter as an implemented safety-first local control
plane, not as a skeleton-only project.

## Documents That Are Mostly Current

- `docs/architecture.md`: reflects the implemented control-plane layers,
  backend boundaries, role routing, gates, artifacts, comments, and remaining
  architectural debt.
- `docs/operator-lifecycle-runbook.md`: reflects the current issue-to-PR
  lifecycle and includes planner, worker, review, merge, integration, cleanup,
  and recovery commands.
- `docs/troubleshooting.md`: reflects current stuck-state diagnosis, artifact
  takeover, CI run selection, stale worktree handling, and handoff expectations.
- `docs/artifacts-reference.md`: mostly useful, but still needs schema-level
  precision for worker/reviewer fields and takeover artifact naming.

## Documents With Stale or Misleading Content

### `README.md`

The README still says:

- `Status (BOOTSTRAP-002): Structural skeleton complete`;
- `Orchestration logic: Not implemented`;
- `Implementation phase: Not started`;
- `No actual dispatch, scheduling, GitHub integration, or state machine logic has
  been written`;
- command sections are labeled as bootstrap phase and omit most implemented
  lifecycle surfaces.

This is materially misleading. The repository now has implemented planner,
scheduler/control-plane, lifecycle, worktree, runner, review, merge,
integration, cleanup, role/backend, artifact, report, gate, and H050 roadmap
surfaces.

Recommended follow-up: H050-067 should replace the bootstrap README with a
compact current-state README and link to the runbook, troubleshooting guide, and
architecture document.

### `docs/workflow.md`

This document is still titled `Signposter Workflow Overview (Skeleton)` and says
the flow is conceptual/future. It omits:

- planner manifests and dependency advancement;
- worktree claim/prompt/execute/report/gate/complete;
- PR/review/merge/integration/cleanup ownership;
- explicit dry-run/apply and execution guards;
- recovery/takeover behavior.

Recommended follow-up: H050-068 should rewrite this as a current workflow
overview, with enough detail to orient operators without duplicating the runbook.

### `docs/state-machine.md`

This document still says the state machine has no implementation and lists
future phases such as `queued`, `planning`, and `gate-evaluation`. Current
GitHub workflow labels are actually centered on:

- `state:ready`;
- `state:active`;
- `state:done`;
- `state:merged`;
- `state:blocked`;
- `state:failed`;
- open/closed GitHub issue state;
- merged/unmerged PR state;
- integration and cleanup completion.

Recommended follow-up: add or update a current lifecycle-state document after
the H050 lifecycle and terminal-state audits, rather than rewriting it before
those audits finish.

### `docs/labels.md`

The label list is mostly accurate but still says skeleton/bootstrap/example in
several places and does not emphasize current ownership rules:

- `state:done` does not close an issue;
- `state:merged` belongs to integration;
- gate labels are consumed by gate evaluation and removed by completion;
- planner advancement adds `state:ready` only after dependencies are complete.

Recommended follow-up: update labels documentation after planner and lifecycle
state audits clarify exact semantics.

## Truth Gaps That Should Not Be Fixed Yet

Do not immediately rewrite every old doc in one large task. The safer order is:

1. Update README current-state summary after H050 grounding and early audits.
2. Update workflow overview after planner/lifecycle edge behavior is hardened.
3. Update state-machine and labels docs after H050 lifecycle and planner
   terminal-state tasks reduce ambiguity.
4. Update artifact docs after schema validation and redaction tasks.

This keeps documentation truthful without freezing uncertain edge semantics too
early.

## No Code Bugs Found

This audit found stale documentation, not a code bug. The docs drift is important
because it can mislead an operator into treating implemented lifecycle controls
as unavailable or experimental.

## Safety

- No code was changed.
- No GitHub mutation was performed by this audit implementation.
- No issue was closed by this audit implementation.
- No merge was performed by this audit implementation.
- No execution backend output was trusted as implementation evidence.

## Validation

Targeted validation:

```bash
git diff --check -- docs/audits/h050-002-documentation-truth-gap-audit.md
```

Full validation before push:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
