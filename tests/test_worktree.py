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


# --- HARDENING-008: guarded worktree apply tests ---


def test_format_worktree_apply_plan_dry_run_ready():
    from signposter.worktree import WorktreePlan, format_worktree_apply_plan

    plan = WorktreePlan(
        issue_number=42,
        title="Test feature",
        state="ready",
        route="worker",
        gate="ci",
        base_branch="main",
        proposed_branch="work/issue-42-test-feature",
        proposed_worktree="../signposter-work/42",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=False,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="ready",
        notes=["No branches or worktrees were created."],
    )

    output = format_worktree_apply_plan(plan, dry_run=True)

    assert "Signposter Worktree Apply Plan — Issue #42" in output
    assert "Status:" in output
    assert "ready" in output
    assert "Would run:" in output
    assert "git worktree add -b work/issue-42-test-feature ../signposter-work/42 main" in output
    assert "DRY RUN" in output


def test_format_worktree_apply_plan_blocked():
    from signposter.worktree import WorktreePlan, format_worktree_apply_plan

    plan = WorktreePlan(
        issue_number=99,
        title="Done task",
        state="done",
        route="worker",
        gate=None,
        base_branch="main",
        proposed_branch="work/issue-99-done-task",
        proposed_worktree="../signposter-work/99",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=False,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="blocked — issue is state:done",
        notes=["No branches or worktrees were created."],
    )

    output = format_worktree_apply_plan(plan, dry_run=True)
    assert "blocked — issue is state:done" in output
    assert "Refusing to create worktree" in output


def test_apply_worktree_plan_dry_run_returns_command_no_subprocess():
    from unittest.mock import patch

    from signposter.worktree import WorktreePlan, apply_worktree_plan

    plan = WorktreePlan(
        issue_number=7,
        title="Small task",
        state="ready",
        route="worker",
        gate=None,
        base_branch="main",
        proposed_branch="work/issue-7-small-task",
        proposed_worktree="../signposter-work/7",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=False,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="ready",
        notes=[],
    )

    with patch("signposter.worktree.subprocess.run") as mock_run:
        cmds = apply_worktree_plan(plan, dry_run=True)

    assert len(cmds) == 1
    assert "git worktree add -b" in cmds[0]
    mock_run.assert_not_called()   # important: no real execution in dry-run


def test_apply_worktree_plan_real_apply_calls_subprocess(monkeypatch):
    from signposter.worktree import WorktreePlan, apply_worktree_plan

    plan = WorktreePlan(
        issue_number=8,
        title="Real apply test",
        state="ready",
        route="worker",
        gate=None,
        base_branch="main",
        proposed_branch="work/issue-8-real",
        proposed_worktree="../signposter-work/8",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=False,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="ready",
        notes=[],
    )

    called = []

    def fake_run(cmd, check, capture_output, text):
        called.append(cmd)
        # simulate success
        class FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        return FakeResult()

    monkeypatch.setattr("signposter.worktree.subprocess.run", fake_run)

    cmds = apply_worktree_plan(plan, dry_run=False)

    assert len(cmds) == 1
    assert len(called) == 1
    assert called[0][0] == "git"
    assert called[0][1] == "worktree"
    assert called[0][2] == "add"
    assert called[0][3] == "-b"   # confirms list form, no shell


def test_apply_worktree_plan_refuses_blocked_plan():
    from signposter.worktree import WorktreePlan, apply_worktree_plan

    plan = WorktreePlan(
        issue_number=5,
        title="Blocked",
        state="done",
        route="worker",
        gate=None,
        base_branch="main",
        proposed_branch="work/issue-5-blocked",
        proposed_worktree="../signposter-work/5",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=False,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="blocked — issue is state:done",
        notes=[],
    )

    cmds = apply_worktree_plan(plan, dry_run=False)
    assert cmds == []   # nothing executed or planned when blocked
