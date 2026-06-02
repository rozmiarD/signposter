"""Local planner draft and validation surfaces for Signposter."""

from __future__ import annotations

import json
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLAN_VERSION = "planner.v0.1"
AUTO_CLOSE_RE = re.compile(r"\b(closes|fixes|resolves)\s+#\d+", re.IGNORECASE)

WORKER_ISSUE_PREFERRED_MIN_LINES = 60
WORKER_ISSUE_PREFERRED_MAX_LINES = 120
WORKER_ISSUE_HARD_MAX_LINES = 165
WORKER_ISSUE_HARD_MAX_CHARS = 12000

STOP_CONDITIONS = [
    "ruff check fails",
    "targeted pytest fails",
    "full pytest fails",
    "CI fails",
    "GitHub mutation is requested without --apply",
    "OpenClaw execution is requested without --execute",
    "PR body contains auto-close keywords",
    "merge plan would close an issue",
]


def build_planner_draft(goal: str) -> dict[str, Any]:
    """Build a deterministic local-only planner draft."""
    goal = goal.strip()
    if not goal:
        raise ValueError("planner goal must not be empty")

    return {
        "version": PLAN_VERSION,
        "goal": goal,
        "mode": "supervised",
        "status": "draft",
        "mutation_policy": "local draft only; no GitHub mutation; no OpenClaw execution",
        "required_capabilities": [
            "read-only lifecycle inspection",
            "issue and PR state summarization",
            "gate and CI status visibility",
            "local worktree and branch visibility",
            "compact terminal output",
        ],
        "issues": [
            _issue("WATCH-001", "Define lifecycle watch CLI contract", "cli", []),
            _issue(
                "WATCH-002",
                "Add read-only lifecycle watch data collector",
                "cli",
                ["WATCH-001"],
            ),
            _issue(
                "WATCH-003",
                "Add simple terminal refresh renderer",
                "cli",
                ["WATCH-002"],
            ),
            _issue("WATCH-004", "Add lifecycle watch tests", "tests", ["WATCH-003"]),
            _issue(
                "WATCH-005",
                "Document lifecycle watch operator usage",
                "docs",
                ["WATCH-004"],
            ),
        ],
    }


def write_planner_draft(goal: str, output_path: Path) -> dict[str, Any]:
    """Write a planner draft JSON file and return the plan."""
    plan = build_planner_draft(goal)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return plan


def load_planner_plan(plan_path: Path) -> dict[str, Any]:
    """Load a planner JSON file."""
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("planner file must contain a JSON object")
    return data


def validate_planner_plan(plan: dict[str, Any]) -> list[str]:
    """Return validation errors for a planner plan."""
    errors: list[str] = []

    _require(plan, "version", str, errors)
    _require(plan, "goal", str, errors)
    _require(plan, "issues", list, errors)

    if plan.get("version") != PLAN_VERSION:
        errors.append(f"version must be {PLAN_VERSION}")

    if not str(plan.get("goal", "")).strip():
        errors.append("goal must not be empty")

    issues = plan.get("issues")
    if not isinstance(issues, list) or not issues:
        errors.append("issues must be a non-empty list")
        return errors

    keys: set[str] = set()
    for index, issue in enumerate(issues, start=1):
        if not isinstance(issue, dict):
            errors.append(f"issue #{index} must be an object")
            continue

        key = str(issue.get("key", f"#{index}"))
        if key in keys:
            errors.append(f"{key}: duplicate issue key")
        keys.add(key)

        for field in [
            "key",
            "title",
            "body",
            "phase",
            "risk",
            "role",
            "area",
            "depends_on",
            "acceptance",
            "stop_conditions",
            "allowed_mutations",
        ]:
            if field not in issue:
                errors.append(f"{key}: missing {field}")

        _list_required(issue, "depends_on", key, errors)
        _list_required(issue, "acceptance", key, errors)
        _list_required(issue, "stop_conditions", key, errors)

        if issue.get("allowed_mutations") != []:
            errors.append(f"{key}: allowed_mutations must be empty for local draft plans")

        status = str(issue.get("status", "pending")).strip().lower()
        if status not in ALLOWED_TASK_STATUSES:
            allowed = ", ".join(sorted(ALLOWED_TASK_STATUSES))
            errors.append(f"{key}: status must be one of {allowed}")

        searchable = " ".join(
            str(issue.get(field, "")) for field in ["title", "body", "acceptance"]
        )
        if AUTO_CLOSE_RE.search(searchable):
            errors.append(f"{key}: contains auto-close keyword")

    for issue in issues:
        if isinstance(issue, dict):
            key = str(issue.get("key", "unknown"))
            for dependency in issue.get("depends_on", []):
                if dependency not in keys:
                    errors.append(f"{key}: unknown dependency {dependency}")

    return errors




DONE_STATUSES = {"done"}
BLOCKED_STATUSES = {"blocked", "failed"}
ALLOWED_TASK_STATUSES = {"pending", "active", "done", "blocked", "failed"}



