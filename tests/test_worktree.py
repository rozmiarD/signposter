"""Tests for worktree planning (HARDENING-007)."""

import pytest

from signposter.worktree import (
    _slugify_title,
    generate_proposed_branch,
    generate_proposed_worktree,
)


def _patch_plan_inputs(
    monkeypatch,
    *,
    state: str = "active",
    route: str = "worker",
    role: str = "worker",
    phase: str = "build",
    dirty: bool = False,
    branch_exists: bool = False,
    remote_branch_exists: bool = False,
    worktree_exists: bool = False,
    dependency_blocked: bool = False,
):
    from signposter.dispatch import DispatchDecision
    from signposter.scan import LabeledItem

    item = LabeledItem(
        number=77,
        title="Safety audit task",
        html_url="url",
        labels=[f"state:{state}", f"role:{role}", f"phase:{phase}"],
        item_type="issue",
    )

    monkeypatch.setattr("signposter.worktree.fetch_issue_by_number", lambda r, n: item)
    monkeypatch.setattr("signposter.worktree.fetch_issue_context", lambda r, n: {"body": ""})
    monkeypatch.setattr(
        "signposter.worktree.classify_candidate",
        lambda i: DispatchDecision(
            item=i,
            phase=phase,
            state=state,
            role=role,
            risk="medium",
            area=None,
            proposed_route=route,
            proposed_gate="ci",
            reason="test",
        ),
    )
    monkeypatch.setattr("signposter.worktree.get_current_branch", lambda: "main")
    monkeypatch.setattr("signposter.worktree.has_blocking_dirty_changes", lambda: dirty)
    monkeypatch.setattr("signposter.worktree.branch_exists", lambda b: branch_exists)
    monkeypatch.setattr(
        "signposter.worktree.remote_branch_exists",
        lambda b: remote_branch_exists,
    )
    monkeypatch.setattr("signposter.worktree.worktree_path_exists", lambda p: worktree_exists)
    monkeypatch.setattr(
        "signposter.worktree.is_dependency_blocked",
        lambda r, b: (
            dependency_blocked,
            "dependency #12 is not complete" if dependency_blocked else "",
        ),
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


def test_get_worktree_status_detects_current_issue_worktree(monkeypatch, tmp_path):
    from signposter.worktree import get_worktree_status_for_issue

    worktree_path = tmp_path / "signposter-work" / "42"
    worktree_path.mkdir(parents=True)
    monkeypatch.chdir(worktree_path)

    status = get_worktree_status_for_issue(42, "Test issue")

    assert status["exists"] is True
    assert status["status"] == "available"
    assert status["path"] == str(worktree_path)


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
    monkeypatch.setattr("signposter.worktree.remote_branch_exists", lambda b: False)
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
    monkeypatch.setattr("signposter.worktree.remote_branch_exists", lambda b: False)
    monkeypatch.setattr("signposter.worktree.worktree_path_exists", lambda p: False)
    monkeypatch.setattr("signposter.scan.fetch_issue_context", lambda r, n: {"body": ""})
    monkeypatch.setattr("signposter.worktree.is_dependency_blocked", lambda r, b: (False, ""))

    plan = plan_worktree_for_issue("test/repo", 12)

    assert plan.status == "ready"
    assert "work/issue-12-implement-feature-x" in plan.proposed_branch
    assert "signposter-work/12" in plan.proposed_worktree
    assert any("Protected base branches" in note for note in plan.notes)


@pytest.mark.parametrize("terminal_state", ["done", "failed", "merged"])
def test_worktree_plan_blocks_terminal_states(monkeypatch, terminal_state):
    from signposter.worktree import plan_worktree_for_issue

    _patch_plan_inputs(monkeypatch, state=terminal_state)

    plan = plan_worktree_for_issue("test/repo", 77)

    assert plan.status == f"blocked — issue is state:{terminal_state}"
    assert plan.working_tree_clean is True
    assert plan.branch_exists is False
    assert plan.worktree_exists is False


def test_worktree_plan_blocks_unresolved_dependencies(monkeypatch):
    from signposter.worktree import plan_worktree_for_issue

    _patch_plan_inputs(monkeypatch, dependency_blocked=True)

    plan = plan_worktree_for_issue("test/repo", 77)

    assert plan.status == "blocked — dependency #12 is not complete"
    assert plan.has_unresolved_dependencies is True
    assert plan.dependency_block_reason == "dependency #12 is not complete"


def test_worktree_plan_blocks_dirty_tree_before_creation(monkeypatch):
    from signposter.worktree import plan_worktree_for_issue

    _patch_plan_inputs(monkeypatch, dirty=True)

    plan = plan_worktree_for_issue("test/repo", 77)

    assert plan.status == "blocked — working tree has uncommitted changes"
    assert plan.working_tree_clean is False


def test_worktree_plan_blocks_existing_branch(monkeypatch):
    from signposter.worktree import format_worktree_plan, plan_worktree_for_issue

    _patch_plan_inputs(monkeypatch, branch_exists=True)

    plan = plan_worktree_for_issue("test/repo", 77)
    output = format_worktree_plan(plan)

    assert plan.status.startswith("blocked — proposed branch already exists:")
    assert plan.branch_exists is True
    assert plan.branch_collision_reason == "local branch already exists"
    assert "Branch collision: local branch already exists." in output
    assert "Inspect local branch: git branch --list" in output
    assert "resume it; otherwise clean or rename manually" in output


def test_worktree_plan_blocks_existing_remote_branch(monkeypatch):
    from signposter.worktree import format_worktree_plan, plan_worktree_for_issue

    _patch_plan_inputs(monkeypatch, remote_branch_exists=True)

    plan = plan_worktree_for_issue("test/repo", 77)
    output = format_worktree_plan(plan)

    assert plan.status.startswith("blocked — proposed remote branch already exists:")
    assert plan.branch_exists is False
    assert plan.remote_branch_exists is True
    assert plan.branch_collision_reason == "remote-tracking branch already exists"
    assert "remote branch exists: yes" in output
    assert "collision reason: remote-tracking branch already exists" in output
    assert "Branch collision: remote branch already exists." in output
    assert "Inspect remote branch: git ls-remote --heads origin" in output
    assert "gh pr list --repo <repo> --head" in output


def test_worktree_plan_blocks_existing_worktree_path(monkeypatch):
    from signposter.worktree import plan_worktree_for_issue

    _patch_plan_inputs(monkeypatch, worktree_exists=True)

    plan = plan_worktree_for_issue("test/repo", 77)

    assert plan.status.startswith("blocked — proposed worktree path already exists:")
    assert plan.worktree_exists is True
    assert plan.branch_collision_reason == "worktree path already exists"


def test_worktree_recovery_reuse_smoke_surfaces_existing_worktree_resume(monkeypatch):
    from signposter.worktree import format_worktree_plan, plan_worktree_for_issue

    _patch_plan_inputs(monkeypatch, worktree_exists=True)

    plan = plan_worktree_for_issue("test/repo", 77)
    output = format_worktree_plan(plan)

    assert plan.status.startswith("blocked — proposed worktree path already exists:")
    assert "Existing worktree detected: inspect it and resume from that path" in output
    assert "Resume worker: signposter run --repo <repo> --issue 77 --execute --worktree" in output
    assert "Manual summary: signposter artifact write-worker-summary" in output
    assert "No branches or worktrees were created." in output


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
    assert "No branches or worktrees were created." in output
    assert "No GitHub mutation was performed." in output


def test_format_worktree_apply_plan_blocked_existing_path_has_recovery_guidance():
    from signposter.worktree import WorktreePlan, format_worktree_apply_plan

    plan = WorktreePlan(
        issue_number=42,
        title="Existing path",
        state="ready",
        route="worker",
        gate="ci",
        base_branch="main",
        proposed_branch="work/issue-42-existing-path",
        proposed_worktree="../signposter-work/42",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=True,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="blocked — proposed worktree path already exists: ../signposter-work/42",
        notes=["No branches or worktrees were created."],
        branch_collision_reason="worktree path already exists",
    )

    output = format_worktree_apply_plan(plan, dry_run=True)

    assert "Refusing to create worktree" in output
    assert "Recovery guidance:" in output
    assert "Worktree collision: expected worktree path already exists." in output
    assert "Inspect worktree path: ../signposter-work/42" in output
    assert "No branches or worktrees were created." in output


def test_worktree_recovery_reuse_smoke_apply_does_not_replace_existing_path(
    monkeypatch,
):
    from signposter.worktree import WorktreePlan, apply_worktree_plan, format_worktree_apply_plan

    plan = WorktreePlan(
        issue_number=42,
        title="Existing path",
        state="ready",
        route="worker",
        gate="ci",
        base_branch="main",
        proposed_branch="work/issue-42-existing-path",
        proposed_worktree="../signposter-work/42",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=True,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="blocked — proposed worktree path already exists: ../signposter-work/42",
        notes=["No branches or worktrees were created."],
        branch_collision_reason="worktree path already exists",
    )
    run_calls = []
    monkeypatch.setattr(
        "signposter.worktree.subprocess.run",
        lambda *args, **kwargs: run_calls.append((args, kwargs)),
    )

    output = format_worktree_apply_plan(plan, dry_run=True)
    commands = apply_worktree_plan(plan, dry_run=False)

    assert commands == []
    assert run_calls == []
    assert "Refusing to create worktree." in output
    assert "Worktree collision: expected worktree path already exists." in output
    assert "No GitHub mutation was performed." in output


def test_format_worktree_apply_plan_blocked_dirty_tree_has_recovery_guidance():
    from signposter.worktree import WorktreePlan, format_worktree_apply_plan

    plan = WorktreePlan(
        issue_number=42,
        title="Dirty tree",
        state="ready",
        route="worker",
        gate="ci",
        base_branch="main",
        proposed_branch="work/issue-42-dirty-tree",
        proposed_worktree="../signposter-work/42",
        working_tree_clean=False,
        branch_exists=False,
        worktree_exists=False,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="blocked — working tree has uncommitted changes",
        notes=["No branches or worktrees were created."],
    )

    output = format_worktree_apply_plan(plan, dry_run=True)

    assert "Refusing to create worktree" in output
    assert "Recovery guidance:" in output
    assert "Working tree is dirty." in output
    assert "Inspect changes: git status --short" in output
    assert "Commit, stash, or clean unrelated changes before retrying." in output


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


def test_worktree_plan_ready_for_reviewer_route_worker_build_human_gate(monkeypatch):
    """Reviewer-route build tasks should be eligible when role is worker."""
    from signposter.dispatch import DispatchDecision
    from signposter.scan import LabeledItem
    from signposter.worktree import plan_worktree_for_issue

    item = LabeledItem(
        number=33,
        title="H033D — Add worktree planning for human-gated reviewer-route tasks",
        labels=[
            "phase:build",
            "state:active",
            "risk:high",
            "role:worker",
            "area:core",
            "gate:human",
        ],
        html_url="https://github.com/test/repo/issues/33",
        item_type="issue",
    )

    monkeypatch.setattr("signposter.worktree.fetch_issue_by_number", lambda r, n: item)
    monkeypatch.setattr("signposter.worktree.fetch_issue_context", lambda r, n: {"body": ""})
    monkeypatch.setattr(
        "signposter.worktree.classify_candidate",
        lambda i: DispatchDecision(
            item=i,
            phase="build",
            state="active",
            role="worker",
            risk="high",
            area="core",
            proposed_route="reviewer",
            proposed_gate="human",
            reason="high-risk human-gated implementation",
        ),
    )
    monkeypatch.setattr("signposter.worktree.get_current_branch", lambda: "main")
    monkeypatch.setattr("signposter.worktree.has_blocking_dirty_changes", lambda: False)
    monkeypatch.setattr("signposter.worktree.branch_exists", lambda b: False)
    monkeypatch.setattr("signposter.worktree.remote_branch_exists", lambda b: False)
    monkeypatch.setattr("signposter.worktree.worktree_path_exists", lambda p: False)
    monkeypatch.setattr("signposter.worktree.is_dependency_blocked", lambda r, b: (False, ""))

    plan = plan_worktree_for_issue("test/repo", 33)

    assert plan.status == "ready"
    assert plan.route == "reviewer"
    assert plan.gate == "human"
    notes = "\n".join(plan.notes)
    assert "Reviewer-route build task is supported" in notes
    assert "Human-gated issue" in notes


def test_worktree_plan_blocks_reviewer_route_non_worker_role(monkeypatch):
    """Reviewer-route worktree planning must stay blocked for non-worker roles."""
    from signposter.dispatch import DispatchDecision
    from signposter.scan import LabeledItem
    from signposter.worktree import plan_worktree_for_issue

    item = LabeledItem(
        number=34,
        title="Reviewer only task",
        labels=[
            "phase:review",
            "state:active",
            "risk:high",
            "role:reviewer",
            "area:core",
            "gate:human",
        ],
        html_url="https://github.com/test/repo/issues/34",
        item_type="issue",
    )

    monkeypatch.setattr("signposter.worktree.fetch_issue_by_number", lambda r, n: item)
    monkeypatch.setattr("signposter.worktree.fetch_issue_context", lambda r, n: {"body": ""})
    monkeypatch.setattr(
        "signposter.worktree.classify_candidate",
        lambda i: DispatchDecision(
            item=i,
            phase="review",
            state="active",
            role="reviewer",
            risk="high",
            area="core",
            proposed_route="reviewer",
            proposed_gate="human",
            reason="review-only task",
        ),
    )
    monkeypatch.setattr("signposter.worktree.get_current_branch", lambda: "main")
    monkeypatch.setattr("signposter.worktree.has_blocking_dirty_changes", lambda: False)
    monkeypatch.setattr("signposter.worktree.branch_exists", lambda b: False)
    monkeypatch.setattr("signposter.worktree.remote_branch_exists", lambda b: False)
    monkeypatch.setattr("signposter.worktree.worktree_path_exists", lambda p: False)
    monkeypatch.setattr("signposter.worktree.is_dependency_blocked", lambda r, b: (False, ""))

    plan = plan_worktree_for_issue("test/repo", 34)

    assert plan.status.startswith("blocked — route is 'reviewer'")
    assert "role:worker phase:build" in plan.status


def test_format_worktree_plan_includes_reviewer_route_human_gate_notes():
    from signposter.worktree import WorktreePlan, format_worktree_plan

    plan = WorktreePlan(
        issue_number=33,
        title="H033D",
        state="active",
        route="reviewer",
        gate="human",
        base_branch="main",
        proposed_branch="work/issue-33-h033d",
        proposed_worktree="../signposter-work/33",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=False,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="ready",
        notes=[
            "No branches or worktrees were created.",
            "Reviewer-route build task is supported because role is worker.",
            "Human-gated issue: local worktree planning is allowed; gate remains separate.",
        ],
    )

    output = format_worktree_plan(plan)

    assert "route: reviewer" in output
    assert "gate: human" in output
    assert "Reviewer-route build task is supported" in output
    assert "Human-gated issue" in output


def test_format_worktree_plan_includes_recovery_hints():
    from signposter.worktree import WorktreePlan, format_worktree_plan

    plan = WorktreePlan(
        issue_number=42,
        title="Resume interrupted worker",
        state="active",
        route="worker",
        gate="ci",
        base_branch="main",
        proposed_branch="work/issue-42-resume-interrupted-worker",
        proposed_worktree="../signposter-work/42",
        working_tree_clean=True,
        branch_exists=False,
        worktree_exists=True,
        has_unresolved_dependencies=False,
        dependency_block_reason=None,
        status="blocked — proposed worktree path already exists: ../signposter-work/42",
        notes=["No branches or worktrees were created."],
        branch_collision_reason="worktree path already exists",
    )

    output = format_worktree_plan(plan)

    assert "Recovery hints:" in output
    assert "Worktree collision: expected worktree path already exists." in output
    assert "Inspect worktree path: ../signposter-work/42" in output
    assert "Existing worktree detected" in output
    assert "signposter run --repo <repo> --issue 42 --execute --worktree" in output
    assert "signposter artifact write-worker-summary" in output
    assert "signposter report --repo <repo> --issue 42" in output
    assert "signposter gate --repo <repo> --issue 42 --dry-run" in output
