# H050-004 Test Coverage Gap Map

## Scope

This audit maps the current test surface against the H050 control-plane roadmap.
It is intentionally bounded: no code change, no test rewrite, no unrelated
formatting churn, no GitHub mutation by the implementation, and no manual issue
closure.

## Inventory Method

Commands used from the issue worktree:

```bash
rg --files tests | sort
rg -c "^def test_|^    def test_" tests | sort
rg -c "^def test_|^    def test_" tests | awk -F: '{sum += $2} END {print sum}'
rg -n "TODO|xfail|skip|not implemented|unimplemented|bootstrap|skeleton" tests src/signposter docs
```

Current baseline:

- test files: 51 top-level `tests/test_*.py` files;
- source files: 44 top-level `src/signposter/*.py` files;
- counted test functions: 869;
- full local test suite passes in the current checkout.

## Coverage Map

### Planner and DAG

Current tests:

- `tests/test_planner.py`: 124 tests;
- `tests/test_dependencies.py`: 11 tests;
- `tests/test_issue_manifest.py`: 7 tests;
- `tests/test_issue_factory.py`: 6 tests;
- `tests/test_scheduler.py`: 19 tests.

Covered well:

- planner draft, validate, seed, next, status, run, advance, impact, step, and
  side-task plan formatting;
- dependency parsing and dependency-blocked scheduler selection;
- issue DAG manifest plan/apply surfaces;
- ready/active/done/merged counting in planner dashboards.

Remaining gaps:

- partial seed/apply recovery when GitHub issue creation succeeds for only part
  of a wave;
- duplicate manifest mapping reconciliation when issue numbers drift;
- expensive `--sync-github` behavior over large manifests, including bounded
  timeout/reporting;
- planner advance idempotence across repeated terminal-state calls;
- side-task insertion apply and return-to-mainline behavior under mixed
  completed/active siblings.

### Lifecycle, Orchestrator, and Scheduler

Current tests:

- `tests/test_lifecycle.py`: 40 tests;
- `tests/test_orchestrator.py`: 62 tests;
- `tests/test_scheduler.py`: 19 tests;
- `tests/test_control_status.py`: 7 tests;
- `tests/test_full_lifecycle_happy_path.py`: 3 tests.

Covered well:

- lifecycle status/watch formatting;
- lifecycle next and orchestration planning surfaces;
- bounded automation summaries;
- scheduler selection and skipped-candidate explanations;
- a narrow full lifecycle happy path.

Remaining gaps:

- interrupted active issue recovery where prompt artifacts exist but worker raw
  output is unsafe;
- branch/worktree exists but lifecycle status is run from a non-root CWD;
- active issue with PR already merged but integration not yet applied;
- cleanup-only recovery for already-merged PRs after process interruption;
- repeated run-next loop behavior when one cycle succeeds and the next cycle is
  dependency-blocked.

### Gate and Evidence

Current tests:

- `tests/test_gate.py`: 43 tests;
- `tests/test_artifact.py`: 17 tests;
- `tests/test_artifact_safety.py`: 5 tests;
- `tests/test_report.py`: 23 tests;
- `tests/test_comments.py`: 17 tests;
- `tests/test_safety.py`: 11 tests.

Covered well:

- CI, human, review, and no-op gate evidence paths;
- unsafe raw artifact preflight;
- bounded report excerpts;
- auto-close keyword and secret redaction checks;
- manual worker/reviewer artifact generation and validation.

Remaining gaps:

- structured evidence parsing that avoids phrase-only blockers without becoming
  permissive;
- canonical takeover raw/summary pairing for backend diagnostics stored under
  `*.codex-runtime.*`;
- end-to-end report/gate behavior when backend artifacts are written outside the
  issue worktree;
- artifact prompt path reporting after worktree execution, where the report can
  show `Prompt used: missing` despite a prompt existing in the main repo
  artifacts directory.

### Review, Merge, Integration, Cleanup, and PR Linkage

Current tests:

- `tests/test_review.py`: 75 tests;
- `tests/test_merge.py`: 31 tests;
- `tests/test_integration.py`: 30 tests;
- `tests/test_cleanup.py`: 19 tests;
- `tests/test_pr.py`: 5 tests;
- `tests/test_pr_linkage.py`: 4 tests;
- `tests/test_mutation_boundaries.py`: 6 tests;
- `tests/test_transitions.py`: 8 tests.

Covered well:

- review artifact parsing, validation, gate, and submit planning;
- medium/high risk and scope override surfaces;
- non-author approval checks;
- integration issue closure ownership;
- cleanup idempotence for merged PRs;
- transition comments and mutation boundaries.