def mark_planner_task(
    plan_path: Path,
    task_key: str,
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a task status in a local planner JSON file."""
    status = status.strip().lower()
    if status not in ALLOWED_TASK_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_TASK_STATUSES))
        return {
            "status": "blocked",
            "errors": [f"status must be one of {allowed}"],
            "task": task_key,
            "task_status": status,
        }

    plan = load_planner_plan(plan_path)
    errors = validate_planner_plan(plan)
    if errors:
        return {
            "status": "blocked",
            "errors": errors,
            "task": task_key,
            "task_status": status,
        }

    target = None
    for issue in plan["issues"]:
        if issue["key"] == task_key:
            target = issue
            break

    if target is None:
        return {
            "status": "blocked",
            "errors": [f"unknown task {task_key}"],
            "task": task_key,
            "task_status": status,
        }

    target["status"] = status
    if reason:
        target["status_reason"] = reason
    else:
        target.pop("status_reason", None)
    target["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")

    post_errors = validate_planner_plan(plan)
    if post_errors:
        return {
            "status": "blocked",
            "errors": post_errors,
            "task": task_key,
            "task_status": status,
        }

    plan_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "updated",
        "errors": [],
        "task": task_key,
        "task_status": status,
        "reason": reason or "",
    }


def format_planner_mark_result(plan_path: Path, result: dict[str, Any]) -> str:
    """Format local planner mark result."""
    lines = [
        "Signposter Planner Mark",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Task:",
        f"  {result['task']}",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Task status:",
        f"  {result['task_status']}",
    ]

    if result.get("reason"):
        lines.extend(["", "Reason:", f"  {result['reason']}"])

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Safety:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)



def format_gh_issue_create_command(
    *,
    repo: str,
    title: str,
    body_file: Path,
    labels: list[str],
) -> str:
    """Format a future gh issue create command without executing it."""
    args = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body-file",
        str(body_file),
    ]
    for label in labels:
        args.extend(["--label", label])

    quoted = [shlex.quote(arg) for arg in args]
    return " \\\n  ".join(quoted)


def build_planner_next(plan: dict[str, Any]) -> dict[str, Any]:
    """Choose the next dependency-ready issue from a planner plan."""
    errors = validate_planner_plan(plan)
    if errors:
        return {
            "status": "blocked",
            "reason": "plan validation failed",
            "errors": errors,
            "next": None,
        }

    issues = plan["issues"]
    completed = {
        issue["key"]
        for issue in issues
        if _issue_status(issue) in DONE_STATUSES
    }
    remaining = [
        issue
        for issue in issues
        if _issue_status(issue) not in DONE_STATUSES
    ]

    if not remaining:
        return {
            "status": "completed",
            "reason": "all issues are completed",
            "errors": [],
            "next": None,
        }

    blocked = [
        issue["key"]
        for issue in remaining
        if _issue_status(issue) in BLOCKED_STATUSES
    ]

    for issue in remaining:
        status = _issue_status(issue)
        if status in BLOCKED_STATUSES:
            continue

        missing_dependencies = [
            dependency
            for dependency in issue["depends_on"]
            if dependency not in completed
        ]
        if missing_dependencies:
            continue

        return {
            "status": "ready",
            "reason": "first dependency-ready issue selected",
            "errors": [],
            "next": {
                "key": issue["key"],
                "title": issue["title"],
                "status": status,
                "depends_on": issue["depends_on"],
            },
        }

    return {
        "status": "waiting",
        "reason": "no dependency-ready issue is available",
        "errors": [f"blocked issue: {key}" for key in blocked],
        "next": None,
    }


def format_planner_next(plan_path: Path, next_plan: dict[str, Any]) -> str:
    """Format planner next result."""
    lines = [
        "Signposter Planner Next",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Status:",
        f"  {next_plan['status']}",
        "",
        "Reason:",
        f"  {next_plan['reason']}",
    ]

    if next_plan["next"]:
        issue = next_plan["next"]
        deps = ", ".join(issue["depends_on"]) if issue["depends_on"] else "none"
        lines.extend(
            [
                "",
                "Next issue:",
                f"  {issue['key']} — {issue['title']}",
                f"  status: {issue['status']}",
                f"  depends on: {deps}",
            ]
        )

    if next_plan["errors"]:
        lines.extend(["", "Notes:"])
        lines.extend(f"  - {error}" for error in next_plan["errors"])

    lines.extend(
        [
            "",
            "Safety:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)



def evaluate_worker_issue_body_size(body: str) -> dict[str, Any]:
    """Evaluate whether a worker issue body fits the bounded task policy."""
    line_count = len(body.splitlines())
    char_count = len(body)
    warnings: list[str] = []
    errors: list[str] = []

    if line_count > WORKER_ISSUE_HARD_MAX_LINES:
        errors.append(
            f"issue body has {line_count} lines; hard max is "
            f"{WORKER_ISSUE_HARD_MAX_LINES}; split into A/B/C"
        )
    elif line_count > WORKER_ISSUE_PREFERRED_MAX_LINES:
        warnings.append(
            f"issue body has {line_count} lines; preferred max is "
            f"{WORKER_ISSUE_PREFERRED_MAX_LINES}"
        )
    elif line_count < WORKER_ISSUE_PREFERRED_MIN_LINES:
        warnings.append(
            f"issue body has {line_count} lines; preferred min is "
            f"{WORKER_ISSUE_PREFERRED_MIN_LINES}"
        )

    if char_count > WORKER_ISSUE_HARD_MAX_CHARS:
        errors.append(
            f"issue body has {char_count} chars; hard max is "
            f"{WORKER_ISSUE_HARD_MAX_CHARS}; split into A/B/C"
        )

    if errors:
        status = "blocked"
    elif warnings:
        status = "warning"
    else:
        status = "pass"

    return {
        "status": status,
        "line_count": line_count,
        "char_count": char_count,
        "warnings": warnings,
        "errors": errors,
    }



def format_planner_roadmap(plan: dict[str, Any]) -> str:
    """Format a generic planner-level roadmap template.

    This is not a worker issue body and does not render concrete issue DAG
    entries. Concrete worker tasks belong to seed/next/issue-body surfaces.
    """
    errors = validate_planner_plan(plan)
    if errors:
        return "\n".join(
            [
                "Roadmap Template",
                "",
                "Status:",
                "blocked",
                "",
                "Validation errors:",
                _markdown_bullets(errors),
            ]
        )

    return "\n".join(
        [
            "Roadmap Template",
            "",
            "User goal:",
            f"{plan['goal']}",
            "",
            "Purpose:",
            "Convert a broad user goal into a bounded, reviewable, dependency-aware plan.",
            "",
            "Roadmap role:",
            "* Describe strategy, scope, sequencing, risk, and validation.",
            "* Decide what must become worker-ready tasks.",
            "* Keep architecture-level decisions separate from execution details.",
            "* Prevent broad work from becoming one oversized worker issue.",
            "",
            "Outcome:",
            "* Clear implementation direction.",
            "* Explicit non-goals and boundaries.",
            "* A later issue DAG made of small worker-ready tasks.",
            "* Validation and stop conditions before any execution.",
            "",
            "Non-goals:",
            "* Do not execute worker tasks from the roadmap document.",
            "* Do not mutate GitHub from the roadmap document.",
            "* Do not run OpenClaw from the roadmap document.",
            "* Do not include full worker issue bodies inside the roadmap.",
            "* Do not hard-code product-specific task names in the roadmap template.",
            "",
            "Planning sections:",
            "* Intent and desired end state.",
            "* Scope and non-goals.",
            "* Assumptions and constraints.",
            "* Required capabilities.",
            "* Proposed milestones.",
            "* Dependency strategy.",
            "* Risk model.",
            "* Validation strategy.",
            "* Stop conditions.",
            "* Follow-up and branching policy.",
            "* Done definition.",
            "",
            "Milestone model:",
            "* M1 — clarify intent, outcome, and boundaries.",
            "* M2 — identify required capabilities.",
            "* M3 — split work into small worker-ready tasks.",
            "* M4 — define dependencies and ordering.",
            "* M5 — validate task size, safety, and acceptance criteria.",
            "* M6 — execute one task at a time through guarded workflow.",
            "",
            "Worker task sizing policy:",
            f"* Preferred range: {WORKER_ISSUE_PREFERRED_MIN_LINES}–"
            f"{WORKER_ISSUE_PREFERRED_MAX_LINES} lines.",
            f"* Hard max: {WORKER_ISSUE_HARD_MAX_LINES} lines.",
            f"* Hard max chars: {WORKER_ISSUE_HARD_MAX_CHARS}.",
            "* Split larger work into A/B/C follow-up tasks.",
            "",
            "Risk model:",
            "* low — small bounded local change.",
            "* medium — broader refactor or guarded GitHub mutation.",
            "* high — secrets, auth, CI, release, destructive action, or external side effect.",
            "",
            "Mutation policy:",
            "* GitHub mutation only with --apply.",
            "* OpenClaw execution only with --execute.",
            "* Merge must not close issues.",
            "* Issue closure belongs to integration apply.",
            "",
            "Validation strategy:",
            "* ruff check .",
            "* targeted pytest for changed surface.",
            "* full pytest.",
            "* real CLI smoke command.",
            "* CI after push.",
            "",
            "Stop conditions:",
            _markdown_bullets(STOP_CONDITIONS),
            "",
            "Follow-up policy:",
            "* Create follow-up tasks when scope exceeds worker task limits.",
            "* Use dependencies instead of embedding blockers inside oversized tasks.",
            "* Return to pending DAG items after blockers are resolved.",
            "",
            "Done definition:",
            "* Roadmap has a clear issue DAG candidate.",
            "* Worker tasks fit the sizing policy.",
            "* Blockers and risks are explicit.",
            "* Required validation strategy is defined.",
            "* No unintended GitHub mutation or OpenClaw execution occurred.",
        ]
    )


def format_planner_issue_body(plan: dict[str, Any], issue: dict[str, Any]) -> str:
    """Format a planner task as a bounded GitHub issue body."""
    dependencies = issue.get("depends_on", [])
    dependency_lines = _markdown_bullets(dependencies, fallback="none")
    dependency_metadata_lines = _format_issue_dependency_metadata(dependencies)
    acceptance_lines = _markdown_bullets(issue.get("acceptance", []))
    stop_condition_lines = _markdown_bullets(issue.get("stop_conditions", []))

    return "\n".join(
        [
            f"Task: {issue['key']} — {issue['title']}",
            "",
            "Context:",
            "Signposter status:",
            "",
            "* Planner draft / validate / seed / next / mark surfaces exist locally.",
            "* Current planner mode is supervised and local-first.",
            "* GitHub mutation is forbidden unless explicitly guarded by --apply.",
            "* OpenClaw execution is forbidden unless explicitly guarded by --execute.",
            f"* Source plan goal: {plan['goal']}",
            "",
            "Problem:",
            issue["body"],
            "",
            "Goal:",
            f"Complete this narrow task: {issue['title']}.",
            "",
            "Target command:",
            "```bash",
            _target_command_for_issue(issue),
            "```",
            "",
            "Expected output:",
            "```text",
            "Signposter <Feature>",
            "",
            "Status:",
            "ready / blocked / pending / completed",
            "",
            "Notes:",
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
            "```",
            "",
            "Scope:",
            "* Do this exact thing.",
            "* Do not do unrelated refactors.",
            "* Do not mutate GitHub unless explicitly required and guarded by --apply.",
            "* Do not close issues unless this task is specifically about issue close apply.",
            "* Do not run OpenClaw unless this task is specifically about execution.",
            "* Keep output compact and deterministic.",
            "",
            "Dependencies:",
            dependency_lines,
            "",
            "Dependency metadata:",
            dependency_metadata_lines,
            "",
            "Rules:",
            "1. If a required precondition is missing, block safely.",
            "2. If status is blocked, do not show misleading ready/apply wording.",
            "3. If a mutation step fails, stop and report partial state clearly.",
            "4. Never continue to later mutations after an earlier critical mutation failed.",
            "5. Never print secrets or tokens.",
            "",
            "Implementation guidance:",
            "* Prefer modifying existing Signposter modules where practical.",
            "* Add a new module only if the feature is clearly separate.",
            "* Keep helper functions small.",
            "* Keep CLI output human-readable and deterministic.",
            "* Capture stderr/stdout for failed subprocesses when subprocesses are used.",
            "",
            "Tests:",
            "* Add or update targeted tests for the changed surface.",
            "* Cover happy path / ready path.",
            "* Cover blocked path for missing or unsafe preconditions.",
            "* Cover safety notes in output.",
            "",
            "Acceptance:",
            acceptance_lines,
            "* ruff check . passes.",
            "* python -m pytest tests/ -q passes.",
            "* No unintended GitHub mutations.",
            "* No unrelated files changed.",
            "* Existing flows unchanged.",
            "",
            "Stop conditions:",
            stop_condition_lines,
            "",
            "Report back:",
            "* whether a code bug was found",
            "* chosen CLI shape",
            "* files changed",
            "* tests added/updated",
            "* sample ready output",
            "* sample blocked output",
            "* ruff/pytest result",
        ]
    )


def build_planner_seed_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Build a dry-run issue seed plan from a validated planner plan."""
    errors = validate_planner_plan(plan)
    if errors:
        return {"status": "blocked", "errors": errors, "issues": []}

    issues = []
    for issue in plan["issues"]:
        body = format_planner_issue_body(plan, issue)
        body_size = evaluate_worker_issue_body_size(body)
        labels = [
            f"phase:{issue['phase']}",
            f"risk:{issue['risk']}",
            f"role:{issue['role']}",
            f"area:{issue['area']}",
        ]
        if not issue["depends_on"]:
            labels.append("state:ready")

        issues.append(
            {
                "key": issue["key"],
                "title": issue["title"],
                "github_title": f"{issue['key']} — {issue['title']}",
                "labels": labels,
                "depends_on": issue["depends_on"],
                "body": body,
                "body_size": body_size,
            }
        )

    body_errors = [
        f"{issue['key']}: {error}"
        for issue in issues
        for error in issue["body_size"]["errors"]
    ]
    if body_errors:
        return {"status": "blocked", "errors": body_errors, "issues": issues}

    return {"status": "ready", "errors": [], "issues": issues}


def _format_issue_dependency_metadata(dependencies: list[str]) -> str:
    if not dependencies:
        return "* none"

    lines: list[str] = []
    for dependency in dependencies:
        lines.extend(
            [
                f"* key: {dependency}",
                "  github issue: assigned during guarded seed apply",
                "  status: pending",
            ]
        )
    return "\n".join(lines)







def validate_seed_plan_labels(
    seed_plan: dict[str, Any],
    existing_labels: set[str],
) -> dict[str, Any]:
    """Validate that every label required by a seed plan exists."""
    required_labels = sorted(
        {
            label
            for issue in seed_plan.get("issues", [])
            for label in issue.get("labels", [])
        }
    )
    missing_labels = [
        label for label in required_labels if label not in existing_labels
    ]

    if missing_labels:
        return {
            "status": "blocked",
            "required_labels": required_labels,
            "missing_labels": missing_labels,
            "errors": [
                f"missing GitHub label: {label}" for label in missing_labels
            ],
        }

    return {
        "status": "ready",
        "required_labels": required_labels,
        "missing_labels": [],
        "errors": [],
    }


def format_seed_label_preflight(result: dict[str, Any]) -> str:
    """Format seed label preflight result."""
    lines = [
        "",
        "Seed Label Preflight",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Required labels:",
    ]
    lines.extend(f"  - {label}" for label in result["required_labels"])

    if result["missing_labels"]:
        lines.extend(["", "Missing labels:"])
        lines.extend(f"  - {label}" for label in result["missing_labels"])

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub issue was created.",
            "  No OpenClaw execution was performed.",
        ]
    )
    return "\n".join(lines)


