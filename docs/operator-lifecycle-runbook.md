# Signposter Operator Lifecycle Runbook

## Purpose

This runbook describes the current Signposter operator flow for moving a scoped
GitHub issue through worktree execution, evidence, gate, PR, review, merge,
integration, cleanup, and planner advancement.

Signposter is the control plane. Prefer Signposter planning surfaces over direct
manual GitHub mutation. Use direct `gh` only when Signposter prints it as a
planned bridge or the current surface is planning-only.

## Related References

- `docs/troubleshooting.md`: recovery-first checklist for interrupted or
  inconsistent lifecycle states.
- `docs/artifacts-reference.md`: worker/reviewer artifact schema fields,
  takeover preservation, and GitHub redaction boundaries.
- `docs/architecture.md`: control-plane layers, module ownership, and safety
  boundaries behind the command flow.
- `docs/workflow.md`: compact lifecycle overview for status, evidence, and
  planner advancement.

## Safety Rules

- Dry-run/read-only is the default.
- GitHub mutation requires explicit `--apply`.
- Execution backends require explicit execution permission.
- Raw backend artifacts stay local under `artifacts/runs/`.
- GitHub comments get bounded summaries only.
- Do not use auto-close PR keywords such as `Closes`, `Fixes`, or `Resolves`.
- `state:done` does not close an issue.
- Issue closure belongs to `integration apply`.
- Merge requires green CI, review gate pass, and non-author approval when policy
  requires it.
- High-risk and medium/high-scope paths require explicit override flags.
- If a critical mutation fails, stop before later lifecycle mutations.

## Common Variables

```bash
REPO=rozmiarD/signposter
ISSUE=284
PR=368
WORKTREE=../signposter-work/$ISSUE
```

Run commands from the repository root unless the command explicitly targets the
isolated worktree.

## Planner First

Use the manifest-scoped planner to identify the next roadmap task.

```bash
signposter planner run \
  --manifest configs/planner.example-seed-manifest.json \
  --sync-github \
  --dry-run
```

If a completed dependency can promote the next task, dry-run first:

```bash
signposter planner advance \
  --manifest configs/planner.example-seed-manifest.json \
  --issue $ISSUE \
  --sync-github \
  --dry-run
```

Apply only when the plan lists the exact label mutation expected:

```bash
signposter planner advance \
  --manifest configs/planner.example-seed-manifest.json \
  --issue $ISSUE \
  --sync-github \
  --apply
```

## Issue Start

Confirm the issue is eligible and dependency-clear.

```bash
signposter run --repo $REPO --issue $ISSUE --dry-run
signposter worktree plan --repo $REPO --issue $ISSUE
```

Expected safe output:

- issue is `state:ready`;
- dependency status is clear;
- worktree plan status is `ready`;
- no branch or worktree was created by the plan.

## Worktree, Claim, Prompt

Create the isolated worktree only after the worktree plan is ready.
Protected base branches such as `main`, `master`, and `trunk` are valid bases
for planning and sync, but worker changes must happen in the issue worktree on
its task branch.

```bash
signposter worktree apply --repo $REPO --issue $ISSUE --apply
signposter run --repo $REPO --issue $ISSUE --claim --write-prompt
```

The claim step mutates labels from `state:ready` to `state:active` and adds the
planned gate label. Prompt generation writes a local artifact and does not run a
backend.

## Execution and Takeover

Run the assigned backend only when execution is intended.

```bash
signposter run --repo $REPO --issue $ISSUE --execute --worktree
```

If backend output is unavailable, malformed, or classified as a runtime problem:

1. preserve the original raw and summary artifacts with a diagnostic suffix;
2. continue from the existing worktree when safe;
3. complete the scoped implementation manually;
4. write a clean worker summary in the canonical artifact path;
5. keep raw diagnostic output local.

Manual worker summaries should include:

- repository and issue;
- agent/backend or takeover note;
- exit code and completion status;
- changed files;
- validation evidence;
- safety notes;
- gate recommendation.

## Local Validation

Run targeted validation first, then full validation before push.

```bash
MAIN_REPO=~/projects/signposter
PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/ruff" check <changed-files>
PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/python" -m pytest <targeted-tests> -q

PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/ruff" check .
PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/python" -m pytest tests/ -q
```

For documentation-only changes, `git diff --check -- <doc-file>` is the targeted
validation. Full ruff and pytest still run before push.

## Report, Gate, Complete

Post only a bounded runner report to GitHub.

```bash
signposter report \
  --repo $REPO \
  --issue $ISSUE \
  --summary artifacts/runs/issue-$ISSUE-worker.summary.md \
  --apply
```

Evaluate the gate before completing the issue.

```bash
signposter gate --repo $REPO --issue $ISSUE --dry-run
signposter complete --repo $REPO --issue $ISSUE --apply
```

