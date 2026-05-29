# signposter

**Status (BOOTSTRAP-002):** Structural skeleton complete.  
**Orchestration logic:** Not implemented.  
**Implementation phase:** Not started.

Signposter is a local GitHub / OpenClaw workflow dispatcher designed to stay completely separate from the Neutral Agent Pack.

## MVP Status

- GitHub issue workflow is supported.
- Signposter can claim, generate prompts, run OpenClaw reviewer, capture artifacts, report results, and complete review tasks.
- Worker agent is now configured for low-risk build/docs tasks.
- This is still an experimental local orchestrator.

## Isolated Worker Execution

Signposter now supports isolated worker execution via guarded worktrees:

- worktree planning is available
- guarded worktree creation is available
- worker execution can explicitly run from an existing worktree

## Current State

Only the following exists:

- Package directory structure under `src/signposter/`
- Configuration contracts and example files
- High-level architecture documentation
- Basic test skeleton

No actual dispatch, scheduling, GitHub integration, or state machine logic has been written.

## Available Commands (Bootstrap Phase)

### `signposter doctor`

Run a read-only preflight check of the local environment:

```bash
signposter doctor
```

The doctor command verifies:
- Python version compatibility
- Git repository and working tree status
- Presence of `gh` (GitHub CLI) and authentication state
- Presence of `openclaw`
- Availability of `pytest` and `ruff`
- Existence of example configuration files
- Existence of core documentation

It is safe to run at any time and makes no changes.

### `signposter scan`

Read-only scanner for GitHub repositories (bootstrap phase):

```bash
signposter scan --repo ExatronOmega/signposter
```

The scanner reports:
- Count of open issues and pull requests
- Recent workflow runs
- Items matching neutral workflow labels (`state:ready`, `phase:*`, `gate:*`, etc.)

It uses the GitHub CLI in read-only mode and performs **no** mutations.

### `signposter dispatch --dry-run`

Classifies candidate items and produces a proposed routing plan (bootstrap phase):

```bash
signposter dispatch --repo ExatronOmega/signposter --dry-run
```

The dry-run command reuses the scanner and applies simple routing rules based on labels such as `phase:*`, `role:*`, `risk:*`, and `state:*`.

**Important:** In the current bootstrap phase, `--dry-run` is mandatory. No actions are ever taken on GitHub.

### `signposter claim --dry-run`

Determines which `state:ready` items would be claimed for execution and what label transitions would occur (bootstrap phase):

```bash
signposter claim --repo ExatronOmega/signposter --dry-run
```

The claim planner only considers items currently labeled `state:ready`. It proposes:
- Moving the item to `state:active`
- Adding the appropriate `gate:*` label based on dispatch classification
- A lease owner (in dry-run: `local-dry-run-worker`)

This is the last purely planning step before any real claiming logic would be implemented.

### `signposter release / complete / fail --dry-run`

Manage already-claimed (`state:active`) items (bootstrap phase):

```bash
signposter release  --repo <owner/repo> --issue N --dry-run
signposter complete --repo <owner/repo> --issue N --dry-run
signposter fail     --repo <owner/repo> --issue N --dry-run
```

- `release`: Returns an active item to `state:ready` (removes active + gate labels)
- `complete`: Marks an active item as `state:done`
- `fail`: Marks an active item as `state:failed`

These commands are currently **dry-run only** and validate that the target item is in `state:active`.

### `signposter run --dry-run`

Plans how a selected claimable item would be executed via OpenClaw (bootstrap phase):

```bash
signposter run --repo <owner/repo> --dry-run
```

The runner planner reuses the claim planner and proposes:
- OpenClaw profile based on role + phase (e.g. `reviewer` for `role:reviewer + phase:review`)
- Working directory and prompt artifact path
- Command shape (not executed)

## Project Structure

```
signposter/
├── src/signposter/
│   ├── domain/           # Core domain models (Task, Job, State, Gate, Risk, Phase, Role, Area)
│   ├── github/           # GitHub integration surface (stub)
│   ├── scheduler/        # Scheduling and timing layer (stub)
│   ├── dispatcher/       # Central routing and dispatch (stub)
│   ├── runners/          # Execution workers (stub)
│   ├── gates/            # Gate and approval logic (stub)
│   ├── state/            # State machine and persistence contracts (stub)
│   └── config/           # Configuration loading layer (stub)
│
├── configs/
│   ├── repos.example.yaml
│   ├── routing.example.yaml
│   ├── labels.example.yaml
│   ├── agents.example.yaml
│   └── scheduler.example.yaml
│
├── docs/
│   ├── architecture.md
│   ├── workflow.md
│   ├── labels.md
│   └── state-machine.md
│
├── tests/
├── scripts/
├── pyproject.toml
└── README.md
```

## Configuration Contracts

Example configuration files in `configs/` define the structural contracts for:
- Repositories under management
- Routing rules
- Label semantics
- Worker roles (planner, reviewer, executor, gatekeeper, ...)
- Scheduler behavior

These files use only comments and safe dummy values.

## Documentation

See `docs/` for current structural thinking:
- `architecture.md`
- `workflow.md`
- `labels.md`
- `state-machine.md`

## Development Setup

```bash
cd ~/projects/signposter

# Activate venv (created during skeleton setup)
source .venv/bin/activate

# Re-install in editable mode after changes
pip install -e ".[dev]"
```

## Validation

```bash
ruff check .
pytest -v
```

Signposter lifecycle smoke-test completed successfully.

---

**Important:** This project must remain clearly separated from the Neutral Agent Pack at all times.

*Bootstrap phase initialized: 2026-05-27*