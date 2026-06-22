"""Handoff planning for isolated worker branches (planning / dry-run only).

Provides a planning surface for committing, pushing, and handing off work
done inside a Signposter-managed worktree.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signposter.git_utils import get_current_branch, get_git_status_short
from signposter.worktree import (
    generate_proposed_branch,
    generate_proposed_worktree,
    get_worktree_status_for_issue,
)


@dataclass(frozen=True)
class HandoffPlan:
    issue_number: int
    title: str
    workflow_state: str | None  # from labels, e.g. "done", "active"
    github_issue_state: str | None  # "OPEN", "CLOSED"

    worktree_path: str
    branch: str
    worktree_exists: bool
    current_branch_in_worktree: str | None

    status_lines: list[str]  # e.g. ["M README.md", "?? newfile"]
    changed_files: list[str]
    has_changes: bool

    suggested_commit_message: str
    suggested_next_commands: list[str]

    status: str  # "ready" or "blocked — <reason>"
    notes: list[str]


@dataclass(frozen=True)
class HandoffSnapshotArtifact:
    label: str
    path: str
    exists: bool


@dataclass(frozen=True)
class HandoffSnapshot:
    repo: str
    repo_root: str
    branch: str
    head: str
    git_status_lines: tuple[str, ...]
    manifest_path: str | None
    planner_status: str
    planner_counts: dict[str, int]
    active_issue: int | None
    next_issue: int | None
    stop_reason: str
    resume_command: str
    local_warnings: tuple[str, ...]
    artifacts: tuple[HandoffSnapshotArtifact, ...]
    status: str
    notes: tuple[str, ...]


def _slug_for_commit(title: str) -> str:
    """Create a short slug for commit messages."""
    if not title:
        return "task"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "task"


def _normalize_label_names(labels: list[object]) -> list[str]:
    """Return label names from either strings or GitHub label dicts."""
    names: list[str] = []
    for label in labels:
        if isinstance(label, str):
            names.append(label)
        elif isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def _infer_commit_prefix(labels: list[object]) -> str:
    """Very lightweight prefix inference."""
    label_names = _normalize_label_names(labels)
    if any(lbl.startswith("area:docs") for lbl in label_names):
        return "docs:"
    if any(lbl.startswith("area:tests") for lbl in label_names):
        return "test:"
    return "work:"


def _parse_status_path(line: str) -> str:
    """Return file path from git status --short style output.

    Handles both raw porcelain lines, e.g. " M README.md",
    and normalized/trimmed lines, e.g. "M README.md".
    """
    if not line.strip():
        return ""

    if line.startswith("?? "):
        return line[3:].strip()

    # Raw porcelain v1 uses two status columns followed by a space:
    # " M README.md", "M  README.md", "MM README.md".
    if len(line) >= 4 and line[2] == " ":
        return line[3:].strip()

    # Some helpers normalize/strip the leading blank status column,
    # producing "M README.md" instead of " M README.md".
    if len(line) >= 3 and line[1] == " ":
        return line[2:].strip()

    return line.strip()


def _git_output(args: list[str], *, cwd: str | Path = ".") -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _issue_ref(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _planner_active_issue(planner_run: dict[str, Any] | None) -> int | None:
    if planner_run is None:
        return None
    active_tasks = planner_run.get("active_tasks", [])
    if not isinstance(active_tasks, list) or len(active_tasks) != 1:
        return None
    active = active_tasks[0]
    if not isinstance(active, dict):
        return None
    return _issue_ref(active.get("github_issue"))


def _planner_next_issue(planner_run: dict[str, Any] | None) -> int | None:
    if planner_run is None:
        return None
    next_plan = planner_run.get("next")
    if not isinstance(next_plan, dict):
        return None
    next_task = next_plan.get("next")
    if not isinstance(next_task, dict):
        return None
    return _issue_ref(next_task.get("github_issue"))


def _planner_stop_reason(planner_run: dict[str, Any] | None) -> str:
    if planner_run is None:
        return "manifest not provided"
    active_tasks = planner_run.get("active_tasks", [])
    if isinstance(active_tasks, list) and len(active_tasks) > 1:
        return "multiple active manifest tasks require explicit operator selection"
    next_plan = planner_run.get("next")
    if isinstance(next_plan, dict):
        reason = next_plan.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return "none"


def _planner_counts(planner_run: dict[str, Any] | None) -> dict[str, int]:
    if planner_run is None:
        return {}
    counts = planner_run.get("status_counts", {})
    if not isinstance(counts, dict):
        return {}
    compact: dict[str, int] = {}
    for key, value in counts.items():
        try:
            compact[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return compact


def _main_repo_root_for_snapshot(repo_root: Path) -> Path:
    if repo_root.parent.name == "signposter-work":
        candidate = repo_root.parent.parent / "signposter"
        if candidate.exists():
            return candidate
    return repo_root


def _worktree_path_for_issue(repo_root: Path, issue: int) -> Path:
    if repo_root.name == str(issue) and repo_root.parent.name == "signposter-work":
        return repo_root
    return repo_root.parent / "signposter-work" / str(issue)


def _snapshot_artifacts_for_issue(
    repo_root: Path,
    issue: int | None,
) -> tuple[HandoffSnapshotArtifact, ...]:
    if issue is None:
        return ()
    main_repo = _main_repo_root_for_snapshot(repo_root)
    worktree = _worktree_path_for_issue(repo_root, issue)
    paths = [
        ("worker prompt", main_repo / "artifacts" / "prompts" / f"issue-{issue}.md"),
        (
            "worker summary",
            worktree / "artifacts" / "runs" / f"issue-{issue}-worker.summary.md",
        ),
        (
            "worker raw",
            worktree / "artifacts" / "runs" / f"issue-{issue}-worker.raw.txt",
        ),
    ]
    return tuple(
        HandoffSnapshotArtifact(
            label=label,
            path=str(path),
            exists=path.exists(),
        )
        for label, path in paths
    )


def _resume_command(
    *,
    repo: str,
    manifest_path: str | None,
    active_issue: int | None,
    next_issue: int | None,
) -> str:
    if active_issue is not None:
        return f"signposter lifecycle status --repo {repo} --issue {active_issue}"
    if next_issue is not None:
        return f"signposter run --repo {repo} --issue {next_issue} --dry-run"
    if manifest_path:
        return f"signposter planner run --manifest {manifest_path} --sync-github --dry-run"
    return f"signposter control-plane status --repo {repo}"


def build_handoff_snapshot(
    *,
    repo: str,
    manifest_path: str | None = None,
    planner_run: dict[str, Any] | None = None,
    local_warnings: tuple[str, ...] = (),
    cwd: str | Path = ".",
) -> HandoffSnapshot:
    """Build a read-only operator handoff snapshot from existing surfaces."""
    repo_root_raw = _git_output(["rev-parse", "--show-toplevel"], cwd=cwd)
    repo_root = Path(repo_root_raw).resolve() if repo_root_raw else Path(cwd).resolve()
    branch = _git_output(["branch", "--show-current"], cwd=repo_root) or "unknown"
    head = _git_output(["rev-parse", "--short", "HEAD"], cwd=repo_root) or "unknown"
    git_status_lines = tuple(get_git_status_short(cwd=repo_root))
    active_issue = _planner_active_issue(planner_run)
    next_issue = _planner_next_issue(planner_run)
    stop_reason = _planner_stop_reason(planner_run)
    status = (
        "blocked"
        if stop_reason != "none" and active_issue is None and next_issue is None
        else "ready"
    )

    return HandoffSnapshot(
        repo=repo,
        repo_root=str(repo_root),
        branch=branch,
        head=head,
        git_status_lines=git_status_lines,
        manifest_path=manifest_path,
        planner_status=str(planner_run.get("planner_status", "not evaluated"))
        if planner_run is not None
        else "not evaluated",
        planner_counts=_planner_counts(planner_run),
        active_issue=active_issue,
        next_issue=next_issue,
        stop_reason=stop_reason,
        resume_command=_resume_command(
            repo=repo,
            manifest_path=manifest_path,
            active_issue=active_issue,
            next_issue=next_issue,
        ),
        local_warnings=local_warnings,
        artifacts=_snapshot_artifacts_for_issue(repo_root, active_issue or next_issue),
        status=status,
        notes=(
            "Read-only handoff snapshot.",
            "No GitHub mutation was performed.",
            "No manifest mutation was performed.",
            "No lifecycle command was executed.",
            "No backend execution was performed.",
        ),
    )


def _format_issue(value: int | None) -> str:
    return f"#{value}" if value is not None else "none"


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "not evaluated"
    ordered = [
        "total",
        "ready",
        "active",
        "waiting",
        "done",
        "merged",
        "blocked",
        "completed",
    ]
    return " ".join(f"{key}={counts.get(key, 0)}" for key in ordered if key in counts)


def format_handoff_snapshot(snapshot: HandoffSnapshot) -> str:
    """Format a compact handoff snapshot suitable for session resume."""
    lines = [
        "Signposter Handoff Snapshot",
        "",
        "Status:",
        f"  {snapshot.status}",
        "",
        "Repository:",
        f"  repo: {snapshot.repo}",
        f"  root: {snapshot.repo_root}",
        f"  branch: {snapshot.branch}",
        f"  head: {snapshot.head}",
        f"  dirty: {'yes' if snapshot.git_status_lines else 'no'}",
    ]
    if snapshot.git_status_lines:
        lines.append("  changes:")
        lines.extend(f"    {line}" for line in snapshot.git_status_lines[:8])
        if len(snapshot.git_status_lines) > 8:
            lines.append(f"    ... {len(snapshot.git_status_lines) - 8} more")

    lines.extend(
        [
            "",
            "Planner:",
            f"  manifest: {snapshot.manifest_path or 'not provided'}",
            f"  status: {snapshot.planner_status}",
            f"  counts: {_format_counts(snapshot.planner_counts)}",
            "",
            "Current task:",
            f"  active: {_format_issue(snapshot.active_issue)}",
            f"  next: {_format_issue(snapshot.next_issue)}",
            f"  stop reason: {snapshot.stop_reason}",
            "",
            "Lifecycle:",
            "  status: not evaluated",
            f"  command: {snapshot.resume_command}",
            "",
            "PR / CI / review:",
            "  status: not evaluated",
            "  source: run lifecycle status for the active or next issue",
            "",
            "Integration / cleanup:",
            "  status: not evaluated",
            "  source: run lifecycle status for the active or next issue",
            "",
            "Local artifacts:",
        ]
    )
    if snapshot.artifacts:
        for artifact in snapshot.artifacts:
            state = "present" if artifact.exists else "missing"
            lines.append(f"  {artifact.label}: {artifact.path} ({state})")
    else:
        lines.append("  none")

    lines.extend(["", "Recovery / bugs:"])
    if snapshot.local_warnings:
        lines.append("  local warnings:")
        lines.extend(f"    {warning}" for warning in snapshot.local_warnings[:5])
        if len(snapshot.local_warnings) > 5:
            lines.append(f"    ... {len(snapshot.local_warnings) - 5} more")
    else:
        lines.append("  local warnings: none")

    lines.extend(
        [
            "",
            "Resume:",
            f"  command: {snapshot.resume_command}",
            "",
            "Notes:",
        ]
    )
    lines.extend(f"  {note}" for note in snapshot.notes)
    return "\n".join(lines)


def build_handoff_snapshot_artifact(snapshot: HandoffSnapshot) -> dict[str, Any]:
    """Build a compact JSON artifact for planner loop handoff/resume."""
    return {
        "version": "handoff.snapshot-artifact.v0.1",
        "status": snapshot.status,
        "repo": snapshot.repo,
        "repository": {
            "root": snapshot.repo_root,
            "branch": snapshot.branch,
            "head": snapshot.head,
            "dirty": bool(snapshot.git_status_lines),
            "git_status_lines": list(snapshot.git_status_lines),
        },
        "planner": {
            "manifest": snapshot.manifest_path,
            "status": snapshot.planner_status,
            "counts": snapshot.planner_counts,
        },
        "current_task": {
            "active_issue": snapshot.active_issue,
            "next_issue": snapshot.next_issue,
            "stop_reason": snapshot.stop_reason,
            "resume_command": snapshot.resume_command,
        },
        "local_warnings": list(snapshot.local_warnings),
        "artifacts": [
            {
                "label": artifact.label,
                "path": artifact.path,
                "exists": artifact.exists,
            }
            for artifact in snapshot.artifacts
        ],
        "notes": list(snapshot.notes),
    }


def write_handoff_snapshot_artifact(
    snapshot: HandoffSnapshot,
    path: str | Path,
) -> Path:
    """Write a local JSON handoff artifact without external mutation."""
    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(build_handoff_snapshot_artifact(snapshot), indent=2) + "\n",
        encoding="utf-8",
    )
    return artifact_path


def plan_handoff_for_issue(repo: str, issue_number: int) -> HandoffPlan:
    """Produce a HandoffPlan (read-only, no mutations)."""
    from signposter.dispatch import classify_candidate
    from signposter.scan import LabeledItem, fetch_issue_by_number, fetch_issue_context

    # 1. Fetch issue and classify
    item: LabeledItem | None = fetch_issue_by_number(repo, issue_number)
    if item is None:
        return HandoffPlan(
            issue_number=issue_number,
            title="unknown",
            workflow_state=None,
            github_issue_state=None,
            worktree_path=generate_proposed_worktree(issue_number),
            branch=generate_proposed_branch(issue_number, "unknown"),
            worktree_exists=False,
            current_branch_in_worktree=None,
            status_lines=[],
            changed_files=[],
            has_changes=False,
            suggested_commit_message=f"work: issue-{issue_number}",
            suggested_next_commands=[],
            status=f"blocked — could not fetch issue #{issue_number}",
            notes=["No commit, push, PR, merge, or issue close was performed."],
        )

    dispatch = classify_candidate(item)
    workflow_state = dispatch.state

    # Get labels for prefix inference
    context = fetch_issue_context(repo, issue_number) or {}
    labels = context.get("labels", []) if isinstance(context.get("labels"), list) else []

    # Worktree info
    ws = get_worktree_status_for_issue(issue_number, item.title)
    worktree_path = ws["path"]
    expected_branch = ws["branch"]
    worktree_exists = ws["exists"]

    if not worktree_exists:
        return HandoffPlan(
            issue_number=issue_number,
            title=item.title,
            workflow_state=workflow_state,
            github_issue_state="OPEN",  # we don't fetch real state here for simplicity
            worktree_path=worktree_path,
            branch=expected_branch,
            worktree_exists=False,
            current_branch_in_worktree=None,
            status_lines=[],
            changed_files=[],
            has_changes=False,
            suggested_commit_message=(
                f"{_infer_commit_prefix(labels)} issue-{issue_number} "
                f"{_slug_for_commit(item.title)}"
            ),
            suggested_next_commands=[],
            status="blocked — expected worktree is missing",
            notes=["No commit, push, PR, merge, or issue close was performed."],
        )

    # Git status inside the worktree
    status_lines = get_git_status_short(cwd=worktree_path)
    changed_files = []
    for line in status_lines:
        path = _parse_status_path(line)
        if path:
            changed_files.append(path)

    has_changes = len(changed_files) > 0

    current_branch = get_current_branch(cwd=worktree_path)

    # Suggested commit message
    prefix = _infer_commit_prefix(labels)
    slug = _slug_for_commit(item.title)
    suggested_commit = f"{prefix} {slug}"

    # Next commands
    next_cmds = [
        f"git -C {worktree_path} diff",
        f"git -C {worktree_path} add -A",
        f'git -C {worktree_path} commit -m "{suggested_commit}"',
        f"git -C {worktree_path} push -u origin {expected_branch}",
    ]

    # Status determination
    if not has_changes:
        status = "blocked — no changes found in worktree"
    elif workflow_state != "done":
        status = f"blocked — issue is not state:done (current: {workflow_state})"
    else:
        status = "ready"

    notes = [
        "No commit, push, PR, merge, or issue close was performed.",
        "GitHub issue should remain open until explicit integration + close policy exists.",
    ]

    return HandoffPlan(
        issue_number=issue_number,
        title=item.title,
        workflow_state=workflow_state,
        github_issue_state="OPEN",  # conservative default for planning
        worktree_path=worktree_path,
        branch=expected_branch,
        worktree_exists=True,
        current_branch_in_worktree=current_branch,
        status_lines=status_lines,
        changed_files=changed_files,
        has_changes=has_changes,
        suggested_commit_message=suggested_commit,
        suggested_next_commands=next_cmds,
        status=status,
        notes=notes,
    )


def format_handoff_plan(plan: HandoffPlan) -> str:
    """Compact human-readable handoff plan output."""
    lines = [f"Signposter Handoff Plan — Issue #{plan.issue_number}\n"]

    lines.append("Issue:")
    lines.append(f"  title: {plan.title}")
    lines.append(f"  workflow state: {plan.workflow_state or 'unknown'}")
    lines.append(f"  github issue: {plan.github_issue_state or 'open'}")

    lines.append("\nWorktree:")
    lines.append(f"  status: {'available' if plan.worktree_exists else 'missing'}")
    lines.append(f"  path: {plan.worktree_path}")
    lines.append(f"  branch: {plan.branch}")

    if plan.current_branch_in_worktree:
        lines.append(f"  current branch in worktree: {plan.current_branch_in_worktree}")

    lines.append("\nChanges:")
    if plan.has_changes:
        for f in plan.changed_files[:10]:
            lines.append(f"  {f}")
        if len(plan.changed_files) > 10:
            lines.append(f"  ... ({len(plan.changed_files)} total)")
        lines.append(f"  files changed: {len(plan.changed_files)}")
    else:
        lines.append("  (no changes detected)")

    lines.append("\nSuggested commit:")
    lines.append(f"  {plan.suggested_commit_message}")

    if plan.suggested_next_commands:
        lines.append("\nSuggested next commands:")
        for cmd in plan.suggested_next_commands:
            lines.append(f"  {cmd}")

    lines.append("\nStatus:")
    lines.append(f"  {plan.status}")

    if plan.notes:
        lines.append("\nNotes:")
        for n in plan.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)
