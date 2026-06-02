# H049-003 Lifecycle State-Machine Audit

Date: 2026-06-02
Repository: ExatronOmega/signposter
Base commit: `3de51d2`

## Purpose

This audit records current lifecycle state-machine behavior before H049 changes automation, stuck-state recovery, gates, merge, integration, cleanup, or planner advancement.

## State Sources

Signposter currently combines several state sources:

- GitHub issue state: `OPEN` / `CLOSED`
- workflow labels: `state:ready`, `state:active`, `state:done`, `state:failed`, `state:blocked`, `state:merged`
- phase labels: `phase:*`
- risk labels: `risk:*`
- role labels: `role:*`
- area labels: `area:*`
- gate labels: `gate:ci`, `gate:review`, `gate:human`
- PR state and review state from GitHub
- local worktree and branch existence
- local artifact existence and parser output
- main/PR CI state

The user-visible lifecycle truth is produced mostly by `src/signposter/lifecycle.py`.

## Main Lifecycle Path

Current normal worker path:

1. Issue starts as `OPEN` with `state:ready`.
2. `worktree plan/apply` creates an isolated branch and worktree.
3. `run --claim` mutates labels from `state:ready` to `state:active` and adds a gate label.
4. `run --write-prompt` writes a local prompt artifact.
5. `run --execute` may run a backend only with explicit execution permission.
6. `report --apply` posts a bounded summary comment.
7. `gate --dry-run` evaluates local artifacts and issue labels.
8. `complete --apply` transitions `state:active` to `state:done` and removes gate labels.
9. PR is created and reviewed.
10. `merge apply --apply` merges only after gates, CI, approval, scope, and risk checks pass.
11. `integration apply --apply` changes `state:done` to `state:merged` and closes the issue.
12. `cleanup apply --apply` removes local worktree and local branch.

Important invariant:

- `state:done` does not close the GitHub issue.
- Issue closure belongs to integration.

## Transition Modules

### Basic issue transitions

`src/signposter/transitions.py` owns:

- `active -> ready` for release;
- `active -> done` for complete;
- `active -> failed` for fail;
- gate label removal on those transitions.

### Claim transition

`src/signposter/claim.py` owns:

- `ready -> active`;
- adding the planned gate label;
- claim comments.

### Integration transition

`src/signposter/integration.py` owns:

- `done -> merged`;
- issue closure after merged PR or validated no-op integration;
- main CI check before normal integration.

### Cleanup transition

`src/signposter/cleanup.py` owns:

- local worktree deletion;
- local branch deletion;
- no GitHub mutation.

## Terminal State Interpretations

Terminal states are interpreted in several places:

- planner uses `COMPLETED_PLANNER_STATES = {"closed", "done", "merged"}`;
- scheduler uses `TERMINAL_STATES = {"done", "merged", "blocked", "failed"}`;
- lifecycle treats `state:merged` plus closed issue as integrated complete;
- cleanup requires merged PR, closed issue, and `state:merged`;
- gate treats closed plus `state:merged` as completed/not applicable;
- worktree planning blocks terminal states.

Risk:

- These definitions are close but not identical. H049 should avoid adding another state definition and should consolidate or clearly document ownership.

## Gate State Behavior

Current gates:

- `gate:ci` validates worker artifacts.
- `gate:review` validates reviewer-style issue artifacts.
- `gate:human` validates explicit human approval evidence.

The gate command requires `state:active` for active work and treats closed plus `state:merged` as not applicable/completed.

Risk:

- Gate evidence is still partly phrase-based.
- Worker raw output can block if provider/runtime noise remains in the active raw artifact path.

## Observed H049-003 Classification Finding

During #210 execution:

- `run --issue 210 --dry-run` before claim showed `route: worker`, `gate: ci`.
- After `run --claim`, GitHub labels correctly showed:
  - `state:active`
  - `gate:ci`
  - `role:worker`
  - `phase:build`
  - `risk:medium`
  - `area:core`
- The refreshed run plan then printed `route: reviewer` and `gate: None`.

This is a real lifecycle/dispatch clarity issue. The source labels are correct, so the mismatch is likely in refreshed classification, dispatch fallback, or output formatting after claim. It should be handled in a follow-up H049 task before relying on fully automatic loops.

## Lifecycle Status Strengths

`lifecycle status` gives a useful cross-phase view:

- issue state and workflow label;
- PR state and merge commit;
- review decision and non-author approval;
- integration status;
- cleanup status;
- issue/PR linkage source and confidence;
- auto-close keyword detection.

This is the right foundation for operator trust.

## Lifecycle Status Gaps

- It reports status per issue/PR but does not yet summarize the whole active roadmap.
- It cannot by itself resolve manifest-scoped planner truth versus repository-wide scheduler truth.
- It does not expose stale active duration in the same view.
- It depends on several duplicated linkage helpers across modules.
- Some watch/status notes still use historical backend wording.

## Recommended Follow-Up

- H049-004 should audit planner DAG readiness counts and issue dependency truth.
- H049-005 should audit mutation boundaries and stop-on-failure behavior.
- H049-019 and later lifecycle tasks should consider a shared workflow-state helper.
- Add a concrete regression test for the #210 post-claim route/gate mismatch.

## Validation

This audit is documentation-only. Required validation:

- `ruff check .`
- `python -m pytest tests/ -q`

No GitHub mutation is performed by this committed audit file.
