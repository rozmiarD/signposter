# H049-019 Lifecycle Status Schema Audit

Date: 2026-06-02
Repository: ExatronOmega/signposter
Base commit: `60c7fed`

## Purpose

This audit records the current `signposter lifecycle status` operator contract
so later automation, dashboard, stuck-state, and handoff work can depend on a
known field set instead of inferring lifecycle state from prose.

Scope is intentionally read-only:

- map fields currently collected by `src/signposter/lifecycle.py`;
- map visible output sections from `format_lifecycle_status`;
- identify stable fields, derived fields, and brittle edges;
- avoid runtime behavior changes.

## Current Data Model

`LifecycleStatus` is the structured schema behind the detailed status output.
It is a frozen dataclass with these groups.

### Query Fields

- `query_issue`: issue number requested by the operator, if any.
- `query_pr`: PR number requested by the operator, if any.

Exactly one of `query_issue` or `query_pr` is required. Invalid input returns an
incomplete status with no mutation.

### Issue Fields

- `issue_number`: resolved associated issue.
- `issue_state`: GitHub issue state such as `OPEN` or `CLOSED`.
- `workflow_state`: workflow state label, for example `state:done` or `state:merged`.
- `phase`: `phase:*` label.
- `risk`: `risk:*` label.
- `role`: `role:*` label.
- `area`: `area:*` label.

These fields come from GitHub issue state plus workflow labels.

### PR Fields

- `pr_number`: resolved associated PR.
- `pr_state`: GitHub PR state such as `OPEN` or `MERGED`.
- `pr_base`: base branch.
- `pr_head`: head branch.
- `pr_merged`: derived boolean requiring `pr_state == "MERGED"` and a merge commit.
- `merge_commit`: merge commit OID when available.

When the operator queries by issue, PR detection is best-effort. When the
operator queries by PR, the associated issue is inferred from branch/body
linkage.

### Review Fields

- `review_decision`: currently the first detected approved GitHub review state.
- `has_non_author_approval`: true when a non-author approval exists.
- `reviewer_login`: login for the non-author reviewer.

The status view does not parse local reviewer summary artifacts. It reports
GitHub review state.

### Integration Fields

- `integrated`: true when `workflow_state == "state:merged"` and issue state is `CLOSED`.
- `issue_closed`: true when the GitHub issue is closed.

This matches the project invariant that worker completion does not close the
issue. Normal issue closure belongs to integration apply.

### Cleanup Fields

- `expected_worktree`: `../signposter-work/<issue-number>` when issue is known.
- `worktree_exists`: local worktree existence.
- `local_branch_exists`: local PR branch existence.
- `cleanup_complete`: true when both local worktree and local branch are absent.

Cleanup is intentionally local. The status command does not delete anything.

### Linkage Fields

- `linkage_source`: detected issue/PR linkage source.
- `linkage_confidence`: high, medium, or low.
- `formal_github_development_link`: yes only when a closing keyword is the source; otherwise no/unknown.
- `auto_close_keyword`: true when the PR body contains an auto-close keyword.

Current source values include branch-pattern, PR body related issue, closing
keyword, direct, issue-search, and unknown-style fallbacks.

### Overall Fields

- `status`: complete or an incomplete reason.
- `notes`: read-only safety notes.

The detailed formatter renders all groups. The summary formatter renders a
compact line-oriented subset for loop/watch output.

## Detailed Output Contract

`format_lifecycle_status` currently renders these sections in order:

1. `Issue`
2. `PR`
3. `Review`
4. `Integration`
5. `Cleanup`
6. `Linkage`
7. `Status`
8. `Notes`

This ordering is useful and should remain stable for operator scanning and
future machine-friendly parsing. If a future command needs a stricter API, it
should add a structured output mode instead of breaking this text contract.

## Summary Output Contract

`format_lifecycle_status_summary` renders:

- target;
- issue;
- PR;
- status;
- review;
- integration;
- cleanup;
- stop reason;
- read-only notes.

This is the right surface for loops and dashboards. It is compact enough to
repeat, while the detailed status remains the audit/debug view.

## Completion Semantics

Lifecycle status is `complete` only when all required lifecycle evidence is
present:

- associated issue resolved;
- associated PR resolved, except validated no-op completion path;
- issue is closed;
- issue has `state:merged`;
- PR is merged;
- merge commit exists;
- local worktree is absent;
- local branch is absent.

Validated no-op lifecycle has a special complete path when evidence exists and
there is no PR.

## Stable Operator Fields

These fields are safe to treat as high-value operator contract fields:

- issue state and workflow state;
- PR state, head, base, merged, and merge commit;
- review decision and non-author approval;
- integrated and issue closed;
- worktree exists, local branch exists, cleanup complete;
- linkage source, confidence, and auto-close keyword;
- final lifecycle status and stop reason.

## Brittle Edges

- `workflow_state`, `phase`, `risk`, `role`, and `area` are extracted by label
  prefix and keep the last matching label. Multiple labels in one category can
  silently shadow each other.
- PR detection from issue remains best-effort and can report no associated PR
  when branch/body conventions are missing.
- Formal GitHub development link is conservative and usually reports
  `no/unknown`, because auto-close keywords are intentionally avoided.
- Review status is GitHub-only. It does not explain whether local reviewer
  artifact validation passed.
- Cleanup status depends on local filesystem/branch state and can differ across
  machines.
- Error status uses bounded exception text, but the schema does not expose a
  typed error code.

## Recommendations

- Keep detailed lifecycle output stable while adding future structured status
  only as an additive mode.
- Add a shared helper for workflow label category extraction before adding more
  label-dependent surfaces.
- Add explicit duplicate workflow-label diagnostics in a later safety task.
- Keep summary output terse for loops; add detail only to the detailed status
  or a separate diagnostic command.
- Consider including stale-active age and artifact freshness in later
  stuck-state tasks rather than overloading lifecycle status now.

## Validation

This task is documentation/audit only. Runtime behavior is unchanged.

Validation required before completion:

- `ruff check .`
- `python -m pytest tests/ -q`

Safety:

- No GitHub mutation is performed by this document.
- No manifest mutation is performed by this document.
- No issue is closed by this document.
- No merge is performed by this document.
