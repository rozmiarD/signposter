# H049-007 Planner Manifest Schema Audit

Date: 2026-06-02
Repository: ExatronOmega/signposter
Base commit: `533d8b8`

## Purpose

This audit records the current planner plan and seed-manifest schema before H049
extends DAG behavior, manifest sync, side-task insertion, and return-to-mainline
flows.

The scoped question:

Which manifest fields are required today, which fields are computed or
tolerated, and where does schema/versioning need hardening before more
automation depends on it?

No runtime code is changed by this audit.

## Current Schema Layers

Signposter currently has two related but distinct local JSON structures:

- planner plan: `planner.v0.1`
- seed manifest: `planner.seed-manifest.v0.1`

The planner plan is the operator-authored or generated roadmap definition.
The seed manifest is the materialized bridge between task keys and GitHub
issues.

H049 uses:

- `/tmp/signposter-h049-plan.json`
- `/tmp/signposter-h049-manifest.json`

## Planner Plan Schema

The plan schema is validated by `validate_planner_plan`.

Required top-level fields:

- `version`
- `goal`
- `issues`

Required issue fields:

- `key`
- `title`
- `body`
- `phase`
- `risk`
- `role`
- `area`
- `depends_on`
- `acceptance`
- `stop_conditions`
- `allowed_mutations`

Validation behavior:

- plan version must match `planner.v0.1`;
- goal must be non-empty;
- issue keys must be unique;
- dependencies must reference known issue keys;
- `depends_on`, `acceptance`, and `stop_conditions` must be lists;
- `allowed_mutations` must be empty for local draft plans;
- task status, if present, must be one of the allowed planner task statuses;
- auto-close keywords are rejected from searchable task content.

This is a strong enough base for local roadmap validation.

## Seed Manifest Schema

The seed manifest is built by `build_planner_seed_manifest`.

Required top-level fields in practice:

- `version`
- `plan`
- `repo`
- `status`
- `issues`
- `notes`

Required issue fields in practice:

- `key`
- `title`
- `labels`
- `depends_on`
- `body_file`
- `body_size`
- `github_issue`
- `github_url`
- `mainline`
- `parent`
- `return_to`
- `side_task`

Computed issue fields:

- `dependency_metadata`
- `github_depends_on`
- `github_dependency_urls`

Computed top-level field:

- `issue_key_map`

The current manifest status values observed in code are:

- `dry-run`
- `partial`
- `applied`

Planner status surfaces then derive higher-level roadmap status values such as:

- `empty`
- `unseeded`
- `partial`
- `active`
- `completed`

## Tolerated Extras

The seed manifest currently tolerates extra fields. That is useful for H049
because dynamic side-task metadata can be represented without a broad rewrite:

- `mainline`
- `parent`
- `return_to`
- `side_task`

H049-S001 / #291 used those fields successfully:

- `mainline`: `H049`
- `parent`: `210`
- `return_to`: `212`
- `side_task`: `true`

This is useful, but the semantics are not yet fully formalized.

## Compatibility Checks

`prepare_planner_seed_manifest` validates an existing manifest against a newly
generated expected manifest using `_validate_seed_manifest_compatibility`.

Currently checked:

- manifest version;
- repo;
- plan path;
- issue key ordering.

Currently not fully checked:

- issue labels;
- dependency list changes;
- side-task metadata;
- body file paths;
- body size metadata;
- GitHub issue number consistency;
- status transitions;
- unknown extra field policy.

This is acceptable for guarded seeding, but too weak for future autonomous
manifest reconciliation.

## Dependency Metadata Behavior

`_refresh_seed_manifest_dependency_metadata` materializes key dependencies into
GitHub issue metadata:

- each `depends_on` key gets a dependency metadata entry;
- `github_depends_on` includes resolved issue numbers;
- `github_dependency_urls` includes resolved issue URLs;
- `issue_key_map` maps task key to GitHub issue number.

This is the right direction.

Current gap:

- this metadata lives in the local manifest;
- seeded GitHub issue bodies do not yet round-trip these dependencies as
  parseable `Depends-On: #N` lines after issue numbers are known.

That means the manifest can drive H049 correctly, but the repository-wide
scheduler graph cannot yet reconstruct the full manifest graph from GitHub
issues alone.

## Idempotence Behavior

Useful current behavior:

- existing applied manifests are treated as completed/no-op by seed
  preparation;
- partial manifests are reused so missing issues can be continued;
- apply updates the manifest with created issue numbers and URLs;
- dependency metadata is refreshed after issue numbers are known;
- body files are checked before issue creation;
- issue creation stops and writes partial state when a create operation cannot
  finish.

Risk:

- manifest compatibility is key-order based and does not deeply compare schema
  fields that matter for future reconciliation.
- manual H049 side-task insertion required direct local manifest editing.

## H049 Manifest Snapshot

The active H049 manifest now has:

- initial nodes: 80
- side nodes: 1
- total nodes: 81
- initial GitHub issue range: #208 through #287
- side issue: #291
- active workstream roots after H049-006: #214, #226, #239, #249, #260, #271

This confirms the manifest can represent parallel workstream roots and side
tasks, but it also confirms that first-class side-task insertion still needs a
guarded command surface.

## Findings

### Strengths

- Plan validation is explicit and deterministic.
- Seed manifests have a stable version string.
- Manifest preparation is idempotent for applied manifests.
- Partial seed state is preserved.
- Dependency metadata is materialized after issue numbers are known.
- Side-task metadata can be represented without a new top-level concept.

### Gaps

- Seed manifest validation is weaker than plan validation.
- There is no standalone `planner manifest validate` command.
- There is no formal manifest schema document or typed model.
- Unknown extra fields are tolerated but not documented.
- Side-task fields are represented but not fully guarded.
- Dependency metadata does not round-trip back into GitHub issue bodies.
- Manifest mutation after dynamic side-task discovery is manual.

## Recommended Follow-Up

H049 should keep the roadmap direction and implement these later tasks:

1. Add a manifest schema/versioning hardening task before broad reconcile
   automation.
2. Add a guarded side-task insertion path that updates plan and manifest
   together.
3. Add dependency metadata round-trip from manifest to issue body.
4. Add manifest compatibility checks for dependency and side-task field
   changes.
5. Add tests for unknown extra fields so future behavior is explicit.

## Validation

This audit is documentation-only. Required validation:

- `ruff check .`
- `python -m pytest tests/ -q`

No GitHub mutation is performed by this committed audit file.
