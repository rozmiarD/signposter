# Signposter Troubleshooting and Handoff

## Purpose

This guide gives operators a compact recovery path when a Signposter lifecycle
run stops between issue selection, worker execution, PR review, merge,
integration, cleanup, or planner advancement.

Use it together with `docs/operator-lifecycle-runbook.md` and
`docs/artifacts-reference.md`. The runbook gives the normal command sequence;
this guide focuses on diagnosing interrupted or inconsistent states.

## Related References

- `docs/operator-lifecycle-runbook.md`: normal issue-to-cleanup command flow.
- `docs/artifacts-reference.md`: artifact names, schema fields, takeover
  preservation, and redaction boundaries.
- `docs/architecture.md`: module boundaries and lifecycle ownership model.
- `docs/workflow.md`: concise lifecycle overview for status and evidence flow.

## First Evidence

Start from repository root unless a command explicitly targets the isolated
worktree.

```bash
REPO=rozmiarD/signposter
ISSUE=286
PR=370
WORKTREE=../signposter-work/$ISSUE

signposter doctor --automation
git status --short --branch
signposter lifecycle status --repo $REPO --issue $ISSUE
signposter lifecycle next --repo $REPO --issue $ISSUE
signposter orchestrator next --repo $REPO --issue $ISSUE
signposter planner run --manifest configs/planner.example-seed-manifest.json --sync-github --dry-run
```

If a PR exists, inspect the PR and CI state before deciding what to do next.

```bash
gh pr view $PR --repo $REPO --json state,mergeable,reviewDecision,statusCheckRollup
gh pr checks $PR --repo $REPO
```

If a worktree exists, inspect it before recreating anything.

```bash
git -C $WORKTREE status --short --branch
git -C $WORKTREE log --oneline -5
ls -lah artifacts/prompts/
ls -lah artifacts/runs/
```

## Stuck-State Matrix

| Evidence | Likely state | Smallest safe action |
| --- | --- | --- |
| Issue is `state:ready`, dependencies are complete, no worktree | Ready task | Run `worktree plan`, then `worktree apply --apply` if ready |
| Issue is `state:active`, worktree exists, prompt exists, no usable worker summary | Incomplete worker run | Resume execution or take over from the existing worktree |
| Issue is `state:active`, worker summary exists, gate is blocked | Evidence problem or real failure | Inspect summary/raw output, update evidence only if truthful, then re-report and re-gate |
| Issue is `state:done`, no PR | PR not created yet | Run `pr plan`, commit intended files, push, then create PR |
| PR is open, CI is red | PR blocked by validation | Inspect CI logs, fix in the same branch, rerun validation, and wait for green CI |
| PR is open, CI is green, review gate blocked | Review evidence problem | Validate reviewer artifact or rerun reviewer/takeover review |
| PR is merged, issue remains open | Integration pending | Run `integration plan`, then `integration apply --apply` if ready |
| Issue is closed and `state:merged`, worktree remains | Cleanup pending | Run `cleanup plan`, then `cleanup apply --apply` if ready |
| Planner shows dependency complete but dependent task is not ready | Advancement pending | Run `planner advance --dry-run`, then apply only if the mutation is exact |

Do not continue to a later mutation after an earlier critical mutation failed.

## Automation Bug Ledger

When a loop bug should be tracked locally before opening a follow-up issue:

```bash
signposter artifact record-bug \
  --summary "Planner advance blocked after merge integration" \
  --current-issue $ISSUE \
  --apply

signposter artifact show-bugs
```

Entries stay local under `artifacts/automation/bug-ledger.json` by default.

## Worker Artifact Takeover

When an execution backend cannot produce usable output, preserve the diagnostic
artifacts before writing a clean canonical summary.

```bash
cp artifacts/runs/issue-$ISSUE-worker.raw.txt \
  artifacts/runs/issue-$ISSUE-worker.codex-runtime.raw.txt
cp artifacts/runs/issue-$ISSUE-worker.summary.md \
  artifacts/runs/issue-$ISSUE-worker.codex-runtime.summary.md
```