def format_planner_seed_apply_result(
    manifest_path: Path,
    result: dict[str, Any],
) -> str:
    """Format guarded seed apply result."""
    lines = [
        "",
        "Planner Seed Apply",
        "",
        "Manifest:",
        f"  {manifest_path}",
        "",
        "Status:",
        f"  {result['status']}",
    ]

    if result["created"]:
        lines.extend(["", "Created GitHub issues:"])
        for issue in result["created"]:
            url = issue.get("github_url") or ""
            lines.append(f"  {issue['key']} -> #{issue['github_issue']} {url}".rstrip())

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  GitHub mutation is only performed when --apply is explicitly used.",
            "  OpenClaw execution was not performed.",
            "  Task execution was not performed.",
        ]
    )
    return "\n".join(lines)


def apply_planner_seed_manifest(
    manifest_path: Path,
    runner: Any,
) -> dict[str, Any]:
    """Apply a seed manifest using an injected command runner.

    This core function is intentionally runner-injected so tests can use a fake
    runner. The CLI layer must remain responsible for guarding real execution
    behind explicit --apply.
    """
    manifest = _refresh_seed_manifest_dependency_metadata(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    repo = manifest.get("repo", "")
    issues = manifest.get("issues", [])
    idempotence_errors = _validate_seed_manifest_idempotence(manifest)
    if idempotence_errors:
        return {
            "status": "blocked",
            "created": [],
            "errors": idempotence_errors,
        }

    missing_body_files = [
        issue["body_file"]
        for issue in issues
        if issue.get("github_issue") is None and not Path(issue["body_file"]).exists()
    ]
    if missing_body_files:
        return {
            "status": "blocked",
            "created": [],
            "errors": [
                f"missing body file: {body_file}" for body_file in missing_body_files
            ],
        }

    created: list[dict[str, Any]] = []
    for issue in issues:
        if issue.get("github_issue") is not None:
            continue

        args = _build_gh_issue_create_args(
            repo=repo,
            title=issue["title"],
            body_file=Path(issue["body_file"]),
            labels=issue["labels"],
        )
        result = runner(args)
        returncode = int(getattr(result, "returncode", 1))
        stdout = str(getattr(result, "stdout", "") or "")
        stderr = str(getattr(result, "stderr", "") or "")
        output = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)

        if returncode != 0:
            manifest["status"] = "partial"
            write_planner_seed_manifest(manifest, manifest_path)
            return {
                "status": "failed",
                "created": created,
                "errors": [_bounded_error(output or "gh issue create failed")],
            }

        issue_number = _parse_github_issue_number(output)
        if issue_number is None:
            manifest["status"] = "partial"
            write_planner_seed_manifest(manifest, manifest_path)
            return {
                "status": "failed",
                "created": created,
                "errors": [_bounded_error("could not parse created issue number")],
            }

        issue_url = _parse_github_issue_url(output)
        issue["github_issue"] = issue_number
        issue["github_url"] = issue_url
        _refresh_seed_manifest_dependency_metadata(manifest)
        created.append(
            {
                "key": issue["key"],
                "github_issue": issue_number,
                "github_url": issue_url,
            }
        )
        manifest["status"] = "partial"
        write_planner_seed_manifest(manifest, manifest_path)

    _refresh_seed_manifest_dependency_metadata(manifest)
    manifest["status"] = "applied"
    manifest["applied_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    write_planner_seed_manifest(manifest, manifest_path)
    return {"status": "applied", "created": created, "errors": []}


def _build_gh_issue_create_args(
    *,
    repo: str,
    title: str,
    body_file: Path,
    labels: list[str],
) -> list[str]:
    args = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body-file",
        str(body_file),
    ]
    for label in labels:
        args.extend(["--label", label])
    return args


def _parse_github_issue_number(output: str) -> int | None:
    match = re.search(r"/issues/(\d+)\b", output)
    if not match:
        return None
    return int(match.group(1))


def _parse_github_issue_url(output: str) -> str:
    match = re.search(r"https://github\.com/\S+/issues/\d+", output)
    if not match:
        return output.strip()
    return match.group(0)


def _bounded_error(message: str, limit: int = 500) -> str:
    message = message.strip()
    if len(message) <= limit:
        return message
    return message[:limit].rstrip() + "..."




def format_prepared_seed_manifest(
    manifest_path: Path,
    result: dict[str, Any],
) -> str:
    """Format seed manifest preparation/idempotence result."""
    lines = [
        "",
        "Prepared seed manifest:",
        f"  {manifest_path}",
        "",
        "Status:",
        f"  {result['status']}",
    ]

    if result.get("reused_existing"):
        lines.extend(["", "Existing manifest:", "  reused"])
    else:
        lines.extend(["", "Existing manifest:", "  none — created"])

    if result["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  Existing applied manifests are treated as completed/no-op.",
            "  Partial manifests are reused so missing issues can be continued.",
            "  No GitHub mutation was performed during manifest preparation.",
        ]
    )
    return "\n".join(lines)