`complete --apply` changes `state:active` to `state:done`. It does not close the
issue.

## Commit, Push, PR

Plan PR metadata before creating a PR.

```bash
signposter pr plan --repo $REPO --issue $ISSUE
```

Commit only intended files from the issue worktree.

```bash
git -C $WORKTREE status --short
git -C $WORKTREE add <changed-files>
git -C $WORKTREE commit -m "<type>: <concise description>"
git -C $WORKTREE push -u origin HEAD
```

Create the PR using the title and body shape from `signposter pr plan`. The PR
body should include `Related issue: #N` and must not include auto-close keywords.

## CI

After pushing a branch or merging a PR, select the GitHub Actions run by branch
or commit and watch it to completion.

```bash
gh pr checks $PR --repo $REPO
```

For main after merge:

```bash
MERGE_COMMIT="$(gh pr view "$PR" --repo "$REPO" --json mergeCommit --jq '.mergeCommit.oid')"
RUN_ID="$(gh run list -R "$REPO" --branch main --commit "$MERGE_COMMIT" --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch -R "$REPO" "$RUN_ID" --exit-status
```

Do not merge on red CI.

## Review

Generate and execute the reviewer prompt through Signposter where available.

```bash
signposter review write-prompt --repo $REPO --pr $PR
signposter review execute --repo $REPO --pr $PR
```

For high-risk prompt generation:

```bash
signposter review write-prompt --repo $REPO --pr $PR --allow-high-risk
```

If reviewer execution is unavailable, preserve runtime diagnostics and write a
parser-compatible manual reviewer artifact. Validate before submit:

```bash
signposter review validate-artifact --pr $PR --summary-output
signposter review gate --repo $REPO --pr $PR
```

Use explicit risk overrides when required:

```bash
signposter review gate --repo $REPO --pr $PR --allow-medium-risk
signposter review gate --repo $REPO --pr $PR --allow-high-risk
```

Submit approval only after the review gate passes:

```bash
signposter review submit --repo $REPO --pr $PR --apply
```

## Merge

Plan merge first.

```bash
signposter merge plan --repo $REPO --pr $PR
```

If the plan is blocked only by an explicit operator-approved scope or risk
override, repeat the plan with the matching flag:

```bash
signposter merge plan --repo $REPO --pr $PR --allow-medium-scope
signposter merge plan --repo $REPO --pr $PR --allow-medium-risk
signposter merge plan --repo $REPO --pr $PR --allow-high-risk
```

Apply only when merge plan status is `ready`.

```bash
signposter merge apply --repo $REPO --pr $PR --apply
```

Pass the same explicit override flags to `merge apply` when the ready plan used
them.

## Integration

Integration owns issue closure after merge and main CI pass.

```bash
signposter integration plan --repo $REPO --pr $PR
signposter integration apply --repo $REPO --pr $PR --apply
```

Expected result:

- remove `state:done`;
- add `state:merged`;
- close the issue as completed.

Validated no-op tasks use the no-PR integration surface:

```bash
signposter integration noop-plan --repo $REPO --issue $ISSUE
signposter integration noop-apply --repo $REPO --issue $ISSUE --apply
```

## Cleanup

Cleanup owns local worktree and local branch removal only.

```bash
signposter cleanup plan --repo $REPO --pr $PR
signposter cleanup apply --repo $REPO --pr $PR --apply
```

Cleanup does not mutate the issue or PR.

## Final Verification

```bash
signposter lifecycle status --repo $REPO --issue $ISSUE
git status --short --branch
git worktree list
git branch --list "*issue-$ISSUE*"
```

Complete lifecycle status should show:

- issue closed;
- workflow state `state:merged`;
- PR merged;
- integration yes;
- cleanup complete yes.

## Recovery Checklist

When a task appears stuck, inspect before taking over:

```bash
signposter lifecycle status --repo $REPO --issue $ISSUE
signposter lifecycle next --repo $REPO --issue $ISSUE
signposter orchestrator next --repo $REPO --issue $ISSUE
git -C $WORKTREE status --short --branch
ls -l artifacts/runs/
gh pr view $PR --repo $REPO --json state,mergeable,reviewDecision,statusCheckRollup
```

Common recovery paths:

- active issue with worktree and prompt but no usable summary: resume existing
  worktree, preserve artifacts, then write a clean manual worker summary if
  needed;
- active issue with worktree but no prompt: regenerate prompt before execution;
- active issue with prompt but no worktree: repair or recreate worktree before
  continuing;
- PR with red CI: inspect CI logs, fix in the same branch, rerun validation, and
  do not merge until CI is green;
- merged PR with open issue: run integration plan/apply;
- merged/integrated issue with local worktree or branch: run cleanup plan/apply.

Do not continue later mutations after an earlier critical mutation failed.
