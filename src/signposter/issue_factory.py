"""Local task-list to GitHub issue factory."""

from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IssueFactoryTask:
    task_id: str
    title: str
    body: str
    labels: list[str]


@dataclass(frozen=True)
class IssueFactoryItem:
    task: IssueFactoryTask
    exists: bool
    existing_number: int | None
    status: str


@dataclass(frozen=True)
class IssueFactoryPlan:
    repo: str
    task_path: Path
    items: list[IssueFactoryItem]
    apply: bool
    notes: list[str]


def load_issue_factory_tasks(path: Path) -> list[IssueFactoryTask]:
    """Load tasks from JSON or TSV."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("tasks", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("JSON task list must be a list or an object with a tasks list")
        return [_task_from_mapping(row) for row in rows]
    if suffix in {".tsv", ".txt"}:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return [_task_from_mapping(row) for row in csv.DictReader(fh, delimiter="\t")]
    raise ValueError("task list must be .json, .tsv, or .txt")


def plan_issue_factory(
    repo: str,
    task_path: Path,
    *,
    run_command=subprocess.run,
) -> IssueFactoryPlan:
    tasks = load_issue_factory_tasks(task_path)
    existing = _fetch_existing_issue_titles(repo, run_command=run_command)
    items = []
    for task in tasks:
        match = _find_existing_issue(task.task_id, existing)
        items.append(
            IssueFactoryItem(
                task=task,
                exists=match is not None,
                existing_number=match,
                status="exists" if match is not None else "create",
            )
        )
    return IssueFactoryPlan(
        repo=repo,
        task_path=task_path,
        items=items,
        apply=False,
        notes=[
            "Dry-run issue factory plan.",
            "No GitHub mutation was performed.",
            "Use --apply to create missing issues.",
        ],
    )


def apply_issue_factory(
    repo: str,
    task_path: Path,
    *,
    apply: bool = False,
    run_command=subprocess.run,
) -> IssueFactoryPlan:
    plan = plan_issue_factory(repo, task_path, run_command=run_command)
    if not apply:
        return plan

    applied_items: list[IssueFactoryItem] = []
    for item in plan.items:
        if item.exists:
            applied_items.append(item)
            continue
        number = _create_issue(repo, item.task, run_command=run_command)
        applied_items.append(
            IssueFactoryItem(
                task=item.task,
                exists=True,
                existing_number=number,
                status="created",
            )
        )

    return IssueFactoryPlan(
        repo=repo,
        task_path=task_path,
        items=applied_items,
        apply=True,
        notes=[
            "Issue factory apply completed.",
            "Only missing issues were created.",
            "Existing task ids were left unchanged.",
        ],
    )


def format_issue_factory_plan(plan: IssueFactoryPlan) -> str:
    title = "Signposter Issue Factory Apply" if plan.apply else "Signposter Issue Factory Plan"
    lines = [
        title,
        "",
        "Source:",
        f"  {plan.task_path}",
        "",
        "Repository:",
        f"  {plan.repo}",
        "",
        "Tasks:",
    ]
    if not plan.items:
        lines.append("  none")
    for item in plan.items:
        number = f" #{item.existing_number}" if item.existing_number is not None else ""
        labels = ", ".join(item.task.labels) or "none"
        lines.extend(
            [
                f"  {item.task.task_id}: {item.status}{number}",
                f"    title: {item.task.title}",
                f"    labels: {labels}",
            ]
        )
    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in plan.notes)
    return "\n".join(lines)


def _task_from_mapping(row: Any) -> IssueFactoryTask:
    if not isinstance(row, dict):
        raise ValueError("task entry must be an object/row")
    task_id = str(row.get("id") or row.get("task_id") or "").strip()
    title = str(row.get("title") or "").strip()
    if not task_id or not title:
        raise ValueError("task entry requires id/task_id and title")
    body = str(row.get("body") or row.get("description") or "").strip()
    labels = _parse_labels(row.get("labels"))
    return IssueFactoryTask(task_id=task_id, title=title, body=body, labels=labels)


def _parse_labels(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _fetch_existing_issue_titles(repo: str, *, run_command=subprocess.run) -> list[dict[str, Any]]:
    proc = run_command(
        [
            "gh",
            "issue",
            "list",
            "-R",
            repo,
            "--state",
            "all",
            "--limit",
            "200",
            "--json",
            "number,title",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "gh issue list failed")
    return json.loads(proc.stdout or "[]")


def _find_existing_issue(task_id: str, existing: list[dict[str, Any]]) -> int | None:
    needle = task_id.lower()
    for issue in existing:
        if needle in str(issue.get("title", "")).lower():
            return int(issue["number"])
    return None


def _create_issue(repo: str, task: IssueFactoryTask, *, run_command=subprocess.run) -> int | None:
    body = f"Task-ID: {task.task_id}\n\n{task.body}".strip()
    command = [
        "gh",
        "issue",
        "create",
        "-R",
        repo,
        "--title",
        f"{task.task_id} — {task.title}",
        "--body",
        body,
    ]
    for label in task.labels:
        command.extend(["--label", label])
    proc = run_command(command, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "gh issue create failed")
    for token in proc.stdout.split("/"):
        if token.strip().isdigit():
            return int(token.strip())
    return None
