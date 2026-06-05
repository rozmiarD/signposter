"""Tests for PR planning (HARDENING-013)."""

import subprocess

from signposter.comments import contains_auto_close_keyword
from signposter.handoff import HandoffPlan
from signposter.pr import (
    edit_github_issue_with_timeout,
    format_github_command_result,
    format_github_issue_edit_result,
    format_github_issue_read_result,
    format_pr_ci_pending_timeout_status,
    format_pr_plan,
    plan_pr_ci_pending_timeout_status,
    plan_pr_for_issue,
    read_github_issue_with_timeout,
    run_github_command_with_timeout,
)


def _handoff_plan(
    *,
    has_changes: bool,
    changed_files: list[str] | None = None,
    workflow_state: str = "done",
    current_branch: str = "work/issue-4-test-task",
    worktree_exists: bool = True,
    suggested_commit_message: str = "docs: test-task",
) -> HandoffPlan:
    return HandoffPlan(
        issue_number=4,
        title="Test task",
        workflow_state=workflow_state,
        github_issue_state="OPEN",
        worktree_path="../signposter-work/4",
        branch="work/issue-4-test-task",
        worktree_exists=worktree_exists,
        current_branch_in_worktree=current_branch,
        status_lines=["M README.md"] if has_changes else [],
        changed_files=changed_files or [],
        has_changes=has_changes,
        suggested_commit_message=suggested_commit_message,
        suggested_next_commands=["git commit ...", "git push ..."],
        status="ready" if has_changes else "blocked — no changes found in worktree",
        notes=["No commit, push, PR, merge, or issue close was performed."],
    )


def test_pr_plan_blocks_when_worktree_has_uncommitted_changes(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=True,
            changed_files=["README.md"],
        ),
    )

    plan = plan_pr_for_issue("test/repo", 4)

    assert plan.status.startswith("blocked — worktree has uncommitted changes")
    assert plan.has_uncommitted_changes is True
    assert plan.changed_files == ["README.md"]


def test_github_command_timeout_wrapper_records_completed_attempt():
    def fake_run(command, **kwargs):
        assert command == ["gh", "pr", "view", "4"]
        assert kwargs["timeout"] == 3
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return type("Proc", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    result = run_github_command_with_timeout(
        ["gh", "pr", "view", "4"],
        timeout_seconds=3,
        run_command=fake_run,
    )
    output = format_github_command_result(result)

    assert result.status == "completed"
    assert result.returncode == 0
    assert result.stdout == "ok"
    assert "Status:\n  completed" in output
    assert "No follow-up GitHub mutation was performed by this wrapper." in output


def test_github_command_timeout_wrapper_records_timeout():
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output="partial stdout",
            stderr="partial stderr",
        )

    result = run_github_command_with_timeout(
        ("gh", "issue", "edit", "4"),
        timeout_seconds=2,
        run_command=fake_run,
    )
    output = format_github_command_result(result)

    assert result.status == "timeout"
    assert result.returncode is None
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert "Status:\n  timeout" in output
    assert "Callers must stop after timeout unless an explicit recovery path is planned." in output


def test_github_command_formatter_includes_bounded_stderr_excerpt():
    hidden_tail = "line-7 hidden tail"

    def fake_run(command, **kwargs):
        return type(
            "Proc",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "\n".join(
                    [
                        "line-1 failed",
                        "line-2 context",
                        "line-3 context",
                        "line-4 context",
                        "line-5 context",
                        "line-6 context",
                        hidden_tail,
                    ]
                ),
            },
        )()

    result = run_github_command_with_timeout(
        ["gh", "issue", "edit", "4"],
        run_command=fake_run,
    )
    output = format_github_command_result(result)

    assert result.status == "failed"
    assert "stderr: present" in output
    assert "stderr excerpt (bounded):" in output
    assert "line-1 failed" in output
    assert "... (truncated)" in output
    assert hidden_tail not in output