Remaining gaps:

- review execute fallback where the selected reviewer model is unsupported and
  manual takeover artifacts must replace canonical raw/summary;
- stale PR linkage when a remote branch has been deleted before cleanup;
- merge plan/apply consistency for repeated medium-scope docs-only PRs;
- integration behavior when main CI is delayed, missing, or ambiguous rather
  than clearly pass/fail.

### Runner, Backend, Roles, and Token Efficiency

Current tests:

- `tests/test_runner.py`: 68 tests;
- `tests/test_codex_cli_backend.py`: 12 tests;
- `tests/test_execution_backend.py`: 6 tests;
- `tests/test_backend_status.py`: 4 tests;
- `tests/test_role_policy.py`: 15 tests;
- `tests/test_role_routing.py`: 13 tests;
- `tests/test_role_smoke.py`: 11 tests;
- `tests/test_router_conservative_defaults.py`: 7 tests;
- `tests/test_router_role_coverage_matrix.py`: 5 tests;
- `tests/test_token_usage.py`: 3 tests;
- OpenClaw compatibility tests: `tests/test_openclaw_diagnostics.py`,
  `tests/test_openclaw_preflight.py`, and `tests/test_openclaw_runtime.py`.

Covered well:

- compact worker/reviewer prompt contracts;
- Codex CLI command construction and summary metadata;
- role policy and conservative routing defaults;
- backend status visibility and token usage fields.

Remaining gaps:

- provider/account mismatch classification for models that are configured but
  not usable with the current account;
- fallback routing from `xai/grok-build-0.1` to a configured cheaper fallback
  before manual takeover;
- token usage aggregation across an entire roadmap wave;
- artifact placement consistency for `run --execute --worktree`, which currently
  wrote issue #373 and #374 runtime artifacts under the main repository
  `artifacts/runs` directory while the work happened in an issue worktree.

### Worktree, Git, Sync, and Local Validation

Current tests:

- `tests/test_worktree.py`: 21 tests;
- `tests/test_git_utils.py`: 6 tests;
- `tests/test_sync.py`: 14 tests;
- `tests/test_doctor.py`: 15 tests;
- `tests/test_cli.py`: 1 test.

Covered well:

- worktree planning/apply safety;
- branch collisions;
- repository sync planning;
- local validation command discovery;
- bare `signposter` help-only behavior with operator status hint.

Remaining gaps:

- tests for running worktree/lifecycle commands from both the main repo root and
  an issue worktree;
- validation command discovery for docs-only files should prefer `git diff
  --check` over suggesting `ruff check <markdown>`;
- stale issue-provided test paths such as `tests/test_cli_help.py` should be
  detected or reported more clearly by the planner/task generator.

## Prioritized Test Gaps

1. Add tests for canonical manual takeover artifacts replacing unsafe backend
   raw/summary files while preserving `*.codex-runtime.*` diagnostics.
2. Add tests for artifact placement when `run --execute --worktree` is invoked
   from the main repo and from the issue worktree.
3. Add tests for CWD-stable lifecycle/worktree path reporting.
4. Add tests for large-manifest `--sync-github` timeout/report wording.
5. Add tests for delayed or missing main CI during integration planning.
6. Add tests for docs-only validation command discovery.
7. Add tests for provider/account model mismatch fallback routing.
8. Add tests for side-task insertion apply plus return-to-mainline after mixed
   sibling completion.

## Findings

- The suite is broad and currently has strong coverage for the main workflow
  surfaces.
- The most important gaps are edge and recovery behavior, not missing happy-path
  tests.
- Some old `bootstrap` and `skeleton` wording remains in source/docs/tests. This
  matches H050-002/H050-003 and should be handled by later wording tasks rather
  than by this test audit.
- The issue-provided targeted path `tests/test_cli_help.py` is stale; the actual
  CLI help test file is `tests/test_cli.py`.
- No test gap found here blocks completion of this audit, but the takeover and
  artifact-placement gaps should be treated as high-value follow-ups inside the
  existing H050 roadmap.

## Safety

- No code was changed.
- No test was changed.
- No GitHub mutation was performed by this audit implementation.
- No issue was closed by this audit implementation.
- No merge was performed by this audit implementation.
- No execution backend output was trusted as implementation evidence.
- Runtime diagnostics from the attempted worker execution remain local.

## Validation

Targeted validation:

```bash
git diff --check -- docs/audits/h050-004-test-coverage-gap-map.md
```

Full validation before push:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