def prepare_planner_seed_manifest(
    *,
    plan_path: Path,
    repo: str,
    seed_plan: dict[str, Any],
    body_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Prepare or reuse a seed manifest without creating duplicate issues."""
    new_manifest = build_planner_seed_manifest(
        plan_path=plan_path,
        repo=repo,
        seed_plan=seed_plan,
        body_dir=body_dir,
    )

    if not manifest_path.exists():
        write_planner_seed_manifest(new_manifest, manifest_path)
        return {
            "status": "ready",
            "manifest": new_manifest,
            "errors": [],
            "reused_existing": False,
        }

    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    _refresh_seed_manifest_dependency_metadata(existing)
    errors = _validate_seed_manifest_compatibility(
        existing=existing,
        expected=new_manifest,
    )
    errors.extend(_validate_seed_manifest_idempotence(existing))
    if errors:
        return {
            "status": "blocked",
            "manifest": existing,
            "errors": errors,
            "reused_existing": True,
        }

    if _seed_manifest_is_applied(existing):
        return {
            "status": "completed",
            "manifest": existing,
            "errors": [],
            "reused_existing": True,
        }

    return {
        "status": "ready",
        "manifest": existing,
        "errors": [],
        "reused_existing": True,
    }


def _validate_seed_manifest_compatibility(
    *,
    existing: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    errors: list[str] = []

    if existing.get("version") != expected.get("version"):
        errors.append("manifest version mismatch")
    if existing.get("repo") != expected.get("repo"):
        errors.append("manifest repo mismatch")
    if existing.get("plan") != expected.get("plan"):
        errors.append("manifest plan mismatch")

    existing_keys = [issue.get("key") for issue in existing.get("issues", [])]
    expected_keys = [issue.get("key") for issue in expected.get("issues", [])]
    if existing_keys != expected_keys:
        errors.append("manifest issue key mismatch")

    return errors


def _validate_seed_manifest_idempotence(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    keys_seen: set[str] = set()
    issues_by_number: dict[int, str] = {}

    for issue in manifest.get("issues", []):
        key = str(issue.get("key", "")).strip()
        if not key:
            errors.append("seed manifest contains an issue without a task key")
            continue

        if key in keys_seen:
            errors.append(f"duplicate task key in seed manifest: {key}")
        keys_seen.add(key)

        github_issue = issue.get("github_issue")
        if github_issue is None:
            continue

        try:
            github_issue_number = int(github_issue)
        except (TypeError, ValueError):
            errors.append(f"{key}: github_issue must be an integer")
            continue

        previous_key = issues_by_number.get(github_issue_number)
        if previous_key is not None and previous_key != key:
            errors.append(
                f"duplicate GitHub issue mapping: #{github_issue_number} "
                f"is assigned to {previous_key} and {key}"
            )
        issues_by_number[github_issue_number] = key

    return errors


def _seed_manifest_is_applied(manifest: dict[str, Any]) -> bool:
    issues = manifest.get("issues", [])
    return (
        manifest.get("status") == "applied"
        and bool(issues)
        and all(issue.get("github_issue") is not None for issue in issues)
    )


def _refresh_seed_manifest_dependency_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    """Materialize key-based dependencies into GitHub-ready manifest metadata."""
    issues = manifest.get("issues", [])
    issue_index = {
        issue.get("key"): {
            "github_issue": issue.get("github_issue"),
            "github_url": issue.get("github_url", ""),
        }
        for issue in issues
    }

    for issue in issues:
        dependency_metadata: list[dict[str, Any]] = []
        github_depends_on: list[int] = []
        github_dependency_urls: list[str] = []

        for dependency_key in issue.get("depends_on", []):
            dependency = issue_index.get(dependency_key, {})
            github_issue = dependency.get("github_issue")
            github_url = dependency.get("github_url", "")
            dependency_metadata.append(
                {
                    "key": dependency_key,
                    "github_issue": github_issue,
                    "github_url": github_url,
                    "status": "resolved" if github_issue is not None else "pending",
                }
            )
            if github_issue is not None:
                github_depends_on.append(int(github_issue))
            if github_url:
                github_dependency_urls.append(str(github_url))

        issue["dependency_metadata"] = dependency_metadata
        issue["github_depends_on"] = github_depends_on
        issue["github_dependency_urls"] = github_dependency_urls

    manifest["issue_key_map"] = {
        key: value["github_issue"]
        for key, value in issue_index.items()
        if value["github_issue"] is not None
    }
    return manifest




COMPLETED_PLANNER_STATES = {"closed", "done", "merged"}


def build_planner_status_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Return compact planner task counts for dashboard rendering."""
    completed = {
        task.get("key")
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }
    counts = {
        "total": len(tasks),
        "pending": 0,
        "ready": 0,
        "waiting": 0,
        "active": 0,
        "done": 0,
        "merged": 0,
        "blocked": 0,
        "completed": 0,
    }

    for task in tasks:
        state = str(task.get("state", "")).lower()
        workflow_state = str(task.get("workflow_state", "") or "").lower()
        missing_dependencies = [
            dependency
            for dependency in task.get("depends_on", [])
            if dependency not in completed
        ]
        if state == "active":
            counts["active"] += 1
        elif state == "unseeded":
            counts["pending"] += 1
        elif state == "done":
            counts["done"] += 1
            counts["completed"] += 1
        elif state == "merged":
            counts["merged"] += 1
            counts["completed"] += 1
        elif state == "closed":
            counts["completed"] += 1
        elif state in {"blocked", "failed"}:
            counts["blocked"] += 1
        elif missing_dependencies:
            counts["waiting"] += 1
        elif state == "ready" or workflow_state == "ready":
            counts["ready"] += 1
        elif state == "open":
            counts["blocked"] += 1
        else:
            counts["pending"] += 1

    return counts


def _planner_run_reconcile_hints(next_plan: dict[str, Any]) -> list[str]:
    """Build bounded reconcile hints from deterministic next-task analysis."""
    hints: list[str] = []

    blocked_items = next_plan.get("blocked", [])
    for blocked in blocked_items[:3]:
        key = blocked.get("key", "unknown")
        reason = blocked.get("reason", "blocked")
        hints.append(f"{key}: {reason}")

    waiting = next_plan.get("waiting", [])
    if waiting:
        hints.append(f"{len(waiting)} task(s) waiting for dependencies")

    if len(blocked_items) > 3:
        remaining = len(blocked_items) - 3
        hints.append(f"{remaining} additional blocked task(s) omitted")

    return hints


def _planner_impact_level(score: int) -> str:
    """Map deterministic impact score to a compact level."""
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "low"


def apply_planner_advance_plan(
    advance_plan: dict[str, Any],
    *,
    repo: str,
    run_command,
) -> dict[str, Any]:
    """Apply one guarded planner advance label mutation."""
    if advance_plan.get("status") != "ready":
        return {
            "status": "blocked",
            "issue": advance_plan.get("issue"),
            "promoted": [],
            "commands": [],
            "errors": ["advance plan is not ready"],
        }

    targets = advance_plan.get("targets", [])
    if not targets:
        return {
            "status": "blocked",
            "issue": advance_plan.get("issue"),
            "promoted": [],
            "commands": [],
            "errors": ["advance plan has no promotable targets"],
        }

    promoted: list[dict[str, Any]] = []
    commands: list[str] = []
    ordered_targets = sorted(
        targets,
        key=lambda item: (int(item.get("github_issue", 0)), str(item.get("key", ""))),
    )

    for target in ordered_targets:
        labels_to_add = target.get("labels_to_add", [])
        if labels_to_add != ["state:ready"]:
            return {
                "status": "blocked",
                "issue": advance_plan.get("issue"),
                "promoted": [],
                "commands": [],
                "errors": ["advance target must add exactly state:ready"],
            }

        github_issue = str(target["github_issue"])
        command = [
            "gh",
            "issue",
            "edit",
            github_issue,
            "-R",
            repo,
            "--add-label",
            "state:ready",
        ]
        run_command(command)
        commands.append(" ".join(command))
        promoted.append(
            {
                "key": target["key"],
                "github_issue": target["github_issue"],
                "labels_added": labels_to_add,
            }
        )

    return {
        "status": "applied",
        "issue": advance_plan.get("issue"),
        "promoted": promoted,
        "commands": commands,
        "errors": [],
    }


def format_planner_advance_apply_result(result: dict[str, Any]) -> str:
    """Format guarded planner advance apply result."""
    status = str(result.get("status", "unknown"))
    lines = [
        "Signposter Planner Advance Apply",
        "",
        "Status:",
        f"  {status}",
        "",
        "Status detail:",
        (
            "  applied — GitHub label mutations listed below were executed because "
            "--apply was provided."
            if status == "applied"
            else "  blocked — no GitHub label mutation was executed."
        ),
    ]

    if result.get("promoted"):
        lines.extend(["", "Promoted GitHub issues:"])
        for item in result["promoted"]:
            labels = ", ".join(item.get("labels_added", []))
            lines.append(
                f"  {item['key']} -> #{item['github_issue']} "
                f"added labels: {labels}"
            )

    if result.get("commands"):
        lines.extend(["", "Executed commands:"])
        lines.extend(f"  {command}" for command in result["commands"])

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  Issue closure was not performed.",
            "  No manifest mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No LLM analysis was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_run_plan_from_status(
    status: dict[str, Any],
    *,
    manifest_path: str,
) -> dict[str, Any]:
    """Build a read-only planner run dashboard from planner status."""
    next_plan = build_planner_next_from_status(status)
    step_plan = build_planner_step_from_next(next_plan)

    advance_candidates = []
    for task in status.get("tasks", []):
        github_issue = task.get("github_issue")
        task_state = str(task.get("state", "")).lower()
        if github_issue is None or task_state not in COMPLETED_PLANNER_STATES:
            continue

        impact = build_planner_impact_from_status(
            status,
            issue=int(github_issue),
            manifest_path=manifest_path,
        )
        if impact.get("status") != "ready":
            continue
        if impact.get("impact", {}).get("decision") != "advance_mainline":
            continue

        advance_plan = build_planner_advance_plan_from_status(
            status,
            issue=int(github_issue),
            manifest_path=manifest_path,
        )
        if advance_plan.get("status") != "ready":
            continue

        advance_candidates.append(
            {
                "issue": int(github_issue),
                "task_key": task["key"],
                "decision": impact["impact"]["decision"],
                "suggested_command": impact.get("suggested_command"),
                "targets": [target["key"] for target in advance_plan["targets"]],
            }
        )

    return {
        "status": "ready",
        "repo": status.get("repo", ""),
        "manifest_path": manifest_path,
        "planner_status": status.get("status", "unknown"),
        "status_counts": build_planner_status_counts(status.get("tasks", [])),
        "next": next_plan,
        "step": step_plan,
        "advance_candidates": advance_candidates,
        "requires_llm_analysis": any(
            candidate.get("requires_llm_analysis", False)
            for candidate in advance_candidates
        ),
        "notes": [
            "Read-only planner run dashboard.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
            "No claim was performed.",
            "No worktree was created.",
            "No OpenClaw execution was performed.",
            "No LLM analysis was performed.",
        ],
    }