def test_github_command_formatter_redacts_secret_stderr_excerpt():
    token = "ghp_" + ("A" * 30)

    def fake_run(command, **kwargs):
        return type(
            "Proc",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": f"failed with token {token}",
            },
        )()

    result = run_github_command_with_timeout(
        ["gh", "issue", "edit", "4"],
        run_command=fake_run,
    )
    output = format_github_command_result(result)

    assert token not in output
    assert "[REDACTED:github-token]" in output
    assert result.stderr.endswith(token)


def test_github_command_timeout_wrapper_rejects_non_gh_command():
    try:
        run_github_command_with_timeout(["git", "status"])
    except ValueError as exc:
        assert "commands starting with 'gh'" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_github_issue_read_timeout_helper_builds_bounded_read_command():
    def fake_run(command, **kwargs):
        assert command == [
            "gh",
            "issue",
            "view",
            "44",
            "-R",
            "owner/repo",
            "--json",
            "number,title,state",
        ]
        assert kwargs["timeout"] == 4
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return type("Proc", (), {"returncode": 0, "stdout": '{"number":44}', "stderr": ""})()

    result = read_github_issue_with_timeout(
        "owner/repo",
        44,
        fields=("number", "title", "state"),
        timeout_seconds=4,
        run_command=fake_run,
    )
    output = format_github_issue_read_result("owner/repo", 44, result)

    assert result.status == "completed"
    assert result.returncode == 0
    assert result.stdout == '{"number":44}'
    assert "Signposter GitHub Issue Read Result" in output
    assert "No GitHub mutation was performed." in output


def test_github_issue_read_timeout_helper_records_timeout():
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output="partial issue output",
            stderr="partial issue error",
        )

    result = read_github_issue_with_timeout(
        "owner/repo",
        44,
        timeout_seconds=5,
        run_command=fake_run,
    )
    output = format_github_issue_read_result("owner/repo", 44, result)

    assert result.status == "timeout"
    assert result.returncode is None
    assert result.stdout == "partial issue output"
    assert result.stderr == "partial issue error"
    assert "Status:\n  timeout" in output
    assert "stderr excerpt (bounded):" in output
    assert "partial issue error" in output
    assert "callers must stop after timeout before later mutations" in output


def test_github_issue_edit_timeout_helper_builds_bounded_edit_command():
    def fake_run(command, **kwargs):
        assert command == [
            "gh",
            "issue",
            "edit",
            "45",
            "-R",
            "owner/repo",
            "--add-label",
            "state:active,gate:ci",
            "--remove-label",
            "state:ready",
        ]
        assert kwargs["timeout"] == 6
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    result = edit_github_issue_with_timeout(
        "owner/repo",
        45,
        add_labels=("state:active", "gate:ci"),
        remove_labels=("state:ready",),
        timeout_seconds=6,
        run_command=fake_run,
    )
    output = format_github_issue_edit_result("owner/repo", 45, result)

    assert result.status == "completed"
    assert result.returncode == 0
    assert "Signposter GitHub Issue Edit Result" in output
    assert "This helper is for guarded apply paths only." in output


def test_github_issue_edit_timeout_helper_records_timeout():
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output="partial edit output",
            stderr="partial edit error",
        )

    result = edit_github_issue_with_timeout(
        "owner/repo",
        45,
        state="closed",
        timeout_seconds=7,
        run_command=fake_run,
    )
    output = format_github_issue_edit_result("owner/repo", 45, result)

    assert result.status == "timeout"
    assert result.returncode is None
    assert result.stdout == "partial edit output"
    assert result.stderr == "partial edit error"
    assert "Status:\n  timeout" in output
    assert "stderr excerpt (bounded):" in output
    assert "partial edit error" in output
    assert "Callers must stop after timeout before any later mutation." in output


def test_github_issue_edit_timeout_helper_rejects_empty_edit():
    try:
        edit_github_issue_with_timeout("owner/repo", 45)
    except ValueError as exc:
        assert "at least one explicit edit argument" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_pr_ci_pending_status_reports_waiting_before_timeout():
    result = plan_pr_ci_pending_timeout_status(
        "owner/repo",
        44,
        checks_status="pending",
        successful_checks=1,
        failing_checks=0,
        pending_checks=2,
        elapsed_seconds=120,
        timeout_seconds=300,
    )
    output = format_pr_ci_pending_timeout_status(result)

    assert result.status == "pending — CI checks still running"
    assert result.reason == "2 pending check(s), 180s wait budget remaining"
    assert "Signposter PR CI Status — PR #44" in output
    assert "inspect command: gh pr checks 44 --repo owner/repo" in output
    assert "No GitHub mutation was performed." in output


