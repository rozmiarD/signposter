# H050-003 Command Surface Smoke Inventory

## Scope

This audit records the current Signposter command surface for the H050 loop.
It is intentionally bounded: no CLI behavior change, no broad rewrite, no
GitHub mutation by the implementation, and no manual issue closure.

## Commands Inspected

The smoke inventory used the worktree checkout with:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/signposter <command> --help
```

The top-level command surface currently exposes:

- environment and repository inspection: `doctor`, `scan`, `labels`;
- task transitions: `claim`, `release`, `complete`, `fail`;
- worker execution: `run`, `worktree`, `handoff`, `pr`, `report`,
  `artifact`, `gate`;
- planning and roadmap control: `planner`, `issue-factory`, `sync`;
- PR review and post-merge control: `review`, `merge`, `integration`,
  `cleanup`;
- status and control-plane views: `lifecycle`, `orchestrator`, `scheduler`,
  `control-plane`;
- backend and routing views: `roles`, `backend`, `subagent`.

This confirms that the implemented surface is broader than the old bootstrap
wording in the top-level parser description.

## Required Lifecycle Surfaces

### `planner`

`signposter planner --help` lists:

- `draft`, `validate`, `seed`, `next`, `run`, `advance`, `impact`;
- `side-task-plan`, `step`, `mark`, `roadmap`, `status`.

The current H050 planner dashboard is read-only and deterministic. During this
task it reported:

- manifest: `docs/roadmaps/h050-seed-manifest.json`;
- total tasks: 80;
- ready tasks: 1;
- active tasks: 1;
- merged tasks: 2;
- next task: H050-004 / issue #374 while H050-003 is active;
- no GitHub mutation, no manifest mutation, no backend execution, and no LLM
  analysis.

### `lifecycle`

`signposter lifecycle --help` lists:

- `status`;
- `next`;
- `watch`.

`lifecycle status` for issue #373 correctly reports the issue as open and
`state:active`, with no PR detected yet. From the repository root it also sees
the expected worktree path as present.

### `run`

`signposter run --help` confirms the guarded worker surface:

- `--dry-run` for read-only planning;
- `--claim` for explicit GitHub claim mutation;
- `--write-prompt` for prompt artifact generation;
- `--execute` for explicit backend execution;
- `--backend {openclaw,codex-cli}`;
- `--worktree` for isolated worker execution.

For issue #373, dry-run selected:

- backend: `codex-cli`;
- selected role: `WORKER_CODE`;
- model: `openai/gpt-5.3-codex`;
- reasoning: `low`;
- role agent: `codex_worker_code`;
- worktree: `/home/probo/projects/signposter-work/373`.

The backend execution attempt did not produce implementation work because the
configured account rejected the selected model. Runtime diagnostics were kept
local and were not used as task completion evidence.

### `worktree`

`signposter worktree --help` lists:

- `plan`;
- `apply`.

`worktree plan` is guarded and reports existing branch/worktree collision once
the issue is active and the worktree already exists. That is useful blocked
output for operators: it does not imply another worktree will be created.

One finding is CWD-sensitive output: running `worktree plan` from inside the
issue worktree changes the relative `../signposter-work/373` interpretation and
can misleadingly report the worktree path as absent. Standard operator commands
should run from the main repository root until a later path-normalization task
hardens this.

### `review`

`signposter review --help` lists:

- `plan`;
- `write-prompt`;
- `execute`;
- `gate`;
- `validate-artifact`;
- `submit`.

The surface preserves the expected separation between local review artifact
generation/evaluation and explicit GitHub review submission.

### `merge`

`signposter merge --help` lists:

- `plan`;
- `apply`.

The surface preserves the expected guarded merge boundary: planning is read-only
and apply requires an explicit apply flag plus any required risk/scope override.

### `integration`

`signposter integration --help` lists:

- `plan`;
- `apply`;
- `noop-plan`;
- `noop-apply`.

The surface preserves issue-closure ownership in integration rather than worker
completion.

### `cleanup`

`signposter cleanup --help` lists:

- `plan`;
- `apply`.

The surface is local-only and states that no GitHub mutation is part of cleanup.

### `control-plane status`

`signposter control-plane status --help` confirms a compact read-only status
view with:

- `--repo`;
- optional `--manifest`;
- optional `--sync-github`;
- output limits for status and bug-ledger display.

This is the current operator-facing status surface. Bare `signposter` remains
help-only and points operators to `control-plane status`.

## Test Surface Finding

Issue #373 names `python -m pytest tests/test_cli_help.py -q`, but the current
repository does not contain `tests/test_cli_help.py`. The closest implemented
test is `tests/test_cli.py`, which verifies that bare `signposter` remains
help-only and includes the operator status hint.

This task does not create a compatibility shim test file because the scope is a
smoke inventory, not a CLI test layout rewrite. Validation therefore uses the
actual existing targeted CLI test.

## Findings

- The command surface is implemented and broad enough for the current H050
  lifecycle.
- Several help strings still contain old `bootstrap` and `OpenClaw` wording,
  even though Codex CLI is now the default execution backend. This matches the
  documentation truth gap already identified by H050-002 and should be fixed in
  the later docs/CLI wording tasks.
- `worktree plan` and `lifecycle status` are most reliable when run from the
  main repository root because some paths are currently relative to process CWD.
- The issue-provided targeted test path is stale; `tests/test_cli.py` is the
  actual current CLI help test.
- No code bug blocks completion of this docs-only smoke inventory.

## Safety

- No code was changed.
- No GitHub mutation was performed by this audit implementation.
- No issue was closed by this audit implementation.
- No merge was performed by this audit implementation.
- No execution backend output was trusted as implementation evidence.
- Runtime diagnostics from the attempted worker execution remain local.

## Validation

Targeted validation:

```bash
git diff --check -- docs/audits/h050-003-command-surface-smoke-inventory.md
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/test_cli.py -q
```

Full validation before push:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
