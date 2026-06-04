# H050-058 Role Policy Current-State Audit

## Scope

This audit checks the current Signposter role policy and backend default state
against observed execution behavior, routing tests, and cost-safety goals. It
is intentionally documentation-only: no role registry, backend adapter, router,
GitHub mutation behavior, issue closure behavior, or execution command was
changed.

Audited surfaces:

- `src/signposter/role_policy.py`;
- `src/signposter/role_routing.py`;
- `tests/test_role_policy.py`;
- `tests/test_role_routing.py`;
- `signposter roles status`;
- `signposter roles validate`;
- `signposter backend status --default codex-cli`;
- recent H050 runtime artifacts for Codex CLI worker/reviewer execution.

## Current Active Role Set

The active registry contains these Signposter roles:

- `ROUTER_CLASSIFIER`;
- `ARTIFACT_SUMMARIZER`;
- `ISSUE_FACTORY`;
- `PLANNER_MAIN`;
- `WORKER_LIGHT`;
- `WORKER_CODE`;
- `WORKER_CORE`;
- `REVIEWER_LIGHT`;
- `REVIEWER_CORE`;
- `CRITICAL_OVERRIDE`;
- `RECONCILE_LIGHT`;
- `RECONCILE_CORE`;
- `LEGACY_BACKUP`.

`signposter roles validate` currently reports:

```text
Status:
  pass
```

This means the configured registry satisfies the static policy checks: allowed
models, reasoning-effort policy, manual-only critical override, explicit
legacy fallback, and configured profile presence.

## Current Model And Reasoning Policy

The current static policy is:

| Role | Model | Reasoning | Notes |
| --- | --- | --- | --- |
| `ROUTER_CLASSIFIER` | `openai/gpt-5.4-mini` | `minimal` | cheap classification |
| `ARTIFACT_SUMMARIZER` | `openai/gpt-5.4-mini` | `minimal` | bounded evidence extraction |
| `ISSUE_FACTORY` | `openai/gpt-5.4-mini` | `low` | issue shaping |
| `PLANNER_MAIN` | `openai/gpt-5.4` | `medium` | roadmap/DAG planning |
| `WORKER_LIGHT` | `xai/grok-build-0.1` | `low` | docs/tests/simple patch |
| `WORKER_CODE` | `openai/gpt-5.3-codex` | `low` | code-heavy work |
| `WORKER_CORE` | `openai/gpt-5.4` | `medium` | core Signposter semantics |
| `REVIEWER_LIGHT` | `xai/grok-build-0.1` | `low` | docs/small review |
| `REVIEWER_CORE` | `openai/gpt-5.4` | `medium` | core review |
| `CRITICAL_OVERRIDE` | `openai/gpt-5.4` | `high` | manual-only critical path |
| `RECONCILE_LIGHT` | `openai/gpt-5.4-mini` | `low` | simple reconcile |
| `RECONCILE_CORE` | `openai/gpt-5.4` | `medium` | DAG-changing reconcile |
| `LEGACY_BACKUP` | `openai/gpt-5.2` | `low` | explicit legacy fallback |

The static policy still matches the intended cost shape:

- cheap roles use `gpt-5.4-mini` or `grok-build-0.1`;
- code roles use `gpt-5.3-codex` or `gpt-5.4`;
- critical override is manual-only and no longer uses `gpt-5.5`;
- `gpt-5.2` is limited to explicit legacy fallback.

## Backend Defaults

`signposter backend status --default codex-cli` reports:

- default backend: `codex-cli`;
- fallback order: `codex-cli -> openclaw`;
- Codex CLI binary present;
- OpenClaw CLI and auth profile config present as legacy fallback;
- no prompt execution and no token consumption from the status command.

This is a useful read-only surface, but it is currently a binary/config
availability check. It does not prove that a selected role model is usable by
the active account/provider at runtime.

## Deterministic Routing State

`tests/test_role_routing.py` covers the expected deterministic role selection:

- docs issues route to `WORKER_LIGHT`;
- tests-only issues route to `WORKER_LIGHT`;
- normal code issues route to `WORKER_CODE`;
- core/high-risk issues route to `WORKER_CORE`;
- high-risk tests do not use `WORKER_LIGHT`;
- docs/small PR reviews route to `REVIEWER_LIGHT`;
- core PR reviews route to `REVIEWER_CORE`;
- simple reconcile uses `RECONCILE_LIGHT`;
- DAG-changing reconcile uses `RECONCILE_CORE`.

H050-058 itself was classified as:

```text
selected_role: WORKER_CORE
model: openai/gpt-5.4
reasoning: medium
role_agent: codex_worker_core
```

That classification is correct for `area:core` and `risk:medium`.

## Observed Runtime Gap

Recent H050 runtime attempts show that static policy validity and backend
binary readiness are not enough:

- H050-056 / issue #426 selected `WORKER_LIGHT` with `xai/grok-build-0.1` and
  Codex CLI returned `unsupported-model`;
- H050-057 / issue #427 selected `WORKER_CODE` with `openai/gpt-5.3-codex` and
  Codex CLI returned `unsupported-model`;
- H050-057 reviewer selected `REVIEWER_CORE` with `openai/gpt-5.4` and Codex
  CLI returned `unsupported-model`;
- H050-058 / issue #428 selected `WORKER_CORE` with `openai/gpt-5.4` and Codex
  CLI returned `unsupported-model`.

These failures were handled safely by preserving raw runtime artifacts locally
and using manual human/operator summaries. No GitHub mutation was performed by
the failed execution attempts.

The gap is precise:

`backend status` can say the backend is ready while the selected model is not
usable by the active Codex CLI account/provider.

## Current Risks

### Runtime availability is not model-specific

The role registry validates allowed models and profile wiring, but the runtime
path does not yet expose model-specific availability. Operators currently learn
about unavailable models only after an execution attempt writes a runtime
failure artifact.

### Fallback is visible but not actionable enough

Role policy contains fallback metadata such as `WORKER_LIGHT` ->
`openai/gpt-5.4-mini`, but the execution path does not automatically choose the
fallback. That is safe, because silent fallback would hide cost and semantic
changes, but the operator output should make the exact next fallback command or
manual takeover path clearer.

### OpenClaw profile state can differ from active policy

`roles status` shows some OpenClaw profile primary models that differ from the
active policy model, while the policy model appears in fallbacks. This is
statically acceptable today, but it can confuse operators unless the output
continues to separate:

- active policy model;
- OpenClaw profile primary model;
- OpenClaw profile fallback models;
- Codex CLI agent metadata.

### Critical profile still exposes `gpt-5.5`

The active `CRITICAL_OVERRIDE` policy uses `openai/gpt-5.4`, which matches the
current intended policy. The local OpenClaw `main` profile still reports
`openai/gpt-5.5` as profile primary with `openai/gpt-5.4` fallback. This is not
an active policy violation, but it is worth keeping visible.

## Recommended Follow-Up

H050 already has the right next tasks:

1. H050-059: runtime availability status output.
   - Extend backend status or a nearby command so it can report selected role
     model availability, not only binary/config presence.
   - Show unavailable/unsupported model evidence without consuming model tokens
     where possible.

2. H050-060: model fallback transparency.
   - Make fallback metadata actionable in dry-run output.
   - Do not silently execute a fallback model.
   - Show the operator the selected model, fallback model, why fallback is or
     is not active, and the exact recovery path.

3. H050-061: token usage aggregation by role coverage.
   - Preserve current `unknown` token status when backends do not report usage.
   - Aggregate by role/model only when trustworthy usage data exists.

No role-policy rewrite is recommended from this audit alone. The static policy
is coherent; the missing piece is runtime availability and fallback
observability.

## Validation

Targeted validation for this audit:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/test_role_policy.py tests/test_role_routing.py -q
```

Full validation before push remains:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```

## Status

A code bug was not fixed in this task. The audit found a real runtime
observability gap: Signposter can validate role policy statically and can see
that Codex CLI exists, but it cannot yet prove that the selected model is
available before execution. The next tasks should harden model availability
status and fallback transparency without weakening explicit execution or
mutation boundaries.
