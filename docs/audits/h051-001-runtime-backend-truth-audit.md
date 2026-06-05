# H051-001 Runtime Backend Truth Audit

Status: pass
Date: 2026-06-05
Issue: H051-001 / #531

## Scope

This audit records the current runtime backend truth before H051 starts changing
backend behavior. It is intentionally documentation-only and does not alter
backend routing, model policy, fallback behavior, GitHub mutation behavior, or
execution semantics.

## Surfaces Inspected

- `src/signposter/execution_backend.py`
- `src/signposter/codex_cli_backend.py`
- `src/signposter/backend_status.py`
- `src/signposter/role_policy.py`
- `src/signposter/runner.py`
- `tests/test_codex_cli_backend.py`
- `tests/test_backend_status.py`
- `tests/test_runner.py`
- recent local runtime summaries under `artifacts/runs/`

## Current Backend Contract

Signposter's default execution backend is `codex-cli`.

The Codex CLI adapter is deliberately bounded:

- command shape: `codex exec --model <model> --cd <worktree> --output-last-message <path> -`
- prompt transport: prompt artifact read locally and passed through stdin;
- local preflight checks the `codex` binary and prompt artifact;
- stdout and stderr are captured into local raw artifacts;
- a bounded summary artifact records backend, agent, model, reasoning, status,
  token usage status, and takeover guidance;
- GitHub is not mutated by backend execution.

Reasoning effort is currently Signposter metadata. The live raw output for
H051-001 shows Codex CLI running with `reasoning effort: xhigh` while the
Signposter role policy selected `medium`. That mismatch is already represented
by H051-002 and H051-007.

## Live Backend Status

Command:

`signposter backend status --default codex-cli --runs-dir /home/probo/projects/signposter/artifacts/runs`

Observed result:

- default backend: `codex-cli`;
- Codex CLI binary: present at `/home/probo/.local/bin/codex`;
- OpenClaw CLI/config: present as legacy fallback;
- runtime availability: warnings;
- recent diagnostics: repeated `unsupported-model` for `codex-cli`,
  `codex_worker_core`, `openai/gpt-5.4`.

This means static backend readiness is not enough. The local binary and prompt
preflight can pass while the selected model is unavailable or rejected at actual
execution time.

## Runtime Evidence

H051-001 attempted worker execution through Signposter:

- issue: #531;
- selected role: `WORKER_CORE`;
- selected agent: `codex_worker_core`;
- selected model: `openai/gpt-5.4`;
- selected reasoning: `medium`;
- backend: `codex-cli`;
- execution status: `unsupported-model`;
- token usage status: `unknown`;
- automatic fallback: no;
- manual takeover required: yes.

The failed runtime artifacts were preserved locally:

- `artifacts/runs/issue-531-worker.codex-runtime.raw.txt`
- `artifacts/runs/issue-531-worker.codex-runtime.summary.md`

Recent H050 tail tasks show the same runtime pattern for worker and reviewer
routes. Manual takeover artifacts remained necessary even though deterministic
planner, lifecycle, review, merge, integration, and cleanup controls worked.

## Findings

1. `codex-cli` is correctly treated as the default backend in Signposter
   planning output.
2. Signposter records selected role, model, reasoning, backend, agent, and
   fallback policy in dry-run and runtime summaries.
3. Static backend health currently reports `ready` when the CLI binary exists,
   but that does not prove selected model availability.
4. Runtime summaries correctly classify the repeated blocker as
   `unsupported-model`.
5. Automatic fallback is forbidden and was not used.
6. Manual takeover remains the correct recovery path until H051 hardens model
   preflight, reasoning transport, and timeout/stall handling.
7. Token usage accounting remains `unknown` when the backend does not report
   usage.

## H051 Follow-Up Mapping

The current H051 DAG already contains the required follow-up sequence:

- H051-002: Codex model and reasoning mismatch audit;
- H051-006: Codex model preflight normalization;
- H051-007: reasoning effort transport metadata correction;
- H051-008: unsupported-model classification regression;
- H051-009/H051-010: worker and reviewer runtime artifact preservation;
- H051-063/H051-064: token usage aggregation and final report surfaces.

No additional side task is required from this audit.

## Validation

- `git diff --check -- docs/audits/h051-001-runtime-backend-truth-audit.md`
- `signposter backend status --default codex-cli --runs-dir /home/probo/projects/signposter/artifacts/runs`
- `ruff check .`
- `python -m pytest tests/ -q`

## Safety

- No GitHub mutation was performed by this audit.
- No backend execution was performed by this audit beyond the already-preserved
  Signposter worker attempt for #531.
- No raw runtime output was posted to GitHub.
- No fallback or model substitution was performed.
- No issue was closed by this audit.

## Conclusion

H051 should continue with H051-002. The immediate architectural truth is that
Signposter's deterministic control plane is functioning, while the live Codex
runtime path needs model-availability and reasoning-transport hardening before
it can reduce manual takeover frequency.
