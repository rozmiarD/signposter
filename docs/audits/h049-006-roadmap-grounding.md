# H049-006 Roadmap Grounding

Date: 2026-06-02
Repository: ExatronOmega/signposter
Base commit: `d4acaff`

## Purpose

This artifact grounds the H049 roadmap after the initial audit sequence and
the first dynamic side task.

The scoped question:

Does the 80-node H049 roadmap still represent the right development direction,
and what changes are required before continuing mainline automation?

No runtime code is changed by this artifact.

## Roadmap Source Of Truth

The H049 roadmap is currently represented by:

- plan: `/tmp/signposter-h049-plan.json`
- manifest: `/tmp/signposter-h049-manifest.json`
- issue bodies: `/tmp/signposter-h049-bodies/`
- GitHub issues: #208 through #287 for the initial 80 nodes
- side issue: #291 / H049-S001

The manifest is the scoped roadmap truth for this stage. The repository-wide
scheduler remains useful for general ready-work discovery, but it is not the
right source for H049 ordering while older H048 issues remain open.

## Current Planner Snapshot

Read-only command:

```bash
signposter planner run \
  --manifest /tmp/signposter-h049-manifest.json \
  --sync-github \
  --dry-run
```

Observed output after #212 and #291 were integrated:

- planner status: active
- total tasks: 81
- merged tasks: 6
- completed tasks: 6
- active tasks: 1
- waiting dependency tasks: 74
- active task: H049-006 / #213
- advance candidates: none while #213 is active
- LLM analysis required: false

The total is 81 because H049 began with exactly 80 seeded nodes and added one
real side task, H049-S001 / #291.

## Completed Grounding Work

Completed mainline nodes:

- H049-001 / #208 - repository and command-surface audit
- H049-002 / #209 - architecture module boundary map
- H049-003 / #210 - lifecycle state-machine audit
- H049-004 / #211 - planner DAG behavior audit
- H049-005 / #212 - GitHub mutation boundary audit

Completed side node:

- H049-S001 / #291 - block premature planner advance for multi-dependency
  tasks

All completed items went through the normal lifecycle:

- worktree
- claim
- worker artifact
- validation
- report
- gate
- complete
- PR
- review
- merge
- integration
- cleanup

## Roadmap Validity

The roadmap remains valid.

The audit-first sequence has produced useful evidence and did not invalidate
the broader direction. The strongest next direction is still Signposter
control-plane maturity:

- planner and DAG correctness;
- lifecycle state clarity;
- stuck-state detection and recovery;
- bounded evidence and comments;
- dry-run/apply safety;
- GitHub workflow quality;
- token-efficient prompts and routing;
- maintainability and documentation alignment.

The roadmap should continue, but it must incorporate discoveries made during
the first six tasks.

## Required Side Task Already Added

H049-S001 / #291 was required before continuing past #212.

Reason:

- `planner advance --issue 210 --dry-run` could promote #212 while #211 was
  incomplete.
- That exposed a real multi-dependency advance risk.

Resolution:

- #291 changed planner advance to require all downstream dependencies to be
  complete before a target is promotable.
- Regression tests were added.
- The manifest was updated locally so H049-005 depends on H049-S001.
- After #291 completed, `planner advance --issue 291 --apply` promoted #212.

This confirms that dynamic side-task insertion is possible today, but still
requires operator-managed manifest editing. Later H049 tasks should make this
safer and first-class.

## Active Follow-Up Findings

### Planner Status Counts

`planner run` still reports many tasks as `ready` because counts are based on
labels/state rather than actionable dependency readiness.

This should be fixed in the planner progress/reporting workstream.

### Post-Claim Classification

For #210, #211, and #212, refreshed run-plan output sometimes showed route or
gate values that disagreed with GitHub labels after claim.

For #213 the refreshed output was correct.

This inconsistency should be fixed before unattended loops rely on refreshed
classification output.

### Dependency Metadata Round-Trip

Planner dependencies are key-based in the manifest. Scheduler dependencies are
issue-number based from GitHub issue bodies.

The H049 manifest can drive the roadmap correctly, but seeded issue metadata
still needs hardening before repository-wide scheduler graph output can fully
replace manifest-scoped planning.

### Backend Availability

Signposter can plan and invoke Codex CLI roles, but current account/model
availability makes live worker/reviewer execution unreliable:

- `openai/gpt-5.3-codex` is not available through the current Codex CLI account
  path.
- `openai/gpt-5.4` is not available through the current Codex CLI account path.
- `xai/grok-build-0.1` is selected for reviewer-light but currently runs
  through the wrong provider path.

The workflow remains valid because manual artifacts are first-class enough for
controlled takeover, but automation cannot yet be considered unattended.

## Continue / Stop Decision

Continue.

The roadmap should continue with H049-007 after H049-006 completes, but later
implementation tasks should prioritize the findings above instead of adding
new disconnected features.

The next expected planner action after H049-006 integration:

```bash
signposter planner advance \
  --manifest /tmp/signposter-h049-manifest.json \
  --issue 213 \
  --sync-github \
  --dry-run
```

If ready, the corresponding apply should promote H049-007 / #214.

## Validation

This artifact is documentation-only. Required validation:

- `ruff check .`
- `python -m pytest tests/ -q`

No GitHub mutation is performed by this committed artifact file.
