"""Tests for handoff planning (HARDENING-012)."""

from unittest.mock import patch

from signposter.handoff import (
    HandoffSnapshot,
    HandoffSnapshotArtifact,
    build_handoff_snapshot,
    format_handoff_plan,
    format_handoff_snapshot,
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


def test_handoff_commit_prefix_accepts_github_label_dicts():
    from signposter.handoff import _infer_commit_prefix

    labels = [{"name": "area:docs"}, {"name": "state:done"}]

    assert _infer_commit_prefix(labels) == "docs:"

def test_handoff_parse_status_path_preserves_file_name_for_trimmed_modified_status():
    from signposter.handoff import _parse_status_path

    assert _parse_status_path("M README.md") == "README.md"


def test_handoff_parse_status_path_preserves_file_name_for_raw_porcelain_modified_status():
    from signposter.handoff import _parse_status_path

    assert _parse_status_path(" M README.md") == "README.md"


def test_handoff_parse_status_path_handles_untracked_file():
    from signposter.handoff import _parse_status_path

    assert _parse_status_path("?? notes/new.md") == "notes/new.md"


def test_format_handoff_snapshot_contains_resume_sections() -> None:
    snapshot = HandoffSnapshot(
        repo="ExatronOmega/signposter",
        repo_root="/repo/signposter",
        branch="main",
        head="abc1234",
        git_status_lines=(),
        manifest_path="docs/roadmaps/h050-seed-manifest.json",
        planner_status="active",
        planner_counts={"total": 80, "active": 1, "merged": 56, "waiting": 23},
        active_issue=427,
        next_issue=None,
        stop_reason="none",
        resume_command="signposter lifecycle status --repo ExatronOmega/signposter --issue 427",
        local_warnings=("stale local branch: work/issue-1-old",),
        artifacts=(
            HandoffSnapshotArtifact(
                label="worker prompt",
                path="/repo/signposter/artifacts/prompts/issue-427.md",
                exists=True,
            ),
        ),
        status="ready",
        notes=("No GitHub mutation was performed.",),
    )

    output = format_handoff_snapshot(snapshot)

    assert "Signposter Handoff Snapshot" in output
    assert "Status:\n  ready" in output
    assert "Planner:" in output
    assert "counts: total=80 active=1 waiting=23 merged=56" in output
    assert "Current task:\n  active: #427\n  next: none" in output
    assert "Local artifacts:" in output
    assert "worker prompt: /repo/signposter/artifacts/prompts/issue-427.md (present)" in output
    assert "Resume:" in output
    assert "No GitHub mutation was performed." in output


def test_build_handoff_snapshot_uses_manifest_active_issue_for_resume(
    tmp_path,
) -> None:
    planner_run = {
        "planner_status": "active",
        "status_counts": {"total": 2, "active": 1, "waiting": 1},
        "next": {"next": None, "reason": "active task in progress"},
        "active_tasks": [{"key": "H050-057", "github_issue": 427, "state": "active"}],
    }
    repo = tmp_path / "signposter"
    repo.mkdir()

    with patch("signposter.handoff._git_output") as mock_git, \
         patch("signposter.handoff.get_git_status_short", return_value=[]):
        mock_git.side_effect = [
            str(repo),
            "main",
            "abc1234",
        ]

        snapshot = build_handoff_snapshot(
            repo="ExatronOmega/signposter",
            manifest_path="docs/roadmaps/h050-seed-manifest.json",
            planner_run=planner_run,
            cwd=repo,
        )

    assert snapshot.status == "ready"
    assert snapshot.active_issue == 427
    assert snapshot.next_issue is None
    assert snapshot.resume_command == (
        "signposter lifecycle status --repo ExatronOmega/signposter --issue 427"
    )
    assert snapshot.repo_root == str(repo)


def test_build_handoff_snapshot_anchors_artifacts_from_worktree_root(tmp_path) -> None:
    base = tmp_path / "projects"
    main_repo = base / "signposter"
    worktree = base / "signposter-work" / "427"
    main_repo.mkdir(parents=True)
    worktree.mkdir(parents=True)
    planner_run = {
        "planner_status": "active",
        "status_counts": {"total": 1, "active": 1},
        "next": {"next": None},
        "active_tasks": [{"key": "H050-057", "github_issue": 427, "state": "active"}],
    }

    with patch("signposter.handoff._git_output") as mock_git, \
         patch("signposter.handoff.get_git_status_short", return_value=[]):
        mock_git.side_effect = [
            str(worktree),
            "work/issue-427-task",
            "abc1234",
        ]

        snapshot = build_handoff_snapshot(
            repo="ExatronOmega/signposter",
            manifest_path="docs/roadmaps/h050-seed-manifest.json",
            planner_run=planner_run,
            cwd=worktree,
        )

    artifact_paths = {artifact.label: artifact.path for artifact in snapshot.artifacts}

    assert artifact_paths["worker prompt"] == str(
        main_repo / "artifacts" / "prompts" / "issue-427.md"
    )
    assert artifact_paths["worker summary"] == str(
        worktree / "artifacts" / "runs" / "issue-427-worker.summary.md"
    )
