# Signposter

[![CI: pytest](https://github.com/rozmiarD/signposter/actions/workflows/ci.yml/badge.svg)](https://github.com/rozmiarD/signposter/actions/workflows/ci.yml)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**Signposter is a local, safety-first workflow control plane for supervised GitHub issue lifecycles.**

It is built around a simple idea: autonomous coding help is only useful when it stays bounded, inspectable, and operator-controlled.

Signposter is separate from the Neutral Agent Pack.

## What Signposter does

Signposter moves GitHub issues through a deterministic development lifecycle under explicit operator gates. It combines:

- dependency-aware planner manifests and roadmap advancement;
- isolated worker worktrees and guarded issue claiming;
- worker and reviewer prompt generation with Codex CLI execution (OpenClaw legacy compatibility);
- bounded local artifacts and GitHub comment summaries;
- CI, review, merge, integration, and cleanup gates;
- lifecycle, scheduler, orchestrator, and control-plane status surfaces.

The control plane is deterministic: it reads GitHub and local repository state, plans safe next steps, writes local artifacts, enforces gates, and mutates GitHub only on guarded `--apply` paths. LLM-backed execution runs only when the operator explicitly enables `--execute`.

## What makes it different

Signposter is not a free-running autonomous agent.

- GitHub mutation requires `--apply`; backend execution requires `--execute`.
- Worker changes run from isolated task branches/worktrees; protected base branches are refused.
- Raw backend output stays local under `artifacts/runs/`; GitHub sees bounded summaries only.
- `state:done` does not close an issue — post-merge integration owns closure and `state:merged`.
- Merge requires green CI, review gate, approval, and explicit readiness checks.

Default behavior is read-only or dry-run. If a critical mutation fails, stop and inspect state before continuing.

## Architecture at a glance

High-level governed flow:

`manifest -> planner -> worktree -> worker run -> report/gate -> complete -> PR review -> merge -> integration -> cleanup -> planner advance`

Main control-plane layers:

- **Planner** — manifest-scoped dependency graph, seeding, advancement, reconcile hints
- **Scheduler** — repository-wide ready-task discovery from GitHub labels
- **Orchestrator / lifecycle** — cross-phase truth for a single issue or PR
- **Runner / backends** — Codex CLI (default) and legacy OpenClaw execution adapters
- **Review / merge / integration / cleanup** — PR gates, merge safety, issue closure, local teardown

See `docs/architecture.md` for module boundaries and `docs/workflow.md` for the full lifecycle.

## Safe quickstart

```bash
git clone https://github.com/rozmiarD/signposter.git
cd signposter
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
signposter doctor
signposter backend status
```

Read-only status for a target repo (replace with your `owner/repo`):

```bash
signposter lifecycle status --repo rozmiarD/signposter --issue <issue>
signposter planner run --manifest configs/planner.example-seed-manifest.json --sync-github --dry-run
```

Operator step-by-step flow: `docs/operator-lifecycle-runbook.md`. Recovery checklist: `docs/troubleshooting.md`.

Example planner inputs: `configs/planner.example-plan.json` and `configs/planner.example-seed-manifest.json`.

## Typical lifecycle

1. Planner marks a dependency-ready issue as `state:ready`.
2. `worktree apply --apply` creates an isolated branch/worktree.
3. `run --claim --write-prompt` claims the issue and writes a prompt artifact.
4. `run --execute --worktree` runs the selected backend when explicitly enabled.
5. `report --apply`, `gate`, and `complete --apply` move evidence through worker gates.
6. PR review, merge, integration, and cleanup complete the loop; planner advances downstream tasks.

The loop is resumable via planner, lifecycle, worktree, artifact, PR, CI, review, integration, and cleanup status commands.

## Limits and non-goals

Signposter is **not**:

- an unconstrained autonomous coding agent;
- a hosted CI/CD or project-management service;
- a guarantee that backend execution will succeed on every attempt;
- a replacement for operator judgment on risk/scope overrides.

Backend availability is not the same as model availability. When execution fails, preserve raw and summary artifacts locally, write a bounded manual summary, and continue through the normal gates.

## Repository guide

- `src/signposter/` — control-plane implementation
- `tests/` — regression and contract coverage
- `docs/` — public operator documentation
- `configs/` — example planner, routing, and label configs

Operator-internal audits and roadmaps (`docs/audits/`, `docs/roadmaps/`) are gitignored and kept local only — they are not published from this repository.

### Preserved branch: `test-last-know-working`

This branch is intentionally kept. It captures the last known working configuration that allowed the Codex mechanism to finish in-flight work without interruption — Signposter could run until the roadmap completed rather than stopping on token limits. That behavior was reported to OpenAI and subsequently fixed. The branch retains the H052 roadmap manifests and alignment used during that validation; it is a historical reference, not the active development line. Current work lives on `main`.

Stale worker branches (`work/h038-*`, `work/issue-*`) are abandoned task branches and were not merged.

## Development and validation

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/ -q
```

Inside an isolated worktree, reuse the main clone virtualenv:

```bash
MAIN_REPO=~/projects/signposter
PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/ruff" check .
PYTHONPATH="$PWD/src" "$MAIN_REPO/.venv/bin/python" -m pytest tests/ -q
```

## Documentation map

1. `docs/architecture.md` — layers and module boundaries
2. `docs/workflow.md` — lifecycle and safety boundaries
3. `docs/operator-lifecycle-runbook.md` — operator flow
4. `docs/artifacts-reference.md` — worker/reviewer artifact fields
5. `docs/troubleshooting.md` — recovery checklist