def test_pr_ci_pending_status_blocks_after_timeout():
    result = plan_pr_ci_pending_timeout_status(
        "owner/repo",
        44,
        checks_status="pending",
        successful_checks=1,
        failing_checks=0,
        pending_checks=2,
        elapsed_seconds=301,
        timeout_seconds=300,
    )
    output = format_pr_ci_pending_timeout_status(result)

    assert result.status == "blocked — CI pending timeout"
    assert result.reason == "2 pending check(s) exceeded 300s wait budget"
    assert "Status:\n  blocked — CI pending timeout" in output
    assert "Callers must stop after pending timeout before merge or integration." in output
    assert "No merge was performed." in output


def test_pr_ci_pending_status_ready_when_checks_pass():
    result = plan_pr_ci_pending_timeout_status(
        "owner/repo",
        44,
        checks_status="pass",
        successful_checks=3,
        failing_checks=0,
        pending_checks=0,
        elapsed_seconds=12,
        timeout_seconds=300,
    )

    assert result.status == "ready"
    assert result.reason == "PR checks passed"
    assert result.pending_checks == 0


def test_pr_plan_ready_for_clean_branch_with_committed_changes(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(has_changes=False),
    )
    monkeypatch.setattr(
        "signposter.pr._get_branch_changed_files",
        lambda worktree, base, source: ["README.md"],
    )

    plan = plan_pr_for_issue("test/repo", 4)

    assert plan.status == "ready"
    assert plan.base_branch == "main"
    assert plan.source_branch == "work/issue-4-test-task"
    assert plan.changed_files == ["README.md"]
    assert "Related issue: #4" in plan.suggested_pr_body
    assert "Closes" not in plan.suggested_pr_body


def test_pr_plan_title_body_are_stable_and_non_closing(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=False,
            changed_files=[],
            suggested_commit_message="work: h050-036-pr-plan-title-and-body-determinism",
        ),
    )
    monkeypatch.setattr(
        "signposter.pr._get_branch_changed_files",
        lambda worktree, base, source: ["src/signposter/pr.py", "tests/test_pr.py"],
    )

    plan = plan_pr_for_issue("test/repo", 4)

    assert plan.suggested_pr_title == "work: h050-036-pr-plan-title-and-body-determinism"
    assert plan.suggested_pr_body == "\n".join(
        [
            "## Summary",
            "",
            "- Signposter handoff for issue #4.",
            "- Source branch: `work/issue-4-test-task`",
            "- Base branch: `main`",
            "",
            "## Changed files",
            "",
            "- `src/signposter/pr.py`",
            "- `tests/test_pr.py`",
            "",
            "## Safety notes",
            "",
            "- Generated by Signposter PR planning surface.",
            "- No merge or issue close is implied by this PR.",
            "- Issue should remain open until explicit integration/close policy.",
            "",
            "Related issue: #4",
        ]
    )
    assert contains_auto_close_keyword(plan.suggested_pr_title) is False
    assert contains_auto_close_keyword(plan.suggested_pr_body) is False
    assert "Closes #4" not in plan.suggested_pr_body
    assert "Fixes #4" not in plan.suggested_pr_body


def test_format_pr_plan_preserves_safe_related_issue_wording(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=False,
            suggested_commit_message="work: h050-036-pr-plan-title-and-body-determinism",
        ),
    )
    monkeypatch.setattr(
        "signposter.pr._get_branch_changed_files",
        lambda worktree, base, source: ["tests/test_pr.py"],
    )

    output = format_pr_plan(plan_pr_for_issue("test/repo", 4))

    assert "title: work: h050-036-pr-plan-title-and-body-determinism" in output
    assert "Related issue: #4" in output
    assert "No merge or issue close is implied by this PR." in output
    assert "No PR, merge, push, close, or GitHub mutation was performed." in output
    assert "Closes #4" not in output
    assert "Fixes #4" not in output


