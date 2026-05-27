# Signposter Architecture (Skeleton)

**Status:** Structural skeleton only. No orchestration logic implemented.

## High-Level Components

- **domain**: Core business concepts (Task, Job, Run, State, Gate, Risk, Phase, Role, Area).
- **config**: Configuration loading and validation contracts.
- **state**: State machine definitions and persistence abstractions.
- **scheduler**: Timing, planning, and phase progression.
- **dispatcher**: Central routing and work distribution.
- **runners**: Execution workers (planner, reviewer, executor, gatekeeper, etc.).
- **gates**: Decision points that control progression between phases.
- **github**: GitHub-specific surface (events, labels, repositories).

## Design Principles (to be respected in future work)

- Clear separation between domain and infrastructure.
- Configuration-driven behavior where possible.
- Explicit state transitions.
- Gate-based risk control instead of implicit trust.
- Neutral terminology (no "AI" in public contracts).

## Current State

Only package structure and configuration contracts exist.
Real implementation of any component above is out of scope for the bootstrap phase.