Then continue from the existing worktree when it is clean enough to inspect.
Complete only the scoped task, run validation, and write a bounded worker
summary at:

```text
artifacts/runs/issue-$ISSUE-worker.summary.md
```

The summary should state:

- repository and issue;
- agent/backend or manual takeover note;
- exit code and completion status;
- exact changed files;
- targeted and full validation evidence;
- safety notes;
- gate recommendation.

Raw runtime diagnostics stay local. GitHub gets the bounded summary through
`signposter report --apply`.

## Reviewer Artifact Takeover

If reviewer execution fails or produces malformed output, preserve reviewer
diagnostics before replacing the canonical reviewer summary.

```bash
cp artifacts/runs/pr-$PR-reviewer.raw.txt \
  artifacts/runs/pr-$PR-reviewer.codex-runtime.raw.txt
cp artifacts/runs/pr-$PR-reviewer.summary.md \
  artifacts/runs/pr-$PR-reviewer.codex-runtime.summary.md
```

A manual reviewer summary must keep the parser-compatible fields:

```text
Verdict: APPROVE
Confidence: 0.90
Risk: low
Scope match: yes
CI considered: yes
Merge recommendation: yes
Automerge eligible: no
```

Validate before submitting:

```bash
signposter review validate-artifact --pr $PR --summary-output
signposter review gate --repo $REPO --pr $PR
```

Submit only after the gate passes and the required external reviewer identity is
available.

## CI Run Selection

For PR branches, prefer PR checks:

```bash
gh pr checks $PR --repo $REPO
```

After merge, select the main-branch run by the merge commit instead of using an
interactive picker.

```bash
MERGE_COMMIT="$(gh pr view "$PR" --repo "$REPO" --json mergeCommit --jq '.mergeCommit.oid')"
RUN_ID="$(gh run list -R "$REPO" --branch main --commit "$MERGE_COMMIT" --limit 1 --json databaseId --jq '.[0].databaseId')"

if [ -z "$RUN_ID" ]; then
  sleep 15
  RUN_ID="$(gh run list -R "$REPO" --branch main --commit "$MERGE_COMMIT" --limit 1 --json databaseId --jq '.[0].databaseId')"
fi

gh run watch -R "$REPO" "$RUN_ID" --exit-status
```

If CI is red, inspect logs and fix the same PR branch. Do not merge on red CI.

## Stale Worktree or Branch

Before cleanup, confirm the lifecycle state and ownership.

```bash
signposter cleanup plan --repo $REPO --pr $PR
git worktree list
git branch --list "*issue-$ISSUE*"
```

Use cleanup apply only when the plan identifies the expected worktree and local
branch.

```bash
signposter cleanup apply --repo $REPO --pr $PR --apply
```

Do not delete unrelated branches or worktrees while recovering one issue.

## Handoff Snapshot

A handoff should let the next operator continue without guessing. Include:

- repository path, branch, short commit, and `git status --short --branch`;
- active roadmap manifest path;
- current issue, PR, branch, and worktree;
- lifecycle status and next required action;
- planner status and next ready task;
- last successful validation commands;
- latest PR CI or main CI result;
- artifacts that were preserved or replaced;
- explicit blocker, if any;
- exact next command to run.

If the state is inconsistent, say which sources disagree. Prefer lifecycle and
planner read-only surfaces first, then GitHub issue/PR evidence, then local
artifacts and worktree state.

## Safety Invariants

- Dry-run/read-only is the default.
- GitHub mutation requires explicit `--apply`.
- Execution backends require explicit execution permission.
- Raw execution artifacts stay local.
- GitHub comments receive bounded summaries only.
- `state:done` does not close a GitHub issue.
- Integration owns issue closure.
- Cleanup owns local worktree and local branch removal.
- Merge requires green CI and passing gates.
- Risk and scope overrides must be explicit.
- If a critical mutation fails, stop before later lifecycle mutations.