def test_pr_plan_generated_body_and_output_pass_auto_close_scanner_smoke(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=False,
            suggested_commit_message="work: h051-051-pr-body-auto-close-scanner-smoke",
        ),
    )
    monkeypatch.setattr(
        "signposter.pr._get_branch_changed_files",
        lambda worktree, base, source: ["src/signposter/pr.py", "tests/test_pr.py"],
    )

    plan = plan_pr_for_issue("test/repo", 4)
    output = format_pr_plan(plan)

    assert plan.status == "ready"
    assert contains_auto_close_keyword(plan.suggested_pr_title) is False
    assert contains_auto_close_keyword(plan.suggested_pr_body) is False
    assert contains_auto_close_keyword(output) is False
    assert "Related issue: #4" in plan.suggested_pr_body


def test_pr_plan_blocks_auto_close_keyword_in_suggested_metadata(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=False,
            suggested_commit_message="fix: closes #4",
        ),
    )
    monkeypatch.setattr(
        "signposter.pr._get_branch_changed_files",
        lambda worktree, base, source: ["README.md"],
    )

    plan = plan_pr_for_issue("test/repo", 4)

    assert plan.status == "blocked — suggested PR metadata contains auto-close keyword"


def test_pr_plan_blocks_url_auto_close_keyword_in_suggested_metadata(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=False,
            suggested_commit_message=(
                "work: resolves https://github.com/ExatronOmega/signposter/issues/4"
            ),
        ),
    )
    monkeypatch.setattr(
        "signposter.pr._get_branch_changed_files",
        lambda worktree, base, source: ["README.md"],
    )

    plan = plan_pr_for_issue("test/repo", 4)

    assert plan.status == "blocked — suggested PR metadata contains auto-close keyword"


def test_pr_plan_blocks_extended_auto_close_keyword_in_suggested_metadata(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=False,
            suggested_commit_message="work: fixes ExatronOmega/signposter#4",
        ),
    )
    monkeypatch.setattr(
        "signposter.pr._get_branch_changed_files",
        lambda worktree, base, source: ["README.md"],
    )

    plan = plan_pr_for_issue("test/repo", 4)

    assert plan.status == "blocked — suggested PR metadata contains auto-close keyword"


def test_pr_plan_blocks_when_not_state_done(monkeypatch):
    monkeypatch.setattr(
        "signposter.pr.plan_handoff_for_issue",
        lambda repo, issue: _handoff_plan(
            has_changes=False,
            workflow_state="active",
        ),
    )

    plan = plan_pr_for_issue("test/repo", 4)

    assert plan.status == "blocked — issue is not state:done (current: active)"


def test_format_pr_plan_contains_key_sections():
    plan = _handoff_plan(has_changes=False)
    pr_plan = plan_pr_for_issue_from_handoff_for_test(plan, ["README.md"])

    output = format_pr_plan(pr_plan)

    assert "Signposter PR Plan — Issue #4" in output
    assert "base: main" in output
    assert "head: work/issue-4-test-task" in output
    assert "README.md" in output
    assert "gh pr create" in output
    assert "No PR, merge, push, close, or GitHub mutation was performed" in output
    assert "inspect and reuse it instead of creating duplicates" in output


def plan_pr_for_issue_from_handoff_for_test(
    handoff: HandoffPlan,
    branch_files: list[str],
):
    import signposter.pr as pr_module

    original_handoff = pr_module.plan_handoff_for_issue
    original_diff = pr_module._get_branch_changed_files
    try:
        pr_module.plan_handoff_for_issue = lambda repo, issue: handoff
        pr_module._get_branch_changed_files = lambda worktree, base, source: branch_files
        return pr_module.plan_pr_for_issue("test/repo", 4)
    finally:
        pr_module.plan_handoff_for_issue = original_handoff
        pr_module._get_branch_changed_files = original_diff
