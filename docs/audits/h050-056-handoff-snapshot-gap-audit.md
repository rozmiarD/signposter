# H050-056 Handoff Snapshot Command Gap Audit

## Scope

This audit checks whether existing Signposter handoff and status surfaces can
produce a complete operator handoff snapshot for an interrupted roadmap run.
It is intentionally documentation-only: no code path, GitHub mutation behavior,
planner state, issue closure, merge behavior, or cleanup behavior was changed.

Audited surfaces:

- `signposter handoff plan`;
- `signposter lifecycle status`;
- `signposter planner status`;
- `signposter control-plane status`;
- `src/signposter/handoff.py`;
- `src/signposter/control_status.py`;
- CLI registration for the handoff and control-plane commands.

## Current Handoff Surface

`signposter handoff plan --repo ExatronOmega/signposter --issue 426` is a
read-only branch/worktree handoff planner. In the active H050-056 worktree it
reported:

- issue title and workflow state;
- expected worktree path and worker branch;
- current branch inside the worktree;
- changed files, if any;
- suggested commit message;
- suggested local git commands for diff, add, commit, and push;
- blocked status when no changes exist;
- safety notes saying no commit, push, PR, merge, or issue close happened.

This is useful for a narrow branch handoff, but it is not a full project
handoff snapshot.

The command has only one subcommand:

```bash
signposter handoff plan --repo OWNER/REPO --issue N
```

There is no `handoff snapshot` command and no handoff surface that aggregates
planner, lifecycle, PR, CI, review, integration, cleanup, bug-ledger, local
artifact, and next-task state in one stable output.

## Existing Snapshot Pieces

### Planner Status

`signposter planner status --manifest docs/roadmaps/h050-seed-manifest.json
--sync-github` gives the strongest roadmap inventory. It can show the full
H050 task list, dependency edges, GitHub issue numbers, and current task
states. During this audit it showed:

- 80 total H050 tasks;
- 55 merged;
- H050-056 / issue #426 active;
- 24 waiting downstream tasks;
- H050-080 depending on H050-078 and H050-079.

Gap: planner status does not include PR, CI, review, integration, cleanup, raw
artifact, or local branch/worktree details for each task.

### Lifecycle Status

`signposter lifecycle status --repo ExatronOmega/signposter --issue 426`
provides the strongest single-issue lifecycle view. From the main repository it
reported issue #426 as active, no PR detected, expected worktree present, and
cleanup incomplete. That is correct for an active worker task.

Gap: lifecycle status is issue-scoped. It does not summarize the roadmap, next
dependency-ready task, planner advance candidates, bug ledger, backend status,
or handoff instructions.

### Control-Plane Status

`signposter control-plane status --repo ExatronOmega/signposter --manifest
docs/roadmaps/h050-seed-manifest.json --sync-github` gives the best compact
operator view. During this audit it showed:

- current active task #426;
- planner counts;
- scheduler and orchestrator decision;
- active issue diagnostics;
- stale local worker state warnings;
- recent bug ledger entries;
- read-only safety notes.

Gap: control-plane status is not yet a handoff snapshot. It does not include
recent commit, main/origin sync, last completed task, PR/CI details for active
work, integration/cleanup state for recent PRs, exact validation commands, raw
artifact paths, or a final "resume from here" command sequence.

The current control-plane status also surfaced an important ambiguity: planner
state pointed at active H050 issue #426, while scheduler/orchestrator selected
old issue #187. The status correctly blocked on disagreement, but a handoff
snapshot command should make this kind of cross-roadmap disagreement explicit
and should prefer a manifest-scoped next task when a manifest is provided.

### CWD-Sensitive Worktree Reporting

Running lifecycle status from the H050-056 worktree reported the expected
worktree as missing because the displayed path is relative to the current
working directory:

```text
expected worktree: ../signposter-work/426
worktree exists: no
```

Running the same command from the main repository reported:

```text
expected worktree: ../signposter-work/426
worktree exists: yes
```

This is a real handoff snapshot gap. A complete snapshot must either anchor all
local paths to the repository root or print absolute paths for local
worktree/branch/artifact evidence.

## Missing Complete Snapshot Fields

A complete handoff snapshot should include, at minimum:

- repository path, current branch, HEAD, and main/origin sync status;
- dirty-tree status for the main repo and active worktree;
- active roadmap manifest path and manifest status;
- task counts and first eligible next task;
- active issue, labels, worktree, prompt, worker summary, and gate state;
- associated PR, CI, review, merge, integration, and cleanup state when present;
- local artifact paths for worker and reviewer raw/summary files;
- recent bug-ledger entries and unresolved recovery notes;
- stale local branch/worktree warnings;
- last completed issue/PR when detectable;
- exact safe resume command;
- exact stop reason if the workflow is blocked;
- safety notes for dry-run/apply and execute boundaries.

None of the existing surfaces alone provides all of these fields.

## Recommended CLI Shape

Add a future read-only command:

```bash
signposter handoff snapshot \
  --repo ExatronOmega/signposter \
  --manifest docs/roadmaps/h050-seed-manifest.json \
  --sync-github
```

Expected behavior:

- read-only by default and always;
- no GitHub mutation;
- no manifest mutation;
- no lifecycle mutation;
- no backend execution;
- bounded output suitable for pasting into a new operator session;
- stable absolute or repo-root-relative paths;
- explicit blocked/ready status;
- exact next command to resume.

Recommended output sections:

```text
Signposter Handoff Snapshot

Repository:
Planner:
Current task:
Lifecycle:
PR / CI / review:
Integration / cleanup:
Local artifacts:
Recovery / bugs:
Resume:
Status:
Notes:
```

## Non-Goals For The Future Command

The future snapshot command should not:

- create commits or branches;
- push or open PRs;
- close issues;
- run worker or reviewer backends;
- edit manifest files;
- hide disagreement between planner, scheduler, and orchestrator;
- replace the detailed lifecycle or planner commands.

## Risk Assessment

The current state is safe but incomplete.

No evidence suggests that `handoff plan` mutates state unexpectedly. The risk is
operator confusion: an interrupted autonomous loop currently requires several
separate commands to reconstruct state, and some local path results depend on
the directory from which the command is run.

## Recommended Follow-Up

Implement `signposter handoff snapshot` as a narrow read-only aggregation
surface. It should reuse existing planner, lifecycle, control-plane,
bug-ledger, and git helpers instead of introducing a parallel state model.

Acceptance for that follow-up:

- includes repo sync and dirty-tree status;
- includes manifest-scoped planner status and active/next task;
- includes lifecycle summary for the active or selected issue;
- includes PR/CI/review/integration/cleanup state when known;
- includes local artifact paths and stale worker-state warnings;
- uses stable path anchoring independent of current working directory;
- blocks clearly on planner/scheduler/orchestrator disagreement;
- prints no secrets and no raw logs;
- has tests for ready, blocked, and missing-manifest paths.

## Validation

Targeted validation for this documentation-only audit:

```bash
git diff --check -- docs/audits/h050-056-handoff-snapshot-gap-audit.md
```

Full validation before push remains:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```

## Status

A code bug was not fixed in this task. The audit found a real product gap:
Signposter has several useful handoff/status pieces, but no complete handoff
snapshot command. The follow-up should be a small read-only aggregation command,
not a rewrite of planner, lifecycle, or control-plane logic.
