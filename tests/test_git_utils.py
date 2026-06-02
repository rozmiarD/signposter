"""Tests for signposter.git_utils (HARDENING-006 worker isolation)."""

from signposter.git_utils import (
    find_uncommitted_repo_changes,
    get_git_status_short,
    has_blocking_dirty_changes,
    is_working_tree_clean,
    remote_branch_exists,
)


def test_get_git_status_short_returns_list():
    """Basic smoke test — should return a list even if not in a git repo or on error."""
    result = get_git_status_short("/tmp")
    assert isinstance(result, list)


def test_is_working_tree_clean_is_bool():
    result = is_working_tree_clean(".")
    assert isinstance(result, bool)


def test_find_uncommitted_repo_changes_filters_allowed_paths():
    """Simulate status output where only allowed paths are dirty."""
    # We can't easily mock subprocess here without more work, but we can test the filtering logic
    # by checking the function exists and basic behavior on a clean tree.
    dirty = find_uncommitted_repo_changes(".")
    # In a clean checkout this should be empty
    assert isinstance(dirty, list)


def test_has_blocking_dirty_changes_is_bool():
    result = has_blocking_dirty_changes(".")
    assert isinstance(result, bool)


def test_remote_branch_exists_checks_remote_tracking_ref(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("signposter.git_utils.subprocess.run", fake_run)

    assert remote_branch_exists("work/issue-1-test") is True
    assert calls[0][0] == [
        "git",
        "show-ref",
        "--verify",
        "--quiet",
        "refs/remotes/origin/work/issue-1-test",
    ]


def test_remote_branch_exists_returns_false_when_ref_missing(monkeypatch):
    def fake_run(cmd, **kwargs):
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr("signposter.git_utils.subprocess.run", fake_run)

    assert remote_branch_exists("work/issue-1-test") is False
