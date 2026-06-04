# H050-001 Repository Truth and H049 Carryover Audit

## Scope

This audit records the repository state after H049 completion and H050 bootstrap.
It is intentionally bounded: no code change, no refactor, no new workflow
surface, and no manual issue closure.

## Current Repository Truth

- Source modules: 52 Python files under `src/signposter`.
- Tests: 51 top-level `tests/test_*.py` files.
- Docs: 19 files under `docs`.
- GitHub workflow: one CI workflow at `.github/workflows/ci.yml`.
- CI workflow runs on pull requests and pushes to `main`.
- CI installs `.[dev]`, runs `ruff check .`, then runs `pytest -v --tb=short`.

The implemented command surface is broader than the old bootstrap docs describe.
`signposter --help` now exposes:

- environment and repository inspection: `doctor`, `scan`, `labels`;
- scheduling and control-plane views: `scheduler`, `control-plane`,
  `orchestrator`, `lifecycle`;
- planner and roadmap flow: `planner`, `issue-factory`, `sync`;
- lifecycle execution flow: `run`, `worktree`, `handoff`, `pr`, `report`,
  `artifact`, `gate`;
- post-PR flow: `review`, `merge`, `integration`, `cleanup`;
- backend and routing inspection: `roles`, `backend`, `subagent`.

## H049 Carryover State

H049 is complete from the planner point of view:

- manifest: `/tmp/signposter-h049-manifest.json`;
- total tasks: 82;
- merged tasks: 82;
- completed tasks: 82;
- ready, active, waiting, done, blocked: 0;
- next task: none;
- advance candidates: none.

H049-080 / issue #287 is closed with `state:merged`, PR #451 merged, integration
complete, and local cleanup complete.

## H050 Entry State

H050 is seeded and active:

- manifest: `docs/roadmaps/h050-seed-manifest.json`;
- total tasks: 80;
- active tasks: 1;
- waiting tasks: 79;
- ready tasks: 0 after H050-001 was claimed;
- active task: H050-001 / issue #371;
- no advance candidate exists until H050-001 is merged.

This is the expected state after re-entering the next roadmap loop.

## Module Boundary Observations

The repository has clear implemented surfaces for planner, lifecycle,
orchestrator, runner/backend, role routing, gates, review, merge, integration,
cleanup, comments, reports, artifacts, token usage, and worktrees.

The strongest carryover risk is not missing broad architecture. The stronger
next risk is edge behavior: idempotence, stale state recovery, bounded evidence,
CI selection, comment safety, and operator wording around blocked vs ready
states.

## Documentation Truth Observations

Current docs are mixed:

- `docs/architecture.md`, `docs/operator-lifecycle-runbook.md`, and
  `docs/troubleshooting.md` describe the current lifecycle more accurately.
- `README.md` and `docs/workflow.md` still contain bootstrap-era claims that
  understate current implementation.
- `docs/artifacts-reference.md` is useful but should be tightened around current
  worker/reviewer schema fields and takeover evidence.

This matches the early H050 docs-truth tasks already seeded in the roadmap.

## Test Coverage Observations

The test suite has broad coverage across planner, lifecycle, worktree, runner,
review, merge, integration, cleanup, gates, comments, artifacts, router, backend,
orchestrator, and smoke paths.

The remaining coverage need is edge-case depth, not just more happy paths:

- partial planner seed/apply recovery;
- duplicate or stale manifest mapping;
- active issue stale-state detection;
- malformed worker/reviewer artifacts;
- red CI diagnosis and stale CI selection;
- idempotent integration and cleanup terminal states;
- phrase-sensitive gate evidence regressions.

## H050 Direction Check

The H050 roadmap matches repository truth:

- H050 starts with audits and planner seed/idempotence hardening.
- It then moves through lifecycle recovery, evidence/gates, GitHub workflow,
  validation/CI, operator status, role/runtime/token visibility, docs truth, and
  final smoke/audit tasks.
- It avoids a broad rewrite and keeps tasks small enough for Signposter lifecycle
  execution.

## Safety Notes

- No code was changed by this audit.
- No GitHub mutation was performed by this audit implementation.
- No issue was closed by this audit implementation.
- No merge was performed by this audit implementation.
- No execution backend output was trusted as implementation evidence.
- Runtime diagnostics from the attempted worker execution remain local.

## Validation

Targeted validation:

```bash
git diff --check -- docs/audits/h050-001-repository-truth-audit.md
```

Full validation before push:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
