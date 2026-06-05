# H051-002 Codex Model and Reasoning Mismatch Audit

Status: pass
Date: 2026-06-05
Issue: H051-002 / #532

## Scope

This audit records the current mismatch between Signposter's selected
role/model/reasoning metadata and what the live Codex CLI runtime reports. It is
documentation-only and does not change routing, execution, fallback, or GitHub
mutation behavior.

## Evidence Sources

- `src/signposter/role_policy.py`
- `src/signposter/role_routing.py`
- `src/signposter/codex_cli_backend.py`
- `src/signposter/runner.py`
- `src/signposter/review.py`
- `tests/test_role_routing.py`
- `tests/test_codex_cli_backend.py`
- `tests/test_runner.py`
- runtime summaries for issues #531 and #532

## Current Intended Policy

For issue #532, Signposter planned execution as:

- backend: `codex-cli`;
- issue labels: `phase:build`, `risk:medium`, `role:worker`, `area:runner`;
- selected role: `WORKER_CORE`;
- selected agent: `codex_worker_core`;
- selected model: `openai/gpt-5.4`;
- selected reasoning effort: `medium`;
- automatic fallback: no;
- manual takeover required after persistent backend blocker.

This is internally consistent with current code because `role_routing.CORE_AREAS`
includes `runner`. `select_role_for_issue()` routes any core area, including
`runner`, to `WORKER_CORE`.

## Mismatches Found

### 1. Area wording versus route choice

The H051 plan describes H051-002 as a medium-risk runner audit with expected
tier "standard coding worker; use stronger reasoning for lifecycle ambiguity."
The actual dry-run selected `WORKER_CORE`.

This is not an execution failure by itself, but it is operator-visible
friction: the route reason says "core or high-risk build task" even when the
task is medium risk and the decisive signal is `area:runner`.

H051 should decide whether this is correct conservative behavior or whether
`runner` should use a more nuanced route split:

- audit/docs-only runner tasks may use `WORKER_CODE` or `WORKER_LIGHT`;
- backend execution semantics, token accounting, and safety code should remain
  `WORKER_CORE`.

### 2. Reasoning effort is metadata-only for Codex CLI

`codex_cli_backend.CodexCliInvocation` records `reasoning_effort`, and the
summary says:

`Reasoning Transport: Signposter metadata only`

The command shape does not pass a reasoning flag to Codex CLI. The live raw
runtime header for #532 reports:

- Signposter selected reasoning: `medium`;
- Codex CLI runtime header: `reasoning effort: xhigh`.

This means Signposter currently preserves intended reasoning metadata, but it
does not enforce the reasoning effort at runtime for Codex CLI.

### 3. Static model allowlist differs from live model availability

`role_policy.py` allows `openai/gpt-5.4`, and current role validation can pass.
The live runtime then classifies execution as `unsupported-model` for the same
model.

The practical truth is:

- registry validity means "allowed by Signposter policy";
- backend preflight means "binary and prompt artifact exist";
- runtime availability means "the selected account/backend accepts the selected
  model now."

Only the third signal proves that a worker can actually run.

## Impact

The current behavior is safe because:

- no silent fallback occurs;
- raw runtime output remains local;
- summaries record selected backend/model/reasoning;
- unsupported model output is classified and requires takeover;
- GitHub mutation remains separate from execution.

The current behavior is inefficient because:

- medium-risk runner audits route to the stronger core worker;
- runtime reasoning may be higher than Signposter's selected metadata;
- repeated unsupported-model attempts consume operator time and can waste tokens
  before takeover.

## Existing H051 Follow-Up Coverage

No new side task is required. H051 already contains direct follow-ups:

- H051-006: Codex model preflight normalization;
- H051-007: reasoning effort transport metadata correction;
- H051-008: unsupported-model classification regression;
- H051-061 through H051-067: router/model selection and route smoke coverage;
- H051-063/H051-064: token usage aggregation and report surfaces.

## Validation

- `git diff --check -- docs/audits/h051-002-codex-model-reasoning-mismatch-audit.md`
- `python -m pytest tests/test_runner.py tests/test_codex_cli_backend.py -q`
- `ruff check .`
- `python -m pytest tests/ -q`

## Safety

- No GitHub mutation was performed by this audit.
- No backend routing or role policy was changed.
- No fallback or model substitution was performed.
- No raw runtime output was posted to GitHub.
- No issue was closed by this audit.

## Conclusion

Signposter's metadata path is auditable, but H051 needs to harden the boundary
between intended model/reasoning policy and live Codex CLI behavior. The next
implementation tasks should treat model availability and reasoning transport as
separate, testable concerns rather than assuming a passing static role policy
means live execution will work.
