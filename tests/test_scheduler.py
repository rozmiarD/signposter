from __future__ import annotations

from unittest.mock import patch

from signposter.scan import LabeledItem
from signposter.scheduler import SchedulerNext, format_scheduler_next, select_next_issue


def _issue(number: int, labels: list[str]) -> LabeledItem:
    return LabeledItem(
        number=number,
        title=f"Issue {number}",
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=labels,
        item_type="issue",
    )


def test_scheduler_selects_first_ready_issue_without_manifest() -> None:
    issues = [
        _issue(1, ["state:done"]),
        _issue(2, ["state:active"]),
        _issue(3, ["state:ready", "phase:build"]),
    ]

    with (
        patch("signposter.scheduler.fetch_open_issues", return_value=issues),
        patch("signposter.scheduler.fetch_issue_context", return_value={"body": ""}),
        patch("signposter.scheduler.is_dependency_blocked", return_value=(False, "none")),
    ):
        result = select_next_issue("example/repo")

    assert result.status == "ready"
    assert result.issue is not None
    assert result.issue.number == 3
    assert "#1: state:done" in result.skipped
    assert "#2: state:active" in result.skipped


def test_scheduler_skips_dependency_blocked_ready_issue() -> None:
    issues = [
        _issue(3, ["state:ready"]),
        _issue(4, ["state:ready"]),
    ]

    with (
        patch("signposter.scheduler.fetch_open_issues", return_value=issues),
        patch("signposter.scheduler.fetch_issue_context", return_value={"body": ""}),
        patch(
            "signposter.scheduler.is_dependency_blocked",
            side_effect=[(True, "blocked by #2 -> state:active"), (False, "none")],
        ),
    ):
        result = select_next_issue("example/repo")

    assert result.status == "ready"
    assert result.issue is not None
    assert result.issue.number == 4
    assert "#3: blocked by #2 -> state:active" in result.skipped


def test_scheduler_completed_when_no_ready_issue() -> None:
    with patch("signposter.scheduler.fetch_open_issues", return_value=[]):
        result = select_next_issue("example/repo")

    assert result.status == "completed"
    assert result.issue is None
    assert "no open dependency-clear state:ready issue found" in result.reason


def test_scheduler_format_contains_safety_notes() -> None:
    scheduled = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="none",
        skipped=[],
        notes=["No GitHub mutation was performed."],
    )

    out = format_scheduler_next(scheduled)

    assert "Signposter Scheduler Next" in out
    assert "No GitHub mutation was performed." in out
