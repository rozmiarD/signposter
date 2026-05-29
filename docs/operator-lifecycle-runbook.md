# Signposter Operator Lifecycle Runbook

## Purpose

Concise local operator runbook for taking a low-risk issue through the full Signposter workflow from `state:ready` to merged, integrated, and cleaned up. This runbook reflects the hardened workflow after SMOKE-002 and subsequent safety improvements.

## Safety Rules

- Dry-run / read-only by default on all commands.
- GitHub mutations require explicit `--apply`.
- OpenClaw execution requires explicit `--execute`.
- Do **not** use auto-close keywords (`Closes`, `Fixes`, `Resolves`) in commits or PR bodies.
- Issues close **only** via `integration apply`.
- Keep raw artifacts local (`artifacts/runs/`).
- Do not expose OpenClaw publicly.

## Safe PR–issue Linkage

Supported default linkage:
- Branch name pattern: `work/issue-N-slug`
- PR body line: `Related issue: #N`

Intentionally not used by default:
- `Closes #N`, `Fixes #N`, `Resolves #N`
- Formal GitHub "Development" links

Why this model:
- Merge must never close the issue.
- Only `signposter integration apply` owns issue closure.
- Lifecycle can still correctly detect the relationship via branch pattern + "Related issue" text (reported as `source: branch-pattern`, `formal GitHub development link: no/unknown`).

## Happy Path

### 1. Preflight

```bash
REPO=ExatronOmega/signposter
ISSUE=42

git status
git fetch origin
signposter labels check --repo $REPO
signposter labels ensure --repo $REPO
signposter sync plan --repo $REPO
```

### 2. Issue Expectations

- Must have required workflow labels (see `signposter labels check`).
- Expected starting state: `state:ready`.
- Low-risk worker issue assumptions: docs-only or narrow scoped change, clear acceptance criteria, no high-risk signals.

### 3. Worktree Setup

```bash
signposter run --repo $REPO --issue $ISSUE --dry-run
signposter worktree plan --repo $REPO --issue $ISSUE
signposter worktree apply --repo $REPO --issue $ISSUE --apply
```

### 4. Worker Execution

```bash
signposter run --repo $REPO --issue $ISSUE --claim
signposter run --repo $REPO --issue $ISSUE --write-prompt
signposter run --repo $REPO --issue $ISSUE --execute --worktree
```

### 5. Evidence Check

```bash
WORKTREE=../signposter-work/$ISSUE
git -C $WORKTREE status
git -C $WORKTREE diff

# Inspect worker output
ls -l artifacts/runs/issue-$ISSUE-*.summary.md
cat artifacts/runs/issue-$ISSUE-*.summary.md
```

### 6. Report / Gate / Complete

```bash
signposter report --repo $REPO --issue $ISSUE --apply
signposter gate --repo $REPO --issue $ISSUE

# NOTE: complete --apply now requires a passing gate decision
signposter complete --repo $REPO --issue $ISSUE --apply
```

### 7. Handoff / Commit / Push

```bash
signposter handoff plan --repo $REPO --issue $ISSUE

git -C $WORKTREE add .
git -C $WORKTREE commit -m "docs: <concise description>

- Scope followed 100%
- README.md only
- Dirty guard: clean
"
git -C $WORKTREE push -u origin HEAD
```

### 8. PR Creation

```bash
signposter pr plan --repo $REPO --issue $ISSUE

gh pr create \
  -R $REPO \
  --head $(git -C $WORKTREE rev-parse --abbrev-ref HEAD) \
  --base main \
  --title "docs: <title>" \
  --body "$(cat artifacts/prompts/issue-$ISSUE.md)"
# Do NOT include Closes/Fixes/Resolves keywords
```

### 9. Review

```bash
signposter review plan --repo $REPO --pr $PR
signposter review write-prompt --repo $REPO --pr $PR
signposter review execute --repo $REPO --pr $PR
signposter review gate --repo $REPO --pr $PR
signposter review submit --repo $REPO --pr $PR --apply
```

### 10. Merge

```bash
signposter merge plan --repo $REPO --pr $PR
signposter merge apply --repo $REPO --pr $PR --apply

# Wait for main CI to pass on the merge commit
```

### 11. Integration

```bash
signposter integration plan --repo $REPO --pr $PR
signposter integration apply --repo $REPO --pr $PR --apply
```

### 12. Cleanup

```bash
signposter cleanup plan --repo $REPO --pr $PR
signposter cleanup apply --repo $REPO --pr $PR --apply
```

### 13. Final Verification

```bash
signposter lifecycle status --repo $REPO --issue $ISSUE
signposter lifecycle status --repo $REPO --pr $PR

git worktree list
git branch --list "*issue-$ISSUE*"
```

## Recovery / Blocked States

### Missing Labels
```bash
signposter labels check --repo $REPO
signposter labels ensure --repo $REPO
signposter labels ensure --repo $REPO --apply
```

### Push Rejected / Fetch First
```bash
signposter sync plan --repo $REPO
signposter sync apply --repo $REPO --rebase --apply
# Then retry push
```

### Gate NEEDS-WORK
```bash
# Inspect evidence
cat artifacts/runs/issue-$ISSUE-*.summary.md
cat artifacts/runs/issue-$ISSUE-*.raw.txt

# Rerun worker or improve evidence, then re-report + gate
# Do NOT run complete until gate returns PASS
```

### Worktree Dirty
```bash
git -C $WORKTREE status
# Do not use --allow-dirty unless intentionally bypassing safety checks
```

### Already Integrated
- `integration apply` reports `completed` (safe no-op).

### Already Cleaned
- `cleanup apply` reports `completed` (safe no-op).

### Completed Issue Gate
- `gate --issue N` on CLOSED + `state:merged` reports `NOT-APPLICABLE` / `completed`.

## Variables

- `REPO=ExatronOmega/signposter`
- `ISSUE=N`
- `PR=P`
- `WORKTREE=../signposter-work/N`
- `BRANCH=work/issue-N-slug`

## Notes

- SMOKE-002 validated the full end-to-end flow (issue #6 / PR #7).
- The workflow is intentionally manual and observable at every step.
- All mutations are explicit and reversible until the final integration step.
