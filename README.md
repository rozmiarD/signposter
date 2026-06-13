# signposter

Signposter is a local, safety-first workflow control plane for moving GitHub
issues through a bounded development lifecycle.

It is not a free-running autonomous agent. The control plane stays
deterministic: it reads GitHub and local repository state, plans safe next
steps, writes local artifacts, enforces gates, and performs mutations only when
the operator uses the guarded apply/execute paths.

Signposter is separate from the Neutral Agent Pack.

## Current Status

Signposter is beyond the original bootstrap skeleton. The repository now has
working surfaces for:

- GitHub issue scanning and label-based dispatch;
- dependency-aware planner manifests and roadmap advancement;
- guarded issue claiming and completion;
- isolated worker worktree planning and creation;
- worker prompt generation and execution planning;
- Codex CLI execution backend support with legacy OpenClaw compatibility;
- bounded local worker and reviewer artifacts;
- CI, review, human, and no-op gate evaluation;
- PR review prompt generation, review artifact parsing, and review submission;
- merge planning/apply with risk and scope overrides;
- post-merge integration that owns issue closure;
- local cleanup for merged worktrees and branches;
- lifecycle, scheduler, orchestrator, control-plane, backend, and bug-ledger
  status surfaces.

The system is still supervised. Operators should expect to inspect plans,
approve guarded mutations, watch CI, and recover backend/runtime failures with
bounded manual artifacts when needed.

## Safety Model

Default behavior is read-only or dry-run.

Core invariants:

- GitHub mutation requires an explicit `--apply`.
- Backend execution requires an explicit `--execute`.
- Worker mutation runs from an isolated task branch/worktree; protected base
  branches such as `main`, `master`, and `trunk` are refused for direct worker
  execution.
- Raw backend output stays local under `artifacts/runs/`.
- GitHub comments use bounded summaries.
- `state:done` does not close a GitHub issue.
- Post-merge integration owns issue closure and `state:merged`.
- Merge requires green CI, review gate, required approval, and merge readiness.
- Risk/scope overrides are explicit operator choices.
- Cleanup removes local worktrees and branches only through guarded cleanup
  apply paths.

If a critical mutation fails, stop and inspect state before continuing.

## Typical Lifecycle

A normal Signposter-managed issue follows this shape:

1. Planner marks a dependency-ready issue as `state:ready`.
2. `worktree plan` previews the isolated branch/worktree.
3. `worktree apply --apply` creates the local worktree.
4. `run --claim --write-prompt` claims the issue and writes a prompt artifact.
5. `run --execute --worktree` attempts the selected execution backend.
6. Worker output is summarized into a bounded local artifact.
7. `report --apply` posts a bounded GitHub issue comment.
8. `gate --dry-run` validates evidence.
9. `complete --apply` moves the issue to `state:done`.
10. A PR is opened for the worker branch.
11. PR CI is watched to completion.
12. Reviewer prompt/artifact/gate/approval runs.
13. `merge plan` verifies merge readiness.
14. `merge apply --apply` merges without closing the issue.
15. `integration apply --apply` moves the issue to `state:merged` and closes it.
16. `cleanup apply --apply` removes local worker state.
17. Planner advances downstream dependencies.

The loop is resumable by inspecting planner, lifecycle, worktree, artifact, PR,
CI, review, integration, and cleanup state.

## Common Commands

Read-only status and planning:

```bash
signposter --help
signposter doctor
signposter backend status
signposter roles status
signposter planner run --manifest docs/roadmaps/h050-seed-manifest.json --sync-github --dry-run
signposter lifecycle status --repo ExatronOmega/signposter --issue <issue>
signposter control-plane status --repo ExatronOmega/signposter --manifest docs/roadmaps/h050-seed-manifest.json --sync-github
```

Issue execution:

```bash
signposter run --repo ExatronOmega/signposter --issue <issue> --dry-run
signposter worktree plan --repo ExatronOmega/signposter --issue <issue>
signposter worktree apply --repo ExatronOmega/signposter --issue <issue> --apply
signposter run --repo ExatronOmega/signposter --issue <issue> --claim --write-prompt
signposter run --repo ExatronOmega/signposter --issue <issue> --execute --worktree
```

Evidence and completion:

```bash
signposter artifact write-worker-summary --repo ExatronOmega/signposter --issue <issue> --agent human/operator --apply
signposter report --repo ExatronOmega/signposter --issue <issue> --summary artifacts/runs/issue-<issue>-worker.summary.md --apply
signposter gate --repo ExatronOmega/signposter --issue <issue> --dry-run
signposter complete --repo ExatronOmega/signposter --issue <issue> --apply
```

Review, merge, integration, and cleanup:

```bash
signposter review write-prompt --repo ExatronOmega/signposter --pr <pr>
signposter review execute --repo ExatronOmega/signposter --pr <pr>
signposter review gate --repo ExatronOmega/signposter --pr <pr>
signposter review submit --repo ExatronOmega/signposter --pr <pr> --apply
signposter merge plan --repo ExatronOmega/signposter --pr <pr>
signposter merge apply --repo ExatronOmega/signposter --pr <pr> --apply
signposter integration plan --repo ExatronOmega/signposter --pr <pr>
signposter integration apply --repo ExatronOmega/signposter --pr <pr> --apply
signposter cleanup plan --repo ExatronOmega/signposter --pr <pr>
signposter cleanup apply --repo ExatronOmega/signposter --pr <pr> --apply
```

Use explicit risk or scope override flags only when the corresponding dry-run
plan explains the blocker and the operator accepts it.

## Execution Backends

Codex CLI is the current default execution backend. OpenClaw surfaces remain as
legacy compatibility where the code still supports them.

Backend availability is not the same as selected-model availability. If an
execution attempt cannot produce usable output, preserve the raw and summary
artifacts locally, write a bounded manual worker or reviewer summary, and
continue through the normal Signposter gates.

## Project Layout

Important modules live under `src/signposter/`:

- `planner.py`, `issue_manifest.py`, `issue_factory.py`, `dependencies.py`:
  roadmap, manifest, issue seeding, dependency, and advancement logic;
- `runner.py`, `execution_backend.py`, `codex_cli_backend.py`,
  `codex_subagent.py`: worker prompt, execution backend, and subagent contracts;
- `gate.py`, `artifact.py`, `artifact_safety.py`: worker evidence and gate
  validation;
- `review.py`, `merge.py`, `integration.py`, `cleanup.py`: PR review, merge,
  issue closure, and local cleanup safety;
- `lifecycle.py`, `orchestrator.py`, `control_status.py`, `scheduler/`:
  status, resumability, orchestration, and operator-facing control-plane
  views;
- `role_policy.py`, `role_routing.py`: deterministic role/model/reasoning
  selection metadata.

Tests live under `tests/` and should be updated with each behavior change.

## Development Setup

```bash
cd ~/projects/signposter
source .venv/bin/activate
pip install -e ".[dev]"
```

## Validation

Use targeted validation for the changed surface, then run the full suite before
pushing:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/ -q
```

Inside an isolated worktree, prefer:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
