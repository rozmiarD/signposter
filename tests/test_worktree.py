"""Tests for worktree planning (HARDENING-007)."""


from signposter.worktree import (
    _slugify_title,
    generate_proposed_branch,
    generate_proposed_worktree,
)


def test_slugify_title_basic():
    assert _slugify_title("Implement feature X") == "implement-feature-x"
    assert _slugify_title("Fix bug in parser!") == "fix-bug-in-parser"


def test_slugify_title_truncates():
    long_title = "This is a very long title that should be truncated for the branch name"
    slug = _slugify_title(long_title, max_len=30)
    assert len(slug) <= 30
    assert not slug.endswith("-")


def test_generate_proposed_branch():
    branch = generate_proposed_branch(42, "Add new worker isolation")
    assert branch.startswith("work/issue-42-")
    assert "add-new-worker-isolation" in branch


def test_generate_proposed_worktree():
    path = generate_proposed_worktree(42)
    assert "signposter-work/42" in path


def test_worktree_plan_blocked_for_done_issue(monkeypatch):
    """Plan for a done issue should be blocked."""
    from signposter.dispatch import DispatchDecision
    from signposter.scan import LabeledItem
    from signposter.worktree import plan_worktree_for_issue

    fake_item = LabeledItem(
        number=99,
        title="Already done task",
        html_url="url",
        labels=["state:done"],
        item_type="issue",
    )

    def fake_fetch(repo, num):
        return fake_item

    def fake_classify(item):
        return DispatchDecision(
            item=item,
            phase="build",
            state="done",
            role="worker",
            risk="low",
            area=None,
            proposed_route="worker",
            proposed_gate="ci",
            reason="test",
        )

    monkeypatch.setattr("signposter.worktree.fetch_issue_by_number", fake_fetch)
    monkeypatch.setattr("signposter.worktree.classify_candidate", fake_classify)
    monkeypatch.setattr("signposter.worktree.get_current_branch", lambda: "main")
    monkeypatch.setattr("signposter.worktree.has_blocking_dirty_changes", lambda: False)
    monkeypatch.setattr("signposter.worktree.branch_exists", lambda b: False)
    monkeypatch.setattr("signposter.worktree.worktree_path_exists", lambda p: False)
    monkeypatch.setattr("signposter.scan.fetch_issue_context", lambda r, n: {"body": ""})
    monkeypatch.setattr("signposter.worktree.is_dependency_blocked", lambda r, b: (False, ""))

    plan = plan_worktree_for_issue("test/repo", 99)

    assert plan.status.startswith("blocked — issue is state:done")
    assert "No branches or worktrees were created" in plan.notes[0]


def test_worktree_plan_ready_for_active_worker(monkeypatch):
    """Plan for an active worker issue with clean tree should be ready."""
    from signposter.dispatch import DispatchDecision
    from signposter.scan import LabeledItem
    from signposter.worktree import plan_worktree_for_issue

    fake_item = LabeledItem(12, "Implement feature X", "url", ["state:active"], "issue")

    def fake_fetch(repo, num):
        return fake_item

    def fake_classify(item):
        return DispatchDecision(
            item=item, phase="build", state="active", role="worker",
            risk="medium", area=None, proposed_route="worker", proposed_gate="ci", reason="test"
        )

    monkeypatch.setattr("signposter.worktree.fetch_issue_by_number", fake_fetch)
    monkeypatch.setattr("signposter.worktree.classify_candidate", fake_classify)
    monkeypatch.setattr("signposter.worktree.get_current_branch", lambda: "main")
    monkeypatch.setattr("signposter.worktree.has_blocking_dirty_changes", lambda: False)
    monkeypatch.setattr("signposter.worktree.branch_exists", lambda b: False)
    monkeypatch.setattr("signposter.worktree.worktree_path_exists", lambda p: False)
    monkeypatch.setattr("signposter.scan.fetch_issue_context", lambda r, n: {"body": ""})
    monkeypatch.setattr("signposter.worktree.is_dependency_blocked", lambda r, b: (False, ""))

    plan = plan_worktree_for_issue("test/repo", 12)

    assert plan.status == "ready"
    assert "work/issue-12-implement-feature-x" in plan.proposed_branch
    assert "signposter-work/12" in plan.proposed_worktree
