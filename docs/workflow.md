# Signposter Workflow Overview

Signposter is a local workflow control plane for moving GitHub issues through a
safe, auditable lifecycle. It is deterministic by default: commands inspect
state, render plans, and perform mutations only when the operator uses explicit
guards such as `--apply` or `--execute`.

The workflow is supervised. Signposter can automate large parts of the loop, but
it does not remove the need for operator-visible evidence, green CI, review
approval, or guarded mutation boundaries.

## Core Concepts

- **Planner**: Turns a roadmap manifest into dependency-aware tasks and advances
  downstream work when dependencies are completed.
- **Scheduler**: Reads existing issue state and dependency metadata to identify
  the next eligible task.
- **Orchestrator/lifecycle**: Shows and moves one issue or PR through bounded
  lifecycle steps.
- **Runner/backend**: Executes a concrete worker or reviewer step only after
  explicit execution permission.
- **Gate**: Validates evidence before a state transition, review, merge, or
  integration step.
- **Integration**: Owns issue closure after a PR has merged.
- **Cleanup**: Removes local worktrees and branches after integration.

## States

The most common issue states are:

- `state:ready`: dependencies are satisfied and the issue may be claimed;
- `state:active`: work has been claimed and a gate is expected;
- `state:done`: worker evidence passed and the issue is ready for PR/review;
- `state:merged`: the associated PR was merged and integration closed the issue;
- `state:blocked`: the workflow must stop until the blocker is resolved.

`state:done` does not close the GitHub issue. Issue closure belongs to
integration after merge.

## Standard Issue-To-Merge Flow

1. Planner selects or promotes the next dependency-ready task.
2. `run --dry-run` shows routing, role, backend, model, reasoning, worktree, and
   fallback/takeover metadata.
3. `worktree plan` previews the branch/worktree.
4. `worktree apply --apply` creates the isolated worktree.
5. `run --claim --write-prompt` moves the issue to `state:active`, adds the
   gate label, and writes a local prompt artifact.
6. `run --execute --worktree` may execute the selected backend when explicitly
   allowed.
7. Worker output is normalized into local raw and summary artifacts.
8. If backend output is unavailable or unusable, a human/operator summary may
   replace the canonical bounded summary while preserving runtime diagnostics
   under non-canonical artifact names.
9. `report --apply` posts a bounded GitHub issue comment.
10. `gate --dry-run` validates evidence and proposes the next state.
11. `complete --apply` moves `state:active` to `state:done`.
12. The worker branch is pushed and a PR is opened.
13. CI is watched for the exact PR head commit.
14. Reviewer prompt, reviewer artifact, review gate, and formal approval run.
15. `merge plan` checks CI, approval, review gate, mergeability, risk/scope
    overrides, issue linkage, and auto-close keyword safety.
16. `merge apply --apply` merges the PR without closing the issue.
17. Main-branch CI is watched for the merge commit.
18. `integration apply --apply` moves `state:done` to `state:merged` and closes
    the issue.
19. `cleanup apply --apply` removes the local worktree and local branch.
20. Planner advance promotes downstream tasks when all dependencies are complete.

## Safety Boundaries

- Dry-run and status commands must not mutate GitHub or local workflow state.
- GitHub mutation requires `--apply`.
- Backend execution requires `--execute`.
- Mutating worker execution must run from an isolated task branch/worktree.
  Protected base branches such as `main`, `master`, and `trunk` may be used for
  sync/status/base planning, but direct worker mutation there is refused.
- Raw execution output remains local.
- GitHub comments and reviews must use bounded summaries.
- Merge must not rely on auto-close keywords.
- Medium/high risk and medium/large scope paths require explicit operator
  overrides where the relevant plan requires them.
- A later lifecycle mutation must not continue after an earlier critical
  mutation fails.

## Evidence And Artifacts

Worker artifacts should record:

- repository and issue number;
- agent/backend metadata;
- exit code and completion status;
- changed files;
- implemented or verified behavior;
- targeted and full validation evidence;
- safety notes;
- gate recommendation.

Reviewer artifacts should record:

- verdict;
- confidence;
- risk;
- scope match;
- CI considered;
- merge recommendation;
- automerge eligibility;
- findings and reasoning summary.

Raw backend output remains local under `artifacts/runs/`. Reports and GitHub
comments should include only bounded excerpts.

## Recovery

When a task is stuck, inspect state before retrying:

- issue labels and comments;
- lifecycle status;
- worktree and branch;
- prompt, raw, summary, and last-message artifacts;
- PR state and CI;
- review gate state;
- integration and cleanup state;
- planner manifest and dependency status.

Prefer the smallest Signposter-native recovery path. If backend execution cannot
produce usable output, preserve runtime artifacts and create a bounded
human/operator summary. If a bug blocks the loop, record it in the bug ledger or
create a narrow follow-up issue with dependencies and acceptance criteria.

## Validation

Before push, run targeted validation for the changed surface and then full local
validation:

```bash
ruff check .
python -m pytest tests/ -q
```

Inside an isolated worktree, reuse the main clone virtualenv:

```bash
MAIN_REPO=~/projects/signposter
PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/ruff" check .
PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/python" -m pytest tests/ -q
```

After push, watch GitHub Actions for the exact PR commit. Do not merge on red or
pending CI.
