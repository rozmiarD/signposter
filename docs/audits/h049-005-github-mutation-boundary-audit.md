# H049-005 GitHub Mutation Boundary Audit

Date: 2026-06-02
Repository: ExatronOmega/signposter
Base commit: `1365e8a`

## Purpose

This audit records current GitHub mutation boundaries before H049 expands
automation, stuck-state recovery, planner advancement, and operator loops.

The scoped question:

Are GitHub writes explicit, planned, guarded by `--apply`, and understandable
to the operator before mutation?

No runtime code is changed by this audit.

## Surfaces Reviewed

The audit reviewed these mutation-capable surfaces:

- issue claim;
- issue release, complete, and fail transitions;
- runner report comments;
- review submit;
- merge apply;
- post-merge integration apply;
- validated no-op integration apply;
- planner issue seed apply;
- planner advance apply;
- issue-factory apply;
- labels ensure;
- sync apply;
- cleanup apply.

It also reviewed worktree behavior because worktree commands mutate local git
state, even though they do not mutate GitHub.

## Current Safety Model

The implemented safety model is consistent with the project rules:

- default command surfaces are read-only or dry-run;
- GitHub mutation paths require explicit `--apply`;
- execution backends require explicit `--execute`;
- raw backend logs stay local;
- reports post bounded summaries;
- merge does not close issues;
- integration owns issue closure;
- cleanup is local-only;
- PR bodies are expected to avoid auto-close keywords.

This remains the correct architecture.

## Mutation Inventory

### Claim

Module: `src/signposter/claim.py`

Mutation:

- `gh issue edit` to replace `state:ready` with `state:active` and add a gate
  label;
- `gh issue comment` to record the claim.

Boundary:

- dry-run planning exists;
- apply path is explicit;
- dependency blocking is checked before selecting ready work;
- required labels are checked before claim mutation.

Current risk:

- post-claim run-plan output can display stale or incorrect route/gate fields
  even when GitHub labels are correct. This was observed on #210, #211, and
  #212 and should become a follow-up fix before fully unattended loops depend
  on post-claim classification output.

### Release / Complete / Fail

Module: `src/signposter/transitions.py`

Mutation:

- `gh issue edit` label transition;
- `gh issue comment` transition note.

Boundary:

- transition plan validates current state;
- complete moves only `state:active` to `state:done`;
- complete does not close the issue.

Current risk:

- transition ownership is clear, but terminal-state definitions are still
  interpreted in several modules.

### Report

Module: `src/signposter/report.py`

Mutation:

- `gh issue comment` with a bounded runner report.

Boundary:

- dry-run is default;
- `--apply` is required for posting;
- raw output is referenced but not posted in full;
- stale/failover artifact signals are checked before report posting.

Current risk:

- report comments depend on the active raw artifact path being safe. When a
  backend produces provider noise, the workflow must move that raw log out of
  the active path before reporting a manual takeover artifact.

### Review Submit

Module: `src/signposter/review.py`

Mutation:

- `gh pr review --approve --body-file ...`.

Boundary:

- review gate is evaluated first;
- self-review is blocked unless a separate reviewer token is configured;
- medium/high risk paths require explicit override flags;
- no merge or issue close is performed.

Current risk:

- reviewer execution may be unavailable because of backend model/provider
  limits. The review artifact path handles this, but the operator-visible
  backend reason should remain clear and bounded.

### Merge Apply

Module: `src/signposter/merge.py`

Mutation:

- `gh pr merge --squash --delete-branch`.

Boundary:

- merge plan checks PR state, mergeability, CI, reviewer gate, non-author
  approval, scope, risk, issue linkage, and auto-close keywords;
- apply refuses unless the plan status is `ready`;
- merge does not close the issue;
- local cleanup is not part of merge.

Current risk:

- remote branch deletion is part of the GitHub merge command. This is expected
  and visible, but should remain clearly separated from local cleanup.

### Integration Apply

Module: `src/signposter/integration.py`

Mutation:

- remove `state:done`;
- add `state:merged`;
- close the associated issue as completed;
- post integration comment.

Boundary:

- PR must be merged;
- associated issue must be detected;
- issue must be in the correct pre-integration state;
- main CI must pass;
- integration owns issue closure.

