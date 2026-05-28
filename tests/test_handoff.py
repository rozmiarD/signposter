"""Tests for handoff planning (HARDENING-012)."""

from unittest.mock import patch

from signposter.handoff import (
    format_handoff_plan,
    plan_handoff_for_issue,
)


def test_plan_handoff_blocks_when_worktree_missing():
    with patch("signposter.handoff.get_worktree_status_for_issue") as mock_ws, \
         patch("signposter.scan.fetch_issue_by_number") as mock_fetch:

        mock_fetch.return_value = type("Item", (), {
            "number": 99, "title": "Some task", "labels": []
        })()

        mock_ws.return_value = {
            "status": "missing",
            "path": "../signposter-work/99",
            "branch": "work/issue-99-some-task",
            "exists": False,
        }

        plan = plan_handoff_for_issue("test/repo", 99)

        assert plan.status == "blocked — expected worktree is missing"
        assert "No commit, push, PR, merge, or issue close" in plan.notes[0]


def test_plan_handoff_detects_changes_and_suggests_commit():
    fake_item = type("Item", (), {
        "number": 4,
        "title": "Test task: isolated worker README note",
        "labels": ["area:docs", "state:done"],
    })()

    with patch("signposter.scan.fetch_issue_by_number", return_value=fake_item), \
         patch("signposter.handoff.get_worktree_status_for_issue") as mock_ws, \
         patch("signposter.handoff.get_git_status_short") as mock_status, \
         patch("signposter.handoff.get_current_branch") as mock_branch, \
         patch("signposter.scan.fetch_issue_context") as mock_ctx, \
         patch("signposter.dispatch.classify_candidate") as mock_classify:

        mock_ws.return_value = {
            "status": "available",
            "path": "../signposter-work/4",
            "branch": "work/issue-4-test-task-isolated-worker-readme-note",
            "exists": True,
        }

        mock_status.return_value = [" M README.md"]
        mock_branch.return_value = "work/issue-4-test-task-isolated-worker-readme-note"
        mock_ctx.return_value = {"labels": ["area:docs", "state:done"]}

        # Make classify return state=done
        mock_classify.return_value = type("D", (), {"state": "done", "proposed_route": "worker"})()

        plan = plan_handoff_for_issue("test/repo", 4)

        assert plan.worktree_exists is True
        assert plan.has_changes is True
        assert "README.md" in plan.changed_files
        assert "docs:" in plan.suggested_commit_message
        assert "No commit, push, PR, merge, or issue close" in plan.notes[0]
        assert plan.status == "ready"  # because we mocked state:done in classification indirectly


def test_format_handoff_plan_contains_key_sections():
    from signposter.handoff import HandoffPlan

    plan = HandoffPlan(
        issue_number=4,
        title="Test task",
        workflow_state="done",
        github_issue_state="OPEN",
        worktree_path="../signposter-work/4",
        branch="work/issue-4-xxx",
        worktree_exists=True,
        current_branch_in_worktree="work/issue-4-xxx",
        status_lines=["M README.md"],
        changed_files=["README.md"],
        has_changes=True,
        suggested_commit_message="docs: add isolated worker note",
        suggested_next_commands=["git add ..."],
        status="ready",
        notes=["No commit, push, PR, merge, or issue close was performed."],
    )

    output = format_handoff_plan(plan)

    assert "Signposter Handoff Plan — Issue #4" in output
    assert "workflow state: done" in output
    assert "github issue: OPEN" in output
    assert "work/issue-4-xxx" in output
    assert "M README.md" in output or "README.md" in output
    assert "No commit, push, PR, merge, or issue close was performed" in output
    assert "docs: add isolated worker note" in output
