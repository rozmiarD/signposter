# H049-016 Dynamic Side-Task Insertion Design

## Status

Completed design. No runtime mutation path is introduced by this task.

## Purpose

Dynamic side tasks let Signposter pause a mainline task sequence, insert a
small required fix or prerequisite, complete it, and then return to the
original mainline without losing dependency context.

The design keeps side-task insertion deterministic-first:

- Planner records graph metadata.
- Scheduler selects dependency-ready side tasks before mainline tasks.
- Orchestrator still executes one issue through normal lifecycle.
- Integration still owns issue closure.
- No LLM reconcile is invoked by default.

## Minimal Manifest Fields

Existing manifest entries already tolerate these fields and should keep them as
the side-task contract:

- `side_task`: boolean marker; true means the task is an inserted branch.
- `parent`: source task or GitHub issue that caused insertion.
- `return_to`: mainline task or GitHub issue to resume after side completion.
- `mainline`: optional grouping key for the roadmap sequence.
- `depends_on`: task keys that must be completed before the side task can run.
- `github_depends_on`: resolved GitHub issue numbers for operator visibility.
- `dependency_metadata`: resolved dependency URLs and status hints.

The planner should continue to tolerate absent fields as `None` or false so
older manifests remain valid.

## Dependency Semantics

Side tasks are ordinary DAG nodes with one extra scheduling rule:

- A side task is eligible only when all `depends_on` tasks are completed.
- If a side task and a mainline task are both ready, the side task wins.
- The side task must complete through the same issue, PR, review, merge,
  integration, and cleanup lifecycle.
- After side-task integration, `return_to` becomes the operator-visible target
  for resuming the mainline.
- A side task must not close or mutate the mainline issue directly.

This matches the current `planner next` behavior that prefers dependency-ready
side tasks before mainline tasks.

## Insertion Flow

The safe insertion flow should be:

1. Build a local side-task plan with reason, scope, dependencies, and return
   pointer.
2. Render issue body and manifest preview locally.
3. Validate label availability and manifest idempotence.
4. Show dry-run output with planned GitHub issue creation and manifest update.
5. Apply only with explicit `--apply`.
6. Execute the created issue through the normal Signposter lifecycle.
7. After integration and cleanup, run planner status/next to return to the
   mainline.

If any mutation fails, later mutation steps must stop.

## Operator Status

Planner/status output should make side branches visible without a new product
surface:

- show `side-task: yes`;
- show `parent`;
- show `return_to`;
- show dependency readiness;
- show whether the mainline is waiting on a side task;
- show the exact next command.

The output should stay compact enough for routine loop use.

## Dry-Run And Apply Boundary

Dry-run must show:

- task key and proposed GitHub title;
- labels;
- dependencies;
- side-task fields;
- return pointer;
- body path;
- exact planned `gh issue create` command;
- exact planned manifest mutation;
- notes that no GitHub, manifest, OpenClaw, or LLM mutation occurred.

Apply must:

- create the GitHub issue only after dry-run-ready state;
- update the manifest only after issue creation succeeds;
- stop immediately on the first critical failure;
- never close any issue;
- never run a worker/reviewer backend.

## LLM Boundary

LLM reconcile may help draft a candidate side task only after deterministic
state reports ambiguity. The deterministic planner must still own:

- final dependency graph shape;
- labels;
- mutation plan;
- idempotence checks;
- dry-run/apply boundary;
- lifecycle state transitions.

Default side-task insertion should remain zero-token unless the operator
explicitly asks for LLM-assisted reconcile.

## Next Implementation Slice

The next narrow implementation task should add a guarded planner surface for
creating a side-task insertion plan from existing manifest state. It should
start local-only and test:

- required fields are present;
- missing `return_to` blocks;
- missing `parent` blocks;
- side-task dry-run output is compact;
- no GitHub mutation happens without `--apply`.

Actual issue creation and manifest append can be a follow-up if that first
surface is still too large.

## Validation

This design is a documentation/audit artifact. Runtime behavior is unchanged.
Validation for this task should run repository lint and tests to confirm no
code behavior was affected.
