# H051-003 GitHub CLI Stall Evidence Audit

Status: pass
Date: 2026-06-05
Issue: H051-003 / #533

## Scope

This audit records the current evidence path for GitHub CLI stalls and slow
GitHub synchronization inside Signposter. It is documentation-only and does not
change subprocess handling, GitHub mutation behavior, planner behavior, PR
linkage, or CI selection logic.

## Evidence Sources

- `src/signposter/cli.py`
- `src/signposter/runner.py`
- `src/signposter/review.py`
- `src/signposter/worktree.py`
- `src/signposter/lifecycle.py`
- `src/signposter/merge.py`
- `src/signposter/integration.py`
- `src/signposter/cleanup.py`
- `tests/test_pr.py`
- `tests/test_pr_linkage.py`
- recent H051 planner sync and lifecycle commands

## Current GitHub CLI Use

Signposter uses `gh` for several deterministic control-plane operations:

- issue reads, label edits, and bounded comments;
- planner GitHub synchronization against seeded issues;
- PR metadata, review, checks, merge, and issue linkage inspection;
- worktree branch safety checks;
- integration and cleanup readiness checks.

Most mutating paths remain guarded by explicit `--apply`. Dry-run paths print
operator-readable plans and notes before mutation. That safety boundary held
during H051-001 through H051-003.

## Observed Runtime Behavior

During H051, GitHub-backed planner commands frequently took tens of seconds
when run with `--sync-github` against the 80-node manifest. The commands did not
prompt interactively and eventually returned deterministic output, but they
produced no progress output while waiting for GitHub issue state reads.

Concrete examples from this run:

- `planner advance --manifest docs/roadmaps/h051-seed-manifest.json --issue 532 --sync-github --dry-run`
  returned `blocked` after a slow sync and correctly reported that H051-005 was
  still waiting for H051-003 and H051-004.
- `planner run --manifest docs/roadmaps/h051-seed-manifest.json --sync-github --dry-run`
  returned `ready` after a slow sync and selected H051-003 / issue #533.

The behavior is safe but not yet operator-friendly. A long quiet period can look
like a stall even when the command is still performing bounded GitHub reads.

## Existing Safeguards

Current safeguards are strongest around mutation boundaries:

- issue claim uses explicit label mutation and bounded claim comment;
- worktree apply previews branch/worktree creation before `--apply`;
- PR planning avoids auto-close keywords and keeps issue closure out of PR
  metadata;
- merge planning requires checks, review gate, non-author approval, scope/risk
  overrides, and no auto-close keywords;
- integration owns issue closure after merge;
- cleanup is local-only and remains guarded by `--apply`.

The targeted PR tests confirm the PR safety surface:

- PR bodies use `Related issue: #N` rather than auto-close keywords;
- ambiguous branch/body issue linkage is blocked;
- PR planning blocks unsafe suggested metadata;
- dry-run output states that no PR, merge, push, close, or GitHub mutation was
  performed.

## Evidence Gaps

GitHub CLI stall evidence is still weaker than the mutation guard model:

1. Planner GitHub sync does not show compact progress while it loops over many
   issues.
2. Some GitHub read paths have bounded subprocess timeouts, but timeout evidence
   is not uniformly exposed in operator output.
3. There is no shared GitHub subprocess wrapper that consistently records
   command category, timeout, bounded stderr, and recovery hint.
4. Slow-but-successful reads and true stalled reads can look similar to the
   operator until the command exits.
5. Retry guidance is mostly local to each command surface rather than expressed
   as a reusable GitHub CLI diagnostic contract.

## Safety Impact

The current behavior does not weaken Signposter's GitHub mutation safety. The
risk is operational: long quiet `gh` reads can cause unnecessary manual
intervention, duplicate inspection, or premature takeover during large planner
syncs.

This should be fixed as a visibility and evidence problem before adding more
automatic loop behavior around large DAGs.

## H051 Follow-Up Mapping

No side task is required. The current H051 DAG already contains the relevant
implementation follow-ups:

- H051-004: planner loop continuation audit;
- H051-005: safe next-step selector implementation;
- H051-013: GitHub CLI stall detector;
- H051-014: GitHub CLI stall takeover hint;
- H051-020: progress comment compactness baseline;
- H051-027: remote CI run selection timeout guard;
- H051-033: compact operator log event format.

## Validation

- `git diff --check -- docs/audits/h051-003-github-cli-stall-evidence-audit.md`
- `python -m pytest tests/test_pr.py tests/test_pr_linkage.py -q`
- `ruff check .`
- `python -m pytest tests/ -q`

## Safety

- No GitHub mutation was performed by this audit.
- No Signposter GitHub read or write behavior was changed.
- No PR, merge, integration, cleanup, or issue closure behavior was changed.
- No raw runtime output was posted to GitHub.
- No fallback or model substitution was performed.

## Conclusion

Signposter's GitHub mutation boundaries are intact, but large planner syncs and
other GitHub read-heavy surfaces need better quiet-period evidence. H051 should
continue with the existing implementation tasks that add safe next-step
selection, GitHub stall diagnostics, compact progress evidence, and timeout
guarding without changing the dry-run/apply contract.
