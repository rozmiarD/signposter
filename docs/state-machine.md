# Lifecycle State Model

**Status:** Implemented via GitHub labels and `lifecycle.py`.

Signposter does not use a separate persistence-backed state machine. Issue
lifecycle is expressed through workflow labels and deterministic control-plane
transitions.

## Issue workflow labels

Primary states are documented in `docs/workflow.md`:

- `state:ready` → `state:active` → `state:done` → `state:merged`
- `state:blocked` and `state:failed` stop progress until cleared

`state:done` does not close GitHub issues. Integration owns closure after merge.

## Planner vs scheduler scope

- **Planner** (manifest-scoped): dependency DAG and roadmap advancement
- **Scheduler** (repository-scoped): next eligible open issue from GitHub labels

See `docs/architecture.md` for module boundaries and safety invariants.