Current risk:

- integration depends on issue/PR linkage helpers that are duplicated with
  merge and cleanup. H049 should avoid adding another linkage implementation.

### No-op Integration Apply

Module: `src/signposter/integration.py`

Mutation:

- validated no-op path can move `state:done` to `state:merged` and close the
  issue without a PR when the gate evidence says the no-op is complete.

Boundary:

- no-op integration has a dedicated plan;
- dry-run output lists planned mutations only when ready;
- gate evidence is considered before closure.

Current risk:

- no-op closure is powerful and should stay structurally tested because it
  bypasses the normal PR merge signal.

### Planner Seed / Issue Factory

Modules:

- `src/signposter/planner.py`
- `src/signposter/issue_factory.py`

Mutation:

- `gh issue create` for planned tasks.

Boundary:

- planner validation runs before issue creation;
- seed plan is dry-run first;
- `--apply` is required;
- body size, auto-close keywords, labels, and duplicate titles are checked;
- issue-factory apply compares existing issue titles before creation.

Current risk:

- seeded task dependencies are key-based in the manifest, while scheduler
  graph dependencies are issue-number based. H049 should harden metadata
  round-trip before relying on repository-wide scheduler graph as a roadmap
  source.

### Planner Advance

Module: `src/signposter/planner.py`

Mutation:

- `gh issue edit #N --add-label state:ready`.

Boundary:

- dry-run is required before apply;
- apply refuses non-ready plans;
- apply only permits adding exactly `state:ready`;
- H049-S001 now prevents promoting a downstream task unless all dependencies
  are complete.

Current risk:

- multi-target advance is sequential. If a later target mutation has a
  command-level error, earlier targets may already be promoted. This is not a
  current blocker for H049-S001, but future automation should make partial
  mutation results explicit and stop later workflow steps immediately.

### Labels Ensure

Module: `src/signposter/labels.py`

Mutation:

- create missing workflow labels.

Boundary:

- check is read-only;
- ensure is dry-run by default;
- `--apply` is required for label creation.

Current risk:

- label definitions are foundational; label ensure should remain a preflight,
  not an implicit side effect of unrelated lifecycle commands.

### Sync Apply

Module: `src/signposter/sync.py`

Mutation:

- local git fetch/pull/rebase behavior, depending on options.

Boundary:

- dry-run by default;
- `--apply` required;
- intended to keep local main synchronized before lifecycle work.

Current risk:

- sync is local/git-mutating rather than GitHub-mutating, but it can still
  change checkout state. It should remain explicit in automated loops.

### Cleanup Apply

Module: `src/signposter/cleanup.py`

Mutation:

- remove local worktree;
- delete local branch.

Boundary:

- no GitHub mutation;
- requires merged PR;
- associated issue must be closed with `state:merged`;
- apply is explicit;
- already-clean state is treated as completed.

Current risk:

- cleanup currently depends on branch naming and issue linkage. This is
  acceptable for the current lifecycle, but H049 should keep branch collision
  hardening on the roadmap.

## Cross-Cutting Findings

### Strengths

- Mutation-capable paths are visible and mostly guarded.
- Merge and integration have the right ownership split.
- Cleanup is local-only.
- Review submission has an identity guard.
- Report output is bounded.
- Tests already cover many dry-run and apply boundaries.

### Gaps

- GitHub mutation logic is spread across many modules, so wording and failure
  reporting are not fully uniform.
- Some apply functions call multiple commands sequentially and rely on the
  caller to stop after an error.
- Issue/PR linkage helpers are duplicated in merge, integration, cleanup, and
  lifecycle surfaces.
- Planner and scheduler dependency metadata are not yet a complete
  round-trip.
- Post-claim classification can disagree with labels in operator output.

## Recommended Follow-Up

Add or keep H049 tasks for:

1. post-claim route/gate classification consistency;
2. issue/PR linkage helper consolidation;
3. planner/scheduler dependency metadata round-trip hardening;
4. partial-apply failure reporting for multi-command mutations;
5. dry-run/apply wording consistency sweep.

## Validation

This audit is documentation-only. Required validation:

- `ruff check .`
- `python -m pytest tests/ -q`

No GitHub mutation is performed by this committed audit file.
