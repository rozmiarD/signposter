# H050-005 Roadmap Grounding Audit

## Scope

This grounding audit reconciles H050-002, H050-003, and H050-004 into a single
source-of-truth note for the next H050 implementation wave. It does not change
code, rewrite docs, add tests, mutate GitHub, or close issues.

## Current Source of Truth

H050 is the active roadmap:

- manifest: `docs/roadmaps/h050-seed-manifest.json`;
- plan: `docs/roadmaps/h050-plan.json`;
- seeded issues: 80;
- GitHub issue range: #371 through #450;
- current lifecycle after early audits: H050-001 through H050-004 are merged,
  integrated, and cleaned up;
- current active task: H050-005 / issue #375.

The manifest schema uses top-level `issues`, not `tasks`. Each issue entry
stores:

- `key`;
- `title`;
- `labels`;
- `depends_on`;
- `github_issue`;
- `github_url`;
- dependency metadata and GitHub dependency references.

This matters for future automation: planner sync and audit code should read
`.issues[]` for the seeded manifest unless a later schema migration explicitly
changes the contract.

## Grounded Early Findings

### Repository Truth

H050-001 confirmed the repository is no longer a skeleton. Signposter already
has implemented surfaces for planner, scheduler/control-plane, lifecycle,
worktree, runner, review, merge, integration, cleanup, gates, reports,
artifacts, role/backend status, token usage, and subagent planning.

The next work should therefore focus on reliability and edge behavior, not a
large architectural rewrite.

### Documentation Truth

H050-002 confirmed stale documentation in:

- `README.md`;
- `docs/workflow.md`;
- `docs/state-machine.md`;
- `docs/labels.md`;
- parts of `docs/artifacts-reference.md`.

This work is already represented later in H050:

- H050-067 / #437: README current-state update;
- H050-068 / #438: workflow document current lifecycle update;
- H050-069 / #439: artifact docs schema update.

No duplicate documentation issue is needed now.

### Command Surface

H050-003 confirmed the implemented command surface is broad and operator-ready,
including planner, lifecycle, run, worktree, review, merge, integration,
cleanup, and control-plane status.

It also surfaced:

- old `bootstrap` and `OpenClaw` wording in help strings;
- stale issue-provided test path `tests/test_cli_help.py`;
- CWD-sensitive worktree/lifecycle path reporting.

Those findings map to existing H050 work:

- H050-045 / #415: worktree existing-path blocked output;
- H050-053 / #423: status stale worktree warning;
- H050-067 / #437 and H050-068 / #438: stale wording cleanup;
- H050-049 / #419: validation result artifact schema.

No side task is needed unless a later implementation proves one of these issues
blocks safe lifecycle execution.

### Test Coverage

H050-004 confirmed the suite is broad:

- 51 top-level test files;
- 869 counted test functions;
- broad coverage for planner, lifecycle, gate, review, merge, integration,
  cleanup, artifacts, runner, router, backend, worktree, comments, reports, and
  orchestrator surfaces.

The main gaps are edge and recovery behavior:

- unsafe backend raw/summary takeover;
- artifact placement for worktree execution;
- CWD-stable worktree/lifecycle reporting;
- delayed or ambiguous main CI;
- docs-only validation command discovery;
- provider/model fallback routing;
- side-task return-to-mainline behavior.

These are already represented later in H050:

- H050-019 / #389 and H050-020 / #390: worker summary and worktree recovery;
- H050-021 / #391: reviewer stale diagnostic coverage;
- H050-027 / #397 and H050-028 / #398: CI gate and docs-only artifact fields;
- H050-034 / #404: raw artifact locality tests;
- H050-041 / #411: integration stale main-CI guard;
- H050-047 / #417 and H050-048 / #418: CI run selection and diagnosis;
- H050-049 / #419: validation result artifact schema;
- H050-075 / #445: side-task return regression.

No duplicate test-roadmap issue is needed now.

## First 20 DAG Nodes

The first wave is correctly ordered:

- H050-001 / #371: repository truth and H049 carryover audit;
- H050-002 / #372: documentation truth gap audit, depends on H050-001;
- H050-003 / #373: command surface smoke inventory, depends on H050-001;
- H050-004 / #374: test coverage gap map, depends on H050-001;
- H050-005 / #375: this grounding audit, depends on H050-002 through H050-004;
- H050-006 / #376: planner seed manifest compatibility audit, depends on
  H050-005;
- H050-007 / #377: planner seed dry-run output determinism, depends on
  H050-006;
- H050-008 / #378: planner seed idempotence resume coverage, depends on
  H050-006;
- H050-009 / #379: planner seed partial-apply recovery guard, depends on
  H050-007 and H050-008;
- H050-010 / #380: seed label preflight clarity, depends on H050-009;
- H050-011 / #381: planner unseeded task status handling, depends on H050-010;
- H050-012 / #382: planner next root and waiting consistency, depends on
  H050-011;
- H050-013 / #383: planner advance duplicate-ready guard, depends on H050-012;
- H050-014 / #384: planner stale GitHub issue detection, depends on H050-013;
- H050-015 / #385: planner roadmap status artifact, depends on H050-014;
- H050-016 / #386: lifecycle terminal-state consistency audit, depends on
  H050-015;
- H050-017 / #387: lifecycle next blocked wording coverage, depends on
  H050-016;
- H050-018 / #388: active issue stale-age surface, depends on H050-017;
- H050-019 / #389: takeover plan for missing worker summary, depends on
  H050-018;
- H050-020 / #390: worker prompt and worktree recovery hints, depends on
  H050-019.

This ordering is coherent: it grounds the roadmap before planner seed
idempotence, then moves into lifecycle/recovery and artifact safety.

## Duplicate Work Check

No duplicate roadmap work was found in the first audit wave.

The repeated observations from H050-002 through H050-004 are not duplicate
tasks; they are shared evidence that later tasks should consume:

- docs truth issues are intentionally deferred to H050-067 through H050-069;
- path/worktree findings are intentionally deferred to worktree/status tasks;
- backend and takeover findings are intentionally deferred to artifact/recovery
  tasks;
- CI and validation findings are intentionally deferred to H050-041, H050-047,
  H050-048, and H050-049.

The correct next task after this one is H050-006 / issue #376: planner seed
manifest compatibility audit.

## Dynamic Side-Task Decision

No side-DAG node is needed at this point.

Reasons:

- all early findings map to existing H050 nodes;
- none of the findings blocks safe execution of the next planner-focused tasks;
- adding side tasks now would duplicate existing roadmap intent;
- the current safest path is to continue the seeded DAG in order.

## Safety

- No code was changed.
- No test was changed.
- No GitHub mutation was performed by this audit implementation.
- No issue was closed by this audit implementation.
- No merge was performed by this audit implementation.
- No execution backend output was trusted as implementation evidence.
- Runtime diagnostics from the attempted worker execution remain local.

## Validation

Targeted validation:

```bash
git diff --check -- docs/audits/h050-005-roadmap-grounding.md
```

Full validation before push:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
