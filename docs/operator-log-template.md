# Signposter Operator Log Template

Use this compact log shape while running a Signposter-controlled workflow. Keep
entries short enough to scan, but specific enough to reconstruct the lifecycle
state without rereading raw artifacts.

## Entry Template

```text
Issue:
  H050-000 / #000 — short title

Branch/worktree:
  branch: work/issue-000-short-title
  worktree: ../signposter-work/000

Route:
  role: worker | reviewer | deterministic
  model tier: light | code | core | none

Lifecycle:
  state: ready | active | done | merged | blocked
  gate: ci | review | human | none

Command:
  <last command run>

Result:
  pass | blocked | failed | applied
  evidence: <artifact, PR, CI run, or concise reason>

Next action:
  <one safe next step>
```

## Rules

- Record one entry after each major Signposter step: plan, apply, execute,
  report, gate, complete, PR, review, merge, integration, cleanup.
- Write the last command exactly enough that another operator can rerun or audit
  it.
- For blocked states, write the stop reason and the smallest safe recovery
  action.
- For model/backend failures, name the failing backend and preserve the local
  artifact path; do not paste raw logs into GitHub comments.
- For GitHub mutations, state whether the command used `--apply`.
- For execution backends, state whether the command used `--execute`.
- Do not record secrets, tokens, or unbounded raw output.

## Example

```text
Issue:
  H050-055 / #425 — Operator log template standardization

Branch/worktree:
  branch: work/issue-425-h050-055-operator-log-template-standardization
  worktree: ../signposter-work/425

Route:
  role: worker
  model tier: light

Lifecycle:
  state: active
  gate: ci

Command:
  signposter gate --repo ExatronOmega/signposter --issue 425 --dry-run

Result:
  pass
  evidence: artifacts/runs/issue-425-worker.summary.md

Next action:
  signposter complete --repo ExatronOmega/signposter --issue 425 --apply
```
