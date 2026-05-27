# signposter

**Status (BOOTSTRAP-002):** Structural skeleton complete.  
**Orchestration logic:** Not implemented.  
**Implementation phase:** Not started.

Signposter is a local GitHub / OpenClaw workflow dispatcher designed to stay completely separate from the Neutral Agent Pack.

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

---

**Important:** This project must remain clearly separated from the Neutral Agent Pack at all times.

*Bootstrap phase initialized: 2026-05-27*