def format_planner_run_plan(result: dict[str, Any]) -> str:
    """Format a read-only planner run dashboard."""
    lines = [
        "Signposter Planner Run",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Repo:",
        f"  {result.get('repo', '')}",
        "",
        "Manifest:",
        f"  {result.get('manifest_path', '')}",
        "",
        "Planner status:",
        f"  {result.get('planner_status', 'unknown')}",
    ]

    counts = result.get("status_counts", {})
    if counts:
        lines.extend(
            [
                "",
                "Task counts:",
                f"  total: {counts.get('total', 0)}",
                f"  pending: {counts.get('pending', 0)}",
                f"  ready: {counts.get('ready', 0)}",
                f"  waiting: {counts.get('waiting', 0)}",
                f"  active: {counts.get('active', 0)}",
                f"  done: {counts.get('done', 0)}",
                f"  merged: {counts.get('merged', 0)}",
                f"  blocked: {counts.get('blocked', 0)}",
                f"  completed: {counts.get('completed', 0)}",
            ]
        )

    next_plan = result.get("next", {})
    next_task = next_plan.get("next")
    lines.extend(["", "Next task:"])
    if next_task:
        deps = ", ".join(next_task.get("depends_on", [])) or "none"
        lines.extend(
            [
                f"  {next_task['key']} — issue: #{next_task['github_issue']} — "
                f"state: {next_task['state']}",
                f"  {next_task.get('github_url', '')}",
                f"  depends on: {deps}",
            ]
        )
    else:
        lines.append("  none")

    reconcile_hints = _planner_run_reconcile_hints(next_plan)
    lines.extend(["", "Reconcile hints:"])
    if reconcile_hints:
        lines.extend(f"  - {hint}" for hint in reconcile_hints)
    else:
        lines.append("  none")

    step = result.get("step", {})
    lines.extend(["", "Suggested step command:"])
    if step.get("suggested_command"):
        lines.append(f"  {step['suggested_command']}")
    else:
        lines.append("  none")

    candidates = result.get("advance_candidates", [])
    lines.extend(["", "Advance candidates:"])
    if candidates:
        for candidate in candidates:
            targets = ", ".join(candidate.get("targets", [])) or "none"
            lines.extend(
                [
                    f"  issue #{candidate['issue']} / {candidate['task_key']}:",
                    f"    decision: {candidate['decision']}",
                    f"    targets: {targets}",
                    f"    suggested command: {candidate['suggested_command']}",
                ]
            )
    else:
        lines.append("  none")

    lines.extend(
        [
            "",
            "Requires:",
            f"  LLM analysis: {str(result.get('requires_llm_analysis', False)).lower()}",
        ]
    )

    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in result.get("notes", []))
    return "\n".join(lines)


def build_planner_advance_plan_from_status(
    status: dict[str, Any],
    *,
    issue: int,
    manifest_path: str,
) -> dict[str, Any]:
    """Build a dry-run plan to promote downstream planner tasks."""
    tasks = status.get("tasks", [])
    repo = status.get("repo", "")
    source_task = next((item for item in tasks if item.get("github_issue") == issue), None)

    if source_task is None:
        return {
            "status": "blocked",
            "issue": issue,
            "source_task": None,
            "targets": [],
            "planned_github_mutations": [],
            "planned_manifest_mutations": [],
            "requires_llm_analysis": False,
            "manifest_path": manifest_path,
            "reasons": [f"issue #{issue} is not present in the planner manifest"],
        }

    source_state = str(source_task.get("state", "")).lower()
    if source_state not in COMPLETED_PLANNER_STATES:
        return {
            "status": "blocked",
            "issue": issue,
            "source_task": source_task,
            "targets": [],
            "planned_github_mutations": [],
            "planned_manifest_mutations": [],
            "requires_llm_analysis": False,
            "manifest_path": manifest_path,
            "reasons": [
                f"issue is not completed: state={source_task.get('state')}",
                "advance waits for completed/merged/done source tasks",
            ],
        }

    source_key = source_task.get("key")
    downstream = [
        task
        for task in tasks
        if source_key in task.get("depends_on", [])
    ]
    completed = {
        task["key"]
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }

    targets = []
    blocked_downstream = []
    planned_github_mutations = []
    for task in downstream:
        github_issue = task.get("github_issue")
        state = str(task.get("state", "")).lower()
        labels = set(task.get("labels", []))
        if github_issue is None:
            continue
        if state not in {"open", "unknown"}:
            continue
        if "state:ready" in labels:
            continue
        missing_dependencies = [
            dependency
            for dependency in task.get("depends_on", [])
            if dependency not in completed
        ]
        if missing_dependencies:
            blocked_downstream.append(
                {
                    "key": task["key"],
                    "missing_dependencies": missing_dependencies,
                }
            )
            continue

        target = {
            "key": task["key"],
            "github_issue": github_issue,
            "github_url": task.get("github_url", ""),
            "state": task.get("state"),
            "labels_to_add": ["state:ready"],
        }
        targets.append(target)

        command = f"gh issue edit {github_issue}"
        if repo:
            command += f" -R {repo}"
        command += " --add-label state:ready"
        planned_github_mutations.append(command)

    status_value = "ready" if targets else "blocked"
    reasons = [
        f"source issue is completed: state={source_task.get('state')}",
        "advance dry-run used zero LLM tokens",
    ]
    if targets:
        reasons.append("one or more downstream tasks can be promoted")
    elif downstream:
        reasons.append("no downstream task is currently promotable")
        if blocked_downstream:
            reasons.append("one or more downstream tasks are waiting for dependencies")
            for blocked in blocked_downstream[:3]:
                missing = ", ".join(blocked["missing_dependencies"])
                reasons.append(f"{blocked['key']} waits for dependencies: {missing}")
    else:
        reasons.append("source task has no downstream dependents")

    return {
        "status": status_value,
        "issue": issue,
        "source_task": source_task,
        "targets": targets,
        "planned_github_mutations": planned_github_mutations,
        "planned_manifest_mutations": [],
        "requires_llm_analysis": False,
        "manifest_path": manifest_path,
        "reasons": reasons,
    }


