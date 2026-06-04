# H050-046 Local Validation Discovery Audit

## Scope

This audit covers how Signposter currently discovers or documents local
validation commands for code, test, and docs-only changes. It is intentionally
documentation-only: no code path, GitHub mutation, issue closure, execution
backend behavior, or unrelated documentation was changed.

## Current Surfaces

### Project-level validation

The active project standard is still explicit and conservative:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```

This standard appears in recent H050 worker summaries, prior audit artifacts,
and the operator lifecycle runbook. It is the only full validation path that
should be considered sufficient before push.

### `signposter doctor validation`

`src/signposter/doctor.py` provides a read-only validation command planner via
`build_validation_command_plan()` and `format_validation_command_plan()`.

Observed behavior:

- changed files are de-duplicated while preserving order;
- targeted ruff defaults to `ruff check <changed-files>`;
- targeted pytest is inferred from changed tests when present;
- full validation is always `ruff check .` and `python -m pytest tests/ -q`;
- output explicitly says no validation command was executed.

This is a useful operator aid, not a gate. It does not execute commands and it
does not prove validation passed.

### Worker and report artifacts

Manual worker summaries accept explicit targeted and full validation command
fields. Report comments include bounded validation excerpts. This is currently
the strongest auditable validation evidence path because it records what was
actually run for a specific issue.

## Gaps

### Docs-only command discovery

For Markdown-only changes, targeted ruff should not be the primary targeted
command. The safer docs-only targeted command is:

```bash
git diff --check -- <changed-docs>
```

Current `doctor validation` does not distinguish docs-only changes from Python
changes. For a docs-only file it can suggest `ruff check docs/...`, which is not
the clearest targeted validation and may mislead operators into thinking a
Markdown whitespace check happened.

### Source-to-test inference

When a source file such as `src/signposter/gate.py` changes and no test file is
provided, targeted pytest falls back conservatively instead of mapping to a
likely `tests/test_gate.py`. That is safe, but less token- and time-efficient
than a simple source/test convention lookup.

### Command wrapper consistency

`doctor validation` prints generic `ruff` and `python -m pytest` commands.
Lifecycle summaries in this repo usually run through the repo venv with
`PYTHONPATH="$PWD/src"`. The generic command is portable, but it is less exact
than the commands used by the Signposter lifecycle loop.

### Artifact path clarity

Worker summaries record commands as strings, but there is no structured
validation result object yet that separates:

- command planned;
- command executed;
- exit code;
- target file set;
- full versus targeted scope.

H050 already has a later task for validation result artifact schema, so this
audit should not implement that schema here.

## Recommended Follow-up

1. Add docs-only validation discovery:
   - if all changed files are documentation or Markdown, targeted validation
     should include `git diff --check -- <files>`;
   - targeted ruff should either be omitted from the targeted section or moved
     to full validation only.

2. Add simple source-to-test inference:
   - `src/signposter/<name>.py` should suggest `tests/test_<name>.py` when the
     test file exists;
   - unknown mappings should remain conservative.

3. Keep full validation unchanged:
   - `ruff check .`;
   - `python -m pytest tests/ -q`.

4. Keep command discovery read-only:
   - no validation execution;
   - no GitHub mutation;
   - no manifest mutation.

5. Defer structured validation result artifacts to the dedicated H050 schema
   task.

## Validation

Targeted validation for this documentation-only audit:

```bash
git diff --check -- docs/audits/h050-046-local-validation-discovery-audit.md
```

Full validation before push remains:

```bash
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/ruff check .
PYTHONPATH="$PWD/src" /home/probo/projects/signposter/.venv/bin/python -m pytest tests/ -q
```

## Status

No code bug was fixed in this task. The current validation discovery surface is
safe and read-only, but it is not precise enough for docs-only changes or cheap
source-to-test targeted validation. The recommended follow-up is to improve
`signposter doctor validation` in a separate implementation task.
