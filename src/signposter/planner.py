"""Local planner draft and validation surfaces for Signposter."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PLAN_VERSION = "planner.v0.1"
AUTO_CLOSE_RE = re.compile(r"\b(closes|fixes|resolves)\s+#\d+", re.IGNORECASE)

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




DONE_STATUSES = {"done", "merged", "completed"}
BLOCKED_STATUSES = {"blocked", "failed"}


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


def build_planner_seed_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Build a dry-run issue seed plan from a validated planner plan."""
    errors = validate_planner_plan(plan)
    if errors:
        return {"status": "blocked", "errors": errors, "issues": []}

    issues = []
    for issue in plan["issues"]:
        issues.append(
            {
                "key": issue["key"],
                "title": issue["title"],
                "labels": [
                    f"phase:{issue['phase']}",
                    f"risk:{issue['risk']}",
                    f"role:{issue['role']}",
                    f"area:{issue['area']}",
                ],
                "depends_on": issue["depends_on"],
            }
        )

    return {"status": "ready", "errors": [], "issues": issues}


def format_planner_seed_plan(plan_path: Path, seed_plan: dict[str, Any]) -> str:
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
            lines.append(f"  {issue['key']} — {issue['title']}")
            lines.append(f"    labels: {labels}")
            lines.append(f"    depends on: {deps}")

    lines.extend(
        [
            "",
            "Notes:",
            "  Dry-run only.",
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
