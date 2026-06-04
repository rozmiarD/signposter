# H050-016 Lifecycle Terminal-State Consistency Audit

## Scope

This audit covers Signposter's terminal lifecycle states and the boundaries
between worker completion, PR merge, integration, and cleanup.

Audited surfaces:

- `state:done`;
- `state:merged`;
- closed GitHub issues;
- merged PRs;
- integration status;
- cleanup status.

Non-goals:

- no lifecycle rewrite;
- no new state label;
- no manual issue closure;
- no GitHub mutation;
- no unrelated documentation cleanup.

## Current Terminal-State Invariants

The lifecycle has one clear ownership chain:

1. Worker completion moves an active issue to `state:done`.
2. `state:done` does not close the GitHub issue.
3. PR merge merges code only; it must not rely on auto-close keywords.
4. Integration owns the transition from `state:done` to `state:merged`.
5. Integration owns GitHub issue closure after merge or validated no-op.
6. Cleanup is local-only and runs after merged PR plus integrated issue.
7. A complete lifecycle requires merge, integration, and cleanup evidence.

This ownership split is consistent with earlier H049 audits and with the H050
execution path used for issues #371 through #385.

## Consistency Matrix

| Surface | Required terminal evidence | Owner | Safe next action when missing |
| --- | --- | --- | --- |
| `state:done` | Issue is open, worker gate has passed, gate labels are removed | `complete` | Create or update the PR; do not close the issue |
| Merged PR | PR is merged, CI and review gates passed, no unsafe auto-close keyword | `merge` | Run integration plan/apply after main CI is suitable |
| Integrated issue | Issue is closed and has `state:merged` | `integration` | Run integration recovery; do not close manually |
| `state:merged` | Issue is closed and PR/no-op integration evidence exists | `integration` | Treat worker/issue gates as not applicable; check cleanup |
| Cleanup complete | Expected worktree and local branch are absent | `cleanup` | Run cleanup plan/apply; keep it local-only |

## Evidence From Recent H050 Lifecycles

Recent H050 tasks exercise the intended terminal sequence:

- H050-011 / issue #381 / PR #462 completed worker, PR, review, merge,
  integration, cleanup, and planner advancement.
- H050-012 / issue #382 / PR #463 completed the same sequence.
- H050-013 / issue #383 / PR #464 completed the same sequence after summary
  wording was corrected to avoid misleading gate evidence.
- H050-014 / issue #384 / PR #465 completed with explicit medium-risk and
  medium-scope merge allowances.
- H050-015 / issue #385 / PR #466 completed with explicit medium-risk and
  medium-scope merge allowances.

For these tasks, issue closure occurred through integration, not through worker
completion or PR body keywords. Cleanup followed integration and removed local
worktrees and branches.

## Terminal-State Checks

### `state:done`

`state:done` is an intermediate terminal state for the worker portion of the
lifecycle. It proves the scoped issue work passed its gate, but the issue is
still expected to remain open until PR merge and integration. Any automation
that treats `state:done` as issue-closed would blur the worker and integration
owners.

Expected operator interpretation:

- ready for PR/review/merge flow;
- issue still open;
- integration still pending;
- cleanup still pending.

### Merged PR

A merged PR proves code landed, but it is not enough to complete the issue. The
merge path is responsible for review, CI, scope, risk, and mergeability gates.
It should not use auto-close keywords as a shortcut for issue closure.

Expected operator interpretation:

- integration is the next owner;
- main CI may still need to be considered by integration;
- issue should remain open until integration applies.

### `state:merged` and Closed Issue

The strongest integrated state is the pair:

- GitHub issue is closed;
- workflow label is `state:merged`.

This pair is what allows lifecycle status to report integration complete and
lets later gates treat the original worker issue as completed/not applicable.
If only one side exists, the state is inconsistent and should be reconciled
through integration rather than by manual label or issue edits.

### Cleanup Complete

Cleanup completion is local evidence:

- expected worktree is absent;
- expected local branch is absent.

Cleanup must stay local-only. It should not mutate issue state, close issues,
merge PRs, or delete unrelated branches.

## Risks And Gaps

- Terminal-state ownership is conceptually clear, but related checks still
  appear across planner, scheduler, gate, lifecycle, integration, cleanup,
  worktree, and merge modules.
- Issue-to-PR linkage can rely on branch-pattern evidence when GitHub's formal
  development link is missing or unknown.
- Runtime backend diagnostics can produce unusable worker/reviewer artifacts;
  the canonical summary path must preserve bounded, gate-friendly evidence.
- Manual artifact summaries may have no paired raw implementation output when
  a human/operator takeover is used after backend unavailability.

None of these findings require changing terminal-state semantics in this task.

## Recommendations

- Keep integration as the sole owner of issue closure.
- Keep `state:done` open and visibly distinct from `state:merged`.
- Keep PR bodies free of auto-close keywords unless a future explicit task
  changes that policy.
- Keep cleanup idempotent and local-only.
- Prefer shared terminal-state helpers before adding more state interpretation
  sites.
- Add future regression coverage for inconsistent pairs such as open plus
  `state:merged`, closed plus `state:done`, and merged PR without integration.

## Safety Notes

- No code changed in this audit.
- No GitHub mutation was performed by this audit implementation.
- No issue was closed by this audit implementation.
- No merge was performed by this audit implementation.
- No cleanup was performed by this audit implementation.
- Backend diagnostics from the attempted worker execution remain local.

## Validation

Targeted validation:

```bash
git diff --check -- docs/audits/h050-016-lifecycle-terminal-state-consistency-audit.md
```

Full validation before push:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
