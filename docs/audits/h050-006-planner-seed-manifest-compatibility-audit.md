# H050-006 Planner Seed Manifest Compatibility Audit

## Scope

This audit reviews existing seed manifest compatibility checks for partial,
applied, duplicate, and mismatched manifests. It does not change planner code or
tests. The goal is to establish the current compatibility contract before the
next H050 planner-seed hardening tasks.

## Seed Manifest Contract

The active H050 seed manifest uses:

- version: `planner.seed-manifest.v0.1`;
- top-level list: `issues`;
- issue key field: `key`;
- GitHub mapping field: `github_issue`;
- dependency fields: `depends_on`, `dependency_metadata`,
  `github_depends_on`, and `github_dependency_urls`;
- status values observed in seed flow: `dry-run`, `partial`, and `applied`.

This is distinct from the issue-DAG manifest surface in
`src/signposter/issue_manifest.py`, which uses version
`planner.issue-dag-manifest.v0.1` and top-level `tasks`. The two manifest shapes
are both intentional today, but future code must not treat them as
interchangeable.

## Existing Compatibility Checks

`prepare_planner_seed_manifest` and `apply_planner_seed_manifest` currently
cover the important no-duplicate baseline.

### New Manifest

When the seed manifest path does not exist:

- a new manifest is written;
- status is `ready`;
- no GitHub mutation is performed during preparation.

Covered by `test_prepare_planner_seed_manifest_creates_new_manifest`.

### Applied Manifest

When an existing manifest is `applied` and every issue has `github_issue`:

- preparation reuses the existing manifest;
- status is `completed`;
- CLI seed apply is a no-op and does not call `gh issue create`.

Covered by:

- `test_prepare_planner_seed_manifest_reuses_completed_manifest`;
- `test_cli_planner_seed_apply_completed_manifest_is_noop`.

### Partial Manifest

When an existing manifest is `partial` and some issues already have
`github_issue`:

- preparation reuses the manifest;
- apply skips already-created issues;
- missing issues are created only for unmapped entries;
- dependency metadata is refreshed after missing issue numbers are recorded.

Covered by:

- `test_prepare_planner_seed_manifest_reuses_partial_manifest`;
- `test_cli_planner_seed_apply_partial_manifest_continues_missing_only`;
- `test_apply_planner_seed_manifest_stops_on_runner_failure`.

### Incompatible Manifest

Compatibility currently blocks when:

- manifest version differs;
- repo differs;
- plan path differs;
- issue key order differs.

Covered directly for repo mismatch by:

- `test_prepare_planner_seed_manifest_blocks_incompatible_manifest`;
- `test_cli_planner_seed_apply_blocks_incompatible_manifest`.

The version, plan, and key-order checks exist in
`_validate_seed_manifest_compatibility`, but only repo mismatch has explicit CLI
coverage today.

### Duplicate and Invalid Mapping

Idempotence currently blocks when:

- a manifest issue lacks a task key;
- duplicate task keys exist;
- `github_issue` is not an integer;
- two different task keys map to the same GitHub issue number.

Covered by:

- `test_apply_planner_seed_manifest_blocks_duplicate_task_key_before_create`;
- `test_apply_planner_seed_manifest_blocks_duplicate_github_issue_mapping`;
- `test_prepare_planner_seed_manifest_blocks_duplicate_existing_task_key`.

The missing-key and non-integer issue-number branches are present in the code
but do not have direct tests in the seed apply/preparation cluster.

### Missing Body Files

Seed apply blocks before mutation when a missing issue body file is required for
an unmapped issue.

Covered by `test_apply_planner_seed_manifest_blocks_missing_body_files`.

## Compatibility Gaps

The current compatibility layer is good enough for H050 execution, but it is
narrow. These are the real gaps to address in the following planner tasks:

1. Version and plan mismatch should have explicit tests, not only repo mismatch.
2. Existing manifest issue keys are compared by exact ordered list; there is no
   diagnostic that explains added, removed, or reordered keys separately.
3. Existing manifests are not compared for title, labels, body file, body size,
   or dependency changes once keys match.
4. `github_url` is not checked for consistency with `github_issue`.
5. `issue_key_map` is derived during refresh, but stale map contents in an input
   manifest are not explicitly diagnosed before refresh overwrites them.
6. Partial apply failure writes `status: partial`, but current output does not
   show a structured resume plan for the exact next missing key.
7. The seed manifest and issue-DAG manifest have different shapes; operators can
   confuse `.issues[]` and `.tasks[]`, as seen during this audit. That deserves
   clearer CLI/doc wording before more automation depends on both shapes.
8. Label preflight happens in the CLI path, but the core apply function assumes
   labels/body files are already safe; this separation is correct but should
   remain explicit in tests.

## No Immediate Side Task Needed

No new side task is required now.

Reasons:

- duplicate key and duplicate GitHub mapping guards already exist;
- partial and applied manifests are already reusable without duplicate issue
  creation;
- the current H050 manifest is applied, mapped, and usable;
- the gaps above are already aligned with the next H050 planner sequence:
  H050-007 through H050-015.

The correct next task remains H050-007 / issue #377: planner seed dry-run output
determinism.

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
git diff --check -- docs/audits/h050-006-planner-seed-manifest-compatibility-audit.md
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/test_planner.py -q
```

Full validation before push:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```
