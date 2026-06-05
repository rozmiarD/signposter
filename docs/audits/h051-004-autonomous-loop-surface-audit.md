# H051-004 Autonomous Loop Surface Audit

Status: pass
Date: 2026-06-05
Issue: H051-004 / #534

## Scope

This audit records the current autonomous-loop, orchestrator, scheduler, and
control-plane status surfaces before H051 starts adding next-step and recovery
behavior. It is documentation-only and does not change lifecycle execution,
planner selection, scheduler selection, GitHub mutation behavior, or artifact
layout.

## Evidence Sources

- `src/signposter/orchestrator.py`
- `src/signposter/control_status.py`
- `src/signposter/scheduler.py`
- `src/signposter/lifecycle.py`
- `tests/test_orchestrator.py`
- `tests/test_control_status.py`
- live read-only smoke commands during H051-004

## Current Loop Surfaces

Signposter already has multiple deterministic loop-facing commands:

- `signposter orchestrator next`
- `signposter orchestrator step`
- `signposter orchestrator loop`
- `signposter orchestrator tail`
- `signposter orchestrator tail-loop`
- `signposter orchestrator run-next`
- `signposter orchestrator run-next-loop`
- `signposter orchestrator autonomy-smoke`
- `signposter control-plane status`

The core orchestrator module is intentionally conservative. Planning is
read-only by default, execution actions require explicit `--execute`, mutation
actions require explicit `--apply`, and bounded loop structures record stop
reasons instead of continuing blindly.

## Live Smoke Evidence

Command:

`signposter control-plane status --repo ExatronOmega/signposter --manifest docs/roadmaps/h051-seed-manifest.json --sync-github --limit 20`

Observed result:

- status: `ready`;
- active task: `#534`;
- planner counts: `total=80 ready=0 active=1 waiting=76 merged=3 blocked=0`;
- scheduler status: `completed`;
- orchestrator status: `not evaluated`;
- refresh mode: `single-snapshot`;
- auto-refresh: `off`;
- local warning: stale local branch
  `work/issue-186-h048n-add-planner-dag-auto-advance-status-for-code`.

Command:

`signposter orchestrator run-next --repo ExatronOmega/signposter --manifest docs/roadmaps/h051-seed-manifest.json --sync-github --summary`

Observed result:

- selected: `#534`;
- source: `planner-manifest`;
- action: `write-prompt`;
- status: `actionable`;
- stop: `none`;
- recovery: `none`.

The smoke commands did not mutate GitHub, did not mutate the manifest, and did
not run an execution backend.

## Findings

1. The control-plane snapshot is a useful operator surface for the current DAG
   state. It shows the active issue, planner counts, refresh policy, and local
   stale-worker warnings in one bounded read-only view.
2. Auto-refresh is deliberately off. This is consistent with H051's safety goal:
   repeated GitHub reads must stay explicit until stall evidence and timeout
   behavior are stronger.
3. `orchestrator run-next --summary` can select from the planner manifest and
   produce a compact next action. That is the right shape for future loop
   automation.
4. The run-next smoke was invoked from the worker worktree and returned
   `action: write-prompt` even though the issue had already been claimed and the
   prompt/runtime artifacts existed in the main repository artifact tree. This
   suggests the loop surface still depends on current working directory and
   artifact locality. Future autonomous loop code should normalize the repo root
   and artifact directories before making lifecycle decisions.
5. The local stale branch warning for issue #186 is surfaced but not acted on by
   status. That is correct for read-only status, but a later cleanup/recovery
   task should decide whether stale local state needs an explicit Signposter
   cleanup plan.

## Existing Strengths

- `plan_orchestrator_next()` separates execution actions from mutation actions.
- `run_orchestrator_step()` executes at most one allow-listed lifecycle command.
- loop result objects record cycles requested, cycles run, stop reason, and stop
  category.
- control-plane status composes planner, scheduler, orchestrator, local worker
  warnings, and bug ledger data.
- tests cover read-only status notes, execution blocking without `--execute`,
  mutation classification, stale active issue diagnostics, stale local worker
  state warnings, and disagreement between target-selecting sources.

## Evidence Gaps

1. There is not yet a single "resume from current repo truth" command that
   normalizes root paths, active issue state, artifact locations, PR state, and
   cleanup state before choosing an action.
2. Control-plane status does not evaluate orchestrator state unless explicitly
   supplied by the command handler.
3. Planner/scheduler/orchestrator agreement can be "not evaluated" even when an
   active task is visible. This is safe, but it means the operator still needs a
   follow-up command to get the lifecycle action.
4. CWD-sensitive artifact checks can make a loop action look earlier than the
   true lifecycle state if commands are run from a worktree instead of the main
   repository root.
5. Current surfaces are still supervised. They are close to a loop contract, but
   not yet sufficient for unattended operation.

## H051 Follow-Up Mapping

No side task is required. The H051 DAG already contains the next implementation
steps:

- H051-005: safe next-step selector implementation;
- H051-011/H051-012: worker and reviewer artifact health checks;
- H051-015: active issue age/stale detector;
- H051-016: takeover plan command;
- H051-017: manual takeover artifact preservation helper;
- H051-018: loop resume decision table;
- H051-031/H051-032: control dashboard and read-only auto-refresh refinement;
- H051-033: compact operator log event format.

The CWD/artifact-locality finding should be considered when implementing
H051-005 and H051-018.

## Validation

- `git diff --check -- docs/audits/h051-004-autonomous-loop-surface-audit.md`
- `python -m pytest tests/test_orchestrator.py tests/test_control_status.py -q`
- `ruff check .`
- `python -m pytest tests/ -q`

## Safety

- No GitHub mutation was performed by this audit.
- No lifecycle command behavior was changed.
- No planner, scheduler, orchestrator, or status behavior was changed.
- No execution backend was started by the audit implementation.
- No issue was closed by this audit.
- No local cleanup was performed by this audit.

## Conclusion

Signposter already has the right building blocks for supervised loop execution:
planner selection, scheduler selection, lifecycle-next planning, bounded
orchestrator steps, and compact control-plane status. H051 should next harden
the bridge between these pieces: root/path normalization, artifact health,
stuck-state detection, explicit takeover planning, and a safe next-step selector
that can continue a DAG without making hidden mutations.
