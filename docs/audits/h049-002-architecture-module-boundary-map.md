# H049-002 Architecture Module Boundary Map

Date: 2026-06-02
Repository: ExatronOmega/signposter
Base commit: `059a679`

## Purpose

This audit maps the current implemented Signposter module boundaries before H049 changes lifecycle automation, planner DAG behavior, stuck-state recovery, comments, gates, or documentation.

## Current High-Level Shape

Signposter is implemented as a local deterministic workflow control plane. The code is no longer a skeleton: it has planner, scheduler, lifecycle, orchestrator, execution backend, artifact, gate, review, merge, integration, cleanup, and GitHub mutation surfaces.

The strongest architectural boundary is:

- deterministic control plane decides state, gates, and mutations;
- execution adapters run external agents only when explicitly requested;
- artifacts remain local by default;
- GitHub comments receive bounded summaries;
- integration owns issue closure after merge.

## Module Groups

### CLI Wiring

- `src/signposter/cli.py`

This is the command router for all user-facing surfaces. It currently owns a large amount of argparse setup and command dispatch. It is the broadest module in the repo and should be treated as a coordination layer, not a policy home.

Boundary recommendation:

- keep CLI thin where practical;
- move policy and formatting logic into feature modules;
- test CLI handlers with mocked core functions.

### Planner and DAG

- `src/signposter/planner.py`
- `src/signposter/issue_manifest.py`
- `src/signposter/issue_factory.py`
- `src/signposter/dependencies.py`

Responsibilities:

- local planner draft and validation;
- seed manifest generation;
- guarded GitHub issue creation from planner tasks;
- manifest-backed status, next, step, impact, advance, mark, and roadmap surfaces;
- GitHub issue DAG manifest creation from issue body metadata;
- dependency parsing.

Current boundary tension:

- planner manifests preserve task-key dependencies;
- scheduler graph parses GitHub issue references from issue bodies;
- generated planner issue bodies do not yet make dependency metadata round-trip cleanly for GitHub issue graph parsing;
- planner task counts can imply readiness differently from next-task reconcile hints.

### Scheduler and Control Plane

- `src/signposter/scheduler/__init__.py`
- `src/signposter/control_status.py`
- `src/signposter/orchestrator.py`

Responsibilities:

- repository-wide next-ready issue selection from GitHub labels;
- graph metadata display for open workflow issues;
- active issue diagnostics;
- compact control-plane status;
- deterministic orchestrator next/step/loop planning.

Current boundary tension:

- scheduler is repository-wide and can see stale ready issues from previous roadmaps;
- planner is manifest-scoped and can correctly select the active roadmap task;
- control-plane status must make this source-of-truth distinction explicit.

### Lifecycle State

- `src/signposter/lifecycle.py`
- `src/signposter/transitions.py`

Responsibilities:

- issue/PR/review/integration/cleanup status aggregation;
- next lifecycle action planning;
- read-only lifecycle watch contract;
- label transition planning for basic issue states.

Current boundary tension:

- lifecycle is the cross-phase truth surface, but some state labels and terminal behavior are also interpreted in planner, scheduler, gate, merge, integration, cleanup, and worktree modules.

Recommendation:

- audit duplicated state checks before adding more automation;
- prefer one shared helper or clear ownership for terminal state semantics.

### Worktree, Branch, Handoff, and PR Planning

- `src/signposter/worktree.py`
- `src/signposter/git_utils.py`
- `src/signposter/handoff.py`
- `src/signposter/pr.py`
- `src/signposter/sync.py`

Responsibilities:

- guarded worktree and branch planning/apply;
- local git status and branch checks;
- handoff and PR metadata planning;
- repository sync/rebase planning.

Boundary recommendation:

- keep destructive git operations guarded and narrow;
- keep PR creation as planned metadata unless a guarded apply path is explicitly added.

### Execution Backends and Runner

- `src/signposter/runner.py`
- `src/signposter/execution_backend.py`
- `src/signposter/codex_cli_backend.py`
- `src/signposter/codex_stage_contract.py`
- `src/signposter/codex_subagent.py`
- `src/signposter/openclaw_runtime.py`
- `src/signposter/openclaw_preflight.py`
- `src/signposter/openclaw_diagnostics.py`

