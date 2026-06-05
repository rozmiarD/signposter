# H050-064 Planner Prompt Compactness Audit

## Scope

This audit checks planner and planner-adjacent prompt/template surfaces for
token efficiency, duplicated safety prose, and bounded-context behavior. It is
documentation-only: no planner code, runner code, reviewer code, GitHub
mutation behavior, issue closure behavior, backend execution behavior, or
manifest state was changed.

Audited surfaces:

- `src/signposter/planner.py`;
- `src/signposter/runner.py`;
- `src/signposter/review.py`;
- `src/signposter/artifact.py`;
- current H050 generated issue body shape;
- existing H050 prompt-budget tests and audits.

## Current State

Planner-related prompt output currently exists in three different places.

### Planner GitHub Issue Bodies

`format_planner_issue_body` renders the seeded GitHub issue body for every
planner DAG node. It is deterministic and guarded by seed dry-run/apply flow.
It also has body-size checks through `evaluate_worker_issue_body_size`, so very
large generated issue bodies can block before issue creation.

The body is operator-friendly, but it repeats the same safety and workflow
prose in every generated issue:

- current Signposter status;
- dry-run/apply mutation boundary;
- execution boundary;
- generic expected output;
- generic scope rules;
- generic blocked-state rules;
- generic stop conditions.

This is safe, but not compact. On an 80-node roadmap the duplicated template
text is much larger than the task-specific content. The repeated prose also
propagates into worker prompts because issue bodies are embedded back into
prompt artifacts.

### Runner Planner Prompt

`runner.render_prompt` has a planner-specific compact path through
`_render_compact_planner_prompt` when the dispatch role is `planner`. That
prompt already has a bounded prompt budget report for issue body and comments.
It includes:

- selected role/model/reasoning metadata;
- prompt budget report;
- bounded issue body;
- bounded recent comments;
- scoped planner rules;
- compact output contract.

This is the strongest current planner prompt surface. It should be preserved as
the default design direction for future planner execution prompts.

### Worker And Reviewer Prompt Budgeting

Worker prompts already use `PROMPT_COMPACTION_LIMITS`, compact issue/comment
sections, and a `## Prompt Budget Report` section. Reviewer prompts use
`REVIEW_PROMPT_LIMITS`, changed-file/body/diff budgets, and a `## Prompt
Budget` section.

Recent H050 tests added coverage that worker and reviewer budget warnings keep
scope, safety, fallback/takeover, and structured review contract fields
visible. That means the immediate token-efficiency gap is not the worker or
reviewer prompt budget machinery itself.

## Findings

### Finding 1 - Planner issue bodies duplicate safety prose

Severity: medium

The generated issue body template is intentionally self-contained, but it
duplicates high-level safety rules across every seeded task. This inflates
GitHub issue bodies, prompt artifacts, GitHub comment excerpts, and future
handoff context. The duplication is especially visible in large manifests such
as H050.

Recommended follow-up:

- keep the issue body self-contained enough for auditability;
- move repeated policy text toward a shorter named policy block;
- keep task-specific scope, dependencies, acceptance, validation, and stop
  conditions explicit in each issue;
- add tests that compare body size and ensure required task-boundary fields are
  still present.

### Finding 2 - Planner issue body size is checked, but compactness is not

Severity: low

`evaluate_worker_issue_body_size` catches bodies that are too short, too long,
or exceed hard limits. It does not currently flag avoidable duplication or
report how much of the body is task-specific versus boilerplate. That is a
quality gap, not a safety bug.

Recommended follow-up:

- add a small body-shape test for generated planner issue bodies;
- assert that required safety notes remain present;
- assert that repeated policy prose remains bounded;
- avoid adding a runtime LLM summarizer for this deterministic template check.

### Finding 3 - Planner execution prompt is already compact enough

Severity: none

The planner execution prompt in `runner.py` already has the right structure:
role metadata, budget report, bounded context, bounded rules, and compact
output contract. No immediate code change is recommended for that path.

Recommended follow-up:

- preserve this path when planner execution grows;
- avoid copying the larger worker issue body template into planner execution
  prompts;
- keep planner prompt tests focused on required fields and omission markers.

### Finding 4 - Reviewer and worker prompt budget warnings are covered

Severity: none

The current H050 sequence already added worker and reviewer prompt budget
coverage. Those surfaces should not be rewritten as part of planner compactness
unless future validation finds a concrete regression.

## Chosen CLI Shape

No CLI change is recommended from this audit alone.

If compactness enforcement is later implemented, the smallest useful surface is
a read-only planner validation detail, not a new standalone command:

```bash
signposter planner validate --plan docs/roadmaps/<plan>.json
```

or the existing seed dry-run output:

```bash
signposter planner seed --plan docs/roadmaps/<plan>.json --dry-run
```

The output should remain deterministic:

```text
Planner issue body compactness:
  status: pass
  repeated policy lines: bounded
  required task fields: present

Notes:
  No GitHub mutation was performed.
  No backend execution was performed.
```

A blocked example should stay explicit:

```text
Planner issue body compactness:
  status: blocked
  reason: generated issue body exceeds hard size limit

Notes:
  No GitHub issue was created.
  No manifest mutation was performed.
```

## Recommended Next Work

Do not rewrite planner prompts now. The most useful next task is a narrow test
or implementation step that reduces repeated issue-body boilerplate while
preserving these required fields:

- task key and title;
- concrete scope;
- non-goals;
- dependencies and dependency metadata;
- route/role/risk/gate labels;
- acceptance criteria;
- validation commands;
- stop conditions;
- dry-run/apply and execute boundaries.

That follow-up should stay deterministic and should not call an LLM.

## Validation

Targeted validation for this audit:

```bash
git diff --check -- docs/audits/h050-064-planner-prompt-compactness-audit.md
```

Full validation before push remains:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```

## Status

A production code bug was not fixed in this task. The audit found one concrete
compactness gap: generated planner issue bodies repeat safe but bulky policy
text across every roadmap node. The runner planner prompt and reviewer/worker
budget machinery are already bounded enough for the current roadmap stage.
