# H049-004 Planner DAG Behavior Audit

Date: 2026-06-02
Repository: ExatronOmega/signposter
Base commit: `e9e0628`

## Purpose

This audit records current planner DAG behavior before H049 changes dependency
advancement, side-task insertion, stuck-state recovery, or automatic loop
execution.

The narrow scope is planner behavior only:

- manifest-backed task status;
- dependency-ready task selection;
- guarded downstream advancement;
- GitHub issue metadata round-trip behavior;
- interaction with the repository-wide scheduler.

No planner code is changed by this audit.

## Planner Surfaces Reviewed

Current planner command surfaces include:

- `planner draft`
- `planner validate`
- `planner seed`
- `planner status`
- `planner next`
- `planner step`
- `planner run`
- `planner impact`
- `planner advance`
- `planner mark`
- `planner roadmap`

The H049 roadmap used these surfaces to create an 80-node manifest and seed
GitHub issues #208 through #287. That establishes the planner as the scoped
truth source for this development stage.

## Current DAG Truth Sources

The planner currently combines these sources:

- local plan JSON task keys and `depends_on` values;
- seed manifest entries with GitHub issue numbers;
- GitHub issue state and workflow labels refreshed into manifest status;
- optional `github_depends_on` metadata materialized from key dependencies;
- repository-wide dependency parsing from GitHub issue bodies;
- scheduler graph parsing of `Depends-On: #N` issue metadata.

This split is workable, but it means the planner and scheduler can disagree
unless the operator is explicit about the manifest being used.

## H049 Manifest Behavior

The H049 manifest is the correct source for the current roadmap because it
contains all 80 task keys, issue numbers, and ordered dependencies.

Observed behavior:

- `planner next --manifest /tmp/signposter-h049-manifest.json --sync-github`
  selected the next H049 task correctly.
- After #208 and #209 completed, the manifest-scoped next task advanced to
  #210 / H049-003.
- After #210 completed, #211 / H049-004 remained the next mainline task.
- Dependency-waiting tasks remained visible in reconcile output.

This is the correct high-level behavior for the H049 execution loop.

## Scheduler Scope Finding

The repository-wide scheduler is not roadmap-scoped. During H049, it still saw
older stale H048 work, including issue #187, as candidate work.

That is not a scheduler correctness bug by itself. It means automatic loops
must be explicit about which graph they are driving:

- manifest-scoped planner for a concrete roadmap;
- repository-wide scheduler for general ready-work discovery.

H049 should avoid using repository-wide scheduler output as the only truth for
this roadmap until stale ready issues and roadmap scope are handled more
clearly.

## Dependency Readiness Strength

`build_planner_next_from_status` has a sound core dependency check:

- it builds the completed task-key set from completed planner states;
- it computes missing dependencies for each task;
- it places incomplete-dependency tasks into the waiting list;
- it selects the first dependency-ready task;
- it prioritizes dependency-ready side tasks before mainline tasks.

This is the right behavior to preserve.

## Readiness Count Gap

`build_planner_status_counts` counts `ready` from state labels only. It does not
subtract tasks that are open but still dependency-waiting.

Observed operator effect:

- dashboard counts can imply many tasks are ready;
- reconcile hints simultaneously say many tasks are waiting for dependencies.

This is confusing for automation because the compact count looks broader than
the actionable next-task set.

Recommended follow-up:

- add an actionable-ready count based on dependency readiness;
- keep label counts if useful, but name them clearly;
- make `planner run` output distinguish label-ready/open from
  dependency-ready.

## Advance Safety Finding

`build_planner_advance_plan_from_status` currently finds downstream tasks by
checking whether the completed source key appears in `depends_on`.

It does not check whether every other dependency for the downstream task is
also complete before proposing `state:ready`.

Concrete H049 observation:

- #210 / H049-003 completed;
- #212 / H049-005 depends on both H049-003 and H049-004;
- H049-004 / #211 was not complete;
- `planner advance --issue 210 --dry-run` proposed promoting #212.

The dry-run guard prevented mutation because it was not applied, but this is a
real advance-planning bug. It can prematurely expose downstream work to
automatic selection in a multi-dependency graph.

Follow-up issue:

- #291 / H049-S001 - Block premature planner advance for multi-dependency
  tasks.

Until that issue is completed, H049 should not apply planner advance from #210
or #211 if the target task has additional incomplete dependencies.

## Issue Metadata Round-Trip Gap

Planner task dependencies are key-based in the manifest. Scheduler dependency
parsing is GitHub-issue-number based and reads body lines like `Depends-On:
#123`.

Seeded H049 issues preserve rich task body content, but the GitHub dependency
round-trip is not yet strong enough to make the repository-wide scheduler a
drop-in replacement for manifest-scoped planner truth.

Recommended follow-up:

- ensure seeded issue bodies contain parseable GitHub dependency lines after
  issue numbers are known;
- keep this idempotent so repeated sync does not duplicate dependency text;
- test scheduler graph output against seeded planner issues.

## Post-Claim Classification Finding

During #210 and #211, `run --dry-run` before claim reported worker/CI routing
correctly. After `run --claim`, the refreshed run plan printed:

- route: reviewer
- gate: None

GitHub labels remained correct:

- `role:worker`
- `gate:ci`
- `state:active`

This looks like a display or refreshed-classification issue rather than a label
mutation issue. It should be fixed before fully unattended loops rely on
post-claim route/gate output.

## Side-Task Handling Status

The planner already has side-task fields:

- `mainline`
- `parent`
- `return_to`
- `side_task`

`planner next` can prioritize a dependency-ready side task before mainline
work. This is a good base for dynamic side-DAGs.

Current gap:

- side-task creation and manifest insertion are not yet a complete guarded
  operator flow;
- return-to-mainline behavior depends on correct advance and readiness logic;
- the multi-dependency advance bug must be fixed before side-task return is
  trusted.

## Recommended H049 Ordering

Immediate ordering after this audit:

1. Complete #211 / H049-004.
2. Complete #291 / H049-S001 before promoting #212.
3. Continue with #212 / H049-005 only after multi-dependency advance is fixed.
4. Add a later H049 task for actionable-ready planner counts.
5. Add a later H049 task for issue dependency metadata round-trip hardening.
6. Add a later H049 task for post-claim route/gate classification consistency.

## Validation

This audit is documentation-only. Required validation:

- `ruff check .`
- `python -m pytest tests/ -q`

No GitHub mutation is performed by this committed audit file.