Responsibilities:

- plan and execute worker runs;
- resolve backend command shape;
- capture raw and summary artifacts;
- classify backend runtime states;
- keep OpenClaw as legacy fallback where present;
- plan bounded local subagent dispatch contracts.

Current boundary tension:

- some output notes still mention OpenClaw even when the active backend is not OpenClaw;
- live model availability can block otherwise valid execution plans;
- reasoning effort is preserved as Signposter metadata even when backend transport differs.

### Roles and Routing

- `src/signposter/role_policy.py`
- `src/signposter/role_routing.py`
- `src/signposter/role_smoke.py`

Responsibilities:

- role/model/reasoning registry;
- deterministic stage-to-role routing;
- backend-specific agent selection;
- smoke planning/execution for role availability.

Boundary recommendation:

- keep cost policy explainable but conservative;
- do not let cheap routing make irreversible workflow decisions;
- keep fallback explicit and visible.

### Gates, Review, Merge, Integration, Cleanup

- `src/signposter/gate.py`
- `src/signposter/review.py`
- `src/signposter/merge.py`
- `src/signposter/integration.py`
- `src/signposter/cleanup.py`

Responsibilities:

- worker, CI, review, human, and no-op gate decisions;
- PR review planning/execution/gating/submission;
- guarded merge eligibility/apply;
- post-merge issue integration and issue closure;
- local cleanup of finished worktrees/branches.

Current boundary tension:

- gate evidence remains partly phrase-based;
- merge/integration/cleanup each independently detect PR/issue linkage and state;
- issue closure ownership is correctly centralized in integration, but linkage helpers are duplicated.

### GitHub Comments and Artifacts

- `src/signposter/comments.py`
- `src/signposter/report.py`
- `src/signposter/artifact.py`
- `src/signposter/artifact_safety.py`
- `src/signposter/bug_ledger.py`

Responsibilities:

- compact transition comments;
- bounded runner reports;
- manual worker/reviewer artifact creation and validation;
- stale/failover/provider-noise detection;
- automation bug ledger entries.

Boundary recommendation:

- keep raw backend output local;
- keep report comments bounded and redacted;
- prefer structural artifact fields over phrase-sensitive gate matching.

### Repository Labels and Scanning

- `src/signposter/labels.py`
- `src/signposter/scan.py`
- `src/signposter/dispatch.py`
- `src/signposter/claim.py`

Responsibilities:

- label preflight and guarded label creation;
- read-only GitHub issue/PR scanning;
- dispatch classification;
- explicit issue claiming.

Boundary tension:

- `labels.py` required-label list is smaller than the labels the wider workflow now uses.

## Test Surface Map

The test suite is broad and covers:

- planner, issue factory, issue manifest, scheduler, orchestrator;
- lifecycle, transitions, claim, worktree, PR;
- gate, review, merge, integration, cleanup;
- runner, Codex CLI backend, OpenClaw runtime/preflight/diagnostics;
- role policy, role routing, role smoke, subagent;
- artifact, artifact safety, report, comments, bug ledger;
- labels, scan, sync, doctor, control status.

The largest test files are planner, orchestrator, review, runner, merge, gate, lifecycle, and integration. That matches the highest-risk architecture areas.

## Immediate Architecture Risks

- `cli.py` is large and should not absorb more policy.
- Planner manifest truth and GitHub issue graph truth are not fully unified.
- Scheduler can select stale ready issues outside the active roadmap.
- Several modules duplicate PR-to-issue linkage helpers.
- Several modules duplicate workflow state interpretation.
- Documentation is stale and can mislead operators.
- Backend wording is not fully backend-neutral.
- Live backend model availability can require manual takeover despite valid routing.

## H049 Use

This boundary map should guide H049 follow-up:

- H049-003 should focus on shared lifecycle state semantics.
- H049-004 should focus on planner DAG truth and dependency reporting.
- H049-005 should focus on mutation boundaries.
- Later H049 tasks should reduce duplication only where it clearly improves safety or clarity.

## Validation

This audit is documentation-only. Required validation:

- `ruff check .`
- `python -m pytest tests/ -q`

No GitHub mutation is performed by this committed audit file.