def format_planner_advance_plan(result: dict[str, Any]) -> str:
    """Format a dry-run planner advance plan."""
    issue = result["issue"]
    status = str(result.get("status", "unknown"))
    status_detail = (
        "ready — dry-run only; use planner advance --apply to add listed labels"
        if status == "ready"
        else "blocked — dry-run only; do not run apply until reasons are resolved"
    )
    lines = [
        f"Signposter Planner Advance — Issue #{issue}",
        "",
        "Status:",
        f"  {status}",
        "",
        "Status detail:",
        f"  {status_detail}",
    ]

    source_task = result.get("source_task")
    if source_task:
        lines.extend(
            [
                "",
                "Source task:",
                f"  {source_task['key']} — state: {source_task['state']}",
            ]
        )

    targets = result.get("targets", [])
    lines.extend(["", "Would promote:"])
    if targets:
        for target in targets:
            lines.append(
                f"  {target['key']} — issue: #{target['github_issue']} — "
                f"state: {target['state']}"
            )
    else:
        lines.append("  none")

    lines.extend(["", "Planned GitHub mutations:"])
    mutations = result.get("planned_github_mutations", [])
    if mutations:
        lines.append("  Preview only; these commands were not executed.")
        lines.extend(f"  {mutation}" for mutation in mutations)
    else:
        lines.append("  none")

    lines.extend(["", "Planned manifest mutations:"])
    manifest_mutations = result.get("planned_manifest_mutations", [])
    if manifest_mutations:
        lines.extend(f"  {mutation}" for mutation in manifest_mutations)
    else:
        lines.append("  none")

    if result.get("reasons"):
        lines.extend(["", "Reasons:"])
        lines.extend(f"  - {reason}" for reason in result["reasons"])

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only; command previews were not executed.",
            "  No GitHub mutation was performed.",
            "  No issue was closed.",
            "  No manifest mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No LLM analysis was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_impact_from_status(
    status: dict[str, Any],
    *,
    issue: int,
    manifest_path: str,
) -> dict[str, Any]:
    """Build a token-free planner impact assessment from a status summary."""
    tasks = status.get("tasks", [])
    task = next((item for item in tasks if item.get("github_issue") == issue), None)
    if task is None:
        return {
            "status": "blocked",
            "issue": issue,
            "task": None,
            "impact": {
                "score": 0,
                "level": "unknown",
                "decision": "issue_not_in_manifest",
            },
            "downstream_tasks": [],
            "requires_llm_analysis": False,
            "suggested_command": None,
            "reasons": [f"issue #{issue} is not present in the planner manifest"],
        }

    state = str(task.get("state", "")).lower()
    downstream_tasks = [
        item["key"]
        for item in tasks
        if task.get("key") in item.get("depends_on", [])
    ]

    if state not in COMPLETED_PLANNER_STATES:
        return {
            "status": "blocked",
            "issue": issue,
            "task": task,
            "impact": {
                "score": 0,
                "level": "none",
                "decision": "wait_for_completion",
            },
            "downstream_tasks": downstream_tasks,
            "requires_llm_analysis": False,
            "suggested_command": None,
            "reasons": [
                f"issue is not completed: state={task.get('state')}",
                "impact scoring waits for completed/merged/done tasks",
            ],
        }

    score = 10 if downstream_tasks else 0
    level = _planner_impact_level(score)
    decision = "advance_mainline" if score < 40 else "requires_reconcile"

    reasons = [
        f"issue is completed: state={task.get('state')}",
        "deterministic impact scoring used zero LLM tokens",
    ]
    if downstream_tasks:
        reasons.append("task has downstream dependents")
    else:
        reasons.append("task has no downstream dependents")

    suggested_command = None
    if decision == "advance_mainline":
        suggested_command = (
            f"signposter planner advance --manifest {manifest_path} "
            f"--issue {issue} --dry-run"
        )

    return {
        "status": "ready",
        "issue": issue,
        "task": task,
        "impact": {
            "score": score,
            "level": level,
            "decision": decision,
        },
        "downstream_tasks": downstream_tasks,
        "requires_llm_analysis": decision != "advance_mainline",
        "suggested_command": suggested_command,
        "reasons": reasons,
    }


def format_planner_impact(result: dict[str, Any]) -> str:
    """Format a token-free planner impact assessment."""
    issue = result["issue"]
    impact = result["impact"]
    lines = [
        f"Signposter Planner Impact — Issue #{issue}",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Impact:",
        f"  score: {impact['score']}",
        f"  level: {impact['level']}",
        f"  decision: {impact['decision']}",
    ]

    task = result.get("task")
    if task:
        lines.extend(
            [
                "",
                "Task:",
                f"  {task['key']} — state: {task['state']}",
            ]
        )

    downstream = result.get("downstream_tasks", [])
    lines.extend(
        [
            "",
            "Downstream:",
            f"  downstream: {', '.join(downstream) if downstream else 'none'}",
            "",
            "Requires:",
            f"  LLM analysis: {str(result.get('requires_llm_analysis', False)).lower()}",
        ]
    )

    if result.get("suggested_command"):
        lines.extend(
            [
                "",
                "Suggested next command:",
                f"  {result['suggested_command']}",
            ]
        )

    if result.get("reasons"):
        lines.extend(["", "Reasons:"])
        lines.extend(f"  - {reason}" for reason in result["reasons"])

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No manifest mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No LLM analysis was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_next_from_status(status: dict[str, Any]) -> dict[str, Any]:
    """Choose the next dependency-ready task from a planner status summary."""
    tasks = status.get("tasks", [])
    if not tasks:
        return {
            "status": "completed",
            "reason": "no planner tasks found",
            "next": None,
            "waiting": [],
            "blocked": [],
        }

    completed = {
        task["key"]
        for task in tasks
        if str(task.get("state", "")).lower() in COMPLETED_PLANNER_STATES
    }

    waiting: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    ready_mainline: dict[str, Any] | None = None
    missing_ready_label = False

    for task in tasks:
        state = str(task.get("state", "")).lower()
        if state in COMPLETED_PLANNER_STATES:
            continue

        if task.get("github_issue") is None:
            blocked.append(
                {
                    "key": task["key"],
                    "reason": "task is not seeded to a GitHub issue",
                }
            )
            continue

        missing_dependencies = [
            dependency
            for dependency in task.get("depends_on", [])
            if dependency not in completed
        ]
        if missing_dependencies:
            waiting.append(
                {
                    "key": task["key"],
                    "reason": "waiting for dependencies",
                    "missing_dependencies": missing_dependencies,
                }
            )
            continue

        if state == "ready":
            next_task = {
                "key": task["key"],
                "title": task["title"],
                "github_issue": task["github_issue"],
                "github_url": task["github_url"],
                "state": task["state"],
                "depends_on": task["depends_on"],
                "mainline": task.get("mainline"),
                "parent": task.get("parent"),
                "return_to": task.get("return_to"),
                "side_task": bool(task.get("side_task", False)),
            }
            if task.get("side_task"):
                return {
                    "status": "ready",
                    "reason": "dependency-ready side-task selected before mainline",
                    "next": next_task,
                    "waiting": waiting,
                    "blocked": blocked,
                }
            if ready_mainline is None:
                ready_mainline = next_task
            continue

        if (
            state == "open"
            and task.get("github_state") == "open"
            and not task.get("workflow_state")
        ):
            missing_ready_label = True
            completed_source_issues = [
                dependency_issue
                for dependency_issue in task.get("github_depends_on", [])
                if dependency_issue is not None
            ]
            blocked.append(
                {
                    "key": task["key"],
                    "reason": "dependency-ready task is open but missing GitHub label state:ready",
                    "github_issue": task.get("github_issue"),
                    "reconcile_issues": completed_source_issues,
                }
            )
            continue

        if state == "open":
            next_task = {
                "key": task["key"],
                "title": task["title"],
                "github_issue": task["github_issue"],
                "github_url": task["github_url"],
                "state": task["state"],
                "depends_on": task["depends_on"],
                "mainline": task.get("mainline"),
                "parent": task.get("parent"),
                "return_to": task.get("return_to"),
                "side_task": bool(task.get("side_task", False)),
            }
            if task.get("side_task"):
                return {
                    "status": "ready",
                    "reason": "dependency-ready side-task selected before mainline",
                    "next": next_task,
                    "waiting": waiting,
                    "blocked": blocked,
                }
            if ready_mainline is None:
                ready_mainline = next_task
            continue

        blocked.append(
            {
                "key": task["key"],
                "reason": f"unsupported task state: {task.get('state')}",
            }
        )

    if ready_mainline is not None:
        return {
            "status": "ready",
            "reason": "first dependency-ready open task selected",
            "next": ready_mainline,
            "waiting": waiting,
            "blocked": blocked,
        }

    if len(completed) == len(tasks):
        return {
            "status": "completed",
            "reason": "all planner tasks are completed",
            "next": None,
            "waiting": waiting,
            "blocked": blocked,
        }

    if missing_ready_label:
        return {
            "status": "blocked",
            "reason": "dependency-ready open task is missing GitHub label state:ready",
            "next": None,
            "waiting": waiting,
            "blocked": blocked,
        }

    return {
        "status": "waiting",
        "reason": "no dependency-ready open task is available",
        "next": None,
        "waiting": waiting,
        "blocked": blocked,
    }


