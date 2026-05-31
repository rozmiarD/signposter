from __future__ import annotations

from unittest.mock import patch

from signposter.scan import LabeledItem
from signposter.scheduler import (
    SchedulerNext,
    build_scheduler_graph,
    format_scheduler_explain,
    format_scheduler_graph,
    format_scheduler_next,
    parse_graph_metadata,
    select_next_issue,
)


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


def test_scheduler_prefers_unblocked_side_task_before_mainline() -> None:
    issues = [
        _issue(10, ["state:ready"]),
        _issue(11, ["state:ready"]),
    ]
    bodies = {
        10: {"body": "Mainline: H036"},
        11: {"body": "Side-Task: yes\nParent: #10\nReturn-To: #12"},
    }

    with (
        patch("signposter.scheduler.fetch_open_issues", return_value=issues),
        patch(
            "signposter.scheduler.fetch_issue_context",
            side_effect=lambda repo, number: bodies[number],
        ),
        patch("signposter.scheduler.is_dependency_blocked", return_value=(False, "none")),
    ):
        result = select_next_issue("example/repo")

    assert result.status == "ready"
    assert result.issue is not None
    assert result.issue.number == 11
    assert "side-task" in result.reason


def test_scheduler_does_not_select_blocked_side_task_over_mainline() -> None:
    issues = [
        _issue(10, ["state:ready"]),
        _issue(11, ["state:ready"]),
    ]
    bodies = {
        10: {"body": "Mainline: H036"},
        11: {"body": "Side-Task: yes\nDepends-On: #99"},
    }

    with (
        patch("signposter.scheduler.fetch_open_issues", return_value=issues),
        patch(
            "signposter.scheduler.fetch_issue_context",
            side_effect=lambda repo, number: bodies[number],
        ),
        patch(
            "signposter.scheduler.is_dependency_blocked",
            side_effect=[(False, "none"), (True, "blocked by #99 -> state:active")],
        ),
    ):
        result = select_next_issue("example/repo")

    assert result.status == "ready"
    assert result.issue is not None
    assert result.issue.number == 10
    assert "#11: blocked by #99 -> state:active" in result.skipped


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


def test_scheduler_explain_shows_selected_and_skipped() -> None:
    scheduled = SchedulerNext(
        repo="example/repo",
        status="ready",
        issue=_issue(3, ["state:ready"]),
        reason="first ready",
        skipped=["#1: state:done", "#2: state:active"],
        notes=["No GitHub mutation was performed."],
    )

    out = format_scheduler_explain(scheduled)

    assert "Signposter Scheduler Explain" in out
    assert "selected: #3 — Issue 3" in out
    assert "#1: state:done" in out
    assert "No GitHub mutation was performed." in out


def test_scheduler_explain_shows_none_when_completed() -> None:
    scheduled = SchedulerNext(
        repo="example/repo",
        status="completed",
        issue=None,
        reason="none ready",
        skipped=[],
        notes=["No GitHub mutation was performed."],
    )

    out = format_scheduler_explain(scheduled)

    assert "selected: none" in out
    assert "Skipped:\n  none" in out


def test_parse_graph_metadata_empty_body() -> None:
    meta = parse_graph_metadata(None)

    assert meta.depends_on == []
    assert meta.mainline is None
    assert meta.parent is None
    assert meta.return_to is None
    assert meta.side_task is False


def test_parse_graph_metadata_full_body() -> None:
    body = """Task: example

Depends-On: #50, #51
Mainline: H036
Parent: #48
Return-To: #52
Side-Task: yes
"""

    meta = parse_graph_metadata(body)

    assert meta.depends_on == [50, 51]
    assert meta.mainline == "H036"
    assert meta.parent == 48
    assert meta.return_to == 52
    assert meta.side_task is True


def test_parse_graph_metadata_side_task_false() -> None:
    meta = parse_graph_metadata("Side-Task: no")

    assert meta.side_task is False


def test_build_scheduler_graph_includes_graph_metadata() -> None:
    issues = [
        _issue(52, ["state:ready"]),
        _issue(53, ["state:active"]),
    ]
    bodies = {
        52: {"body": "Depends-On: #51\nMainline: H036"},
        53: {"body": "Parent: #52\nReturn-To: #54\nSide-Task: yes"},
    }

    with (
        patch("signposter.scheduler.fetch_open_issues", return_value=issues),
        patch(
            "signposter.scheduler.fetch_issue_context",
            side_effect=lambda repo, number: bodies[number],
        ),
    ):
        graph = build_scheduler_graph("example/repo")

    assert graph.repo == "example/repo"
    assert [item.number for item in graph.items] == [52, 53]
    assert graph.items[0].metadata.depends_on == [51]
    assert graph.items[0].metadata.mainline == "H036"
    assert graph.items[1].metadata.parent == 52
    assert graph.items[1].metadata.return_to == 54
    assert graph.items[1].metadata.side_task is True


def test_format_scheduler_graph_shows_key_fields() -> None:
    issues = [_issue(52, ["state:ready"])]
    with (
        patch("signposter.scheduler.fetch_open_issues", return_value=issues),
        patch(
            "signposter.scheduler.fetch_issue_context",
            return_value={"body": "Depends-On: #51\nMainline: H036"},
        ),
    ):
        graph = build_scheduler_graph("example/repo")

    out = format_scheduler_graph(graph)

    assert "Signposter Scheduler Graph" in out
    assert "#52 — Issue 52" in out
    assert "depends on: #51" in out
    assert "mainline: H036" in out
    assert "No GitHub mutation was performed." in out
