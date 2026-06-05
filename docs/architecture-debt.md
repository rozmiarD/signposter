# Signposter Architecture Debt Register

## Purpose

This register tracks architecture debt that affects Signposter's safety,
operator clarity, or autonomous lifecycle reliability. It is intentionally
bounded: each item names the owner surface, risk, next useful test hook, and
status. It is not a backlog for broad rewrites.

## Status Legend

- `open`: known debt with no complete fix yet.
- `mitigated`: current behavior is safe enough, but the design should stay
  visible.
- `watch`: no immediate change required; preserve coverage and operator wording.

## Debt Items

| ID | Owner surface | Risk | Status | Next test hook |
| --- | --- | --- | --- | --- |
| AD-001 | `lifecycle.py`, `gate.py`, `integration.py`, `cleanup.py`, `merge.py` | Terminal workflow state interpretation is spread across modules. A future change can make `state:done`, `state:merged`, issue closure, or cleanup disagree. | open | Add or extend tests that cover done issue, merged PR, validated no-op, integration apply, and cleanup status from one fixture. |
| AD-002 | `lifecycle.py`, `merge.py`, `integration.py`, `cleanup.py`, `pr.py` | Issue-to-PR linkage uses repeated branch-pattern and GitHub metadata logic. Drift can make a merged PR look missing or attach cleanup to the wrong branch. | open | Add shared linkage regression cases for branch-pattern-only links, deleted remote branch, missing formal development link, and wrong branch name. |
| AD-003 | `planner.py`, `issue_manifest.py`, `dependencies.py`, GitHub labels | Manifest truth and GitHub issue state can diverge. Large `--sync-github` runs are slow and make recovery harder when a task is half-promoted. | open | Add planner sync/idempotence tests for duplicate issue mapping, dependency completion, promoted-ready labels, and timeout-friendly status wording. |
| AD-004 | `scheduler/`, `planner.py`, `control_status.py` | Repository-wide scheduler truth and manifest-scoped planner truth are separate. A stale ready issue outside the active manifest can confuse operator output. | mitigated | Add dashboard tests that show manifest path, next manifest task, and out-of-manifest ready issues distinctly. |
| AD-005 | `gate.py`, `artifact.py`, `artifact_safety.py`, `review.py` | Some evidence checks remain phrase-sensitive. Neutral text can still look like a blocker if it reuses backend failure vocabulary in a canonical summary. | open | Add regression cases where neutral safety wording passes only when structured fields are positive, while real failure summaries still block. |
| AD-006 | `report.py`, `comments.py` | Runner report comments are append-only. This is safe for auditability but can create duplicate-looking GitHub comments during retries. | watch | If update mode is added, test trusted-comment selection, multiple-match blocking, dry-run id display, and no edits to user comments. |
| AD-007 | `runner.py`, `codex_cli_backend.py`, `openclaw_*`, `backend_status.py`, `bug_ledger.py` | Role policy can be valid while the live backend account lacks the selected model. Automation then needs takeover while preserving runtime evidence. | mitigated | Keep tests for runtime blocker classification, `.codex-runtime.*` preservation guidance, fallback transparency, and token usage status `unknown`. |
| AD-008 | `worktree.py`, `lifecycle.py`, `git_utils.py` | Some worktree and lifecycle path reporting is current-working-directory sensitive. Running commands from an issue worktree can make relative paths misleading. | open | Add tests that run worktree/lifecycle planning from repo root and issue worktree, then compare expected path ownership and cleanup eligibility. |
| AD-009 | `cli.py` | CLI dispatch remains large and can attract policy logic that belongs in focused modules. This increases review risk for future command additions. | watch | For each new command, require unit coverage in the owning module plus a narrow CLI wiring test, rather than adding policy-only assertions in `cli.py`. |
| AD-010 | `docs/`, `README.md`, `docs/workflow.md`, `docs/operator-lifecycle-runbook.md` | Docs can lag behind implemented lifecycle behavior, especially backend defaults, issue closure ownership, and manual takeover. | mitigated | Keep docs-only smoke checks for stale auto-close wording, raw-log posting claims, OpenClaw-first wording, and current validation commands. |

## Near-Term Priority

1. Keep terminal state and issue/PR linkage tests ahead of new automation loops.
2. Prefer structural artifact fields over phrase matching when changing gates.
3. Keep planner/GitHub sync output explicit about manifest scope and slow sync.
4. Preserve backend runtime diagnostics locally before replacing canonical
   summaries during takeover.

## Non-Goals

- No broad module rewrite is implied by this register.
- No new command surface is implied by this register.
- No GitHub mutation or issue closure behavior changes are made by this
  document.

## Validation

Required validation for updates to this file:

```bash
git diff --check -- docs/architecture-debt.md
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
