# Signposter Architecture

**Status:** Implemented local workflow control plane.

Signposter is a safety-first local orchestrator for moving GitHub issues through a
bounded development lifecycle. It is not a free-running agent. The control plane
is deterministic: code selects states, checks gates, plans mutations, and records
operator-visible evidence. LLM-backed execution is limited to scoped worker,
reviewer, planning, summarization, or reconcile steps, and only runs when the
operator explicitly enables execution.

## Control-Plane Layers

### Planner

The planner turns roadmap manifests into a dependency-aware task graph and
selects the next manifest-scoped task. It owns:

- local planner drafts and validation;
- seed manifests and guarded GitHub issue creation;
- task status, next-task, impact, advance, mark, and side-task planning;
- deterministic reconcile hints before any LLM or human reconcile.

Planner commands are dry-run/read-only by default. GitHub label or issue
creation happens only on guarded `--apply` paths. Planner run and advance are
deterministic and use zero LLM tokens by default.

### Scheduler

The scheduler reads GitHub issue labels and dependency metadata to identify the
next eligible open task. It is repository-wide, while the planner is
manifest-scoped. Operator output must keep that distinction visible so an old
ready issue outside the active manifest does not get mistaken for active roadmap
truth.

### Orchestrator and Lifecycle

The orchestrator plans or runs bounded lifecycle steps. Lifecycle status is the
cross-phase truth surface for a single issue or PR. It combines:

- GitHub issue state and workflow labels;
- PR state, merge commit, review decision, and non-author approval;
- local worktree and branch presence;
- prompt, worker, reviewer, and gate artifacts;
- CI, integration, and cleanup state.

The normal code-change path is:

1. issue is `state:ready`;
2. worktree apply creates an isolated branch/worktree;
3. run claim moves the issue to `state:active` and adds a gate label;
4. prompt generation writes a local prompt artifact;
5. execution may run only with explicit execution permission;
6. report posts a bounded GitHub comment;
7. gate evaluates local evidence;
8. complete moves `state:active` to `state:done`;
9. PR review, approval, and merge gates run;
10. integration moves `state:done` to `state:merged` and closes the issue;
11. cleanup removes local worktree and branch.

`state:done` does not close a GitHub issue. Issue closure belongs to
integration.

## Execution Backends

Runner and backend modules adapt concrete execution tools to Signposter's
artifact contract. Current backend code includes Codex CLI support and legacy
OpenClaw compatibility surfaces.

Execution responsibilities are intentionally narrow:

- resolve role, model, reasoning metadata, and backend command shape;
- run only when explicit execution is requested;
- capture raw output locally under `artifacts/runs/`;
- write bounded summaries for reporting and gates;
- classify runtime failures so takeover paths can preserve evidence.

Backend defaults must not make lifecycle, merge, cleanup, or GitHub mutation
decisions. Those remain deterministic control-plane responsibilities.

## Roles and Routing

Role policy and routing modules select an execution role from task stage, risk,
area, and scope. The registry records model and reasoning metadata so operator
output can explain:

- selected role;
- selected model;
- selected reasoning effort;
- agent/profile name;
- why the role was selected;
- whether execution is deterministic or LLM-backed.

Cost-aware routing is allowed only inside recoverable generation or review
stages. Stronger roles are used for planner, lifecycle, gate, merge, security,
or policy semantics. Cheap roles must not make irreversible safety decisions.

## Gates, Review, Merge, Integration, Cleanup

Gate and post-PR modules are separate safety boundaries:

- `gate.py` validates worker, CI, review, human, and validated no-op evidence;
- `review.py` plans reviewer prompts, parses reviewer summaries, gates review
  decisions, and submits approvals only on guarded apply paths;
- `merge.py` checks CI, review gate, non-author approval, mergeability, risk,
  scope, and auto-close keyword safety before merge;
- `integration.py` owns post-merge label transition and issue closure;
- `cleanup.py` owns local worktree and local branch deletion.

Mutation paths must stop after critical failures. A later lifecycle mutation must
not continue when an earlier required mutation failed.

## Artifacts and Comments

Raw backend output stays local. GitHub comments are bounded summaries. Manual
worker and reviewer artifacts are first-class evidence when backend execution is
unavailable or unusable.

Artifact handling must preserve:

- repository, issue or PR identifier, agent/backend metadata;
- exit status and completion status;
- changed files;
- validation evidence;
- safety notes;
- gate or review recommendation;
- local raw-output path for diagnostics.

The gate layer still has some phrase-sensitive evidence checks. Prefer
structured fields and bounded summaries over noisy raw logs.

## Safety Invariants

- Default behavior is dry-run/read-only.
- GitHub mutation requires explicit `--apply`.
- Backend execution requires explicit execution permission.
- Raw execution artifacts remain local.
- GitHub comments receive bounded summaries only.
- `state:done` does not close issues.
- Integration owns issue closure.
- Merge must not rely on auto-close keywords.
- High-risk paths require explicit operator override.
- Human/operator authority remains the final gate.
- Local validation is required before push.
- CI must be green before merge.

## Primary Module Boundaries

- `cli.py`: argparse wiring and command dispatch.
- `planner.py`, `issue_manifest.py`, `issue_factory.py`, `dependencies.py`:
  roadmap, manifest, issue, and DAG behavior.
- `scheduler/`, `control_status.py`, `orchestrator.py`: task selection,
  control-plane status, next-step planning, and bounded loop behavior.
- `lifecycle.py`, `transitions.py`: lifecycle status and basic label
  transitions.
- `worktree.py`, `handoff.py`, `pr.py`, `sync.py`: local branch/worktree and PR
  planning surfaces.
- `runner.py`, `execution_backend.py`, `codex_cli_backend.py`,
  `openclaw_*`, `codex_subagent.py`: backend execution adapters and artifact
  capture.
- `role_policy.py`, `role_routing.py`, `role_smoke.py`: role/model/reasoning
  policy and smoke planning.
- `gate.py`, `review.py`, `merge.py`, `integration.py`, `cleanup.py`: evidence,
  review, merge, issue closure, and local cleanup safety boundaries.
- `comments.py`, `report.py`, `artifact.py`, `artifact_safety.py`,
  `bug_ledger.py`: bounded comments, manual artifacts, stale-output detection,
  and automation bug records.

## Current Architectural Debt

- Some README/runbook text still describes older bootstrap or OpenClaw-first
  behavior.
- Terminal workflow state ownership is interpreted in several modules and should
  remain audited as automation expands.
- Linkage helpers for issue/PR detection are duplicated across lifecycle,
  merge, integration, and cleanup surfaces.
- Gate evidence should continue moving from phrase checks toward structured
  artifact fields.
- Runtime model availability can block otherwise valid execution plans; takeover
  paths must keep preserving failed runtime diagnostics.