def format_planner_next_from_status(result: dict[str, Any]) -> str:
    """Format next-from-status result."""
    lines = [
        "Signposter Planner Next",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Reason:",
        f"  {result['reason']}",
    ]

    if result["next"] is not None:
        task = result["next"]
        deps = ", ".join(task["depends_on"]) if task["depends_on"] else "none"
        lines.extend(
            [
                "",
                "Next task:",
                f"  {task['key']} — issue: #{task['github_issue']} — state: {task['state']}",
                f"  {task['github_url']}",
                f"  depends on: {deps}",
            ]
        )
        if task.get("side_task"):
            parent = f"#{task['parent']}" if task.get("parent") is not None else "none"
            return_to = f"#{task['return_to']}" if task.get("return_to") is not None else "none"
            mainline = task.get("mainline") or "none"
            lines.extend(
                [
                    "  side-task: yes",
                    f"  parent: {parent}",
                    f"  return-to: {return_to}",
                    f"  mainline: {mainline}",
                ]
            )

    if result["waiting"]:
        lines.extend(["", "Waiting:"])
        for item in result["waiting"]:
            missing = ", ".join(item.get("missing_dependencies", []))
            lines.append(f"  {item['key']} — {item['reason']}: {missing}")

    if result["blocked"]:
        lines.extend(["", "Blocked:"])
        for item in result["blocked"]:
            line = f"  {item['key']} — {item['reason']}"
            github_issue = item.get("github_issue")
            if github_issue is not None:
                line += f" (issue #{github_issue})"
            lines.append(line)
            reconcile_issues = item.get("reconcile_issues", [])
            if reconcile_issues:
                issues = ", ".join(f"#{issue}" for issue in reconcile_issues)
                lines.append(
                    "    reconcile hint: run planner advance/apply from completed "
                    f"dependency issue(s) {issues} to restore state:ready before claim"
                )

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No claim was performed.",
            "  No worktree was created.",
            "  No OpenClaw execution was performed.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)



def _repo_from_github_url(url: str) -> str | None:
    """Best-effort owner/repo extraction from a GitHub issue URL."""
    marker = "github.com/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1]
    parts = tail.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def build_planner_step_from_next(next_result: dict[str, Any]) -> dict[str, Any]:
    """Build a dry-run planner step plan from a planner next result."""
    next_task = next_result.get("next")
    if next_result.get("status") != "ready" or next_task is None:
        return {
            "status": next_result.get("status", "blocked"),
            "reason": next_result.get("reason", "no ready task"),
            "next": next_task,
            "suggested_command": None,
            "errors": next_result.get("errors", []),
        }

    github_issue = next_task.get("github_issue")
    if github_issue is None:
        return {
            "status": "blocked",
            "reason": "next task has no GitHub issue number",
            "next": next_task,
            "suggested_command": None,
            "errors": ["next task has no GitHub issue number"],
        }

    repo = _repo_from_github_url(str(next_task.get("github_url", "")))
    run_command = (
        f"signposter run --repo {repo} --issue {github_issue} --dry-run"
        if repo
        else f"signposter run --issue {github_issue} --dry-run"
    )
    workflow_hints = [
        {
            "label": "inspect lifecycle",
            "command": (
                f"signposter lifecycle status --repo {repo} --issue {github_issue}"
                if repo
                else f"signposter lifecycle status --issue {github_issue}"
            ),
        },
        {
            "label": "claim dry-run",
            "command": (
                f"signposter claim --repo {repo} --dry-run"
                if repo
                else "signposter claim --dry-run"
            ),
        },
        {
            "label": "worktree plan",
            "command": (
                f"signposter worktree plan --repo {repo} --issue {github_issue}"
                if repo
                else f"signposter worktree plan --issue {github_issue}"
            ),
        },
        {
            "label": "run dry-run",
            "command": run_command,
        },
    ]
    if next_task.get("side_task") and next_task.get("return_to") is not None:
        return_to = int(next_task["return_to"])
        workflow_hints.append(
            {
                "label": "return target",
                "command": (
                    f"signposter lifecycle status --repo {repo} --issue {return_to}"
                    if repo
                    else f"signposter lifecycle status --issue {return_to}"
                ),
            }
        )

    return {
        "status": "ready",
        "reason": next_result.get("reason", "next task is ready"),
        "next": next_task,
        "suggested_command": run_command,
        "workflow_hints": workflow_hints,
        "errors": [],
    }


def format_planner_step(result: dict[str, Any]) -> str:
    """Format a dry-run planner step plan."""
    lines = [
        "Signposter Planner Step",
        "",
        "Status:",
        f"  {result['status']}",
        "",
        "Reason:",
        f"  {result.get('reason', '')}",
    ]

    if result.get("next") is not None:
        task = result["next"]
        deps = ", ".join(task["depends_on"]) if task["depends_on"] else "none"
        lines.extend(
            [
                "",
                "Next task:",
                f"  {task['key']} — issue: #{task['github_issue']} — state: {task['state']}",
                f"  {task['github_url']}",
                f"  depends on: {deps}",
            ]
        )

    if result.get("suggested_command"):
        lines.extend(
            [
                "",
                "Suggested command:",
                f"  {result['suggested_command']}",
            ]
        )

    if result.get("workflow_hints"):
        lines.extend(["", "Workflow hints:"])
        for hint in result["workflow_hints"]:
            lines.append(f"  {hint['label']}:")
            lines.append(f"    {hint['command']}")
        lines.extend(
            [
                "  note:",
                "    Hints only; no command above was executed.",
            ]
        )

    if result.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in result["errors"])

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No claim was performed.",
            "  No worktree was created.",
            "  No OpenClaw execution was performed.",
            "  No task execution was performed.",
        ]
    )
    return "\n".join(lines)


def build_planner_status(
    manifest: dict[str, Any],
    issue_states: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Build a local planner status summary from a seed manifest."""
    manifest = _refresh_seed_manifest_dependency_metadata(dict(manifest))
    issue_states = issue_states or {}
    tasks = []

    for issue in manifest.get("issues", []):
        github_issue = issue.get("github_issue")
        state = "unseeded"
        github_state: str | None = None
        workflow_state: str | None = None
        manifest_workflow_state = _workflow_state_from_manifest_labels(
            issue.get("labels", [])
        )
        if github_issue is not None:
            snapshot = issue_states.get(int(github_issue), "unknown")
            if isinstance(snapshot, dict):
                github_state = str(snapshot.get("github_state", "")).lower() or None
                workflow_state = str(snapshot.get("workflow_state", "")).lower() or None
                state = str(snapshot.get("state", "")).lower() or "unknown"
            else:
                state = str(snapshot).lower()
                if state == "open":
                    github_state = "open"
                    if manifest_workflow_state:
                        workflow_state = manifest_workflow_state
                elif state == "closed":
                    github_state = "closed"
                elif state in ALLOWED_TASK_STATUSES:
                    workflow_state = state
                    github_state = "closed" if state == "merged" else "open"

        tasks.append(
            {
                "key": issue.get("key"),
                "title": issue.get("title"),
                "github_issue": github_issue,
                "github_url": issue.get("github_url", ""),
                "state": state,
                "github_state": github_state,
                "workflow_state": workflow_state,
                "labels": issue.get("labels", []),
                "depends_on": issue.get("depends_on", []),
                "github_depends_on": issue.get("github_depends_on", []),
                "dependency_metadata": issue.get("dependency_metadata", []),
                "mainline": issue.get("mainline"),
                "parent": issue.get("parent"),
                "return_to": issue.get("return_to"),
                "side_task": bool(issue.get("side_task", False)),
            }
        )

    if not tasks:
        status = "empty"
    elif all(task["github_issue"] is None for task in tasks):
        status = "unseeded"
    elif any(task["github_issue"] is None for task in tasks):
        status = "partial"
    elif all(task["state"] in {"closed", "merged", "done"} for task in tasks):
        status = "completed"
    else:
        status = "active"

    return {
        "version": "planner.status.v0.1",
        "manifest_status": manifest.get("status", "unknown"),
        "repo": manifest.get("repo", ""),
        "status": status,
        "tasks": tasks,
        "notes": [
            "Local status summary only.",
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
            "No task execution was performed.",
        ],
    }


def _workflow_state_from_manifest_labels(labels: list[Any]) -> str | None:
    for label in labels:
        name = str(label).strip().lower()
        if not name.startswith("state:"):
            continue
        workflow_state = name.split(":", 1)[1].strip()
        if workflow_state:
            return workflow_state
    return None


def format_planner_status(status: dict[str, Any]) -> str:
    """Format planner status summary."""
    counts = build_planner_status_counts(status["tasks"])
    lines = [
        "Signposter Planner Status",
        "",
        "Repo:",
        f"  {status['repo']}",
        "",
        "Manifest status:",
        f"  {status['manifest_status']}",
        "",
        "Status:",
        f"  {status['status']}",
        "",
        "Progress:",
        f"  total: {counts.get('total', 0)}",
        f"  pending: {counts.get('pending', 0)}",
        f"  ready: {counts.get('ready', 0)}",
        f"  waiting: {counts.get('waiting', 0)}",
        f"  active: {counts.get('active', 0)}",
        f"  done: {counts.get('done', 0)}",
        f"  merged: {counts.get('merged', 0)}",
        f"  blocked: {counts.get('blocked', 0)}",
        f"  completed: {counts.get('completed', 0)}",
        "",
        "Tasks:",
    ]

    if not status["tasks"]:
        lines.append("  none")
    else:
        for task in status["tasks"]:
            issue_text = (
                f"#{task['github_issue']}"
                if task["github_issue"] is not None
                else "none"
            )
            url = f" {task['github_url']}" if task["github_url"] else ""
            lines.append(
                f"  {task['key']} — issue: {issue_text} — state: {task['state']}{url}"
            )
            dependency_metadata = task.get("dependency_metadata", [])
            if dependency_metadata:
                deps = ", ".join(
                    f"{dependency['key']} (#{dependency['github_issue']})"
                    if dependency.get("github_issue") is not None
                    else dependency["key"]
                    for dependency in dependency_metadata
                )
                lines.append(f"    depends on: {deps}")
            if task.get("side_task"):
                parent = f"#{task['parent']}" if task.get("parent") is not None else "none"
                return_to = (
                    f"#{task['return_to']}" if task.get("return_to") is not None else "none"
                )
                mainline = task.get("mainline") or "none"
                lines.append(
                    "    side-task: yes"
                    f" · parent: {parent}"
                    f" · return-to: {return_to}"
                    f" · mainline: {mainline}"
                )

    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in status["notes"])
    return "\n".join(lines)


def build_planner_seed_manifest(
    *,
    plan_path: Path,
    repo: str,
    seed_plan: dict[str, Any],
    body_dir: Path,
) -> dict[str, Any]:
    """Build a local seed manifest for future guarded GitHub issue creation."""
    issues = []
    for issue in seed_plan["issues"]:
        body_file = body_dir / f"{issue['key']}.md"
        issues.append(
            {
                "key": issue["key"],
                "title": issue["github_title"],
                "labels": issue["labels"],
                "depends_on": issue["depends_on"],
                "body_file": str(body_file),
                "body_size": issue["body_size"],
                "github_issue": None,
                "github_url": "",
                "mainline": issue.get("mainline"),
                "parent": issue.get("parent"),
                "return_to": issue.get("return_to"),
                "side_task": bool(issue.get("side_task", False)),
            }
        )

    return _refresh_seed_manifest_dependency_metadata(
        {
        "version": "planner.seed-manifest.v0.1",
        "plan": str(plan_path),
        "repo": repo,
        "status": "dry-run",
        "issues": issues,
        "notes": [
            "Local manifest only.",
            "No GitHub mutation was performed.",
            "No GitHub issue was created.",
            "No OpenClaw execution was performed.",
        ],
        }
    )



def format_written_seed_manifest(manifest_path: Path) -> str:
    """Format local seed manifest write result."""
    return "\n".join(
        [
            "",
            "Written seed manifest:",
            f"  {manifest_path}",
            "",
            "Notes:",
            "  Local manifest only.",
            "  No GitHub mutation was performed.",
            "  No GitHub issue was created.",
            "  No OpenClaw execution was performed.",
        ]
    )


def write_planner_seed_manifest(
    manifest: dict[str, Any],
    manifest_path: Path,
) -> None:
    """Write a local planner seed manifest JSON file."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_planner_seed_issue_bodies(
    seed_plan: dict[str, Any],
    body_dir: Path,
) -> list[Path]:
    """Write generated issue bodies to local Markdown files."""
    if seed_plan["status"] != "ready":
        return []

    body_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for issue in seed_plan["issues"]:
        body_path = body_dir / f"{issue['key']}.md"
        body_path.write_text(issue["body"] + "\n", encoding="utf-8")
        written.append(body_path)

    return written


