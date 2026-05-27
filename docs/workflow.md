# Signposter Workflow Overview (Skeleton)

**Status:** Conceptual only.

## Typical Flow (future)

1. Work arrives (via GitHub event, manual trigger, or scheduler).
2. **Dispatcher** evaluates routing rules.
3. Work is assigned a **Role** (planner / reviewer / executor / gatekeeper).
4. Appropriate **Runner** is selected.
5. **Gates** may be evaluated before or after execution.
6. **State** is updated through defined transitions.
7. Results are reported back.

## Key Concepts

- **Phase**: High-level stage of work (queued, review, executing, blocked, completed, etc.).
- **Gate**: Explicit decision point that can block progression.
- **Risk**: Classification that influences routing and required gates.
- **Area**: Logical grouping (backend, infrastructure, documentation...).
- **Role**: Type of worker that should handle the work.

This document will be expanded once real workflow logic is designed.