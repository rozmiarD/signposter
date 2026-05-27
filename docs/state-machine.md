# State Machine (Skeleton)

**Status:** Structural definition only. No implementation.

## Core Entities

- **Task**: Smallest unit of work.
- **Job**: Collection of related tasks.
- **Run**: An execution attempt of a Job/Task.

## Planned States / Phases

Possible phases (subject to change):
- `queued`
- `planning`
- `review`
- `executing`
- `gate-evaluation`
- `blocked`
- `completed`
- `failed`
- `cancelled`

## Transitions

Transitions will be controlled by:
- Explicit gate evaluations
- Worker outcomes
- Scheduler timeouts
- Manual intervention

## Persistence

The `state` package will define the contracts for storing and retrieving current state and history.

This document will be significantly expanded once the state machine is designed.