def format_written_issue_bodies(paths: list[Path]) -> str:
    """Format local issue body write result."""
    lines = [
        "",
        "Written issue body files:",
    ]

    if not paths:
        lines.append("  none")
    else:
        lines.extend(f"  {path}" for path in paths)

    lines.extend(
        [
            "",
            "Notes:",
            "  Local files only.",
            "  No GitHub mutation was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def format_planner_seed_plan(
    plan_path: Path,
    seed_plan: dict[str, Any],
    *,
    repo: str = "<owner/repo>",
    body_dir: Path = Path("artifacts/plans/issue-bodies"),
    show_body: bool = False,
    show_commands: bool = False,
) -> str:
    """Format a dry-run planner seed plan."""
    lines = [
        "Signposter Planner Seed",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Status:",
        f"  {seed_plan['status']}",
    ]

    if seed_plan["errors"]:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in seed_plan["errors"])
    else:
        lines.extend(["", "Proposed GitHub issues:"])
        for issue in seed_plan["issues"]:
            deps = ", ".join(issue["depends_on"]) if issue["depends_on"] else "none"
            labels = ", ".join(issue["labels"])
            body_size = issue.get("body_size", {})
            size_status = body_size.get("status", "unknown")
            line_count = body_size.get("line_count", "?")
            char_count = body_size.get("char_count", "?")
            lines.append(f"  {issue['key']} — {issue['title']}")
            lines.append(f"    GitHub title: {issue['github_title']}")
            lines.append(f"    labels: {labels}")
            lines.append(f"    depends on: {deps}")
            lines.append(
                f"    body size: {size_status} "
                f"({line_count} lines, {char_count} chars)"
            )
            if show_commands:
                body_file = body_dir / f"{issue['key']}.md"
                command = format_gh_issue_create_command(
                    repo=repo,
                    title=issue["github_title"],
                    body_file=body_file,
                    labels=issue["labels"],
                )
                lines.extend(
                    [
                        "    command preview:",
                        "      ----- BEGIN GH COMMAND -----",
                    ]
                )
                lines.extend(f"      {line}" for line in command.splitlines())
                lines.append("      ----- END GH COMMAND -----")
            if show_body:
                lines.extend(
                    [
                        "    body:",
                        "      ----- BEGIN ISSUE BODY -----",
                    ]
                )
                lines.extend(f"      {line}" for line in issue["body"].splitlines())
                lines.append("      ----- END ISSUE BODY -----")

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only.",
            "  Command previews are not executed.",
            "  Body-file paths are preview placeholders.",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def format_planner_draft(plan: dict[str, Any], output_path: Path) -> str:
    """Format a compact human-readable planner draft summary."""
    lines = [
        "Signposter Planner Draft",
        "",
        "Goal:",
        f"  {plan['goal']}",
        "",
        "Proposed issues:",
    ]

    for issue in plan["issues"]:
        deps = ", ".join(issue["depends_on"]) if issue["depends_on"] else "none"
        lines.append(f"  {issue['key']} — {issue['title']} (depends on: {deps})")

    lines.extend(
        [
            "",
            "Status:",
            f"  {plan['status']}",
            "",
            "Output:",
            f"  {output_path}",
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def format_planner_validation(plan_path: Path, errors: list[str]) -> str:
    """Format planner validation result."""
    status = "pass" if not errors else "blocked"
    lines = [
        "Signposter Planner Validate",
        "",
        "Plan:",
        f"  {plan_path}",
        "",
        "Status:",
        f"  {status}",
    ]

    if errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in errors)

    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No GitHub issue was created.",
        ]
    )
    return "\n".join(lines)


def _issue(key: str, title: str, area: str, depends_on: list[str]) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "body": f"{title}. Keep this task narrow and bounded.",
        "phase": "build",
        "risk": "low",
        "role": "worker",
        "area": area,
        "depends_on": depends_on,
        "acceptance": [f"{title} is implemented and tested."],
        "stop_conditions": STOP_CONDITIONS,
        "allowed_mutations": [],
    }


def _require(
    value: dict[str, Any],
    field: str,
    expected_type: type,
    errors: list[str],
) -> None:
    if field not in value:
        errors.append(f"missing {field}")
    elif not isinstance(value[field], expected_type):
        errors.append(f"{field} must be {expected_type.__name__}")


def _list_required(
    issue: dict[str, Any],
    field: str,
    key: str,
    errors: list[str],
) -> None:
    value = issue.get(field)
    if not isinstance(value, list):
        errors.append(f"{key}: {field} must be a list")
    elif field != "depends_on" and not value:
        errors.append(f"{key}: {field} must not be empty")

def _issue_status(issue: dict[str, Any]) -> str:
    return str(issue.get("status", "pending")).strip().lower() or "pending"


def _markdown_bullets(items: list[Any], fallback: str | None = None) -> str:
    if not items:
        if fallback is None:
            return "* none"
        return f"* {fallback}"
    return "\n".join(f"* {item}" for item in items)


def _target_command_for_issue(issue: dict[str, Any]) -> str:
    area = str(issue.get("area", "")).strip()
    key = str(issue.get("key", "")).strip()
    if area == "docs":
        return "python -m pytest tests/ -q"
    if area == "tests":
        return "python -m pytest tests/ -q"
    if key.startswith("WATCH-"):
        return "signposter lifecycle watch --repo ExatronOmega/signposter --issue N --interval 5"
    return "signposter <command> --repo ExatronOmega/signposter"
